"""Строгая схема профиля. Числа источников соответствуют macOS, не DOM keyCode."""
import hashlib
import json
import math

LIMIT = 65536
RESERVED = {46, 53, 58, 59, 61, 62, 63}
ACTIONS = ('W A S D Cross Box Pyramid Moon L3 R3 L1 R1 Up Down Left Right '
           'L2 R2 Options Share PS Touchpad LookUp LookDown LookLeft LookRight '
           'RecoilToggle RecoilDecrease RecoilIncrease RapidFireToggle HoldLeft HoldRight DodgeRoll').split()
# Явная таблица физического положения. Не зависит от языка раскладки.
CODES = {}
CODES.update(dict(zip('KeyA KeyS KeyD KeyF KeyH KeyG KeyZ KeyX KeyC KeyV'.split(), range(10))))
CODES.update(dict(zip(('KeyB KeyQ KeyW KeyE KeyR KeyY KeyT Digit1 Digit2 Digit3 Digit4 Digit6 Digit5 '
                      'Equal Digit9 Digit7 Minus Digit8 Digit0 BracketRight KeyO KeyU BracketLeft KeyI '
                      'KeyP Enter KeyL KeyJ Quote KeyK Semicolon Backslash Comma Slash KeyN KeyM '
                      'Period Tab Space Backquote Backspace').split(), range(11,52))))
CODES.update(Escape=53, MetaRight=54, MetaLeft=55, ShiftLeft=56, CapsLock=57,
             AltLeft=58, ControlLeft=59, ShiftRight=60, AltRight=61, ControlRight=62,
             Fn=63, F6=97, F7=98, F8=100, ArrowLeft=123, ArrowRight=124, ArrowDown=125, ArrowUp=126,
             MouseLeft=256, MouseRight=257, MouseMiddle=258, MouseBack=259, MouseForward=260)


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def require(condition, message, status=400):
    if not condition:
        raise Problem(message, status)


