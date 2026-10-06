#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
uv sync --extra packaging
VERSION="$(uv run python -c 'from ats_onebss import __version__; print(__version__)')"
APP_NAME="ATS-OneBSS-${VERSION}"
./scripts/build_macos_icon.sh
uv run pyinstaller \
  --noconfirm \
  --clean \
  --onedir \
  --windowed \
  --name "$APP_NAME" \
  --icon build/vinaphone.icns \
  --osx-bundle-identifier vn.cnttdvs.ats-onebss \
  --collect-all playwright \
  --collect-submodules encodings \
  --add-data "regions.toml:." \
  --add-data "config.toml:." \
  --add-data "project_rules.toml:." \
  --add-data "Giao phiếu_demo2.xlsx:." \
  --add-data "assets/vinaphone-logo.png:assets" \
  src/ats_onebss/gui_main.py

PLIST="dist/${APP_NAME}.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${VERSION}" "$PLIST"
if ! /usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${VERSION}" "$PLIST" 2>/dev/null; then
  /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string ${VERSION}" "$PLIST"
fi
codesign --force --deep --sign - "dist/${APP_NAME}.app"

echo "Đã tạo ứng dụng macOS tại dist/${APP_NAME}.app"
