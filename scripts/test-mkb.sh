#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p build/tests
CC=${CC:-clang}
CXX=${CXX:-clang++}
"$CC" -std=c11 -Wall -Wextra -Werror -Ivendor/chiaki-ng/lib/include -c vendor/chiaki-ng/lib/src/controller.c -o build/tests/controller.o
"$CXX" -std=c++11 -Wall -Wextra -Werror -pthread -Ivendor/chiaki-ng/lib/include -Ivendor/chiaki-ng/gui/include tests/mkbmapper_test.cpp vendor/chiaki-ng/gui/src/mkbmapper.cpp vendor/chiaki-ng/gui/src/mkbprofile.cpp build/tests/controller.o -o build/tests/mkbmapper-test
build/tests/mkbmapper-test
