"""One bounded durable intent. Its presence, including corruption, blocks grants."""
import os
from .schema import Problem, encode, loads, require, fields, validate, digest
from .store import atomic_write


class Journal:
    LIMIT = 262144

    def __init__(self, directory, name='control-inflight.json'):
        self.path = directory / name
        self._uncertain = False

    def pending(self):
        if self._uncertain:
            return True
        # lexists includes broken symlinks. Never treat an unreadable file as absent.
        if not os.path.lexists(self.path):
            return False
        try:
            return self.read()['phase'] != 'complete'
        except (OSError, ValueError, TypeError, KeyError, Problem):
            return True

    def read(self):
        require(not self.path.is_symlink(), 'Небезопасный control journal')
        with self.path.open('rb') as stream:
            raw = stream.read(self.LIMIT + 1)
        require(len(raw) <= self.LIMIT, 'Control journal превышает лимит')
        value = loads(raw, limit=self.LIMIT)
        require(type(value) is dict and value.get('version') == 1 and
                value.get('command') in ('arm', 'apply', 'live', 'launch', 'store') and
                type(value.get('operationId')) is str, 'Повреждён control journal')
        if value['command'] == 'store':
            fields(value, ('version','operationId','command','phase','beforeBytes','candidateBytes'), ('result',))
            require(value['phase'] in ('intent','terminal','complete') and
                    (value['beforeBytes'] is None or type(value['beforeBytes']) is str) and
                    type(value['candidateBytes']) is str, 'Повреждён store journal')
            if value['phase']!='intent':
                require(value.get('result') in ('before','candidate'), 'Нет terminal store результата')
            for raw in (value['beforeBytes'],value['candidateBytes']):
                if raw is None:continue
                saved=loads(raw)
                fields(saved,('version','revision','selected','profiles','applied','gameSettings'))
                require(encode(saved).decode()==raw and saved['version']==1 and
                        type(saved['revision']) is int and saved['revision']>0 and
                        type(saved['profiles']) is dict and type(saved['selected']) is str and saved['selected'] in saved['profiles'],
                        'Повреждены store bytes')
                for profile in saved['profiles'].values():validate(profile)
            return value
        fields(value, ('version','operationId','command','phase','before','savedRevision','selectedId',
                       'beforeBytes','beforeDigest','candidateBytes','candidateDigest'), ('result','launch'))
        require(value['phase'] in ('intent','terminal','complete') and type(value['before']) is dict and
                type(value['savedRevision']) is int and value['savedRevision'] > 0 and
                type(value['selectedId']) is str and type(value['candidateBytes']) is str,
                'Повреждён control journal')
        if 'result' in value or value['phase']!='intent':
            require(type(value.get('result')) is dict, 'Нет terminal control результата')
        candidate=validate(loads(value['candidateBytes']))
        require(digest(candidate)==value['candidateDigest'] and encode(candidate).decode()==value['candidateBytes'],
                'Повреждены candidate bytes')
        if value['command'] in ('apply', 'live'):
            require(type(value['beforeBytes']) is str, 'Нет before bytes')
            previous=validate(loads(value['beforeBytes']))
            require(digest(previous)==value['beforeDigest'] and encode(previous).decode()==value['beforeBytes'],
                    'Повреждены before bytes')
        before=value['before']
        if value['command']=='launch':
            plan=value.get('launch')
            fields(plan,('launchNonce','binary','binaryDigest','profilePath','profileDigest'),('childPid',))
            require(type(plan['launchNonce']) is str and len(plan['launchNonce'])==32 and
                    all(c in '0123456789abcdef' for c in plan['launchNonce']) and
                    all(type(plan[k]) is str and len(plan[k])==64 and all(c in '0123456789abcdef' for c in plan[k])
                        for k in ('binaryDigest','profileDigest')) and
                    plan['profileDigest']==value['candidateDigest'] and
                    type(plan['binary']) is str and type(plan['profilePath']) is str and
                    ('childPid' not in plan or type(plan['childPid']) is int and plan['childPid']>1),
                    'Повреждена launch identity')
        if value['command'] != 'launch' and (before.get('adapter')=='native' or value['command'] in ('apply', 'live')):
            require(all(type(before.get(k)) is str and bool(before[k]) for k in ('processId','sessionId')) and
                    all(type(before.get(k)) is int and 0<=before[k]<=9007199254740991 for k in
                        ('sessionGeneration','captureGeneration','workerRevision','controlEpoch')),
                    'Нет исходной native identity')
        return value

    def write(self, value):
        raw = encode(value)
        require(len(raw) <= self.LIMIT, 'Control journal превышает лимит')
        atomic_write(self.path, raw)

    def clear(self):
        # Keep bounded terminal evidence at a stable path. Never unlink the only
        # recoverable record before a directory fsync. A complete tombstone can
        # only follow an already durable terminal result.
        value = self.read()
        require(value['phase'] in ('terminal','complete'), 'Нет terminal результата')
        value['phase'] = 'complete'
        # Replacement may succeed before directory fsync fails. Keep the
        # durable terminal evidence recoverable in this process until retry.
        self._uncertain = True
        self.write(value)
        self._uncertain = False
