#!/bin/bash
# 沙箱场景批量执行器：每个场景前重置沙箱中枢状态，输出落文件便于取证
set -u
SB=/tmp/sandbox_minimax
mkdir -p "$SB/out"
cd "$SB" || exit 1

reset_hub() {
  rm -rf "$SB/hubhome/layout.json" "$SB/hubhome/archive"
  rm -f "$SB/home/desktop.conf" "$SB/home/qspace-layouts/sync-state.json"
  : > "$SB/home/qspace-layouts/app.log" 2>/dev/null
}

for sc in "$@"; do
  reset_hub
  echo "########## SCENARIO $sc ##########"
  "$SB/swift/sb/qslb-sb" --sbtest "$sc" > "$SB/out/s$sc.txt" 2>&1
  grep -E "^SBX|^===" "$SB/out/s$sc.txt"
  echo
done
