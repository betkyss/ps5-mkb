"""Локальная конфигурация подключения. Регистрационные ключи здесь не хранятся."""
import ipaddress
from pathlib import Path
from .schema import Problem, loads, require

STATE_DIR = Path.home() / 'Library/Application Support/PS5MKB'


def validate_connection(value):
    require(type(value) is dict and set(value) == {'console_name', 'console_host', 'mouse'},
            'Настройте console_name, console_host и mouse через scripts/configure.py')
    name = value['console_name']
    require(type(name) is str and 0 < len(name) <= 128 and name == name.strip() and
            not name.startswith('-') and all(c.isprintable() for c in name), 'Неверное имя консоли')
    host = value['console_host']
    require(type(host) is str, 'Нужен IP-адрес PS5')
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise Problem('Нужен текущий IP-адрес PS5 из её настроек сети') from exc
    require(address.version == 4 and not address.is_unspecified and not address.is_multicast,
            'Нужен IPv4-адрес PS5')
    mouse = value['mouse']
    require(type(mouse) is dict and set(mouse) == {'vendor_id', 'product_id', 'location_id'},
            'Выберите HID-мышь через scripts/configure.py')
    for key, maximum in [('vendor_id', 65535), ('product_id', 65535), ('location_id', 0xffffffff)]:
        require(type(mouse[key]) is int and 0 < mouse[key] <= maximum, 'Неверный идентификатор мыши: ' + key)
    return dict(console_name=name, console_host=str(address), mouse=dict(mouse))


def load_connection(directory):
    try:
        return validate_connection(loads((Path(directory) / 'connection.json').read_bytes()))
    except OSError as exc:
        raise Problem('Сначала выполните python3 scripts/configure.py для настройки PS5 и мыши', 503) from exc
