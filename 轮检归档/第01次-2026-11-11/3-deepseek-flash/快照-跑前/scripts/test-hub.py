#!/usr/bin/env python3
"""
中枢 API 契约测试 —— 只打沙箱中枢（127.0.0.1:18879），绝不碰生产（18878）
用法: python3 scripts/test-hub.py
"""
import json, os, sys, time, urllib.request, urllib.error, threading

PORT = os.environ.get("SANDBOX_PORT", "18879")
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = open(os.path.join(os.path.dirname(__file__), "..", "hub-sandbox-token.txt")).read().strip() \
        if os.path.exists(os.path.join(os.path.dirname(__file__), "..", "hub-sandbox-token.txt")) else "sandbox-token-123"

passed = failed = 0

def post(body, token=TOKEN):
    req = urllib.request.Request(BASE + "/layout", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Hub-Token": token}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "body": e.read().decode()[:100]}
    except Exception as e:
        return {"err": str(e)[:80]}

def get(path="/layout", token=TOKEN):
    req = urllib.request.Request(BASE + path, headers={"X-Hub-Token": token})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "body": e.read().decode()[:100]}

def reset():
    """每个用例前清空中枢状态，避免用例间互相干扰（保护窗会延续）

    生产 hub 没有 /reset 端点（2026-11-11 轮检 R20/R29 实测：GET/POST 只认
       /layout 与 /status，未知路径 404）-> 旧实现的 POST /reset 静默失败，
       17 项契约测试实际跑在脏状态上（保护窗延续到后续用例）。
       改为直接删除沙箱中枢的 layout.json（真跑清场）。
    """
    import os, glob
    home = os.environ.get("SANDBOX_HOME", "")
    if home:
        for f in glob.glob(os.path.join(home, "layout.json")):
            try: os.remove(f)
            except OSError: pass
        arch = os.path.join(home, "archive")
        if os.path.isdir(arch):
            for f in glob.glob(os.path.join(arch, "*.json")):
                try: os.remove(f)
                except OSError: pass
        return
    req = urllib.request.Request(BASE + "/reset", data=b"", headers={"X-Hub-Token": TOKEN}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  \u2713 {name}")
    else:
        failed += 1
        print(f"  \u2717 {name}  {detail}")

NOW = int(time.time())
def layout(src="MBP", at=None, active=0, items=None, **kw):
    b = {"syncAt": at if at is not None else NOW + 100, "sourceMachine": src,
         "items": items if items is not None else [{"name": "t.txt", "col": 1, "row": 1}],
         "layoutFingerprint": f"fp-{src}-{at}", "specKey": "C"}
    if active: b["userActiveUntil"] = NOW + active
    b.update(kw)
    return b

print("== 中枢契约测试（沙箱 :18879，绝不碰生产 :18878）==")
# ── 保护窗序列（必须连续，不能中间 reset）──
reset()
r = post(layout("MBP", at=NOW+100, active=600))
check("合法上报被接受", r.get("accepted") is True, r)

r = post(layout("MBP", at=NOW+99))
check("旧 syncAt 被拒 (older, 不被保护窗抢答)", r.get("reason") == "older", r)

r = post(layout("MBP", at=NOW+101))
check("无标记覆盖 active 版本 → protected", r.get("reason") == "protected", r)

r = post(layout("Air", at=NOW+102, active=600))
check("另一台带 active 覆盖 → 接受（用户 vs 用户后改者赢）", r.get("accepted") is True, r)

reset()
r = post(layout("NOT-IN-ALLOWLIST", at=NOW+103))
check("白名单外 sourceMachine → 400", r.get("HTTP") == 400, r)

reset()
r = post(layout("MBP", at=NOW+104, items="notalist"))
check("items 非数组 → 400", r.get("HTTP") == 400, r)

reset()
r = post(layout("MBP", at=NOW+105, items=[]))
check("items 空 → 400", r.get("HTTP") == 400, r)

reset()
r = post(layout("MBP", at=NOW+106, items=[{"name": "x"}]))
check("item 缺 col → 400", r.get("HTTP") == 400, r)

reset()
r = post(layout("MBP", at=NOW+107, items=[{"name": "x", "col": -1, "row": 1}]))
check("col 负数 → 400", r.get("HTTP") == 400, r)

reset()
r = post(layout("MBP", at=NOW+108, items=[{"name": "x", "col": 1.5, "row": 1}]))
check("col 是 float → 400", r.get("HTTP") == 400, r)

reset()
r = post(layout("MBP", at=NOW+109, items=[{"name": 123, "col": 1, "row": 1}]))
check("name 非字符串 → 400", r.get("HTTP") == 400, r)

reset()
r = post(layout("MBP", at=NOW+110, items=[{"name": "x", "col": 99999, "row": 1}]))
check("col 超范围 → 400", r.get("HTTP") == 400, r)

reset()
r = post(layout("MBP", at=NOW+111, userActiveUntil="abc"))
check("userActiveUntil 非法类型 → 不 500", "HTTP" not in r or r.get("HTTP") != 500, r)

reset()
r = post(layout("MBP", at=NOW+112, syncAt=NOW+999999))
check("syncAt 未来过远 → 400", r.get("HTTP") == 400, r)

reset()
r = get("/layout", token="wrong-token")
check("错误 token → 401", r.get("HTTP") == 401, r)

reset()
r = post(layout("MBP", at=NOW+113), token="")
check("空 token → 401", r.get("HTTP") == 401, r)

# 并发
reset()
results = []
def race(i):
    results.append(post(layout(f"MBP", at=NOW+200+i)))
ts = [threading.Thread(target=race, args=(i,)) for i in range(6)]
[t.start() for t in ts]; [t.join() for t in ts]
fin = get("/layout")
check("并发后 syncAt 单调（=最大值）", fin.get("syncAt", 0) >= NOW+205, fin.get("syncAt"))

print(f"\n结果: {passed} 通过, {failed} 失败")
sys.exit(1 if failed else 0)
