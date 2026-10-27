#!/usr/bin/env python3
"""Настроить консоль и HID-мышь. Не подключается к PS5 и не включает ввод."""
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.config import STATE_DIR, validate_connection
from dashboard.schema import Problem, encode
from dashboard.store import atomic_write


def mice():
    data = plistlib.loads(subprocess.check_output(['/usr/sbin/ioreg', '-a', '-r', '-c', 'IOHIDDevice']))
    result = {}
    def visit(node):
        if isinstance(node, list):
            for value in node:
                visit(value)
        elif isinstance(node, dict):
            usages = node.get('DeviceUsagePairs', [])
            mouse = node.get('PrimaryUsagePage') == 1 and node.get('PrimaryUsage') == 2
            mouse = mouse or any(pair.get('DeviceUsagePage') == 1 and pair.get('DeviceUsage') == 2 for pair in usages if isinstance(pair, dict))
            ids = tuple(node.get(k) for k in ('VendorID', 'ProductID', 'LocationID'))
            if mouse and all(type(n) is int and n > 0 for n in ids):
                result[ids] = dict(name=str(node.get('Product', 'HID mouse')), vendor_id=ids[0], product_id=ids[1], location_id=ids[2])
            visit(node.get('IORegistryEntryChildren', []))
    visit(data)
    return list(result.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR)
    parser.add_argument('--list-mice', action='store_true')
    parser.add_argument('--console-name')
    parser.add_argument('--console-host')
    parser.add_argument('--vendor-id', type=lambda value: int(value, 0))
    parser.add_argument('--product-id', type=lambda value: int(value, 0))
    parser.add_argument('--location-id', type=lambda value: int(value, 0))
    args = parser.parse_args()
    devices = mice() if args.list_mice or any(n is None for n in (args.vendor_id, args.product_id, args.location_id)) else []
    if args.list_mice:
        print(json.dumps(devices, ensure_ascii=False, indent=2))
        return
    name = args.console_name or input('Имя PS5, точно как в зарегистрированном клиенте: ').strip()
    host = args.console_host or input('Проводной IPv4-адрес PS5: ').strip()
    if all(n is not None for n in (args.vendor_id, args.product_id, args.location_id)):
        mouse = dict(vendor_id=args.vendor_id, product_id=args.product_id, location_id=args.location_id)
    else:
        if not devices:
            raise SystemExit('HID-мышь с location ID не найдена. Подключите USB-мышь или приёмник; см. docs/setup.md.')
        for index, device in enumerate(devices, 1):
            print(f"{index}. {device['name']} · VID {device['vendor_id']:#x}, PID {device['product_id']:#x}, location {device['location_id']:#x}")
        choice = int(input('Номер вашей мыши: '))
        if not 1 <= choice <= len(devices):
            raise SystemExit('Нет мыши с таким номером')
        mouse = {k: devices[choice-1][k] for k in ('vendor_id', 'product_id', 'location_id')}
    connection = validate_connection(dict(console_name=name, console_host=host, mouse=mouse))
    directory = args.state_dir.expanduser()
    import os
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise SystemExit('Нужен собственный каталог состояния без symlink')
    os.chmod(directory, 0o700)
    atomic_write(directory / 'connection.json', encode(connection))
    print('Настройки сохранены. Они будут использованы при следующем подключении из пульта.')


if __name__ == '__main__':
    try:
        main()
    except (Problem, ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
