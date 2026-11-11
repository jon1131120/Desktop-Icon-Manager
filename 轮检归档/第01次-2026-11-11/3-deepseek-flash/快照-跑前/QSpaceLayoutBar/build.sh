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

# 版本号：构建时自动写入「日期.时刻」→ 日志里的 startup 行能区分是哪次构建
# （原来硬编码 2.0.6134 永不变化，三台日志全是同一个版本号，排障时分不清谁跑的是哪版）
BUILD_VER="$(date '+%Y.%m%d.%H%M')"
cp Info.plist "$APP_BUNDLE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_VER" "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null \
    || plutil -replace CFBundleVersion -string "$BUILD_VER" "$APP_BUNDLE/Contents/Info.plist"
echo "版本号: $BUILD_VER"

# 签名：ad-hoc（不需要钥匙串私钥，可在 SSH 非交互环境安全执行）
# 注：Apple Development 证书签名需解锁钥匙串，SSH 下会 errSecInternalComponent，
#     且实测 ad-hoc 版本已在 macOS 27 上稳定运行，故保持 ad-hoc。
codesign --force --sign - "$APP_BUNDLE" || { echo "❌ ad-hoc 签名失败"; exit 1; }

echo "✅ 构建完成: $APP_BUNDLE"
echo "安装: killall $APP_NAME 2>/dev/null; cp -R $APP_BUNDLE /Applications/ && open /Applications/$APP_NAME.app"
