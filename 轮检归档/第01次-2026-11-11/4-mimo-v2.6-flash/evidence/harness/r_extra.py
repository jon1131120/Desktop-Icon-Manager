#!/usr/bin/env python3
"""补充实测：主链路应用+校验 / 吸收期 / 本机重叠推开 / 快照链 / 风暴保护 /
网络失败退避与恢复 / 客户端保护窗退避 / 日志权限。"""
import re, sys, time, json, os, subprocess, glob, shutil
sys.path.insert(0, "/Users/jon/Projects/Desktop-Icon-Manager/轮检归档/第01次-2026-11-11/4-mimo-v2.6-flash/evidence/harness")
from mimo_lib import *

T = int(time.time())
HUBLOG = "/tmp/mimo_sandbox/data/hub.log"

print("== A 主链路：远端布局 → 拉取 → 应用 → 校验 ==")
hub_reset(); reset_root(n=10, spec="B", prefix="q")
# 中枢放一个「另一台机器」的版本：q01..q10 换了位置（A 套）
items = [{"name": f"q{i:02d}.txt", "col": (i % 14) + 1, "row": (i % 9) + 1} for i in range(1, 11)]
items[0]["col"] = 13; items[0]["row"] = 6      # 明显改位
body = {"syncAt": T + 50, "sourceMachine": "Air", "specKey": "A", "items": items,
        "layoutFingerprint": "fp-remote-air", "desktopSettings": {"lod": 64}}
