#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
if [ ! -x "$CHROME" ]; then
  echo "找不到 Chrome，请用 CHROME=/path/to/chrome ./render.sh 指定" >&2
  exit 1
fi

shot() { # $1=query $2=out $3=scale $4=w $5=h
  "$CHROME" --headless --disable-gpu --hide-scrollbars \
    --force-device-scale-factor="$3" --window-size="$4,$5" \
    --screenshot="$2" "file://$PWD/face.html$1" >/dev/null 2>&1
}

shot ""                  preview@3x.png          3 336 480
shot "?mode=bg"          background@3x.png       3 336 480
shot "?mode=clean"       background-clean@3x.png 3 336 480
shot "?mode=guide"       layout-guide@3x.png     3 336 480
shot ""                  preview.png             1 336 480
shot "?mode=bg"          background.png          1 336 480
shot "?mode=clean"       background-clean.png    1 336 480

icon() { # $1=name $2=w $3=h
  "$CHROME" --headless --disable-gpu --hide-scrollbars \
    --force-device-scale-factor=3 --window-size="$2,$3" \
    --default-background-color=00000000 \
    --screenshot="assets/$1@3x.png" "file://$PWD/assets/$1.svg" >/dev/null 2>&1
}
icon weather-sun 42 42
icon battery-shell 25 12
icon sunrise-arrow 10 10
icon sunset-arrow 10 10
icon sun-dot 16 16

echo "已生成：preview / background / background-clean / layout-guide + assets"
