#!/usr/bin/env python3
"""工况1~4：新增对账 / 删除清理 / 防震荡 / 快速连续变化（防抖）。
测试纪律（本脚本踩过的坑）：
  1) rt 是进程内存态 → 每个引擎脚本内先 pull 重建 hubNamesSet 再 bc；
  2) 「刚应用 15 秒忽略期」(main.swift:1652) → bc 注入 now=真实+20，否则同秒必被静默拦；
  3) conf 变更必须作为引擎 op 内联进同一脚本（写盘发生在脚本运行前=全部变成终态）。"""
import re, sys, time, json
sys.path.insert(0, "/Users/jon/Projects/Desktop-Icon-Manager/轮检归档/第01次-2026-11-11/4-mimo-v2.6-flash/evidence/harness")
from mimo_lib import *

T = int(time.time())
def conf_step(n, prefix="f", shift=0, spec="B"):
    items = [{"name": f"/tmp/mimo_sandbox/root/Desktop/{prefix}{i:02d}.txt",
              "col": ((i + shift) % 14) + 1, "row": (i % 9) + 1} for i in range(1, n + 1)]
    return {"op": "conf", "spec": spec, "items": items}

def fp_of(n, prefix="f"):
    write_conf(n, spec="B", prefix=prefix)
    rc, out, _ = run([{"op": "snap"}])
    return re.search(r"fp=(\S+)", out).group(1)

def names(n, prefix="f"):
    return [f"{prefix}{i:02d}.txt" for i in range(1, n + 1)]

def lastlog(pat, k=2):
    return [l for l in applog().splitlines() if pat in l][-k:]

print("== 工况1 新增文件对账（37→38 补报）==")
hub_reset(); reset_root(n=37, spec="B")
run([{"op": "now", "t": T}, {"op": "cycle"}])
run([{"op": "now", "t": T + 1}, {"op": "cycle"}])
n0 = len(hub_get().get("items", []))
rec("首轮上报中枢=37", n0 == 37, {"items": n0, "src": hub_get().get("sourceMachine")})
fp38 = fp_of(38)
rc, out, err = run([{"op": "now", "t": T + 20},
                    {"op": "pull"},
                    {"op": "state", "set": {"baselineFingerprint": fp38}},
                    {"op": "bc"}])
n1 = len(hub_get().get("items", []))
rec("本机38 vs 中枢37 → 对账补报 → 中枢38", n1 == 38,
    {"before": n0, "after": n1, "log": lastlog("名单对账"), "out": out.strip().splitlines()[-4:]})

print("== 工况2 删除死条目清理（30 分钟计时）==")
fp37 = fp_of(37)
rc, out, err = run([{"op": "now", "t": T + 30},
                    {"op": "pull"},
                    {"op": "state", "set": {"baselineFingerprint": fp37}},
                    {"op": "rt", "set": {"ledgerShortSince": T + 30}},
                    {"op": "bc"}])
n2 = len(hub_get().get("items", []))
rec("短缺 20s 内不清理（等 iCloud/尊重删除）", n2 == 38,
    {"items": n2, "log": lastlog("对账"), "out": out.strip().splitlines()[-4:]})
rc, out, err = run([{"op": "now", "t": T + 40},
                    {"op": "pull"},
                    {"op": "rt", "set": {"ledgerShortSince": T + 40 - 1801}},
                    {"op": "state", "set": {"baselineFingerprint": fp37}},
                    {"op": "bc"}])
n3 = len(hub_get().get("items", []))
rec("短缺>30分钟 → 上报清理 → 中枢37", n3 == 37,
    {"items": n3, "log": lastlog("30 分钟", 1), "out": out.strip().splitlines()[-4:]})

print("== 工况3 防震荡（死条目复活不补报 + A/B 交替）==")
fp38b = fp_of(38)
rc, out, err = run([{"op": "now", "t": T + 50},
                    {"op": "pull"},
                    {"op": "rt", "set": {"hubNamesSet": names(38)}},   # prev ← 38（含 f38）
                    {"op": "rt", "set": {"hubNamesSet": names(37)}},   # 当前中枢 37
                    {"op": "state", "set": {"baselineFingerprint": fp38b}},
                    {"op": "bc"}])
n4 = len(hub_get().get("items", []))
lg4 = lastlog("尊重删除", 1)
rec("本机多出曾在中枢的 f38 → 不补报（尊重删除）", n4 == 37 and any("尊重删除" in l for l in lg4),
    {"items": n4, "log": lg4, "out": out.strip().splitlines()[-4:]})
hub_reset(); reset_root(n=38, spec="B")
seq = []
for i in range(6):
    n = 38 if i % 2 == 0 else 37
    write_conf(n, spec="B")
    ops = [{"op": "now", "t": T + 60 + i * 10}, {"op": "pull"}]
    if i > 0:
        rc2, out2, _ = run([{"op": "snap"}])
        fpn = re.search(r"fp=(\S+)", out2).group(1)
        ops.append({"op": "state", "set": {"baselineFingerprint": fpn}})
    rc, out, err = run(ops + [{"op": "bc"}])
    seq.append(len(hub_get().get("items", [])))
rec("A(38)/B(37) 交替 6 轮中枢不摇摆（恒38）", all(x == 38 for x in seq), seq)

print("== 工况4 快速连续变化（5 次 <10s 只报最终版，单进程内联 conf op）==")
hub_reset(); reset_root(n=10, spec="B", prefix="q")
p1 = hub_posts()
script = [{"op": "now", "t": T + 100}, conf_step(10, "q", 0), {"op": "cycle"}]  # 首轮建基线(+1)
for i in range(5):                                                              # 5 次快速变更（间隔 1s）
    script += [{"op": "now", "t": T + 101 + i}, conf_step(10, "q", i + 1), {"op": "bc"}]
script += [{"op": "now", "t": T + 117}, {"op": "bc"}]                            # 停手 16s 后
rc, out, err = run(script, timeout=60)
p2 = hub_posts()
h6 = hub_get()
rec("5 次快速变更+停手16s → 全程恰好 2 次上报（首轮+最终版）", p2 - p1 == 2,
    {"delta": p2 - p1, "hub_items": len(h6.get("items", [])), "out": out.strip().splitlines()[-4:]})
rec("变更被识别为用户拖动（进保护窗，≥1 条日志）", "检测到用户拖动" in applog(),
    lastlog("检测到用户拖动", 1))
rec("防抖窗口内中间态零上报", p2 - p1 == 2, {"accepted_delta": p2 - p1})

dump("matrix-r01_04.json")
print(f"\n工况1-4: {sum(1 for r in results if r['ok'])}/{len(results)} 通过")
