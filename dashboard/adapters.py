"""Фиксированные команды existing baseline и opt-in native socket."""
import hashlib
import errno
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import threading
import uuid
from .schema import Problem, loads, encode, require

ROOT = Path(__file__).resolve().parents[1]
from .config import load_connection, validate_connection

class LaunchNotStarted(Problem):
    """The launcher proves that no client started for this intent."""


def run(argv, timeout=12):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Problem('Команда недоступна или превысила время ожидания', 503) from exc
    if r.returncode:
        # Только helper может вернуть безопасную диагностическую строку. Не печатаем env.
        raise Problem('Команда отклонена: ' + (r.stderr.strip()[-800:] if argv[0] == sys.executable else 'ошибка процесса'), 503)
    return r.stdout


class Baseline:
    """Состояние без клиента. Старые личные сборки не управляются."""
    def cancel(self):
        pass

    def status(self):
        return dict(adapter='baseline', processAlive=False, connected=False, capture='unknown',
                    canApply=False, reason='Клиент не запущен. Подключитесь к PS5.',
                    permission='unknown', device='unknown', digest=None)

    def control(self, action, cancelled=None):
        raise Problem('Нет подключённого клиента', 503)

    def apply(self, profile, expected=None):
        raise Problem('Применение требует подключённого клиента', 409)


class Native:
    def __init__(self, path):
        self.path = Path(path)

    def request(self, command, expected=None, profile=None, operation_id=None, deadline=None, launch_identity=None, audio=None):
        deadline = deadline if deadline is not None else time.monotonic() + (.6 if command == "status" else 7)
        try:
            parent, sockstat = self.path.parent.stat(), self.path.lstat()
            require(parent.st_uid == os.getuid() and not parent.st_mode & 0o077 and
                    sockstat.st_uid == os.getuid() and stat.S_ISSOCK(sockstat.st_mode), 'Небезопасный native socket')
            request = dict(command=command, requestId=uuid.uuid4().hex)
            if audio is not None:
                request.update(audio)
            if expected:
                request.update(processId=expected['processId'], sessionId=expected['sessionId'], expectedDigest=expected['digest'],
                               sessionGeneration=expected.get('sessionGeneration', 0), captureGeneration=expected.get('captureGeneration', 0),
                               controlEpoch=expected.get('controlEpoch', 0))
            if command == 'live':
                request['runtimeRevision'] = expected.get('recoil', {}).get('runtimeRevision', 0)
            if operation_id:
                request['operationId'] = operation_id
            if launch_identity:
                request.update(launchNonce=launch_identity['launchNonce'],binaryDigest=launch_identity['binaryDigest'],
                               expectedDigest=launch_identity['profileDigest'])
            if profile is not None:
                request['profileBytes'] = encode(profile).decode()
            with socket.socket(socket.AF_UNIX) as client:
                def budget():
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'Native общий deadline истёк', 503)
                    client.settimeout(remaining)
                budget()
                client.connect(str(self.path))
                budget()
                client.sendall(encode(request) + b'\n')
                data = bytearray()
                while b'\n' not in data:
                    budget()
                    chunk = client.recv(8192)
                    require(bool(chunk), 'Native bridge закрыл соединение')
                    data.extend(chunk)
                    require(len(data) <= 65536, 'Native ответ слишком велик')
            result = loads(bytes(data))
            require(type(result) is dict, 'Неверный native response envelope', 503)
            require(result.get('requestId') == request['requestId'], 'Native requestId не совпадает')
            if command == 'audio':
                status = result.get('status')
                require(type(status) is dict and status.get('headsetAudioControlVersion') == 1, 'Audio недоступно в этой сборке', 409)
                require(all(status.get(key) == audio[key] for key in ('processId', 'sessionId', 'sessionGeneration')),
                        'Audio session изменилась', 409)
                snapshot = status.get('audio')
                require(type(snapshot) is dict and type(snapshot.get('audioRevision')) is int and snapshot['audioRevision'] >= 0,
                        'Некорректный audio snapshot', 503)
                if result.get('ok') is True:
                    require(snapshot.get('operationId') == audio['operationId'], 'Audio ACK identity не совпадает', 503)
                return dict(status=status, accepted=result.get('ok') is True, error=result.get('error', ''))
            if result.get('ok') is not True:
                raise Problem(result.get('error') or 'Native bridge отклонил запрос', 503)
            status = result['status']
            require(type(status) is dict, 'Неверный native status')
            return dict(status, adapter='native', processAlive=True)
        except (OSError, KeyError, ValueError) as exc:
            raise Problem('Native bridge недоступен или ответ устарел', 503) from exc

    def status(self, deadline=None):
        return self.request('status', deadline=deadline)

    def _focus(self, expected, deadline, cancelled=None):
        pid = expected.get('pid')
        require(type(pid) is int and pid > 1, 'Не подтверждён PID окна клиента', 503)
        binary = ROOT / 'build/client/gui/chiaki.app/Contents/MacOS/chiaki'
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Истекло время активации окна', 503)
        actual = run(['ps', '-p', str(pid), '-o', 'comm='], min(1, remaining)).strip()
        require(Path(actual) == binary, 'PID окна больше не принадлежит клиенту', 503)
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Истекло время активации окна', 503)
        # Qt requestActivate alone cannot bring an inactive macOS app forward.
        # Target the verified PID; never launch another application instance.
        require(cancelled is None or not cancelled.is_set(), 'Операция отменена', 409)
        script = ('ObjC.import("AppKit"); '
                  f'var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier({pid}); '
                  'if (!app || app.isTerminated) throw Error("Client exited"); '
                  'app.unhide; if (!app.activateWithOptions(0)) throw Error("Activation rejected");')
        try:
            run(['osascript', '-l', 'JavaScript', '-e', script], min(2, remaining))
        except Problem as exc:
            raise Problem('Не удалось активировать окно Chiaki. Откройте окно клиента и повторите.', 503) from exc

    def control(self, action, expected=None, operation_id=None, cancelled=None):
        deadline = time.monotonic() + 7
        current = expected if action == 'release' else expected or self.status(deadline=deadline)
        if action == 'arm':
            require(cancelled is None or not cancelled.is_set(), 'Операция отменена', 409)
            self._focus(current, deadline, cancelled)
            require(cancelled is None or not cancelled.is_set(), 'Операция отменена', 409)
        return self.request(action, current, operation_id=operation_id, deadline=deadline)

    def apply(self, profile, expected=None, operation_id=None):
        return self.request('apply', expected or self.status(), profile, operation_id=operation_id)