import urllib.request
req = urllib.request.Request("http://127.0.0.1:18890/layout", data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json", "X-Hub-Token": TOKEN}, method="POST")
urllib.request.urlopen(req, timeout=5).read()
rc, out, err = run([{"op": "now", "t": T + 60}, {"op": "cycle"}])
lg = applog()
applied = [l for l in lg.splitlines() if "拉取中枢" in l or "应用 source" in l or "校验 ok" in l]
rec("拉取→应用→校验 全链路留痕", any("校验 ok" in l for l in applied) and any("应用 source=Air" in l for l in applied),
    {"lines": applied[-3:]})
conf = conf_load()
cells = {}
for a in conf.get("itemAttrs", []):
    c = round((a["x"] - 53) / 90) + 1; r = round((a["y"] - 43) / 114) + 1
    cells.setdefault((c, r), []).append(os.path.basename(a["name"]))
dups = {k: v for k, v in cells.items() if len(v) > 1}
rec("应用后无同格重叠", not dups, {"dups": dups})
vline = [l for l in lg.splitlines() if "校验 ok" in l][-1:]
rec("verify 校验通过（ok=true）", "ok=true" in (vline[0] if vline else ""), vline)

print("== B 吸收期：应用后 60s 内不上报（回声斩断）==")
posts0 = hub_posts()
rc, out, err = run([{"op": "now", "t": T + 65}, {"op": "bc"}])     # 应用后 5 秒
rec("吸收期内 bc 不上报", hub_posts() == posts0,
    {"delta": hub_posts() - posts0, "log": [l for l in applog().splitlines() if "吸收期" in l][-1:]})
st = state_load()
rec("吸收期基线跟随本机（baseline 已更新）", bool(st.get("baselineFingerprint")), st.get("baselineFingerprint", "")[:12])

print("== C 本机重叠自动推开（fixLocalOverlaps）==")
reset_root(n=6, spec="B")
attrs = json.load(open(ROOT + "/conf/desktop.conf"))["itemAttrs"]
attrs[0]["x"] = attrs[1]["x"]; attrs[0]["y"] = attrs[1]["y"]      # 人造同格重叠
attrs[1]["name"] = "/tmp/mimo_sandbox/root/Desktop/dup2.txt"
json.dump({"visible": True, "lod": 64, "itemHSpacing": 22, "fontSize": 11, "itemAttrs": attrs},
          open(ROOT + "/conf/desktop.conf", "w"))
rc, out, err = run([{"op": "now", "t": T + 200}, {"op": "overlap"}])
conf = conf_load()
cells = {}
for a in conf["itemAttrs"]:
    c = round((a["x"] - 53) / 90) + 1; r = round((a["y"] - 43) / 114) + 1
    cells.setdefault((c, r), []).append(1)
dups = {k: v for k, v in cells.items() if len(v) > 1}
rec("同格重叠 → 自动推开 0 残留", not dups and "自动推开" in applog(),
    {"dups": dups, "log": [l for l in applog().splitlines() if "overlap" in l][-1:]})

print("== D 快照链：同分钟防撞 / 自动快照 / 保底 / restore / prune ==")
reset_root(n=8, spec="B")
run([{"op": "now", "t": T + 300}, {"op": "save", "name": "测试A"},
     {"op": "save", "name": "测试A"}, {"op": "save", "name": "测试A"}])
snaps = sorted(os.path.basename(f) for f in glob.glob(ROOT + "/layouts/*.json"))
tri = [s for s in snaps if "测试A" in s]
rec("同分钟同名 3 连存防撞（-1/-2 序号）", len(tri) == 3, tri)
# 自动快照：内容变化 + 10 分钟限流
run([{"op": "now", "t": T + 300}, {"op": "autosnap", "last": 0}])
n_auto1 = len([f for f in glob.glob(ROOT + "/layouts/*.json") if "自动" in os.path.basename(f)])
run([{"op": "now", "t": T + 301}, {"op": "autosnap", "last": 0}])
n_auto2 = len([f for f in glob.glob(ROOT + "/layouts/*.json") if "自动" in os.path.basename(f)])
rec("内容未变二次 autosnap 跳过（same=true）", n_auto1 == n_auto2 and n_auto1 >= 1, (n_auto1, n_auto2))
# 保底：mtime 拨 25h
autos = [f for f in glob.glob(ROOT + "/layouts/*.json") if "自动" in os.path.basename(f)]
if autos:
    old = time.time() - 25 * 3600
    os.utime(autos[0], (old, old))
    run([{"op": "now", "t": T + 302}, {"op": "autosnap", "last": 0}])
    n_auto3 = len([f for f in glob.glob(ROOT + "/layouts/*.json") if "自动" in os.path.basename(f)])
    rec("24h 保底触发新快照", n_auto3 > n_auto2, (n_auto2, n_auto3))
# restore：把 conf 挪乱 → restore 还原
sn = sorted(glob.glob(ROOT + "/layouts/*.json"))[0]
snapd = json.load(open(sn))
orig = {a["name"]: (a["x"], a["y"]) for a in snapd.get("itemAttrs", [])}
attrs = json.load(open(ROOT + "/conf/desktop.conf"))["itemAttrs"]
for a in attrs:                       # 人造乱位
    a["x"] = a["x"] + 90; a["y"] = a["y"] + 114
json.dump({"visible": True, "lod": 64, "itemAttrs": attrs}, open(ROOT + "/conf/desktop.conf", "w"))
run([{"op": "now", "t": T + 303}, {"op": "restore", "path": sn, "restart": False}])
conf = conf_load()
restored = sum(1 for a in conf["itemAttrs"]
               if orig.get(a["name"]) and abs(a["x"] - orig[a["name"]][0]) <= 1)
rec("restore 快照还原乱位（多数图标回原位）", restored >= max(1, len(orig) - 2),
    {"restored": restored, "expect": len(orig)})
# prune：11 份自动快照只留 10
autos_dir = ROOT + "/layouts"
auto_files = sorted(f for f in os.listdir(autos_dir) if "自动" in f)
if len(auto_files) <= 10:
    for i in range(12):
        shutil.copy(os.path.join(autos_dir, auto_files[0]), os.path.join(autos_dir, f"[MBP] 自动 补{i}.json"))
run([{"op": "prune"}])
left = [f for f in os.listdir(autos_dir) if "自动" in f]
rec("prune 自动快照只留 10 份", len(left) <= 10, len(left))

print("== E 风暴保护（60s 内 ≥8 次 → 暂停上报）==")
posts0 = hub_posts()
run([{"op": "now", "t": T + 400},
     {"op": "state", "set": {"recentBroadcasts": [T + 400] * 8, "baselineFingerprint": "old-fp"}},
     {"op": "bc"}])
rec("风暴保护触发（日志+不上报）", "风暴保护" in applog() and hub_posts() == posts0,
    {"delta": hub_posts() - posts0, "log": [l for l in applog().splitlines() if "风暴" in l][-1:]})

print("== F 网络失败退避 + 恢复归零 ==")
subprocess.run(["pkill", "-f", "hub-mimo.py"], check=False)
time.sleep(1)
rc, out, err = run([{"op": "now", "t": T + 500}, {"op": "bc"},
                    {"op": "now", "t": T + 515}, {"op": "pull"}])
lg1 = applog()
rec("中枢不可达 → 上报失败退避 + 拉取失败退避",
    "网络不可达" in lg1 and "拉取失败" in lg1,
    {"bc": [l for l in lg1.splitlines() if "上报失败" in l][-1:],
     "pull": [l for l in lg1.splitlines() if "拉取失败" in l][-1:]})
subprocess.run("QSPACE_HUB_DIR=/tmp/mimo_sandbox/data nohup python3 /tmp/mimo_sandbox/hub-mimo.py >> /tmp/mimo_sandbox/hub18890.log 2>&1 &",
               shell=True)
time.sleep(2)
# streak 是进程内存态：注入 3 模拟「同进程连续失败后网络恢复」
rc, out, err = run([{"op": "rt", "set": {"hubFailStreak": 3, "hubNextRetryAt": T + 560}},
                    {"op": "now", "t": T + 510}, {"op": "pull"}])
lg2 = applog()
rec("网络恢复 → 拉取成功且退避归零（streak 清零日志）",
    "拉取恢复，退避归零" in lg2 and "连续失败 3 次" in lg2,
    [l for l in lg2.splitlines() if "拉取恢复" in l][-1:])

print("== G 客户端保护窗：被 protected 拒 → 退避 → 窗开重报 ==")
hub_reset(); reset_root(n=10, spec="B", prefix="q")
run([{"op": "now", "t": T + 150}, {"op": "cycle"}])               # 本机版本入中枢
# 中枢被 Air 的 active 版本覆盖（注入 layout.json）
lp = "/tmp/mimo_sandbox/data/layout.json"
d = json.load(open(lp)); d["syncAt"] = T + 160; d["sourceMachine"] = "Air"
d["userActiveUntil"] = int(time.time()) + 30; d["userActiveMachine"] = "Air"   # 中枢按真时钟判活
json.dump(d, open(lp, "w"))
write_conf(10, spec="B", prefix="q", shift=7)                      # 本机改动
fp2 = re.search(r"fp=(\S+)", run([{"op": "snap"}])[1]).group(1)
run([{"op": "state", "set": {"lastAppliedAt": 0}}, {"op": "now", "t": T + 170}, {"op": "bc"}])
lg = applog()
rec("非 active 覆盖被拒 reason=protected → 客户端跟随对方", "上报被保护窗拒绝" in lg,
    [l for l in lg.splitlines() if "保护窗" in l][-1:])
st = state_load()
rec("被 protected 拒后 baseline 不吞改动（baseline ≠ fp2）",
    st.get("baselineFingerprint") != fp2, {"baseline": str(st.get("baselineFingerprint"))[:12], "fp2": fp2[:12]})
# 窗开（把中枢 active 拨过去）→ 重报被接受
d = json.load(open(lp)); d["userActiveUntil"] = int(time.time()) - 10   # 真时钟下已过期
json.dump(d, open(lp, "w"))
run([{"op": "state", "set": {"lastAppliedAt": 0}}, {"op": "now", "t": T + 220}, {"op": "bc"}])
h = hub_get()
rec("窗开后本机改动重报被接受（后改者赢/停手者赢）", h.get("sourceMachine") == "MBP" and h.get("syncAt") == T + 220,
    {"src": h.get("sourceMachine"), "syncAt": h.get("syncAt"), "expect": T + 220,
     "log": [l for l in applog().splitlines() if "上报中枢" in l][-1:]})

print("== H 日志与权限 ==")
perms = {f: oct(os.stat(f).st_mode & 0o777) for f in
         ["/tmp/mimo_sandbox/data/layout.json", HUBLOG] if os.path.exists(f)}
rec("layout.json=600；hub.log 记录观察（当前 umask 决定）", perms.get("/tmp/mimo_sandbox/data/layout.json") == "0o600",
    perms)

dump("matrix-extra.json")
print(f"\n补充实测: {sum(1 for r in results if r['ok'])}/{len(results)} 通过")
