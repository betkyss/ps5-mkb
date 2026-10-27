#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
base=c8bea2642d0e95402f373af55584a3b98cd89417
[ "$(git -C vendor/chiaki-ng rev-parse HEAD)" = "$base" ] || { echo 'Неверный upstream commit' >&2; exit 1; }
python3 scripts/verify-patch.py --sources-only
prefix=${HOMEBREW_PREFIX:-/opt/homebrew}
export PATH="$prefix/opt/protobuf@29/bin:$PATH"
export PKG_CONFIG_PATH="$prefix/opt/libplacebo/lib/pkgconfig:$prefix/opt/openssl@3/lib/pkgconfig:$prefix/opt/ffmpeg/lib/pkgconfig:$prefix/opt/sdl2/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
cmake -S vendor/chiaki-ng -B build/client -G Ninja \
    -DOPENSSL_ROOT_DIR="$prefix/opt/openssl@3" \
    -DCMAKE_C_FLAGS="-I$prefix/opt/openssl@3/include" \
    -DCMAKE_CXX_FLAGS="-I$prefix/opt/openssl@3/include" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_PREFIX_PATH="$prefix/opt/qt;$prefix/opt/openssl@3;$prefix/opt/protobuf@29;$prefix" \
    -DCHIAKI_ENABLE_TESTS=OFF -DCHIAKI_ENABLE_CLI=OFF \
    -DCHIAKI_ENABLE_SETSU=OFF -DCHIAKI_ENABLE_STEAMDECK_NATIVE=OFF \
    -DCHIAKI_ENABLE_STEAM_SHORTCUT=OFF -DCHIAKI_USE_SYSTEM_NANOPB=OFF \
    -DCHIAKI_USE_SYSTEM_CURL=OFF "$@"
cmake --build build/client --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-4}"

# Finalize the macOS bundle after CMake's linker signature. Resources and
# Info.plist must be covered before the app is launched outside the terminal.
app=build/client/gui/chiaki.app
codesign --force --sign - --timestamp=none "$app"
codesign --verify --strict "$app"
