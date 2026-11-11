#!/usr/bin/env python3
"""基线 vs 修复后 对照实测：同一套场景驱动，两个 build（生产源码 vs 打补丁副本），
逐场景对比输出，确认「修复前红 → 修复后绿」，且修复未引入回归。"""
import pathlib, shutil, subprocess, os, sys

SB = pathlib.Path("/tmp/sandbox_minimax")
WORK = SB / "fixwork"
SDK = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()

SCENARIOS = ["1","2","3","4","5","6","7","8","8b","8c","9","10","11","12","13","14",
             "15","16","17","18","19","20","21","22"]

def build(src_main: pathlib.Path, outdir: pathlib.Path, tag: str) -> pathlib.Path:
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QS_SRC_MAIN=str(src_main), QS_OUT=str(outdir/"main.swift"))
    r = subprocess.run([sys.executable, str(SB/"mksandbox.py")], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[{tag}] mksandbox 失败:", r.stderr[-800:]); sys.exit(1)
    r2 = subprocess.run(["swiftc", "-O", "-sdk", SDK, "-o", str(outdir/"cli"), str(outdir/"main.swift"),
                         "-framework", "AppKit", "-framework", "Foundation"],
                        capture_output=True, text=True, timeout=600)
    errs = [l for l in r2.stderr.splitlines() if "error:" in l]
    if r2.returncode != 0 or errs:
        print(f"[{tag}] 编译失败:"); print("\n".join(errs[:10])); sys.exit(1)
    print(f"[{tag}] 编译 OK -> {outdir/'cli'}")
    return outdir/"cli"

def run_all(cli: pathlib.Path, home: pathlib.Path, tag: str) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    (home/"qspace-layouts").mkdir(exist_ok=True)
    (home/"qspace-layouts"/"hub-token.txt").write_text("sandbox-token-123")
    (home/"qspace-layouts"/"machine-tag.txt").write_text("Studio")
    res = {}
    for sc in SCENARIOS:
        for f in (SB/"hubhome"/"layout.json", home/"desktop.conf", home/"qspace-layouts"/"sync-state.json"):
            try: f.unlink()
            except FileNotFoundError: pass
        arch = SB/"hubhome"/"archive"
        if arch.is_dir():
            for f in arch.iterdir(): f.unlink()
        (home/"qspace-layouts"/"app.log").write_text("")
        r = subprocess.run([str(cli), "--sbtest", sc], capture_output=True, text=True,
                           env=dict(os.environ, QS_SB=str(home)))
        res[sc] = r.stdout
    (SB/"out"/f"compare-{tag}.txt").write_text(
        "\n".join(f"=== SCENARIO {sc} ===\n{res[sc]}" for sc in SCENARIOS), encoding="utf-8")
    return res

# 基线 build（生产源码，未打补丁）
base_cli = build(pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift"),
                 SB/"swift"/"cmpbase", "base")
# 修复 build（fixwork 副本，已打补丁）
fix_cli = build(WORK/"QSpaceLayoutBar"/"main.swift", SB/"swift"/"cmpfix", "fix")

base = run_all(base_cli, SB/"cmpbase_home", "base")
fix  = run_all(fix_cli,  SB/"cmpfix_home",  "fix")

def fails(txt):
    return [l for l in txt.splitlines() if "FAIL" in l or "★" in l]

print("\n================ 基线 vs 修复后 逐场景对比 ================")
print(f"{'场景':6} {'基线FAIL数':>10} {'修复后FAIL数':>12}  判定")
improved = regressed = same = 0
for sc in SCENARIOS:
    bf, ff = len(fails(base[sc])), len(fails(fix[sc]))
    if ff < bf: verdict = "★ 修复生效"; improved += 1
    elif ff > bf: verdict = "★★ 回归！"; regressed += 1
    else: verdict = "无变化"; same += 1
    print(f"{sc:6} {bf:>10} {ff:>12}  {verdict}")
    if ff > bf:
        for l in fails(fix[sc]): print(f"        新增 FAIL: {l}")
print(f"\n改善 {improved} / 回归 {regressed} / 无变化 {same}")

print("\n=== 修复后仍有 FAIL 的场景明细 ===")
for sc in SCENARIOS:
    if fails(fix[sc]):
        print(f"--- S{sc} ---")
        for l in fails(fix[sc]): print("   ", l)
