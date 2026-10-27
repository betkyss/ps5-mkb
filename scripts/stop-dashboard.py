#!/usr/bin/env python3
"""Остановить пульт. Клиент PS5 и захват ввода эта команда не останавливает."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.config import STATE_DIR

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--state-dir', type=Path, default=STATE_DIR)
args = parser.parse_args()
state = args.state_dir.expanduser()
if not state.exists():
    raise SystemExit('Пульт уже остановлен')
if state.is_symlink() or state.stat().st_uid != os.getuid():
    raise SystemExit('Неверный каталог состояния')
with (state / 'service.lock').open('a') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise SystemExit('Пульт уже остановлен')
    except BlockingIOError:
        pass
    pid = int((state / 'service.pid').read_text())
    command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
    if pid <= 1 or 'python' not in command.lower() or '-m dashboard.server' not in command:
        raise SystemExit('PID не подтверждён, остановка отменена')
    os.kill(pid, signal.SIGTERM)
    print('Остановка пульта запрошена. Клиент PS5 продолжает работать.')
