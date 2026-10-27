#!/bin/sh
# Открыть интерфейс клиента для регистрации консоли.
set -eu
cd "$(dirname "$0")/.."
if pgrep -x chiaki >/dev/null 2>&1; then
    echo 'Клиент chiaki уже работает. Откройте его окно или сначала завершите его.' >&2
    exit 1
fi
binary=build/client/gui/chiaki.app/Contents/MacOS/chiaki
[ -x "$binary" ] || { echo 'Сначала выполните sh scripts/build-client.sh' >&2; exit 1; }
# Не наследуем параметры захвата/трассировки и пути из чужого сеанса.
exec env -i HOME="$HOME" USER="$(id -un)" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    TMPDIR="${TMPDIR:-/tmp}" LANG="${LANG:-en_US.UTF-8}" \
    CHIAKI_FORCE_VULKAN_FALLBACK=1 \
    CHIAKI_FORCE_VULKAN_FALLBACK_REASON='PS5 MKB registration' "$binary"
