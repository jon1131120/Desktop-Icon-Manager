#!/bin/bash
# qspace-rollback.sh — 布局一键回滚（利用中枢归档，三台自动应用）
#
# 用法：
#   ./qspace-rollback.sh              列出最近 15 个布局归档（新的在上）
#   ./qspace-rollback.sh 3            回滚到第 3 个归档
#   ./qspace-rollback.sh --check      只看当前中枢状态
#
# 原理：把归档内容改上新的 syncAt 后 POST 回中枢，
#       三台 Mac 下一轮（10 秒内）自动拉取并应用，无需逐台操作。

set -e
HUB="http://192.168.113.11:18878"
HUB_ZT="http://192.168.191.9:18878"
MINIPC="minipc"
ARCH="/home/jon113/qspace-hub/archive"

# 中枢共享密钥（与客户端同一份；600 权限）。缺失时脚本会 401。
# ⚠️ 归档选择器：列表与回滚必须用**同一个**（同样的排序+过滤），
#    否则编号对不上文件 —— 旧实现列表用 Python 按 mtime 排 + 过滤 unknown，
#    回滚却用 `ls -t | grep -v unknown` → 两套规则 → 回滚到别的文件。
select_archive() {
    local MODE="$1"; local IDXARG="${2:-1}"
    ssh -o BatchMode=yes -o ConnectTimeout=5 "$MINIPC" python3 - "$ARCH" "$MODE" "$IDXARG" <<'PYLIST'
import json, glob, os, sys
arch, mode, idxarg = sys.argv[1], sys.argv[2], sys.argv[3]
fs = sorted(glob.glob(os.path.join(arch, '*.json')), key=lambda f: -os.path.getmtime(f))
fs = [f for f in fs if not f.endswith('-unknown.json')]
if mode == 'pick':
    idx = int(idxarg)
    if idx < 1 or idx > len(fs):
        sys.exit(1)
    print(fs[idx - 1])
else:
    for i, f in enumerate(fs[:15], 1):
        try:
            d = json.load(open(f))
            n = len(d.get('items') or [])
            fp = d.get('layoutFingerprint') or ''
            mark = '✅' if (fp and n > 0) else '❌'
            print('%2d) %-42s %s %3d项 %s' % (i, os.path.basename(f), mark, n, (fp or 'None')[:12]))
        except Exception:
            print('%2d) %-42s ❌ 读取失败' % (i, os.path.basename(f)))
PYLIST
}

RBLOG="$HOME/qspace-layouts/rollback.log"
TOKEN_FILE="$HOME/qspace-layouts/hub-token.txt"
HUB_TOKEN=""
[ -f "$TOKEN_FILE" ] && HUB_TOKEN=$(cat "$TOKEN_FILE")
if [ -z "$HUB_TOKEN" ]; then
    echo "❌ 缺少中枢令牌: $TOKEN_FILE"
    echo "   从另一台 Mac 复制: scp mbp:~/qspace-layouts/hub-token.txt ~/.qspace-layouts/ 2>/dev/null || true"
    exit 1
fi

# 中枢地址自动回退（局域网 → ZeroTier）
pick_hub() {
    if curl -s -m 2 -H "X-Hub-Token: $HUB_TOKEN" "$HUB/status" >/dev/null 2>&1; then echo "$HUB"; return; fi
    if curl -s -m 3 -H "X-Hub-Token: $HUB_TOKEN" "$HUB_ZT/status" >/dev/null 2>&1; then echo "$HUB_ZT"; return; fi
    echo ""
}

API=$(pick_hub)

if [ "$1" = "--check" ]; then
    [ -z "$API" ] && { echo "❌ 中枢不可达（局域网 + ZT 都不通）"; exit 1; }
    echo "中枢: $API"
    curl -s -m 5 -H "X-Hub-Token: $HUB_TOKEN" "$API/status"
    echo ""
    exit 0
fi

if [ -z "$API" ]; then
    echo "❌ 中枢不可达（局域网 + ZT 都不通）"
    exit 1
fi

