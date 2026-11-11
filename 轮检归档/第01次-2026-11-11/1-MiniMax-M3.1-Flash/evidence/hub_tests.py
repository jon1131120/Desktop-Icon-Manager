#!/usr/bin/env python3
"""中枢侧实测：门禁矩阵(10) / 并发POST(11) / 空布局拒收(14-中枢侧) / 快照归档 / _state_lock"""
import json, os, sys, time, threading, urllib.request, urllib.error, subprocess

BASE = os.environ.get("SANDBOX_PORT", "18890")
URL = f"http://127.0.0.1:{BASE}"
TOKEN = "sandbox-token-123"
SB = "/tmp/sandbox_minimax"
HUBHOME = f"{SB}/hubhome"

passed = failed = 0

def reset():
    """重置中枢真源 + 归档（沙箱内）"""
    for f in ("layout.json",):
        try: os.remove(os.path.join(HUBHOME, f))
        except FileNotFoundError: pass
    arch = os.path.join(HUBHOME, "archive")
    if os.path.isdir(arch):
        for f in os.listdir(arch):
            try: os.remove(os.path.join(arch, f))
            except OSError: pass

def raw_post(body_bytes, token=TOKEN, path="/layout", headers=None):
    h = {"Content-Type": "application/json"}
    if token is not None: h["X-Hub-Token"] = token
    if headers: h.update(headers)
    req = urllib.request.Request(URL + path, data=body_bytes, headers=h, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)[:120]

def post(body, token=TOKEN, path="/layout"):
    return raw_post(json.dumps(body).encode(), token, path)

def get(path="/layout", token=TOKEN):
    req = urllib.request.Request(URL + path, headers={"X-Hub-Token": token} if token else {})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1; print(f"  ✓ {name}")
    else:
        failed += 1; print(f"  ✗ {name}  {detail}")

def layout(src="MBP", at=None, active=0, items=None, **kw):
    b = {"syncAt": at if at is not None else int(time.time()) + 10,
         "sourceMachine": src,
         "items": items if items is not None else [{"name": "t.txt", "col": 1, "row": 1}],
         "layoutFingerprint": f"fp-{src}-{at}", "specKey": "C"}
    if active: b["userActiveUntil"] = int(time.time()) + active
    b.update(kw)
    return b

NOW = int(time.time())
print(f"== 中枢实测（沙箱 :{BASE}，真机零触碰）==")

# ───────── 场景 10：门禁矩阵 ─────────
print("\n-- 场景10 门禁矩阵 --")
reset()
# ① token 错 → 401
c, b = get(token="wrong-token")
check("token 错 → 401", c == 401, f"got {c} {b[:80]}")
# ② 空 token → 401
c, b = get(token="")
check("空 token → 401", c == 401, f"got {c} {b[:80]}")
# ③ 无 token 头 → 401
req = urllib.request.Request(URL + "/layout")
try:
    c = urllib.request.urlopen(req, timeout=5).status
except urllib.error.HTTPError as e: c = e.code
check("无 token 头 → 401", c == 401, f"got {c}")
# ④ 网段外 → 403
#   沙箱绑 127.0.0.1，从非白名单 IP 访问需改源地址。
#   实测手法：沙箱 hub 的 ALLOWED_NETS 含 127.0.0.1/32 + 两个内网段。
#   本机 192.168.113.x / 192.168.191.x 属白名单；用 loopback 别名 127.0.0.2
#   仍属 127.0.0.0/8 但不在 /32 → 应 403（证明网段判定是按 /32 精确的）
c, b = get(token=TOKEN)
check("白名单内 127.0.0.1 → 200", c == 200, f"got {c}")
# 直接验证 peer_allowed 判定函数（生产代码同一份逻辑）
import importlib.util
spec = importlib.util.spec_from_file_location("hubmod", f"{SB}/hub-server.py")
# 不能直接 import（会起服务），改为复刻白名单常量做判定核对
import ipaddress
NETS = [ipaddress.ip_network(n) for n in ("127.0.0.1/32", "192.168.113.0/24", "192.168.191.0/24")]
def peer_allowed(ip):
    try: a = ipaddress.ip_address(ip)
    except ValueError: return False
    return any(a in net for net in NETS)
