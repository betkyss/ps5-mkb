"""Пульт PS5 MKB. По умолчанию доступен только на этом компьютере."""
import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import os
from pathlib import Path
import secrets
import signal
import socket
import select
import time
import threading
from .adapters import Adapters, ROOT
from .schema import Problem, loads, encode, require, CODES, RESERVED, ACTIONS
from .service import Service
from .store import Store

from .config import STATE_DIR
WEB = ROOT / 'dashboard/web-isolated'


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    allow_reuse_address = True

    def __init__(self, address, service, allowed_hosts=()):
        self.allowed_hosts = {address[0], '127.0.0.1', 'localhost', *allowed_hosts}
        self.service = service
        self.csrf = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(22)
        self.priority_slots = threading.BoundedSemaphore(2)
        self.audio_slots = threading.BoundedSemaphore(2)
        self._audio_pools = {}
        self._admission_lock = threading.Lock()
        self._admission = {}
        self._admission_stop = threading.Event()
        self._overflow = set()
        super().__init__(address, Handler)
        # One classifier thread bounds admission work even under eviction churn.
        self._classifier = threading.Thread(target=self._admit, daemon=True)
        self._classifier.start()

    @staticmethod
    def _close_request(request):
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        request.close()

    def process_request(self, request, address):
        with self._admission_lock:
            if len(self._admission) >= 64:
                victim = next((sock for sock, item in self._admission.items() if not item[2]), None)
                if victim is None:
                    self._close_request(request)
                    return
                self._close_request(victim)
                del self._admission[victim]
            self._admission[request] = (address, time.monotonic()+2, False)

    def _admit(self):
        while not self._admission_stop.is_set():
            with self._admission_lock:
                pending = [(sock,item) for sock,item in self._admission.items() if not item[2]]
            if not pending:
                self._admission_stop.wait(.01)
                continue
            try:
                readable,_,_ = select.select([sock for sock,_ in pending],[],[],.01)
            except (OSError,ValueError):
                continue  # An incomplete connection was evicted after the copy.
            for request,item in pending:
                with self._admission_lock:
                    if self._admission.get(request) != item:
                        continue
                    address,deadline,_ = item
                    try:
                        if time.monotonic() >= deadline:
                            raise OSError('Incomplete preamble expired')
                        if request not in readable:
                            continue
                        head=request.recv(8193,socket.MSG_PEEK)
                        if not head:
                            raise OSError('Closed preamble')
                        ending=head.find(b'\r\n\r\n')
                        if ending<0 or ending+4>8192:
                            if len(head)>8192:
                                raise OSError('Oversized preamble')
                            continue
                        line=head.split(b'\r\n',1)[0]
                        priority=line in (b'POST /api/release HTTP/1.0',b'POST /api/release HTTP/1.1')
                        audio=line in (b'POST /api/audio HTTP/1.0',b'POST /api/audio HTTP/1.1')
                        pool=self.priority_slots if priority else self.slots
                        if audio:
                            # Read only a complete bounded JSON body. Authentication
                            # and authoritative schema validation still happen in Handler.
                            lengths=[row.split(b':',1)[1].strip() for row in head[:ending].split(b'\r\n')[1:] if row.lower().startswith(b'content-length:')]
                            if len(lengths)!=1 or not lengths[0].isdigit() or int(lengths[0])>2048:
                                raise OSError('Invalid audio preamble')
                            length=int(lengths[0]);payload=head[ending+4:]
                            if len(payload)<length:continue
                            try:
                                body=loads(payload[:length],2048)
                            except Problem as exc:raise OSError('Invalid audio JSON') from exc
                            pool=self.audio_slots
                        if not pool.acquire(False):
                            raise OSError('Command capacity exhausted')
                        if audio:self._audio_pools[request]=pool
                        if priority:
                            self._overflow.add(request)
                        self._admission[request]=(address,deadline,True)
                        try:
                            super().process_request(request,address)
                        except (OSError,RuntimeError) as exc:
                            self._overflow.discard(request)
                            self._audio_pools.pop(request,None)
                            pool.release()
                            raise OSError('Cannot start command worker') from exc
                    except OSError:
                        self._close_request(request)
                        self._admission.pop(request,None)
                # MSG_PEEK stays readable for incomplete headers; bound CPU too.
            self._admission_stop.wait(.005)

    def process_request_thread(self, request, address):
        with self._admission_lock:
            overflow = request in self._overflow
            audio_pool = self._audio_pools.pop(request,None)
        try:
            super().process_request_thread(request, address)
        finally:
            with self._admission_lock:
                self._overflow.discard(request)
                self._admission.pop(request,None)
            (audio_pool or (self.priority_slots if overflow else self.slots)).release()

    def server_close(self):
        self._admission_stop.set()
        self._classifier.join()
        with self._admission_lock:
            for request,item in list(self._admission.items()):
                if not item[2]:
                    self._close_request(request)
                    del self._admission[request]
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'MKB'
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(4)

    def log_message(self, *_):
        pass  # Ни токенов, ни profile bodies в логах.

    def security(self, mutation=False):
        ip = ipaddress.ip_address(self.client_address[0])
        require(ip.is_loopback or ip in ipaddress.ip_network('100.64.0.0/10'), 'Source address запрещён')
        port = self.server.server_address[1]
        hosts = {f'{h}:{port}' for h in self.server.allowed_hosts}
        require(len(self.headers.get_all('Host', [])) == 1 and self.headers['Host'] in hosts, 'Host запрещён')
        origin = self.headers.get('Origin')
        require(len(self.headers.get_all('Origin', [])) <= 1 and (not origin or origin == 'http://' + self.headers['Host']), 'Origin запрещён')
        require(self.headers.get('Sec-Fetch-Site') not in ('cross-site',), 'Cross-site запрос запрещён')
        overflow = self.connection in self.server._overflow
        require(not overflow or (mutation and self.path == '/api/release'), 'Reserved release lane', 503)
        if mutation:
            require(origin == 'http://' + self.headers['Host'], 'Нужен same-origin Origin')
            require(len(self.headers.get_all('X-CSRF-Token', [])) == 1 and self.headers.get('X-CSRF-Token', '').isascii() and
                    secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), self.server.csrf), 'CSRF token не совпадает')
            require(self.headers.get('Content-Type') == 'application/json', 'Нужен application/json')
            require(not self.headers.get('Transfer-Encoding'), 'Transfer-Encoding запрещён')
            lengths = self.headers.get_all('Content-Length', [])
            require(len(lengths) == 1 and len(lengths[0]) <= 5 and lengths[0].isascii() and lengths[0].isdigit() and int(lengths[0]) <= 65536, 'Неверный размер body')

    def send(self, status, value, kind='application/json; charset=utf-8'):
        permitting = isinstance(value,dict) and Service.ACK_GENERATION in value
        if permitting:
            value=dict(value)
            generation=value.pop(Service.ACK_GENERATION)
        if isinstance(value,dict) and 'statusEpoch' in value:
            value=self.server.service.fence_status(value)
        data = encode(value) if not isinstance(value, bytes) else value
        if permitting:
            # Final ACK linearization boundary, after serialization and before
            # headers. Later commands may legitimately change current state.
            self.server.service.check_ack_generation(generation)
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self.security()
            if self.path == '/api/bootstrap':
                result = self.server.service.bootstrap()
                self.send(200, dict(result, csrf=self.server.csrf, codes=CODES, reserved=sorted(RESERVED), actions=ACTIONS))
            elif self.path == '/api/status':
                self.send(200, self.server.service.status())
            elif self.path == '/api/export':
                store = self.server.service.bootstrap()['store']
                self.send(200, store['profiles'][store['selected']])
            elif self.path in ('/', '/app.js', '/style.css'):
                name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[self.path]
                kind = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/style.css': 'text/css; charset=utf-8'}[self.path]
                self.send(200, (WEB / name).read_bytes(), kind)
            else:
                raise Problem('Не найдено', 404)
        except Problem as exc:
            self.send(exc.status, dict(error=str(exc)))
        except OSError:
            self.send(503, dict(error='Служба не смогла прочитать данные'))

    def do_POST(self):
        try:
            self.security(True)
            require(self.path.startswith('/api/') and self.path.count('/') == 2, 'Неизвестный путь')
            data = self.rfile.read(int(self.headers['Content-Length']))
            require(len(data) == int(self.headers['Content-Length']), 'Неполный body')
            self.send(200, self.server.service.mutate(self.path[5:], loads(data)))
        except Problem as exc:
            self.send(exc.status, dict(error=str(exc)))
        except (OSError, TimeoutError):
            self.send(503, dict(error='Ошибка ввода/вывода или timeout'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR)
    parser.add_argument('--listen', action='append', default=[], help='Дополнительный IPv4 Tailscale')
    parser.add_argument('--hostname', action='append', default=[], help='Разрешённое DNS-имя этого пульта')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Порт должен быть от 1 до 65535')
    for host in args.listen:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            parser.error('Нужен IPv4 Tailscale')
        if address.version != 4 or address not in ipaddress.ip_network('100.64.0.0/10'):
            parser.error('Дополнительный listener разрешён только на IPv4 Tailscale')
    import re
    if any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', h) for h in args.hostname):
        parser.error('Неверное DNS-имя')
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(not args.state_dir.is_symlink() and args.state_dir.stat().st_uid == os.getuid(), 'Небезопасный каталог состояния')
    os.chmod(args.state_dir, 0o700)
    fd = os.open(args.state_dir / 'service.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(fd, 'a')
    os.fchmod(fd, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Dashboard уже запущен') from None
    store = Store(args.state_dir, loads((ROOT / 'profiles/arc-raiders-dashboard.json').read_bytes()),
                  loads((ROOT / 'profiles/arc-raiders-linear-zero-deadzone.json').read_bytes()))
    # PID только для stop; singleton lock удерживается весь срок службы.
    atomic_pid = store.directory / 'service.pid'
    atomic_pid.write_text(str(os.getpid()))
    os.chmod(atomic_pid, 0o600)
    service = Service(store, Adapters(store.directory))
    servers = []
    serving = []
    try:
        for host in dict.fromkeys(['127.0.0.1', *args.listen]):
            servers.append(Server((host, args.port), service, allowed_hosts=args.hostname))
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            serving.append(server)
        print(f'PS5 MKB: http://127.0.0.1:{args.port}', flush=True)
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        stopped.wait()
    finally:
        for server in servers:
            if server in serving:
                server.shutdown()
            server.server_close()
        atomic_pid.unlink(missing_ok=True)
        lock.close()


if __name__ == '__main__':
    main()
