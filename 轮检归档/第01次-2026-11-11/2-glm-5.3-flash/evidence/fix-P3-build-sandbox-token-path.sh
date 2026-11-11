# P3（第01次轮检 R21）：build_sandbox.sh token 路径修复——写到 hub 实际读取的 $QSPACE_HUB_DIR/hub-token.txt
# 修复前实测：官方沙箱错误/空 token 均 200 放行（令牌校验失效）；修复后：401 正常拦截
# 应用方式：替换 /tmp/build_sandbox.sh 中 token 写入行
#!/bin/bash
# 沙箱中枢构建（真隔离版）：QSPACE_HUB_DIR 指向沙箱目录，数据与生产物理分离
set -e
SB="/Users/jon/.hermes/cache/scratch/hub-sandbox"
mkdir -p "$SB"
python3 - <<'PYEOF'
import re
hub = open("/Users/jon/Projects/Desktop-Icon-Manager/hub/hub-server.py").read()
sb = hub.replace("18878", "18879")
sb = re.sub(r"ALLOWED_SOURCES\s*=\s*\[[^\]]*\]", "ALLOWED_SOURCES = ['127.0.0.1/32']", sb, count=1)
m = re.search(r"(def _do_POST_inner\(self\):\n        if not self\._gate\(\):\n            return\n)", sb)
assert m, "do_POST 锚点"
reset_code = """        if self.path == '/reset':
            for f in (LATEST,):
                try: os.remove(f)
                except OSError: pass
            self._json(200, {'ok': True, 'reset': True})
            return
"""
sb = sb.replace(m.group(1), m.group(1) + reset_code, 1)
open("/Users/jon/.hermes/cache/scratch/hub-sandbox/hub-sandbox.py", "w").write(sb)
print("沙箱副本已生成")
PYEOF
# P3（第01次轮检 R21 修复）：hub 的 TOKEN_FILE = QSPACE_HUB_DIR/hub-token.txt，
#  旧写法把 token 放 $SB/hub-token.txt → hub 读不到 → load_token() 返回 '' →
#  token_ok 恒 true → 沙箱令牌校验整层失效（实测错误/空 token 均 200 放行）。
mkdir -p "$SB/data"
echo -n "sandbox-token-123" > "$SB/data/hub-token.txt"
chmod 600 "$SB/data/hub-token.txt"
pkill -9 -f hub-sandbox.py 2>/dev/null || true
sleep 1
cd "$SB" && QSPACE_HUB_DIR="$SB/data" nohup python3 hub-sandbox.py > sandbox.log 2>&1 &
sleep 3
curl -s -m 3 -X POST -H 'X-Hub-Token: sandbox-token-123' http://127.0.0.1:18879/reset && echo
curl -s -m 3 -H 'X-Hub-Token: sandbox-token-123' http://127.0.0.1:18879/status | head -c 200; echo
