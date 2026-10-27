#!/usr/bin/env python3
"""Проверить, что клиент собран из зафиксированных исходников и полного патча."""
from pathlib import Path
import subprocess
import sys
from patch_inventory import verify_snapshot

root = Path(__file__).resolve().parent.parent
if sys.argv[1:] not in ([], ['--sources-only']):
    raise SystemExit('Допустим только --sources-only')
try:
    count = verify_snapshot(root / 'vendor/chiaki-ng', root / 'patches/minimal-mkb.patch')
except (OSError, ValueError, subprocess.CalledProcessError) as error:
    raise SystemExit(str(error))
print(f'Проверены upstream, состав патча и {count} изменённых файлов')