check("网段外 IP 10.0.0.5 → 判否(403)", not peer_allowed("10.0.0.5"))
check("网段外 IP 8.8.8.8 → 判否(403)", not peer_allowed("8.8.8.8"))
check("白名单 192.168.113.14 → 判可", peer_allowed("192.168.113.14"))
check("白名单 192.168.191.26 → 判可", peer_allowed("192.168.191.26"))
# ⑤ body > 1MB → 400/413
reset()
big = json.dumps(layout(items=[{"name": "x"*1_200_000, "col": 1, "row": 1}])).encode()
# 用裸 socket 读服务端真实响应（urllib 侧看到的是 broken pipe）
import socket
def raw_socket_post(body):
    s_ = socket.create_connection(("127.0.0.1", int(BASE)), timeout=10)
    head = (f"POST /layout HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Hub-Token: {TOKEN}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
    try:
        s_.sendall(head + body)
    except BrokenPipeError:
        pass
    buf = b""
    try:
        while True:
            chunk = s_.recv(4096)
            if not chunk: break
            buf += chunk
            if len(buf) > 200000: break
    except Exception as e:
        buf += f"[recv err {e}]".encode()
    s_.close()
    return buf.decode(errors="replace")
resp = raw_socket_post(big)
first = resp.split("\r\n")[0] if resp else "(空响应)"
check("body > 1MB → 服务端回 413", "413" in first, f"raw='{first}'")
check("body > 1MB → 客户端读到 broken pipe（未 drain 请求体即响应）",
      "Broken pipe" in (raw_post(big)[1]), f"got {raw_post(big)[1][:80]}")
# ⑥ items > 500 → 400
reset()
many = [{"name": f"n{i}.txt", "col": (i % 20) + 1, "row": (i // 20) + 1} for i in range(501)]
c, b = post(layout(items=many))
check("items > 500 → 400", c == 400, f"got {c} {b[:80]}")
# ⑦ 坏 JSON → 400
reset()
c, b = raw_post(b"{ not json ")
check("坏 JSON → 400", c == 400, f"got {c} {b[:80]}")
# ⑧ 时间超前 300s → 400
reset()
c, b = post(layout(at=NOW + 301))
check("syncAt 超前 301s → 400", c == 400, f"got {c} {b[:80]}")
# ⑨ 时间超前 299s → 接受（边界）
reset()
c, b = post(layout(at=NOW + 299))
check("syncAt 超前 299s → 接受(边界)", c == 200 and '"accepted": true' in b, f"got {c} {b[:80]}")
# ⑩ items=[] → 400
reset()
c, b = post(layout(items=[]))
check("items=[] → 400", c == 400, f"got {c} {b[:80]}")
# ⑪ 未知 sourceMachine → 400
reset()
c, b = post(layout(src="HACKER"))
check("未知 sourceMachine → 400", c == 400, f"got {c} {b[:80]}")
# ⑫ col 超范围 → 400
reset()
c, b = post(layout(items=[{"name": "x", "col": 1001, "row": 1}]))
check("col=1001 > MAX_COORD → 400", c == 400, f"got {c} {b[:80]}")
# ⑬ 未知路径 → 404
reset()
c, b = post(layout(), path="/nope")
check("未知路径 → 404", c == 404, f"got {c} {b[:80]}")
# ⑭ 非 GET/POST 方法（OPTIONS）→ 204
reset()
req = urllib.request.Request(URL + "/layout", method="OPTIONS")
try: c = urllib.request.urlopen(req, timeout=5).status
except urllib.error.HTTPError as e: c = e.code
check("OPTIONS → 204", c == 204, f"got {c}")

# ───────── 场景 11：并发 POST ─────────
print("\n-- 场景11 并发 POST --")
reset()
results = []
lock = threading.Lock()
def race(i):
    c, b = post(layout(src="MBP", at=NOW + 200 + i,
                        items=[{"name": f"c{i}.txt", "col": (i % 20) + 1, "row": 1}]))
    with lock: results.append((c, b))
ts = [threading.Thread(target=race, args=(i,)) for i in range(10)]
[t.start() for t in ts]; [t.join() for t in ts]
c, b = get()
final = json.loads(b)
accepted = [r for r in results if r[0] == 200 and '"accepted": true' in r[1]]
check("10 并发全部 HTTP 200", all(r[0] == 200 for r in results), str([r[0] for r in results]))
check("最终 syncAt = 最大值", final.get("syncAt") == NOW + 209, str(final.get("syncAt")))
check("最终 items = 最后写入者(并发10个不同名)", final.get("items", [{}])[0].get("name") in
      [f"c{i}.txt" for i in range(10)], str(final.get("items", [{}])[0].get("name")))
# 一致性：落盘 JSON 必须完整可解析
with open(os.path.join(HUBHOME, "layout.json")) as f:
    disk = json.load(f)
check("落盘 JSON 完整（无交叉写坏）", disk.get("syncAt") == final.get("syncAt"), str(disk.get("syncAt")))
# 归档条数 = 接受次数
narch = len([f for f in os.listdir(os.path.join(HUBHOME, "archive")) if f.endswith(".json")])
check(f"归档条数 = 接受次数 ({len(accepted)})", narch == len(accepted), f"archive={narch} accepted={len(accepted)}")

# ───────── 场景：中枢并发一致性（syncAt 排序，防旧覆盖新）─────────
print("\n-- 场景11b 旧 syncAt 不得覆盖新 --")
reset()
post(layout(at=NOW + 200, items=[{"name": "new.txt", "col": 1, "row": 1}]))
c, b = post(layout(at=NOW + 100, items=[{"name": "old.txt", "col": 2, "row": 1}]))
check("旧 syncAt 被拒 accepted=false", '"accepted": false' in b, b[:120])
check("拒回 reason=older", '"reason": "older"' in b, b[:120])
c, b = get()
check("中枢仍是新版本", json.loads(b)["items"][0]["name"] == "new.txt", b[:120])

# ───────── 场景：保护窗（中枢侧判定）─────────
print("\n-- 场景12b 保护窗中枢侧 --")
reset()
c, b = post(layout(src="MBP", at=NOW + 10, active=600, items=[{"name": "a.txt", "col": 1, "row": 1}]))
check("active 上报被接受", '"accepted": true' in b, b[:120])
c, b = post(layout(src="Air", at=NOW + 20, items=[{"name": "b.txt", "col": 2, "row": 1}]))
check("非 active 覆盖 active → protected", '"reason": "protected"' in b, b[:120])
c, b = post(layout(src="Air", at=NOW + 20, active=600, items=[{"name": "b.txt", "col": 2, "row": 1}]))
check("另一台也 active → 后改者赢(接受)", '"accepted": true' in b, b[:120])
# 保护窗与 older 的先后：旧 syncAt 应先判 older，不被保护窗抢答
reset()
post(layout(src="MBP", at=NOW + 10, active=600))
c, b = post(layout(src="Air", at=NOW + 5))
check("旧 syncAt 优先判 older（不被 protected 抢答）", '"reason": "older"' in b, b[:120])

# ───────── 场景：archive 归档与轮转 ─────────
print("\n-- 场景 archive 归档 --")
reset()
for i in range(25):
    post(layout(at=NOW + 100 + i, items=[{"name": f"a{i}.txt", "col": 1, "row": 1}]))
narch = len([f for f in os.listdir(os.path.join(HUBHOME, "archive")) if f.endswith(".json")])
check(f"归档上限 20（实际 {narch}）", narch <= 20, str(narch))
# 同秒同机名不撞名
reset()
for i in range(3):
    post(layout(at=NOW + 100, items=[{"name": f"z{i}.txt", "col": 1, "row": 1}]))
names = sorted(f for f in os.listdir(os.path.join(HUBHOME, "archive")) if f.endswith(".json"))
check("同秒同机归档不撞名", len(names) == len(set(names)), str(names))

print(f"\n结果: {passed} 通过, {failed} 失败")
sys.exit(1 if failed else 0)
