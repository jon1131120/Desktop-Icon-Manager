#!/usr/bin/env python3
"""修复后回归：用「打补丁后的 main.swift」重建沙箱 CLI，重跑受影响的场景，
确认「修复前红 → 修复后绿」。生产文件不动（补丁只打在 /tmp/sandbox_minimax/fixwork 副本上）。"""
import pathlib, shutil, subprocess, os, sys

SB = pathlib.Path("/tmp/sandbox_minimax")
WORK = SB / "fixwork"
SRC = WORK / "QSpaceLayoutBar" / "main.swift"
DST = SB / "swift" / "sbfix"
DST.mkdir(parents=True, exist_ok=True)
MAINF = DST / "main.swift"
SBHOME = SB / "fixhome"

# ① 生成路径重定向版（复用 mksandbox.py，源指向 fixwork）
env = dict(os.environ, QS_SRC_MAIN=str(SRC), QS_OUT=str(MAINF))
r = subprocess.run([sys.executable, str(SB/"mksandbox.py")], env=env, capture_output=True, text=True)
if r.returncode != 0:
    print("mksandbox 失败:\n", r.stdout, r.stderr); sys.exit(1)
print(r.stdout.strip())

# ② 注入修复后版本的场景驱动（场景驱动本身不需要改）
# mksandbox 把 sbtest.swift 注入主文件 —— 需要让 mksandbox 用 sbtest_fixed
src_mk = (SB/"mksandbox.py").read_text()
if "sbtest_fixed" not in src_mk:
    src_mk = src_mk.replace(
        'DRIVER = pathlib.Path("/tmp/sandbox_minimax/swift/sbtest.swift").read_text()',
        'DRIVER = pathlib.Path("/tmp/sandbox_minimax/swift/sbtest_fixed.swift").read_text()')
    (SB/"mksandbox.py").write_text(src_mk)
shutil.copy2(SB/"swift/sbtest.swift", SB/"swift/sbtest_fixed.swift")
r = subprocess.run([sys.executable, str(SB/"mksandbox.py")], env=env, capture_output=True, text=True)
if r.returncode != 0:
    print("mksandbox(2) 失败:\n", r.stdout, r.stderr); sys.exit(1)

# ③ 编译
SDK = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
r = subprocess.run(["swiftc", "-O", "-sdk", SDK, "-o", str(DST/"qslb-fix"), str(MAINF),
                    "-framework", "AppKit", "-framework", "Foundation"], capture_output=True, text=True, timeout=600)
errs = [l for l in r.stderr.splitlines() if "error:" in l]
if r.returncode != 0 or errs:
    print("编译失败（error 行 %d 条）:\n" % len(errs), "\n".join(errs[:15])); sys.exit(1)
print("编译 OK（警告 %d 条不算失败）" % len([l for l in r.stderr.splitlines() if "warning:" in l]))

# ④ 沙箱 HOME（独立于生产）
SBHOME.mkdir(parents=True, exist_ok=True)
(SBHOME/"qspace-layouts").mkdir(exist_ok=True)
(SBHOME/"qspace-layouts"/"hub-token.txt").write_text("sandbox-token-123")
(SBHOME/"qspace-layouts"/"machine-tag.txt").write_text("Studio")

# ⑤ 跑受影响场景
SCENARIOS = ["8", "8b", "8c", "11", "16", "22", "17", "1", "2", "3", "4", "5", "6", "7", "9", "12", "13", "14", "15", "18", "19", "20", "21"]
print("\n===== 修复后场景实测 =====")
for sc in SCENARIOS:
    # 重置中枢 + 沙箱 home
    for f in (SB/"hubhome"/"layout.json",):
        try: f.unlink()
        except FileNotFoundError: pass
    arch = SB/"hubhome"/"archive"
    if arch.is_dir():
        for f in arch.iterdir(): f.unlink()
    for f in (SBHOME/"desktop.conf", SBHOME/"qspace-layouts"/"sync-state.json"):
        try: f.unlink()
        except FileNotFoundError: pass
    (SBHOME/"qspace-layouts"/"app.log").write_text("")
    run = subprocess.run([str(DST/"qslb-fix"), "--sbtest", sc], capture_output=True, text=True,
                         env=dict(os.environ, QS_SB=str(SBHOME)))
    print(f"--- SCENARIO {sc} ---")
    for l in run.stdout.splitlines():
        if l.startswith("SBX"): print("  ", l)
    if run.returncode != 0:
        print("  (stderr)", run.stderr[-300:])
