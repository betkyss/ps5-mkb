"""Версионное приватное хранилище. Ошибка чтения не сбрасывает профиль."""
import copy
import os
from pathlib import Path
import tempfile
import uuid
from .schema import Problem, encode, loads, validate, require, digest, LIMIT

RECOMMENDED = {'response': 'Linear', 'lookBoost': 0, 'ramp': 0, 'lookDeadzone': 0,
               'moveDeadzone': 15, 'horizontal': 65, 'vertical': 55, 'zoom': 25,
               'scoped': 50, 'aimAssist': False, 'lookBoostVertical': 0,
               'zoomBoostHorizontal': 0, 'zoomBoostVertical': 0, 'zoomRamp': 0}
DETAILED_SETTINGS = {'lookBoostVertical', 'zoomBoostHorizontal', 'zoomBoostVertical', 'zoomRamp'}


def atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Store:
    def __init__(self, directory, preset, baseline):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not self.directory.is_symlink() and self.directory.stat().st_uid == os.getuid(), 'Нужен собственный каталог состояния')
        os.chmod(self.directory, 0o700)
        self.path = self.directory / 'store.json'
        from .journal import Journal
        self.journal = Journal(self.directory, 'store-inflight.json')
        self.preset, self.baseline = validate(preset), validate(baseline)
        if self.path.exists():
            require(not self.path.is_symlink(), 'Store не может быть symlink')
            os.chmod(self.path, 0o600)
            self.data = loads(self.path.read_bytes())
            self.check(self.data)
        else:
            self.data = dict(version=1, revision=1, selected='arc', profiles={'arc': preset},
                             applied=None, gameSettings={'actual': copy.deepcopy(RECOMMENDED), 'confirmedAt': ''})
            if not self.journal.pending():
                self._write(self.data, None)
            # An interrupted initial write must not prevent the service from
            # exposing release. No store write occurs before explicit recovery.

    def check(self, d):
        from .schema import fields
        fields(d, ('version', 'revision', 'selected', 'profiles', 'applied', 'gameSettings'))
        require(type(d['version']) is int and d['version'] == 1 and type(d['revision']) is int and d['revision'] > 0, 'Повреждена версия store')
        require(type(d['profiles']) is dict and 1 <= len(d['profiles']) <= 20, 'Нужно от 1 до 20 профилей')
        for k, p in d['profiles'].items():
            require(type(k) is str and k.isascii() and k.replace('-', '').isalnum() and len(k) <= 40, 'Некорректный id профиля')
            validate(p)
        require(type(d['selected']) is str and d['selected'] in d['profiles'], 'Выбран неизвестный профиль')
        fields(d['gameSettings'], ('actual', 'confirmedAt'))
        # Older checklists combined Boost/Ramp. Preserve those entries, leaving
        # missing detailed values unknown until the operator records them.
        fields(d['gameSettings']['actual'], RECOMMENDED.keys() - DETAILED_SETTINGS, DETAILED_SETTINGS)
        actual = d['gameSettings']['actual']
        require(actual['response'] in ('Linear', 'Exponential', 'Другое'), 'Неизвестная кривая')
        require(type(actual['aimAssist']) is bool, 'Aim assist должен быть boolean')
        from .schema import number
        for key in actual.keys() - {'response', 'aimAssist'}:
            if actual[key] is None and key in DETAILED_SETTINGS:
                continue
            number(actual[key], 0, 10000)
        require(type(d['gameSettings']['confirmedAt']) is str and len(d['gameSettings']['confirmedAt']) <= 64, 'Некорректная дата')
        if d['applied'] is not None:
            fields(d['applied'], ('digest', 'processId', 'sessionId', 'profile'), ('sessionGeneration', 'workerRevision'))
            validate(d['applied']['profile'])
            from .schema import digest
            require(d['applied']['digest'] == digest(d['applied']['profile']), 'Повреждён applied digest')
            require(all(type(d['applied'][k]) is str and len(d['applied'][k]) <= 80 for k in ('processId', 'sessionId')), 'Повреждена identity')

    def snapshot(self):
        return copy.deepcopy(self.data)

    def expect(self, revision):
        if type(revision) is not int or revision != self.data['revision']:
            raise Problem('Профиль изменился в другом окне. Обновите страницу перед сохранением.', 409)

    def commit(self, candidate):
        candidate = copy.deepcopy(candidate)
        candidate['revision'] = self.data['revision'] + 1
        self.check(candidate)
        serialized = encode(candidate)
        require(len(serialized) <= LIMIT, "Хранилище превышает 64 КиБ")
        self._write(candidate, self.data)
        self.data = candidate
        return self.snapshot()

    def _write(self, candidate, previous):
        require(not self.journal.pending(), 'Store journal требует восстановления', 503)
        entry = dict(version=1, operationId=uuid.uuid4().hex, command='store', phase='intent',
                     beforeBytes=encode(previous).decode() if previous is not None else None,
                     candidateBytes=encode(candidate).decode())
        self.journal.write(entry)
        try:
            serialized = encode(candidate)
            atomic_write(self.path, serialized)
        except OSError as original:
            if previous is not None:
                try:
                    atomic_write(self.path, encode(previous))
                except OSError as rollback:
                    raise Problem(f'Запись store не подтверждена. Rollback store FAILED: {rollback}', 503) from original
            raise
        self.data = copy.deepcopy(candidate)
        entry.update(phase='terminal',result='candidate')
        self.journal.write(entry)
        self.journal.clear()

    def recover(self):
        entry = self.journal.read()
        require(entry['command'] == 'store', 'Неверный store journal', 503)
        if self.path.exists():
            raw = self.path.read_bytes()
        else:
            require(entry['beforeBytes'] is None, 'Store исчез после записи. Требуется ручное восстановление.', 503)
            raw = entry['candidateBytes'].encode()
        require(raw.decode() in (entry['beforeBytes'], entry['candidateBytes']),
                'Store не совпадает с before/candidate. Требуется ручная проверка.', 503)
        value = loads(raw)
        self.check(value)
        # Re-establish durability of the observed bytes before recording outcome.
        atomic_write(self.path, raw)
        self.data=value
        entry.update(phase='terminal',result='candidate' if raw.decode()==entry['candidateBytes'] else 'before')
        self.journal.write(entry)
        self.journal.clear()
        return self.snapshot()


    def save_launch(self, profile):
        # Отдельный immutable snapshot не увеличивает основной store.
        data = encode(validate(copy.deepcopy(profile)))
        require(len(data) <= LIMIT, 'Профиль запуска превышает 64 КиБ')
        path = self.directory / ('launch-' + digest(profile) + '.json')
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            require(not path.is_symlink() and path.read_bytes() == data, 'Snapshot запуска повреждён')
        else:
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                path.unlink(missing_ok=True)
                raise
        directory = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return path

    def launch_profile(self, actual_digest):
        if not isinstance(actual_digest, str) or len(actual_digest) != 64 or any(c not in '0123456789abcdef' for c in actual_digest):
            return None
        path = self.directory / ('launch-' + actual_digest + '.json')
        if not path.exists():
            return None
        require(not path.is_symlink(), 'Snapshot запуска не может быть symlink')
        raw = path.read_bytes()
        profile = validate(loads(raw))
        require(encode(profile) == raw and digest(profile) == actual_digest, 'Snapshot запуска повреждён')
        return profile
