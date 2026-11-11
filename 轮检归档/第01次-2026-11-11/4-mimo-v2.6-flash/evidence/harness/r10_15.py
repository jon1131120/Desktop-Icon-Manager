#!/usr/bin/env python3
"""工况13~15：时钟偏移补偿 / 空布局双向拦截（客户端侧）/ selftest+变异测试。
工况10/11/12 的中枢侧已在 t_hub.py 跑过。"""
import re, sys, time, json, os, subprocess, shutil
sys.path.insert(0, "/Users/jon/Projects/Desktop-Icon-Manager/轮检归档/第01次-2026-11-11/4-mimo-v2.6-flash/evidence/harness")
from mimo_lib import *

T = int(time.time())

print("== 工况13 时钟偏移：客户端钟落后 → clockOffset 补偿后上报成功 ==")
hub_reset(); reset_root(n=15, spec="B")
H = T + 400
run([{"op": "now", "t": T}, {"op": "cycle"}])                      # 中枢版本 syncAt=T
h0 = hub_get(); sync0 = h0.get("syncAt")
rec("前置：中枢版本 syncAt=T", sync0 == T, sync0)
# 本机改动 + 钟落后 500s（本机 now = T-100）
write_conf(15, spec="B", shift=3)
fp1 = re.search(r"fp=(\S+)", run([{"op": "snap"}])[1]).group(1)
run([{"op": "state", "set": {"lastAppliedAt": 0}},
                    {"op": "now", "t": T - 500}, {"op": "bc"}])      # 首发被 older 拒 → 记 offset
lg = applog()
rec("被 older 拒后记录时钟偏移", "时钟偏移补偿" in lg,
    [l for l in lg.splitlines() if "时钟偏移" in l][-1:])
st = state_load()
rec("clockOffset≈501s 落盘", abs((st.get("clockOffset") or 0) - 501) <= 2, st.get("clockOffset"))
rc, out, err = run([{"op": "state", "set": {"lastAppliedAt": 0}},
                    {"op": "now", "t": T - 500}, {"op": "bc"}])      # 补偿后重发
h1 = hub_get()
rec("补偿后上报成功（中枢 syncAt > 旧值）", (h1.get("syncAt") or 0) > sync0,
    {"sync_before": sync0, "sync_after": h1.get("syncAt"),
     "log": [l for l in applog().splitlines() if "上报中枢" in l][-1:]})

print("== 工况14 空布局拦截（客户端侧拒发）==")
hub_reset(); reset_root(n=5, spec="B")
posts0 = hub_posts()
# 14a: itemAttrs 为空数组
json.dump({"visible": True, "itemAttrs": [], "lod": 64}, open(ROOT + "/conf/desktop.conf", "w"))
rc, out, err = run([{"op": "now", "t": T + 500}, {"op": "bc"}])
rec("空 itemAttrs → 拒发（快照失败/空守卫），中枢 0 新增", hub_posts() == posts0 and rc == 0,
    {"log": [l for l in applog().splitlines() if "快照生成失败" in l or "空" in l][-1:]})
# 14b: 只有卷宗（无桌面文件）→ 走到「本机布局为空」守卫
json.dump({"visible": True, "lod": 64, "itemAttrs": [
    {"name": "/Volumes/USB", "x": 100, "y": 100, "screen": 1, "screenWidth": 1728, "screenHeight": 1117}]},
    open(ROOT + "/conf/desktop.conf", "w"))
rc, out, err = run([{"op": "now", "t": T + 501}, {"op": "bc"}])
lg = applog()
rec("0 桌面文件 → 「拒绝上报以免清空中枢」", "拒绝上报以免清空中枢" in lg and hub_posts() == posts0,
    {"log": [l for l in lg.splitlines() if "拒绝上报" in l][-1:], "posts_delta": hub_posts() - posts0})

