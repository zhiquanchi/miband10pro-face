#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
APP="MiBandFaceStudio.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/native/assets"
cp ../native/xwf_build.py ../native/xwf_rle.py ../native/xwf_face.py ../native/xwf_inspect.py ../native/header_template.bin "$APP/Contents/Resources/native/"
cp ../native/assets/background.png ../native/assets/preview.png "$APP/Contents/Resources/native/assets/"
cp Info.plist "$APP/Contents/"
swiftc -O -swift-version 5 -parse-as-library -target arm64-apple-macosx13.0 \
  -framework SwiftUI -framework AppKit MiBandFaceStudio.swift \
  -o "$APP/Contents/MacOS/MiBandFaceStudio"
codesign --force --deep --sign - "$APP"
echo "built $APP"