class Adapters:
    def __init__(self, directory):
        self.native = Native(Path(directory) / 'native.sock')
        self.baseline = Baseline()

    def choose(self):
        if (self.native.path.parent / 'native-rejected.json').exists():
            return self.baseline
        # Существующий socket с ошибкой не переключает mutation на другой процесс.
        return self.native if self.native.path.exists() else self.baseline

    def status(self):
        try:
            return self.choose().status()
        except Problem as exc:
            return dict(adapter='native', processAlive=False, capture='unknown', connected=None,
                        canApply=False, reason=str(exc), digest=None)

    def control(self, action, expected=None, operation_id=None, cancelled=None):
        require(action in ('arm', 'release'), 'Неизвестная команда')
        chosen = self.choose()
        if action == 'release':
            self.baseline.cancel()
            # Native performs identity preflight on the reserved release lane.
            # No ordinary status connection is required, even under saturation.
            if isinstance(chosen, Native):
                return chosen.control(action, expected=expected, operation_id=operation_id)
            try:
                status = chosen.status()
            except Problem:
                status = {}
            if status.get('processAlive') is not True:
                result = subprocess.run(['pgrep', '-x', 'chiaki'], capture_output=True, timeout=3, check=False)
                require(result.returncode == 1, 'Есть неподтверждённый клиент. Освобождение ввода не доказано.')
                return dict(processAlive=False, connected=False, capture='off', reason='Клиент не запущен, удержаний нет',
                            releaseConfirmed=True, releaseProof='no-client', cleanupFinished=True)
        if isinstance(chosen, Native):
            require(cancelled is None or not cancelled.is_set(), 'Операция отменена', 409)
            return chosen.control(action, expected=expected, operation_id=operation_id, cancelled=cancelled)
        return chosen.control(action, cancelled=cancelled)

    def apply(self, profile, expected=None, operation_id=None):
        chosen = self.choose()
        if isinstance(chosen, Native):
            return chosen.apply(profile, expected, operation_id=operation_id)
        return chosen.apply(profile, expected)

    def live(self, profile, expected, operation_id):
        chosen = self.choose()
        require(isinstance(chosen, Native), 'Live update требует native-клиент', 409)
        return chosen.request('live', expected, profile, operation_id=operation_id)

    def audio(self, body):
        chosen = self.choose()
        require(isinstance(chosen, Native), 'Audio требует новую native-сборку', 409)
        return chosen.request('audio', audio=body, deadline=time.monotonic() + 2)

    def prepare_launch(self, profile_path_factory):
        require(not (self.native.path.parent / 'native-rejected.json').exists(),
                'Новая native-сборка отклонена из-за регрессии плавности. Проверьте и восстановите клиент перед новым запуском.')
        # Проверка всех chiaki, а не только PID из metadata. Второй stream запрещён.
        check = subprocess.run(['pgrep', '-x', 'chiaki'], capture_output=True, timeout=3, check=False)
        require(check.returncode == 1, 'Клиент уже работает. Сначала безопасно завершите текущую сессию.')
        require(not self.native.path.exists(), 'Native socket уже существует. Проверьте процесс вручную.')
        binary = ROOT / 'build/client/gui/chiaki.app/Contents/MacOS/chiaki'
        require(binary.is_file(), 'Сначала соберите dashboard app')
        binary_bytes = binary.read_bytes()
        require(b'CHIAKI_MKB_HEADSET_QUIET' in binary_bytes,
                'Установите новую сборку клиента: текущая не поддерживает headset quiet', 503)
        connection = load_connection(self.native.path.parent)
        # До проверки конфликта не создаём и не заменяем snapshot запуска.
        profile_path = profile_path_factory()
        expected_digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        return dict(launchNonce=uuid.uuid4().hex, binary=str(binary), connection=connection,
                    binaryDigest=hashlib.sha256(binary_bytes).hexdigest(),
                    profilePath=str(profile_path), profileDigest=expected_digest)

    def _launch_identity(self, result, plan, pid=None):
        require(result.get('launchNonce')==plan['launchNonce'] and
                result.get('binaryDigest')==plan['binaryDigest'] and
                result.get('digest')==plan['profileDigest'] and
                type(result.get('pid')) is int and (pid is None or result['pid']==pid),
                'Launch nonce, binary, PID или profile bytes не совпадают', 503)
        actual=run(['ps','-p',str(result['pid']),'-o','comm='],2).strip()
        require(Path(actual).resolve()==Path(plan['binary']).resolve() and
                hashlib.sha256(Path(plan['binary']).read_bytes()).hexdigest()==plan['binaryDigest'],
                'Launch process identity не подтверждена',503)

    def recover_launch(self, plan):
        require(Path(plan['binary'])==ROOT/'build/client/gui/chiaki.app/Contents/MacOS/chiaki' and
                Path(plan['profilePath'])==self.native.path.parent/('launch-'+plan['profileDigest']+'.json'),
                'Неизвестные launch paths',503)
        require(hashlib.sha256(Path(plan['binary']).read_bytes()).hexdigest()==plan['binaryDigest'] and
                hashlib.sha256(Path(plan['profilePath']).read_bytes()).hexdigest()==plan['profileDigest'],
                'Launch bytes изменились',503)
        if not self.native.path.exists():
            absent = subprocess.run(['pgrep', '-x', 'chiaki'], capture_output=True, timeout=3, check=False)
            require(absent.returncode == 1, 'Отсутствие клиента после запуска не подтверждено', 503)
            return dict(processAlive=False, connected=False, capture='off', cleanupFinished=True,
                        releaseConfirmed=True, releaseProof='no-client')
        result=self.native.request('release', profile=None, launch_identity=plan)
        self._launch_identity(result, plan, plan.get('childPid'))
        require(type(result.get('sessionGeneration')) is int and result['sessionGeneration']>0 and
                bool(result.get('processId')) and bool(result.get('sessionId')) and
                result.get('controlOperationId')=='' and result.get('workerRevision')==0 and
                result.get('captureGeneration')==0 and type(result.get('controlEpoch')) is int,
                'Запущенный клиент уже участвовал в другой операции',503)
        return result

    def launch(self, profile_path_factory, cancelled=None, spawn=None, plan=None):
        plan = plan or self.prepare_launch(profile_path_factory)
        binary = Path(plan['binary']);profile_path=Path(plan['profilePath'])
        try:
            binary_bytes = binary.read_bytes()
            require(b'CHIAKI_MKB_HEADSET_QUIET' in binary_bytes,
                    'Установите новую сборку клиента: текущая не поддерживает headset quiet', 503)
            require(hashlib.sha256(binary_bytes).hexdigest()==plan['binaryDigest'] and
                    hashlib.sha256(profile_path.read_bytes()).hexdigest()==plan['profileDigest'],
                    'Launch bytes изменились',503)
            connection = validate_connection(plan.get('connection'))
            check = subprocess.run(['pgrep','-x','chiaki'],capture_output=True,timeout=3,check=False)
            require(check.returncode==1 and not self.native.path.exists(), 'Клиент или socket появился до запуска')
            require(cancelled is None or not cancelled.is_set(), 'Запуск отменён', 409)
        except (Problem,OSError,subprocess.SubprocessError) as exc:
            raise LaunchNotStarted(str(exc),503) from exc
        env = {key: os.environ[key] for key in ('HOME', 'USER', 'LOGNAME', 'TMPDIR',
                                                'LANG', 'LC_ALL', 'LC_CTYPE', '__CF_USER_TEXT_ENCODING')
               if key in os.environ}
        env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', CHIAKI_FORCE_VULKAN_FALLBACK='1',
                   CHIAKI_FORCE_VULKAN_FALLBACK_REASON='MKB dashboard: OpenGL, no MoltenVK runtime required',
                   CHIAKI_MKB_WORKER='1', CHIAKI_MKB_MOUSE_SOURCE='hid',
                   CHIAKI_MKB_HID_VID=hex(connection['mouse']['vendor_id']), CHIAKI_MKB_HID_PID=hex(connection['mouse']['product_id']),
                   CHIAKI_MKB_HID_LOCATION=hex(connection['mouse']['location_id']), CHIAKI_MKB_PROFILE=str(profile_path),
                   CHIAKI_MKB_SOCKET=str(self.native.path), CHIAKI_MKB_HEADSET_QUIET='1')
        env['CHIAKI_MKB_LAUNCH_NONCE']=plan['launchNonce']
        attempted=False
        try:
            with open(self.native.path.parent / 'app.log', 'ab') as log:
                os.chmod(log.name, 0o600)
                def create():
                    nonlocal attempted
                    attempted=True
                    try:
                        return subprocess.Popen([str(binary), 'stream', connection['console_name'], connection['console_host']], env=env,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                    except OSError as exc:
                        if isinstance(exc,(PermissionError,FileNotFoundError)) or exc.errno==errno.ENOEXEC:
                            raise LaunchNotStarted(str(exc),503) from exc
                        raise
                child = spawn(create) if spawn else create()
        except (OSError,Problem) as exc:
            if not attempted:raise LaunchNotStarted(str(exc),503) from exc
            raise
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            require(child.poll() is None, 'Запущенный клиент завершился', 503)
            try:
                releasing=cancelled is not None and cancelled.is_set()
                result = self.native.request('release', deadline=deadline, launch_identity=plan) if releasing else self.native.status(deadline=deadline)
                if cancelled is not None and cancelled.is_set():
                    self._launch_identity(result, plan, child.pid)
                    if not releasing:
                        result=self.native.request('release',deadline=deadline,launch_identity=plan)
                        self._launch_identity(result,plan,child.pid)
                    require(result.get('releaseConfirmed') is True and result.get('releaseProof')=='native-cleanup' and
                            result.get('capture')=='off' and result.get('cleanupFinished') is True,
                            'Выключение запущенного клиента не подтверждено',503)
                    raise Problem('Запуск отменён; сопоставленный клиент выключен',409)
                if result.get('connected') is True:
                    self._launch_identity(result, plan, child.pid)
                    require(child.poll() is None,'Клиент завершился',503)
                    return result
            except Problem as exc:
                if cancelled is not None and cancelled.is_set() and exc.status==409:raise
                if cancelled is not None and cancelled.is_set():
                    raise Problem('Запуск отменён; выключение запущенного клиента не подтверждено',503) from exc
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
        raise Problem('Запуск запрошен, но соединение с PS5 не подтверждено. Проверьте статус.', 503)