def loads(data, limit=LIMIT):
    if isinstance(data, str):
        data = data.encode('utf-8')
    require(len(data) <= limit, 'JSON превышает 64 КиБ')
    depth = 0
    quoted = escaped = False
    for char in data:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            require(depth <= 16, 'JSON слишком глубокий')
        elif char in (93, 125):
            depth -= 1
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, 'Повторяющийся JSON ключ: ' + key)
            out[key] = value
        return out
    try:
        value = json.loads(data, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(Problem('Нужно конечное число')))
        def check(item):
            if isinstance(item, str):
                item.encode('utf-8')
            elif isinstance(item, float):
                require(math.isfinite(item), 'Нужно конечное число')
            elif isinstance(item, list):
                for child in item:
                    check(child)
            elif isinstance(item, dict):
                for key, child in item.items():
                    check(key)
                    check(child)
        check(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Problem('Некорректный JSON') from exc


def fields(value, required, optional=()):
    require(type(value) is dict, 'Ожидался объект')
    require(set(required) <= value.keys(), 'Отсутствуют обязательные поля')
    require(value.keys() <= set(required) | set(optional), 'Неизвестное поле')


def number(value, low, high):
    require(type(value) in (int, float) and low <= value <= high and math.isfinite(value),
            f'Число должно быть от {low} до {high}')


def source(code):
    require(code in CODES, 'Эта клавиша не поддерживается записью')
    value = CODES[code]
    require(value not in RESERVED, 'Клавиша зарезервирована для управления захватом')
    return str(value)


def validate(p):
    fields(p, ('version', 'name', 'dpi', 'binds', 'hip', 'ads'), ('radial_deadzone', 'calibration', 'recoil', 'rapid_fire', 'radial_menu'))
    require(type(p['version']) is int and p['version'] == 1, 'Неизвестная версия профиля')
    require(type(p['name']) is str and p['name'].strip() and len(p['name'].encode('utf-16-le')) // 2 <= 120, 'Нужно имя до 120 символов')
    number(p['dpi'], 0, 1000000)
    number(p.get('radial_deadzone', 0), 0, .99)
    if 'radial_menu' in p:
        menu = p['radial_menu']
        fields(menu, ('enabled', 'gain', 'open_actions'), ('open_delay_ms',))
        delay = menu.get('open_delay_ms', 350)
        number(delay, 0, 1500)
        require(delay == int(delay), 'Задержка открытия должна быть целым числом')
        require(type(menu['enabled']) is bool, 'Включение круговых меню должно быть true или false')
        number(menu['gain'], .0001, 1)
        actions = menu['open_actions']
        require(type(actions) is list and len(actions) <= 3 and
                all(type(action) is str and action in ('L1', 'R1', 'Left') for action in actions),
                'Некорректный список круговых меню')
        require(len(set(actions)) == len(actions) and (actions or not menu['enabled']),
                'Выберите круговые меню без повторов')
    if 'rapid_fire' in p:
        rapid = p['rapid_fire']
        fields(rapid, ('enabled', 'interval_ms', 'press_ms'))
        require(type(rapid['enabled']) is bool, 'Включение автотапа должно быть true или false')
        for key, low, high in (('interval_ms', 64, 1000), ('press_ms', 16, 496)):
            value = rapid[key]
            number(value, low, high)
            require(value == int(value) and value % 8 == 0, 'Длительность автотапа должна быть кратна 8 мс')
        require(rapid['press_ms'] < rapid['interval_ms'], 'Нажатие должно быть короче интервала автотапа')
    if 'recoil' in p:
        fields(p['recoil'], ('vertical',), ('enabled', 'mode', 'pulse_peak', 'period_ms'))
        if 'enabled' in p['recoil']:
            require(type(p['recoil']['enabled']) is bool, 'Включение компенсации должно быть true или false')
        number(p['recoil']['vertical'], 0, .25)
        if 'mode' in p['recoil']:
            require(p['recoil']['mode'] in ('constant', 'pulse'), 'Неизвестный режим компенсации')
        if 'pulse_peak' in p['recoil']:
            number(p['recoil']['pulse_peak'], .001, .25)
        if 'period_ms' in p['recoil']:
            period = p['recoil']['period_ms']
            number(period, 32, 496)
            require(period == int(period) and period % 8 == 0, 'Период должен быть кратен 8 мс')

    require(type(p['binds']) is dict, 'Binds должен быть объектом')
    for key, action in p['binds'].items():
        require(type(key) is str and 1 <= len(key) <= 3 and key.isascii() and key.isdecimal() and str(int(key)) == key,
                'Некорректный источник ввода')
        n = int(key)
        require((0 <= n <= 127 or n in (256, 257, 258, 259, 260)) and n not in RESERVED,
                'Источник зарезервирован или не поддерживается HID')
        require(type(action) is str and action in ACTIONS, 'Неизвестное действие')
        require(n != 57 or action == 'DodgeRoll', 'Caps Lock поддерживает только Перекат')
    for name in ('hip', 'ads'):
        m = p[name]
        fields(m, ('gain', 'smoothing_ms', 'cm360', 'x', 'y'))
        for k, low, high in (('gain', .000001, 1), ('smoothing_ms', 0, 1000), ('cm360', 0, 10000)):
            number(m[k], low, high)
        for axis in ('x', 'y'):
            t = m[axis]
            require(type(t) is list and (len(t) == 0 or len(t) >= 2), 'Неполная LUT')
            prev = None
            for row in t:
                require(type(row) is list and len(row) == 2, 'Точка LUT должна содержать speed и stick')
                number(row[0], 0, 1000000)
                number(row[1], 0, 1)
                require((row[0] == 0 if prev is None else row[0] > prev[0] and row[1] > prev[1]), 'LUT должна строго возрастать')
                prev = row
        require(bool(m['x']) == bool(m['y']), 'Нужны обе оси LUT')
        require(not m['x'] or m['cm360'] > 0 and p['dpi'] > 0, 'Для LUT нужны DPI и cm/360')
    if 'calibration' in p:
        bounds = dict(game_version=128, settings=2048, fov=128, optic=128, date=64, notes=2048)
        fields(p['calibration'], (), bounds)
        for k, v in p['calibration'].items():
            require(type(v) is str and len(v.encode('utf-16-le')) // 2 <= bounds[k], 'Некорректные сведения калибровки')
    return p


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(profile):
    return hashlib.sha256(encode(profile)).hexdigest()


def audio_request(value):
    identity = ('operationId', 'processId', 'sessionId', 'sessionGeneration', 'audioRevision')
    actions = ('setVolume', 'setOutputMuted')
    fields(value, identity, actions)
    require(sum(key in value for key in actions) == 1, 'Нужна ровно одна audio-команда')
    for key in identity[:3]:
        require(type(value[key]) is str and 0 < len(value[key]) <= 80, 'Некорректная audio identity')
    for key in identity[3:]:
        require(type(value[key]) is int and 0 <= value[key] <= 9007199254740991, 'Некорректная audio revision')
    if 'setVolume' in value:
        number(value['setVolume'], 0, 100)
    for key in actions[1:]:
        if key in value:
            require(type(value[key]) is bool, 'Audio переключатель должен быть true или false')
    return value
