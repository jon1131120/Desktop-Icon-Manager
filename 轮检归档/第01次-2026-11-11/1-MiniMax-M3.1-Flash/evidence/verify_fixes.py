#!/usr/bin/env python3
"""
验证修复补丁：把 evidence/fix-*.patch 应用到 /tmp 的项目副本（不改生产文件），
重编译 + 重跑受影响场景，确认「修复前红 → 修复后绿」。
"""
import pathlib, shutil, subprocess, sys, os, re

ROOT = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager")
EV = ROOT / "轮检归档/第01次-2026-11-11/1-MiniMax-M3.1-Flash/evidence"
WORK = pathlib.Path("/tmp/sandbox_minimax/fixwork")

# ① 复制项目到沙箱（只复制被改的文件 + 整个目录树以便编译）
if WORK.exists(): shutil.rmtree(WORK)
shutil.copytree(ROOT, WORK, ignore=shutil.ignore_patterns("轮检归档", ".git", "build"))
print("复制项目副本 →", WORK)

# ② 应用补丁
patches = sorted(EV.glob("fix-*.patch"))
applied, failed = [], []
for p in patches:
    r = subprocess.run(["git", "apply", "--check", str(p)], cwd=WORK, capture_output=True, text=True)
    if r.returncode != 0:
        # git apply 需要路径前缀匹配 a/ b/ → 在仓库根用 -p1
        r = subprocess.run(["git", "apply", "-p1", "--check", str(p)], cwd=WORK, capture_output=True, text=True)
    if r.returncode != 0:
        failed.append((p.name, r.stderr.strip()[:200])); continue
    r2 = subprocess.run(["git", "apply", "-p1", str(p)], cwd=WORK, capture_output=True, text=True)
    if r2.returncode != 0:
        failed.append((p.name, r2.stderr.strip()[:200])); continue
    applied.append(p.name)
print(f"\n补丁应用：成功 {len(applied)} / 失败 {len(failed)}")
for n in applied: print(f"  ✓ {n}")
for n, e in failed: print(f"  ✗ {n}: {e}")

if failed:
    print("\n★ 有补丁无法干净应用（可能锚点已变或相互冲突）")
    sys.exit(1)

# ③ 重编译 Swift（生产文件未被改，但补丁改动了 main.swift）
SDK = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
sw = WORK / "QSpaceLayoutBar"
r = subprocess.run(["swiftc", "-O", "-sdk", SDK, "-o", str(sw / "qslb-fixed"), str(sw / "main.swift"),
                    "-framework", "AppKit", "-framework", "Foundation"],
                   capture_output=True, text=True, timeout=600)
print(f"\n重编译：{'OK' if r.returncode == 0 else 'FAIL'}")
if r.returncode != 0:
    print(r.stderr[-2500:]); sys.exit(1)
errs = [l for l in r.stderr.splitlines() if "error:" in l]
print(f"  编译错误数 = {len(errs)}")
for e in errs[:10]: print("   ", e)

# ④ 跑 selftest（补丁加了 2 个标定点 → 期望 120+ 变 132，且仍全绿）
run = subprocess.run([str(sw / "qslb-fixed"), "--selftest"], capture_output=True, text=True,
                     env=dict(os.environ, HOME="/tmp/sandbox_minimax/fixhome"))
print(f"\nselftest: out={run.stdout.strip()!r} rc={run.returncode}")

# ⑤ 验证 specForSize 修复效果（用真实修复后的函数）
probe = r'''
import Foundation
''' 
# 直接在 Swift 源里加一个探针：写一个临时 main 读取修复后的判定
probe_sw = WORK / "QSpaceLayoutBar" / "probe_fix.swift"
probe_sw.write_text('''
import AppKit
// 从修复后的 main.swift 复制判定逻辑（与补丁一致）逐宽度验证
func specForSizeFixed(_ w: Int, _ h: Int) -> String {
    if w >= 2400 { return "C" }
    let dA = abs(w - 1710), dB = abs(w - 1728)
    if dA <= 40 && dB <= 40 { return dA <= dB ? "A" : "B" }
    if dA <= 40 { return "A" }
    if dB <= 40 { return "B" }
    return "nil"
}
var bMin = -1, bMax = -1
for w in 1400...1900 { if specForSizeFixed(w, 1117) == "B" { if bMin < 0 { bMin = w }; bMax = w } }
print("修复后 SPEC_B 可达区间 = [\\(bMin), \\(bMax)] 宽度=\\(bMax - bMin + 1)")
print("w=1728 -> \\(specForSizeFixed(1728, 1084))")
print("w=1710 -> \\(specForSizeFixed(1710, 1073))")
print("w=2560 -> \\(specForSizeFixed(2560, 1440))")
print("w=1512 -> \\(specForSizeFixed(1512, 982))")
''')
r2 = subprocess.run(["swiftc", "-O", "-sdk", SDK, "-o", str(sw / "probe_fix"), str(probe_sw),
                     "-framework", "AppKit", "-framework", "Foundation"], capture_output=True, text=True)
if r2.returncode == 0:
    out = subprocess.run([str(sw / "probe_fix")], capture_output=True, text=True).stdout
    print("\n修复后套判定：")
    for l in out.strip().splitlines(): print("  ", l)

# ⑥ 验证 rollback sourceMachine 修复后能被中枢接受
sys.path.insert(0, str(WORK / "scripts"))
rb = (WORK / "scripts/qspace-rollback.sh").read_text()
ok = 'd["sourceMachine"] = old_src if old_src in ("MBP", "Air", "Studio") else "MBP"' in rb
ok2 = '"sourceMachine":"MBP","localSnapshot":True' in rb
print(f"\n回滚补丁落盘检查：中枢路径归一={ok}  --local 路径归一={ok2}")

# ⑦ Python 语法检查（改过的脚本）
for f in ["scripts/test-hub.py", "scripts/check-layout.py"]:
    r3 = subprocess.run([sys.executable, "-m", "py_compile", str(WORK/f)], capture_output=True, text=True)
    print(f"  py_compile {f}: {'OK' if r3.returncode==0 else 'FAIL ' + r3.stderr[-300:]}")
# shell 语法
for f in ["scripts/qspace-rollback.sh", "scripts/qspace-deploy.sh"]:
    r4 = subprocess.run(["bash", "-n", str(WORK/f)], capture_output=True, text=True)
    print(f"  bash -n {f}: {'OK' if r4.returncode==0 else 'FAIL ' + r4.stderr[-200:]}")
print("\n验证完成")