# ---- 本地手动快照（archive-old）：--local 列出 / --local N 恢复 ----
LOCAL_ARCH="$HOME/qspace-layouts/archive-old"
if [ "$1" = "--local" ]; then
    if [ -z "$2" ]; then
        echo "本地手动快照（archive-old，1 = 最新）："
        ls -t "$LOCAL_ARCH"/*.json 2>/dev/null | head -20 | sed "s|.*/||" | nl -w2 -s ') '
        echo ""
        echo "恢复用法: $0 --local <编号>"
        exit 0
    fi
    IDXL="$2"
    LF=$(ls -t "$LOCAL_ARCH"/*.json 2>/dev/null | sed -n "${IDXL}p")
    [ -z "$LF" ] && { echo "❌ 编号 $IDXL 无效"; exit 1; }
    echo "从本地快照恢复: $(basename "$LF")"
    API="$API" HUB_TOKEN="$HUB_TOKEN" python3 - "$LF" <<'PYEOF'
import json, os, sys, time, urllib.request
p = sys.argv[1]
d = json.load(open(p))
attrs = d.get("itemAttrs") or []
ds = d.get("desktopSettings") or {}
lod = ds.get("lod")
# 按 lod + 桌面根路径最大 x 判套
desk = [a for a in attrs if str(a.get("name","")).startswith("/Users/jon/Desktop/") and isinstance(a.get("x"),(int,float))]
if not desk:
    print("❌ 快照里没有桌面图标"); sys.exit(1)
maxx = max(a["x"] for a in desk)
if lod == 96 or maxx > 2000:   SPEC=("C",120,40,113,148,22,9)
elif maxx > 1600:              SPEC=("B",53,43,90,114,19,9)
else:                          SPEC=("A",80,44,79,114,21,9)
key,x0,y0,cs,rs,cols,rows = SPEC
items=[]
for a in desk:
    c=max(1,min(cols,round((a["x"]-x0)/cs)+1)); r=max(1,min(rows,round((a["y"]-y0)/rs)+1))
    items.append({"name":a["name"],"col":c,"row":r})
# 去重
seen=set(); fixed=[]
for it in sorted(items, key=lambda z:z["name"]):
    c,r=it["col"],it["row"]; g=0
    while (c,r) in seen and g<300:
        c+=1
        if c>cols: c=1; r+=1
        if r>rows: break
        g+=1
    if r<=rows: seen.add((c,r)); fixed.append({"name":it["name"],"col":c,"row":r})
items=fixed
t="|".join(f"{i['name']}:{i['col']},{i['row']}" for i in items)
hv=0xcbf29ce484222325
for ch in t.encode(): hv^=ch; hv=(hv*0x100000001b3)&0xFFFFFFFFFFFFFFFF
payload={"sourceMachine":"local-snapshot","specKey":key,"syncAt":int(time.time()),
         "items":items,"layoutFingerprint":format(hv,'016x'),
         "grid":{"cols":cols,"rows":rows,"x0":x0,"y0":y0,"colStep":cs,"rowStep":rs}}
req=urllib.request.Request(os.environ["API"]+"/layout",data=json.dumps(payload).encode(),
    headers={"Content-Type":"application/json","X-Hub-Token":os.environ.get("HUB_TOKEN","")},method="POST")
print("结果:", urllib.request.urlopen(req,timeout=6).read().decode(), "| 套:",key,"图标:",len(items))
PYEOF
    echo "$(date '+%Y-%m-%d %H:%M:%S') [rollback-local] file=$(basename "$LF") by=$(whoami)@$(hostname -s)" >> "$RBLOG"
    echo "✅ 已提交，三台 10~20 秒内应用"
    exit 0
fi

if [ -z "$1" ]; then
    echo "中枢: $API"
    curl -s -m 5 -H "X-Hub-Token: $HUB_TOKEN" "$API/status"
    echo ""
    echo "最近的布局归档（1 = 最新）："
    select_archive list
    echo ""
    echo "回滚用法: $0 <编号>"
    exit 0
fi

IDX="$1"
FILE=$(select_archive pick "$IDX")
if [ -z "$FILE" ]; then
    echo "❌ 编号 $1 无效（先运行 $0 查看列表）"
    exit 1
fi

echo "回滚到: $(basename "$FILE")"
ssh -o BatchMode=yes "$MINIPC" "cat \"$FILE\"" | API="$API" HUB_TOKEN="$HUB_TOKEN" python3 -c '
import sys, os, json, time, urllib.request
d = json.load(sys.stdin)
items = d.get("items") or []
if not items:
    print("❌ 归档内容为空（可能是污染记录），已中止"); sys.exit(1)
old_src = str(d.get("sourceMachine", "?")).replace("(回滚)", "")
d["syncAt"] = int(time.time())
d["sourceMachine"] = old_src + "(回滚)"
api = os.environ["API"]
req = urllib.request.Request(api + "/layout",
      data=json.dumps(d).encode(),
      headers={"Content-Type": "application/json", "X-Hub-Token": os.environ.get("HUB_TOKEN", "")}, method="POST")
print("结果:", urllib.request.urlopen(req, timeout=6).read().decode())
print("图标:", len(items), "| 来自:", old_src, "| fp:", str(d.get("layoutFingerprint"))[:16])
'
echo "$(date '+%Y-%m-%d %H:%M:%S') [rollback] idx=$IDX file=$(basename "$FILE") by=$(whoami)@$(hostname -s)" >> "$RBLOG"
echo "✅ 已提交到中枢，三台 Mac 将在 10~20 秒内自动应用"
echo "   查看进度: tail -f ~/qspace-layouts/app.log"
