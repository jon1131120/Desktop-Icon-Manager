#!/bin/bash
# QSpaceLayoutBar 构建脚本：swiftc 编译 + 组装 .app bundle
set -e
cd "$(dirname "$0")"

APP_NAME="QSpaceLayoutBar"
BUILD_DIR="build"
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"

mkdir -p "$APP_BUNDLE/Contents/MacOS"

echo "编译 main.swift..."
swiftc -O -sdk "$(xcrun --show-sdk-path)" -o "$APP_BUNDLE/Contents/MacOS/$APP_NAME" main.swift \
    -framework AppKit -framework Foundation

cp Info.plist "$APP_BUNDLE/Contents/Info.plist"

# ad-hoc 签名
codesign --force --sign - "$APP_BUNDLE" 2>/dev/null || true

echo "✅ 构建完成: $APP_BUNDLE"
echo "安装: killall $APP_NAME 2>/dev/null; cp -R $APP_BUNDLE /Applications/ && open /Applications/$APP_NAME.app"
