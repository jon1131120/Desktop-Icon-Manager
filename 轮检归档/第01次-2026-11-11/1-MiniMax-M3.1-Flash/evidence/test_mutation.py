#!/usr/bin/env python3
"""场景15：40 点标定回归 + 变异测试
变异测试：改 mapCell 一行 → selftest 必须变红（否则说明测试无效）"""
import pathlib, shutil, subprocess, sys, os, re

SB = "/tmp/sandbox_minimax"
SRC = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift").read_text()
WORK = pathlib.Path(f"{SB}/mut")
WORK.mkdir(parents=True, exist_ok=True)

SDK = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()

def build_and_run(code: str, tag: str):
    d = WORK / tag
    if d.exists(): shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "main.swift").write_text(code)
    r = subprocess.run(["swiftc", "-O", "-sdk", SDK, "-o", str(d / "qslb"), str(d / "main.swift"),
                        "-framework", "AppKit", "-framework", "Foundation"],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f"  [变异 {tag}] 编译失败:\n{r.stderr[-800:]}")
        return None, None
    # 沙箱化：日志路径重定向（避免写真机 ~/qspace-layouts/app.log）
    code2 = code.replace(
        'let logPath = NSString(string: "~/qspace-layouts/app.log").expandingTildeInPath',
        f'let logPath = "{SB}/mut/{tag}/app.log"')
    (d / "main.swift").write_text(code2)
    r = subprocess.run(["swiftc", "-O", "-sdk", SDK, "-o", str(d / "qslb"), str(d / "main.swift"),
                        "-framework", "AppKit", "-framework", "Foundation"],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f"  [变异 {tag}] 二次编译失败:\n{r.stderr[-500:]}")
        return None, None
    run = subprocess.run([str(d / "qslb"), "--selftest"], capture_output=True, text=True,
                         timeout=120, env=dict(os.environ, HOME=f"{SB}/mut/{tag}"))
    return run.stdout.strip(), run.returncode

print("== 场景15 40 点标定回归 + 变异测试 ==")

# ① 基线（未改）
out, rc = build_and_run(SRC, "baseline")
base_pass = (rc == 0 and "SELFTEST PASS" in (out or ""))
print(f"  {'✓' if base_pass else '✗'} 基线 selftest → out={out!r} rc={rc}")
log = pathlib.Path(f"{SB}/mut/baseline/app.log")
if log.exists():
    for line in log.read_text(errors="replace").splitlines():
        if "selftest" in line: print(f"    {line}")

# ② 变异 1：mapCell 的「固定区」判定 `idxR <= 5` 改成 `<= 4`（退回旧宽度）
mut1 = SRC.replace("let rightAnchor = idxR <= 5", "let rightAnchor = idxR <= 4")
assert mut1 != SRC, "mutation 1 not applied"
out1, rc1 = build_and_run(mut1, "mut1")
mut1_red = (rc1 != 0 and "SELFTEST FAIL" in (out1 or ""))
print(f"  {'✓' if mut1_red else '✗'} 变异1 [mapCell rightAnchor idxR<=5 → <=4] → out={out1!r} rc={rc1} {'(变红 ✓)' if mut1_red else '(未变红 ✗)'}")

# ③ 变异 2：映射区公式 `Ld + (col - Ls)` 改成 `Ld + (col - Ls) + 1`（整体偏移一格）
mut2 = SRC.replace("dc = Ld + (col - Ls)                   // 映射区：贴组件第 o 格 ↔ 贴组件第 o 格",
                   "dc = Ld + (col - Ls) + 1               // 变异：整体偏移一格")
assert mut2 != SRC, "mutation 2 not applied"
out2, rc2 = build_and_run(mut2, "mut2")
mut2_red = (rc2 != 0 and "SELFTEST FAIL" in (out2 or ""))
print(f"  {'✓' if mut2_red else '✗'} 变异2 [映射区 +1 偏移] → out={out2!r} rc={rc2} {'(变红 ✓)' if mut2_red else '(未变红 ✗)'}")

# ④ 变异 3：行平移 `row + (d.zone2.0 - s.zone2.0)` 去掉
mut3 = SRC.replace("dr = row + (d.zone2.0 - s.zone2.0)", "dr = row")
assert mut3 != SRC, "mutation 3 not applied"
out3, rc3 = build_and_run(mut3, "mut3")
mut3_red = (rc3 != 0 and "SELFTEST FAIL" in (out3 or ""))
print(f"  {'✓' if mut3_red else '✗'} 变异3 [行平移去掉] → out={out3!r} rc={rc3} {'(变红 ✓)' if mut3_red else '(未变红 ✗)'}")

# ⑤ 变异 4：clamp 上界 `min(d.cols, dc)` → `min(d.cols, dc)` 不变，改成 cols+1（越界）
mut4 = SRC.replace("dc = max(Ld + 1, min(d.cols, dc))", "dc = max(Ld + 1, min(d.cols + 1, dc))")
assert mut4 != SRC, "mutation 4 not applied"
out4, rc4 = build_and_run(mut4, "mut4")
mut4_red = (rc4 != 0 and "SELFTEST FAIL" in (out4 or ""))
print(f"  {'✓' if mut4_red else '✗'} 变异4 [clamp 越界 +1] → out={out4!r} rc={rc4} {'(变红 ✓)' if mut4_red else '(未变红 ✗)'}")

allok = base_pass and mut1_red and mut2_red and mut3_red and mut4_red
print(f"\n场景15 结论: {'PASS（基线绿 + 4 个变异全红 → 测试有效）' if allok else 'FAIL'}")
sys.exit(0 if allok else 1)
