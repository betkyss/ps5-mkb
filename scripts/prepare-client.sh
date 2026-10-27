#!/bin/sh
# Получить зафиксированный upstream и применить изменения PS5 MKB.
set -eu
cd "$(dirname "$0")/.."
base=c8bea2642d0e95402f373af55584a3b98cd89417
if [ -e vendor/chiaki-ng ]; then
    python3 scripts/verify-patch.py --sources-only
    exit 0
fi
mkdir -p vendor
git init -q vendor/chiaki-ng
git -C vendor/chiaki-ng remote add origin https://github.com/streetpea/chiaki-ng.git
git -C vendor/chiaki-ng fetch --depth=1 origin "$base"
git -C vendor/chiaki-ng checkout --detach FETCH_HEAD
git -C vendor/chiaki-ng submodule update --init --recursive --depth=1
git -C vendor/chiaki-ng apply --check ../../patches/minimal-mkb.patch
git -C vendor/chiaki-ng apply ../../patches/minimal-mkb.patch
python3 scripts/verify-patch.py --sources-only
