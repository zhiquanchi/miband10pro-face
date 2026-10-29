#!/usr/bin/env bash
# macOS / Apple Silicon port of tools/build-qemu.sh (band10pro only).
# Drops the MSYS2/MinGW coupling: no cygpath, no .exe suffix, Homebrew toolchain.
set -euo pipefail

MODEL=${1:-band10pro}
[[ "$MODEL" == band10pro ]] || { echo "this wrapper targets band10pro" >&2; exit 2; }

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_ROOT=${QEMU_BUILD_ROOT:-$PROJECT_ROOT/.build-macos/$MODEL}
SRC=$BUILD_ROOT/qemu
HOMEBREW=${HOMEBREW_PREFIX:-/opt/homebrew}

export PATH="$HOMEBREW/bin:$PATH"
export PKG_CONFIG_PATH="$HOMEBREW/lib/pkgconfig:$HOMEBREW/share/pkgconfig"
export PKG_CONFIG_LIBDIR="$HOMEBREW/lib/pkgconfig"
export CFLAGS=${CFLAGS:-}
# Homebrew's libfdt/zstd live outside the default search paths.
export CFLAGS="$CFLAGS -I$HOMEBREW/include"
export LDFLAGS=${LDFLAGS:-}
export LDFLAGS="$LDFLAGS -L$HOMEBREW/lib"
export PYTHONUTF8=1 LC_ALL=C LANG=C

for dep in cc python3 ninja pkg-config dtc; do
  command -v "$dep" >/dev/null || { echo "Missing build dependency: $dep" >&2; exit 1; }
done

mkdir -p "$BUILD_ROOT/build" "$BUILD_ROOT/tmp"
export TMPDIR="$BUILD_ROOT/tmp"

if [[ ! -d "$SRC" ]]; then
  python3 "$PROJECT_ROOT/tools/prepare_source.py" --model "$MODEL" --output "$SRC"
fi

cd "$BUILD_ROOT/build"
if [[ ! -f build.ninja ]]; then
  "$SRC/configure" \
    --cc=cc --cxx=c++ \
    --target-list=arm-softmmu \
    --without-default-devices \
    --disable-docs --disable-tools --disable-user \
    --disable-guest-agent --disable-slirp --enable-sdl --disable-gtk --disable-curses \
    --disable-opengl --disable-virglrenderer --disable-spice --disable-gnutls \
    --disable-capstone --disable-libnfs --disable-libiscsi --disable-rbd \
    --disable-xen --disable-kvm --disable-werror --disable-rust \
    --extra-cflags="-I$HOMEBREW/include" \
    --extra-ldflags="-L$HOMEBREW/lib"
fi

if [[ "${CONFIGURE_ONLY:-0}" == 1 ]]; then
  echo "Configured $BUILD_ROOT/build"
  exit 0
fi

ninja -j"${BUILD_JOBS:-$(sysctl -n hw.ncpu)}" qemu-system-arm
echo "Built $BUILD_ROOT/build/qemu-system-arm"
