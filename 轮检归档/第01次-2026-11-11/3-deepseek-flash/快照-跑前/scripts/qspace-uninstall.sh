#!/bin/bash
# qspace-uninstall.sh — 完全卸载 QSpaceLayoutBar（含中枢）
#
# 用法：
#   ./qspace-uninstall.sh --dry-run      列出将删除的内容（不删）
#   ./qspace-uninstall.sh mbp            卸载单台（保留中枢和数据）
#   ./qspace-uninstall.sh all            卸载三台（保留中枢和数据）
#   ./qspace-uninstall.sh --purge        连中枢 + 所有数据一起删（危险，需二次确认）
#
# 安全：默认只删 app + launchd，保留 ~/qspace-layouts 数据；--purge 才删数据

set -e
LABEL="com.jon1131120.qspacelayoutbar"
DRY=0
[ "$1" = "--dry-run" ] && { DRY=1; shift; }

say() { [ "$DRY" = "1" ] && echo "  [将执行] $*" || echo "  $*"; }

uninstall_one() {
    local H="$1"
    echo "=== 卸载 $H ==="
    if [ "$H" = "local" ]; then
        say "launchctl bootout gui/$(id -u)/$LABEL"
        [ "$DRY" = "1" ] || launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        say "killall QSpaceLayoutBar"
        [ "$DRY" = "1" ] || killall QSpaceLayoutBar 2>/dev/null || true
        say "rm /Applications/QSpaceLayoutBar.app"
        [ "$DRY" = "1" ] || rm -rf /Applications/QSpaceLayoutBar.app
        say "rm ~/Library/LaunchAgents/$LABEL.plist"
        [ "$DRY" = "1" ] || rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
        say "defaults delete com.jon1131120.qspacelayoutbar"
        [ "$DRY" = "1" ] || defaults delete com.jon1131120.qspacelayoutbar 2>/dev/null || true
    else
        say "ssh $H: bootout + killall + rm app + rm plist + defaults delete"
        [ "$DRY" = "1" ] || ssh -o BatchMode=yes "$H" "
            launchctl bootout gui/\$(id -u)/$LABEL 2>/dev/null || true
            killall QSpaceLayoutBar 2>/dev/null || true
            sleep 1
            rm -rf /Applications/QSpaceLayoutBar.app
            rm -f ~/Library/LaunchAgents/$LABEL.plist
            defaults delete com.jon1131120.qspacelayoutbar 2>/dev/null || true
            echo ' 已卸载'
        "
    fi
    say "保留数据: ~/qspace-layouts/（如需删除请手工 rm -rf）"
}

case "$1" in
    all) for h in mbp air studio; do uninstall_one "$h"; done ;;
    local|mbp|air|studio) uninstall_one "$1" ;;
    --purge)
        echo "⚠️  这会删除：三台 app+plist+defaults+~/qspace-layouts/，以及 MINIPC 中枢服务+数据"
        read -p "确认？输入 yes 继续: " ok
        [ "$ok" = "yes" ] || { echo "已取消"; exit 0; }
        for h in mbp air studio; do uninstall_one "$h"; ssh -o BatchMode=yes "$h" "rm -rf ~/qspace-layouts" || true; done
        echo "=== 卸载中枢 ==="
        ssh -o BatchMode=yes minipc "sudo -n systemctl disable --now qspace-hub; sudo -n rm -f /etc/systemd/system/qspace-hub.service; sudo -n systemctl daemon-reload; rm -rf ~/qspace-hub"
        echo "✅ 完全卸载完成"
        ;;
    *) echo "用法: $0 {--dry-run|all|mbp|air|studio|--purge}"; exit 1 ;;
esac
