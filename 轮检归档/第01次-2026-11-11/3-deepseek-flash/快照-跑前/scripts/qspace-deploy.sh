#!/bin/bash
# qspace-deploy.sh — 一键部署 QSpaceLayoutBar 到指定机器（或全部三台）
#
# 用法：
#   ./qspace-deploy.sh mbp              # 部署到单台
#   ./qspace-deploy.sh all              # 部署到 mbp/air/studio
#   ./qspace-deploy.sh new <ssh别名>    # 全新机器首次部署（含令牌/目录/plist）
#
# 幂等：可重复执行，已存在则覆盖

set -e
APP_SRC="$HOME/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/build/QSpaceLayoutBar.app"
LABEL="com.jon1131120.qspacelayoutbar"
TOKEN_SRC="$HOME/qspace-layouts/hub-token.txt"
LOG="$HOME/qspace-layouts/deploy.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [deploy] $*" | tee -a "$LOG"; }

[ -d "$APP_SRC" ] || { echo "❌ 先构建：cd ~/Projects/Desktop-Icon-Manager/QSpaceLayoutBar && ./build.sh"; exit 1; }
[ -f "$TOKEN_SRC" ] || { echo "❌ 缺令牌文件: $TOKEN_SRC"; exit 1; }

deploy_one() {
    local H="$1"; local FIRST="${2:-0}"
    log "=== 部署到 ${H} 首次=${FIRST} ==="

    # 机器标识：写显式文件，machineTag 不再依赖 hostname 字符串匹配
    # （JondeMacBook-Pro 含连字符匹配不到 "macbookpro"，实测 MBP 因此退化成 hostname 本身）
    local TAG
    case "$H" in
        mbp) TAG="MBP" ;; air) TAG="Air" ;; studio) TAG="Studio" ;;
        *)   TAG="$(echo "$H" | tr -d '[:punct:][:space:]' | tr '[:lower:]' '[:upper:]')" ;;
    esac

    # 本机判断：hostname 不会等于 ssh 别名，用「本机已装且 ssh 到自己」的等价判断
    local IS_LOCAL=0
    [ "$H" = "local" ] && IS_LOCAL=1
    [ "$H" = "mbp" ] && [ "$(scutil --get ComputerName 2>/dev/null)" = "Jon的MacBook Pro" ] && IS_LOCAL=1
    [ "$H" = "air" ] && [ "$(scutil --get ComputerName 2>/dev/null)" = "Jon的MacBook Air" ] && IS_LOCAL=1
    [ "$H" = "studio" ] && [ "$(scutil --get ComputerName 2>/dev/null)" = "Mac Studio" ] && IS_LOCAL=1
    if [ "$IS_LOCAL" = "1" ]; then
        mkdir -p "$HOME/qspace-layouts"
        printf '%s' "$TAG" > "$HOME/qspace-layouts/machine-tag.txt"
        log "本机部署"
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        killall QSpaceLayoutBar 2>/dev/null || true
        sleep 1
        rm -rf /Applications/QSpaceLayoutBar.app
        cp -R "$APP_SRC" /Applications/
        codesign --force --deep --sign - /Applications/QSpaceLayoutBar.app 2>/dev/null || true
        # ⭐ 本机分支也必须 bootstrap —— 漏了这一步进程不会起来（实测 MBP 实例数变 0）
        launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null || \
            launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
        sleep 2
        echo -n '实例数: '; ps -Ao comm | grep -c 'QSpaceLayoutBar$'
        # 实例数强校验（2026-11-11 轮检 R13/R25）：双实例是此前大量怪象的根因，
        #   旧脚本只打印不断续 -> 部署出 2 个实例也没人知道。这里 >1 直接判失败。
        local CNT; CNT=$(ps -Ao comm | grep -c 'QSpaceLayoutBar$')
        if [ "$CNT" -gt 1 ]; then
            echo "实例数=$CNT (>1) 双实例！请 killall 后重试"
            exit 1
        fi
    else
        log "远程部署 ${H}"
        scp -q -r "$APP_SRC" "$H:/tmp/"
        if [ "$FIRST" = "1" ]; then
            log "首次部署: 建目录+分发令牌+机器标识+装plist"
            ssh -o BatchMode=yes "$H" "mkdir -p ~/qspace-layouts"
            printf '%s' "$TAG" | ssh -o BatchMode=yes "$H" "cat > ~/qspace-layouts/machine-tag.txt"
            scp -q "$TOKEN_SRC" "$H:~/qspace-layouts/hub-token.txt"
            ssh -o BatchMode=yes "$H" "chmod 600 ~/qspace-layouts/hub-token.txt"
            scp -q "$HOME/Library/LaunchAgents/$LABEL.plist" "$H:~/Library/LaunchAgents/" 2>/dev/null || \
                ssh -o BatchMode=yes "$H" "mkdir -p ~/Library/LaunchAgents && cat > ~/Library/LaunchAgents/$LABEL.plist" < "$HOME/Library/LaunchAgents/$LABEL.plist"
        fi
        ssh -o BatchMode=yes "$H" "
            launchctl bootout gui/\$(id -u)/$LABEL 2>/dev/null || true
            killall QSpaceLayoutBar 2>/dev/null || true
            sleep 1
            rm -rf /Applications/QSpaceLayoutBar.app
            cp -R /tmp/QSpaceLayoutBar.app /Applications/
            codesign --force --deep --sign - /Applications/QSpaceLayoutBar.app 2>/dev/null || true
            launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/$LABEL.plist 2>/dev/null || true
            sleep 2
            echo -n '实例数: '; ps -Ao comm | grep -c 'QSpaceLayoutBar\$'
        "
    fi
    log "${H} 完成"
}

case "$1" in
    all)   for h in mbp air studio; do deploy_one "$h" 0; done ;;
    new)   [ -z "$2" ] && { echo "用法: $0 new <ssh别名>"; exit 1; }; deploy_one "$2" 1 ;;
    "")    echo "用法: $0 {all|mbp|air|studio|new <别名>}"; exit 1 ;;
    *)     deploy_one "$1" 0 ;;
esac

log "=== 全部完成，验证：ssh <机器> 'python3 /tmp/check.py' ==="