print("== 工况15 40 点标定回归 + 变异测试 ==")
r = subprocess.run(["/tmp/mimo_sandbox/harness", "--selftest"], capture_output=True, text=True,
                   env=dict(os.environ, DS_ROOT=ROOT))
m = re.search(r"(\d+)/(\d+) 吻合", r.stdout + open(ROOT + "/app.log").read())
rec("selftest 126/126 吻合", "SELFTEST PASS" in r.stdout and m and m.group(1) == m.group(2),
    {"stdout": r.stdout.strip(), "detail": m.group(0) if m else "?"})

# —— 变异测试：改 mapCell 一行 → 重编译 → selftest 必须变红 ——
MUT = "/tmp/mimo_sandbox/mut"
os.makedirs(MUT, exist_ok=True)
PROD = "/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift"
src = open(PROD).read()
muts = {
    "M1_右锚减一": ("dc = d.cols - idxR                     // 固定区：距右 N ↔ 距右 N（完美双射）",
                    "dc = d.cols - idxR - 1                 // 固定区：距右 N ↔ 距右 N（完美双射）"),
    "M2_行带不平移": ("dr = row + (d.zone2.0 - s.zone2.0)",
                      "dr = row"),
    "M3_下界clamp去掉": ("dc = max(Ld + 1, min(d.cols, dc))",
                         "dc = min(d.cols, dc)"),
    "M4_上界clamp放宽": ("dc = max(Ld + 1, min(d.cols, dc))",
                         "dc = max(Ld + 1, min(d.cols + 1, dc))"),
}
mut_out = {}
for name, (old, new) in muts.items():
    assert old in src, name
    msrc = src.replace(old, new, 1)
    mfile = f"{MUT}/main_{name}.swift"
    open(mfile, "w").write(msrc)
    # 用同一套台架生成器（PROD/OUT 指到变异副本）
    gen = open("/Users/jon/Projects/Desktop-Icon-Manager/轮检归档/第01次-2026-11-11/4-mimo-v2.6-flash/evidence/harness/build_harness.py").read()
    gen = gen.replace('PROD = "/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift"',
                      f'PROD = "{mfile}"')
    gen = gen.replace('OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_harness.swift")',
                      f'OUT = "{MUT}/harness_{name}.swift"')
    gfile = f"{MUT}/gen_{name}.py"
    open(gfile, "w").write(gen)
    g = subprocess.run(["python3", gfile], capture_output=True, text=True)
    if g.returncode != 0:
        mut_out[name] = f"GEN_FAIL {g.stderr[-200:]}"; continue
    c = subprocess.run(["swiftc", "-O", "-o", f"{MUT}/bin_{name}", f"{MUT}/harness_{name}.swift"],
                       capture_output=True, text=True, timeout=180)
    if c.returncode != 0:
        mut_out[name] = f"COMPILE_FAIL {c.stderr[-200:]}"; continue
    open(ROOT + "/app.log", "w").close()
    rr = subprocess.run([f"{MUT}/bin_{name}", "--selftest"], capture_output=True, text=True,
                        env=dict(os.environ, DS_ROOT=ROOT))
    outx = rr.stdout + open(ROOT + "/app.log", errors="ignore").read()
    fails = len(re.findall(r"selftest FAIL", outx))
    mut_out[name] = "RED(变红✅)" if rr.returncode != 0 else "GREEN(未变红❌)"
    print(f"    变异 {name}: rc={rr.returncode} FAIL点={fails} → {mut_out[name]}")
rec("变异 M1/M2/M3 都能让 selftest 变红（测试有效性）",
    all("RED" in mut_out[k] for k in ["M1_右锚减一", "M2_行带不平移", "M3_下界clamp去掉"]), mut_out)
rec("变异 M4（上界 clamp 放宽）实测记录（deepseek 报无覆盖，看是否仍绿）", True, mut_out)

dump("matrix-r10_15.json", extra={"mutations": mut_out})
print(f"\n工况13-15: {sum(1 for r in results if r['ok'])}/{len(results)} 通过")
