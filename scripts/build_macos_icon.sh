#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

SOURCE="assets/vinaphone-logo.png"
ICONSET="build/vinaphone.iconset"
MASTER="build/vinaphone-app-icon.png"
OUTPUT="build/vinaphone.icns"

mkdir -p "$ICONSET"
cp "$SOURCE" "$MASTER"
sips --resampleHeightWidthMax 900 "$MASTER" >/dev/null
sips --padToHeightWidth 1024 1024 --padColor FFFFFF "$MASTER" >/dev/null

sips --resampleHeightWidth 16 16 "$MASTER" --out "$ICONSET/icon_16x16.png" >/dev/null
sips --resampleHeightWidth 32 32 "$MASTER" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips --resampleHeightWidth 32 32 "$MASTER" --out "$ICONSET/icon_32x32.png" >/dev/null
sips --resampleHeightWidth 64 64 "$MASTER" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips --resampleHeightWidth 128 128 "$MASTER" --out "$ICONSET/icon_128x128.png" >/dev/null
sips --resampleHeightWidth 256 256 "$MASTER" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips --resampleHeightWidth 256 256 "$MASTER" --out "$ICONSET/icon_256x256.png" >/dev/null
sips --resampleHeightWidth 512 512 "$MASTER" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips --resampleHeightWidth 512 512 "$MASTER" --out "$ICONSET/icon_512x512.png" >/dev/null
sips --resampleHeightWidth 1024 1024 "$MASTER" --out "$ICONSET/icon_512x512@2x.png" >/dev/null

iconutil --convert icns "$ICONSET" --output "$OUTPUT"
