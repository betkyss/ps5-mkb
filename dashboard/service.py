"""Cached passive status and durable, cancellable control operations."""
import copy
import threading
import time
import uuid
from .schema import Problem, fields, validate, digest, encode, require, loads, audio_request
from .store import atomic_write, RECOMMENDED
from .journal import Journal
from .adapters import LaunchNotStarted


class Service:
    ACK_GENERATION = '_ackGeneration'

    def __init__(self, store, adapter):
        self.store, self.adapter = store, adapter
        self.lock = threading.Lock()  # short operation registration; no IO under it
        self.operation = None
        self.release_generation = 0
        self.error = ''
        self.started = time.time()
        self.instance = uuid.uuid4().hex
        self.journal = Journal(store.directory)
        self.recovery_required = self.journal.pending() or store.journal.pending() or (store.directory / 'recovery-required').exists()
        self.rejected = (store.directory / 'native-rejected.json').exists()
        self.saved = store.snapshot()
        self.sample_lock = threading.Lock()
        self.sample = None
        self.sampled = 0.0
        self.sample_wall = 0.0
        self.sampling = False
        self.sample_sequence = 0
        self.sample_epoch = 0
        self.sample_version = -1

    def bootstrap(self):
        return dict(store=copy.deepcopy(self.saved), preset=self.store.preset, baseline=self.store.baseline,
                    recommended=RECOMMENDED, serviceInstance=self.instance)

    def _commit(self, data):
        try:
            return self.store.commit(data)
        finally:
            self.saved = self.store.snapshot()

    def _sample(self, epoch):
        began = time.monotonic()
        began_wall = time.time()
        try:
            native = self.adapter.status()
            require(type(native) is dict, 'Неверный native status', 503)
            native = copy.deepcopy(native)
            # Local RTT conservatively adds to the native sample's age.
            native['sampleAgeMs'] = max(0, native.get('sampleAgeMs', 0)) + (time.monotonic() - began) * 1000
        except (OSError, Problem, TypeError, ValueError) as exc:
            native = dict(capture='unknown', connected=None, canApply=False, reason=str(exc), sampleAgeMs=1001)
        finally:
            with self.sample_lock:
                if 'native' in locals() and type(native) is dict and epoch == self.sample_epoch:
                    self.sample = native
                    self.sampled = began
                    self.sample_wall = began_wall
                    self.sample_version = epoch
                    self.sample_sequence += 1
                self.sampling = False

    def status(self):
        now = time.monotonic()
        with self.sample_lock:
            if not self.sampling and (self.sample is None or now-self.sampled >= .25):
                self.sampling = True
                threading.Thread(target=self._sample, args=(self.sample_epoch,), daemon=True).start()
            sample = self.sample
            age = max(0, now-self.sampled)*1000 if self.sampled else 1001
            sequence = self.sample_sequence
            sampled_wall = self.sample_wall
            version = self.sample_version
            epoch = self.sample_epoch
        native = copy.deepcopy(sample) if sample is not None else dict(capture='unknown', connected=None, canApply=False)
        cached_age = age
        age += max(0, native.get('sampleAgeMs', 0))
        stale = age >= 1000 or version != epoch
        for key in ('stateFeedbackAttemptAgeMs', 'stateFeedbackSuccessAgeMs'):
            if type(native.get(key)) in (int, float) and native[key] >= 0:
                native[key] += cached_age
        if (stale or native.get('connected') is not True or
                native.get('stateFeedbackHealth') not in ('ok', 'error') or
                type(native.get('stateFeedbackAttemptAgeMs')) not in (int, float) or
                not 0 <= native['stateFeedbackAttemptAgeMs'] < 1000):
            native['stateFeedbackHealth'] = 'unknown'
        recoil = native.get('recoil')
        if isinstance(recoil, dict):
            recoil_age = recoil.get('sampleAgeMs')
            if type(recoil_age) in (int, float) and recoil_age >= 0:
                recoil['sampleAgeMs'] += cached_age
            else:
                recoil['fresh'] = False
            if type(recoil_age) not in (int, float) or stale or native.get('connected') is not True or native.get('capture') != 'active' or recoil.get('sampleAgeMs', 1001) >= 1000:
                recoil['fresh'] = False
        audio = native.get('audio')
        if isinstance(audio, dict):
            audio['sampleAgeMs'] = max(0, audio.get('sampleAgeMs', 0)) + cached_age
            audio['stale'] = audio['sampleAgeMs'] >= 1000
        if stale:
            native.update(capture='unknown', connected=None, canApply=False, permission='unknown', selectionConfirmed=False)
        if self.rejected:
            native.update(nativeRejected=True, canApply=False)
            native['reason'] = ' '.join(filter(None, (native.get('reason'), 'Новая native-сборка отклонена из-за регрессии плавности. Доступен только принятый baseline.')))
        if self.recovery_required:
            native.update(canApply=False, recoveryRequired=True,
                          reason='Результат предыдущей операции не подтверждён. Выключите ввод и сверьте состояние клиента.')
        saved = self.saved
        selected = saved['profiles'][saved['selected']]
        applied = saved['applied']
        known = bool(not stale and applied and native.get('digest') == applied['digest'] and
                     native.get('processId') == applied['processId'] and native.get('sessionId') == applied['sessionId'] and
                     type(applied.get('sessionGeneration')) is int and native.get('sessionGeneration') == applied['sessionGeneration'] and
                     type(applied.get('workerRevision')) is int and native.get('workerRevision') == applied['workerRevision'])
        service_busy = self.operation is not None
        native_busy = bool(native.get('busy'))
        result = dict(native, serviceAlive=True, busy=service_busy or native_busy, nativeBusy=native_busy,
                    serviceBusy=service_busy, serviceInstance=self.instance, sampleSequence=sequence,
                    sampleAgeMs=age, stale=stale, sampledAt=sampled_wall, statusEpoch=epoch, selectedId=saved['selected'],
                    savedDigest=digest(selected), savedRevision=saved['revision'],
                    appliedDigest=native.get('digest'), appliedKnown=known,
                    pendingProfile=(digest(selected) != native.get('digest')) if native.get('digest') else None, error=self.error)
        return self.fence_status(result)

    def fence_status(self, result):
        # Also called at HTTP serialization. A newer publication cannot lend its
        # epoch to a state copied before cancellation.
        if result.get('statusEpoch') != self.sample_epoch:
            result = dict(result, capture='unknown', connected=None, canApply=False,
                          appliedKnown=False, stale=True, stateFeedbackHealth='unknown', permission='unknown', selectionConfirmed=False)
            if isinstance(result.get('recoil'), dict):
                result['recoil'] = dict(result['recoil'], fresh=False)
        return result

    def check_ack_generation(self, generation):
        # Successful permitting HTTP ACKs linearize at this final check.
        # The caller releases this lock before writing any response bytes.
        with self.lock:
            require(type(generation) is int and generation == self.release_generation,
                    'Подтверждение операции отменено последующим выключением ввода', 409)

    @staticmethod
    def _same(a, b):
        return all(a.get(k) == b.get(k) for k in ('processId', 'sessionId', 'sessionGeneration'))

    @staticmethod
    def _check_cancel(op):
        require(not op['cancelled'].is_set(), 'Операция отменена', 409)

    def _release(self):
        # Invalidating the local intent happens before socket/disk work, including
        # when the granting operation has not yet reached native begin.
        with self.lock:
            self.sample_epoch += 1
            self.release_generation += 1
            op = self.operation
            if op:
                op['cancelled'].set()
        try:
            result = self.adapter.control('release')
            self.check_release(result)
            return dict(status=result)
        finally:
            with self.lock:
                self.sample_epoch += 1
                self.release_generation += 1

    def mutate(self, command, body):
        require(type(body) is dict, 'Ожидался объект')
        if command == 'audio':
            audio_request(body)
            # Independent short lane: no profile lock, journal, revision or cancellation epoch.
            result = self.adapter.audio(body)
            with self.sample_lock:
                self.sampled = 0
            return result
        if command == 'release':
            fields(body, (), ('revision',))
            return self._release()
        if not self.lock.acquire(blocking=False):
            raise Problem('Предыдущая операция ещё выполняется', 409)
        try:
            require(self.operation is None, 'Предыдущая операция ещё выполняется', 409)
            op = dict(id=uuid.uuid4().hex, cancelled=threading.Event(), releaseGeneration=self.release_generation)
            self.operation = op
            self.sample_epoch += 1
        finally:
            self.lock.release()
        try:
            result = self._mutate(command, body, op)
            if command in ('arm','apply','live','launch'):
                result[self.ACK_GENERATION] = op['releaseGeneration']
            self.error = ''
            return result
        except (OSError, Problem) as exc:
            self.error = str(exc)
            self.recovery_required = self.recovery_required or self.journal.pending() or self.store.journal.pending() or 'Rollback store FAILED' in str(exc)
            if isinstance(exc, Problem):
                raise
            raise Problem('Не удалось сохранить состояние. ' + str(exc), 503) from exc
        finally:
            with self.lock:
                if self.operation is op:
                    self.operation = None
                    self.sample_epoch += 1
            # Request a fresh coalesced sample on the next GET.
            with self.sample_lock:
                self.sampled = 0

    def _mutate(self, command, b, op):
        require(not self.recovery_required or command == 'recover', 'Journal требует восстановления. Доступно выключение ввода.')
        if self.rejected and command in ('apply', 'live', 'launch'):
            raise Problem('Новая native-сборка отклонена. Дождитесь исправления и повторной ручной приёмки.', 409)
        if command == 'recover':
            fields(b, ('revision',))
            return self._recover(op)
        if command in ('arm', 'apply', 'live', 'launch'):
            fields(b, ('revision', 'profile', 'runtimeFields') if command == 'live' else ('revision',))
            self.store.expect(b['revision'])
            before = self.adapter.status()
            if self.rejected and command == 'arm':
                require(before.get('adapter') == 'baseline', 'Включение отклонённого native-клиента заблокировано.')
            candidate = copy.deepcopy(self.saved['profiles'][self.saved['selected']])
            if command == 'live':
                candidate = copy.deepcopy(validate(b['profile']))
                require(type(b['runtimeFields']) is list and len(b['runtimeFields']) <= 3 and
                        all(k in ('enabled', 'vertical', 'rapidFireEnabled') for k in b['runtimeFields']),
                        'Неверные runtimeFields')
                require(before.get('adapter') == 'native' and before.get('connected') is True and
                        before.get('capture') in ('active', 'off'), 'Нет подключения для live update', 409)
                if before['capture'] == 'active':
                    runtime = before.get('recoil', {})
                    require(runtime.get('fresh') is True and type(runtime.get('runtimeRevision')) is int and
                            runtime.get('profileRevision') == before.get('workerRevision'),
                            'Нет свежего runtime snapshot', 409)
                    # Only explicit edits own these fields. A neighboring edit
                    # carries forward hotkeys observed by the native owner.
                    recoil = candidate.setdefault('recoil', {'vertical': 0})
                    rapid = candidate.setdefault('rapid_fire', {'enabled': False, 'interval_ms': 120, 'press_ms': 40})
                    if 'enabled' not in b['runtimeFields']: recoil['enabled'] = runtime['enabled']
                    if 'vertical' not in b['runtimeFields']: recoil['vertical'] = runtime['strengthPercent'] / 100
                    if 'rapidFireEnabled' not in b['runtimeFields']: rapid['enabled'] = runtime['rapidFireEnabled']
                    validate(candidate)
            launch = self.adapter.prepare_launch(lambda: self.store.save_launch(candidate)) if command == 'launch' else None
            previous = None
            if command in ('apply', 'live'):
                require(before.get('canApply') is True, before.get('reason') or 'Применение недоступно')
                known = [self.store.baseline, self.store.preset, *self.saved['profiles'].values()]
                if self.saved['applied']:
                    known.append(self.saved['applied']['profile'])
                previous = next((p for p in known if digest(p) == before.get('digest')), None)
                if previous is None:
                    previous = self.store.launch_profile(before.get('digest'))
                require(previous is not None, 'Предыдущий профиль неизвестен. Сначала запустите новую сборку с сохранённым профилем.')
            entry = dict(version=1, operationId=op['id'], command=command, phase='intent',
                         before=before, savedRevision=b['revision'], selectedId=self.saved['selected'],
                         beforeBytes=encode(previous).decode() if previous else None,
                         beforeDigest=digest(previous) if previous else before.get('digest'),
                         candidateBytes=encode(candidate).decode(), candidateDigest=digest(candidate))
            if launch is not None:
                entry['launch'] = launch
            # A directory-fsync error is a failure even if replacement happened.
            self.journal.write(entry)
            self.recovery_required = True
            if op['cancelled'].is_set():
                entry.update(phase='terminal',result={'noEffect':True})
                self.journal.write(entry);self.journal.clear();self.recovery_required=False
                self._check_cancel(op)
            if command == 'launch':
                try:
                    result = self.adapter.launch(lambda: self.store.save_launch(candidate),
                                                 cancelled=op['cancelled'], spawn=lambda create: self._spawn(op, create, entry), plan=launch)
                except LaunchNotStarted:
                    entry.update(phase='terminal',result={'noEffect':True})
                    self.journal.write(entry);self.journal.clear();self.recovery_required=False
                    raise
            elif command == 'arm':
                result = self.adapter.control('arm', expected=before, operation_id=op['id'], cancelled=op['cancelled'])
            elif command == 'live':
                result = self.adapter.live(candidate, before, operation_id=op['id'])
            else:
                result = self.adapter.apply(candidate, before, operation_id=op['id'])
            self._check_cancel(op)
            if command in ('apply','live','arm') and result.get('adapter') != 'baseline':
                require(self._same(before,result) and result.get('controlOperationId')==op['id'] and
                        result.get('controlEpoch',-1)>before.get('controlEpoch',-1),
                        'Native operation identity не подтверждена')
            if command in ('apply', 'live'):
                require(self._same(before, result) and result.get('digest') == digest(candidate) and
                        (result.get('capture') == before.get('capture') if command == 'live' else
                         result.get('capture') == 'off' and result.get('cleanupFinished') is True) and
                        result.get('captureGeneration') == before.get('captureGeneration') and
                        result.get('workerRevision', 0) > before.get('workerRevision', -1), 'Native не подтвердил профиль и neutral')
                data = self.store.snapshot()
                data['applied'] = dict(digest=digest(candidate), processId=result['processId'], sessionId=result['sessionId'],
                                       profile=candidate, sessionGeneration=result.get('sessionGeneration'), workerRevision=result.get('workerRevision'))
                if command == 'live': data['profiles'][data['selected']] = candidate
                saved = self._commit(data)
            elif command == 'arm':
                require(result.get('adapter') == 'baseline' or (self._same(before,result) and result.get('capture') == 'active' and
                        result.get('captureGeneration') == before.get('captureGeneration',-2)+1),
                        'Включение не подтверждено')
            entry.update(phase='terminal', result=result)
            self.journal.write(entry)
            self.journal.clear()
            self.recovery_required = False
            self._check_cancel(op)
            return dict(status=result, **({'store':saved} if command in ('apply', 'live') else {}))
        allowed = {
            'save': ('revision', 'profile'), 'import': ('revision', 'profileText'), 'create': ('revision', 'profile', 'id'),
            'select': ('revision', 'id'), 'reset': ('revision', 'kind'),
            'settings': ('revision', 'gameSettings'),
            'fork-drafts': ('revision','profile','gameSettings','sourceId','id')}
        require(command in allowed, 'Неизвестная команда')
        fields(b, allowed[command])
        self.store.expect(b['revision'])
        data = self.store.snapshot()
        if command == 'import':
            from .schema import loads
            require(type(b['profileText']) is str, 'Импорт должен содержать JSON текст')
            b = dict(b, profile=validate(loads(b['profileText'])))
        if command in ('save', 'create', 'import', 'fork-drafts'):
            validate(b['profile'])
            key = b['id'] if command in ('create','fork-drafts') else data['selected']
            if command in ('create','fork-drafts'):
                require(type(key) is str and key not in data['profiles'], 'Такой профиль уже существует')
            data['profiles'][key] = copy.deepcopy(b['profile'])
            if command in ('create','fork-drafts'):
                data['selected'] = key
            if command=='fork-drafts':
                require(type(b['sourceId']) is str and b['sourceId'] in data['profiles'], 'Исходный профиль не найден')
                data['gameSettings'] = copy.deepcopy(b['gameSettings'])
        elif command == 'select':
            require(type(b['id']) is str and b['id'] in data['profiles'], 'Профиль не найден')
            data['selected'] = b['id']
        elif command == 'reset':
            require(b['kind'] in ('preset', 'baseline'), 'Неизвестный preset')
            data['profiles'][data['selected']] = copy.deepcopy(self.store.baseline if b['kind'] == 'baseline' else self.store.preset)
        elif command == 'settings':
            data['gameSettings'] = b['gameSettings']
        self.store.check(data)
        if command in ('settings','fork-drafts') and data['gameSettings']['confirmedAt']:
            actual = data['gameSettings']['actual']
            require(all(key in actual and actual[key] is not None for key in RECOMMENDED),
                    'Для подтверждения запишите все значения с экрана PS5, включая отдельные Boost и Ramp')
        require(len(encode(data)) <= 65536, 'Хранилище превышает 64 КиБ')
        if command in ('select', 'reset', 'create','fork-drafts'):
            # Ни reset, ни смена профиля не оставляют held sources.
            self.check_release(self.adapter.control('release'))
        return dict(store=self._commit(data))


    def _spawn(self, op, create, entry=None):
        # The single OS spawn is serialized with local cancellation. No socket,
        # readiness wait or filesystem operation holds the coordination lock.
        with self.lock:
            if op['cancelled'].is_set():
                raise LaunchNotStarted('Запуск отменён до spawn',409)
            child = create()
            op['childPid'] = child.pid
        if entry is not None:
            entry['launch']['childPid']=child.pid
            self.journal.write(entry)
        return child

    def _recover(self, op):
        if self.store.journal.pending():
            try:
                self.store.recover()
            finally:
                self.saved = self.store.snapshot()
            if not self.journal.pending():
                self.recovery_required=False
                return dict(store=copy.deepcopy(self.saved))
        require(self.journal.pending(), 'Нет control journal для автоматического восстановления')
        entry = self.journal.read()
        before = entry['before']
        if entry.get('result',{}).get('noEffect') is True and entry['phase'] in ('terminal','complete'):
            self.journal.clear();self.recovery_required=False
            return dict(store=self.store.snapshot())
        if entry['command']=='launch':
            current = self.adapter.recover_launch(entry['launch'])
            self.check_release(current, native=current.get('releaseProof') != 'no-client')
            entry.update(phase='terminal',result=current)
            self.journal.write(entry);self.journal.clear();self.recovery_required=False
            return dict(status=current,store=self.store.snapshot())
        current = self.adapter.status()
        self._recovery_identity(entry, current)
        if before.get('adapter')=='baseline':
            require(current.get('adapter')=='baseline' and current.get('pid')==before.get('pid'), 'Baseline identity изменилась')
            released = self.adapter.control('release', expected=current)
            self.check_release(released)
            require(released.get('pid')==before.get('pid'), 'Baseline identity изменилась при отмене')
        else:
            require(self._same(before,current), 'Identity изменилась. Нужна ручная проверка клиента; journal сохранён.')
            released = self.adapter.control('release', expected=current)
            self.check_release(released, native=True)
            self._recovery_identity(entry, released)
            require(self._same(current,released) and
                    released.get('controlOperationId')==current.get('controlOperationId') and
                    released.get('captureGeneration',-2)>=current.get('captureGeneration',-1) and
                    (released.get('workerRevision')==current.get('workerRevision') or
                     entry['command'] in ('apply', 'live') and current.get('workerRevision')==before.get('workerRevision') and
                     released.get('controlOperationId')==entry['operationId'] and released.get('digest')==entry['candidateDigest'] and
                     released.get('workerRevision',-2)>current.get('workerRevision',-1)) and
                    current.get('controlEpoch',-2)<released.get('controlEpoch',-1)<=current.get('controlEpoch',-2)+2,
                    'Terminal identity изменилась при отмене. Journal сохранён.')
        self._check_cancel(op)
        if entry['command'] in ('apply', 'live'):
            candidate = validate(loads(entry['candidateBytes']))
            previous = validate(loads(entry['beforeBytes']))
            require(digest(candidate)==entry['candidateDigest'] and digest(previous)==entry['beforeDigest'], 'Повреждён journal digest')
            require(released.get('digest') in (entry['candidateDigest'],entry['beforeDigest']), 'Неизвестный applied digest')
            if released.get('digest') == entry['candidateDigest']:
                data = self.store.snapshot()
                data['applied'] = dict(digest=digest(candidate),processId=released['processId'],sessionId=released['sessionId'],
                                       profile=candidate,sessionGeneration=released.get('sessionGeneration'),workerRevision=released.get('workerRevision'))
                if entry['command'] == 'live': data['profiles'][entry['selectedId']] = candidate
                self._commit(data)
        entry.update(phase='terminal',result=released)
        self.journal.write(entry)
        self.journal.clear()
        self.recovery_required=False
        return dict(status=released,store=self.store.snapshot())

    @staticmethod
    def _recovery_identity(entry, current):
        before=entry['before']
        if before.get('adapter')=='baseline':
            return
        accepted=current.get('controlOperationId')==entry['operationId']
        # Safety releases may advance the epoch repeatedly without changing
        # operation identity. The release below still checks its own increment.
        require(current.get('controlOperationId') in (entry['operationId'],before.get('controlOperationId')) and
                current.get('captureGeneration') in (before.get('captureGeneration'),
                    before.get('captureGeneration',-2)+(1 if entry['command']=='arm' and accepted else 0)) and
                type(current.get('controlEpoch')) is int and
                before['controlEpoch']<=current['controlEpoch']<=9007199254740991 and
                type(current.get('workerRevision')) is int and
                (current['workerRevision']==before.get('workerRevision') or
                 entry['command'] in ('apply', 'live') and accepted and current.get('digest')==entry['candidateDigest'] and
                 before['workerRevision']<current['workerRevision']<=9007199254740991) and
                current.get('digest') in (entry['beforeDigest'], entry['candidateDigest'] if accepted and entry['command'] in ('apply', 'live') else entry['beforeDigest']),
                'Другая control operation, revision или generation. Journal сохранён.')

    @staticmethod
    def check_release(result, native=False):
        require(result.get('releaseConfirmed') is True and result.get('releaseProof') in
                ('native-cleanup', 'helper-command', 'no-client'), 'Выключение ввода не подтверждено')
        proof=result['releaseProof']
        if native:
            require(proof == 'native-cleanup', 'Требуется native acknowledgement')
        if proof in ('native-cleanup', 'no-client'):
            require(result.get('capture') == 'off' and result.get('cleanupFinished') is True,
                    'Native cleanup или отсутствие клиента не подтверждено')
        else:
            require(result.get('capture') in ('unknown', 'off'), 'Helper acknowledgement содержит ошибочное состояние')
