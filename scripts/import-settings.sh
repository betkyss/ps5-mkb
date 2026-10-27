#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ "${1:-}" != --copy-official-settings ]; then
    echo 'Для локального импорта после закрытия обоих клиентов: scripts/import-settings.sh --copy-official-settings' >&2
    exit 1
fi
if pgrep -x chiaki >/dev/null 2>&1; then
    echo 'Сначала закройте официальный клиент и прототип.' >&2
    exit 1
fi
prefix=${HOMEBREW_PREFIX:-/opt/homebrew}
cmake -S tools/settings-import -B build/settings-import -DCMAKE_PREFIX_PATH="$prefix/opt/qt"
cmake --build build/settings-import
exec build/settings-import/mkb-settings-import --copy-official-settings
