#!/usr/bin/env python3
"""生成并验证修复补丁（只写 evidence/，不改生产文件）。

P1 中段区死循环 / P2 VIP 尾随空格归位失效 / P2 hubServerTime 裸路径 / P3 syncAt 类型 500
"""
import os, re, shutil, subprocess, sys, json

PROD = "/Users/jon/Projects/Desktop-Icon-Manager"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixes")
os.makedirs(OUT, exist_ok=True)
WORK = "/tmp/ds_sandbox/patchwork"


def make(rel, edits, name):
    os.makedirs(WORK, exist_ok=True)
    src = open(os.path.join(PROD, rel)).read()
    new = src
    for a, b in edits:
        assert a in new, f"锚点未命中 {name}: {a[:60]}"
        new = new.replace(a, b, 1)
    dst_rel = os.path.join(WORK, os.path.basename(rel))
    open(dst_rel, "w").write(new)
    base_rel = dst_rel + ".orig"
    open(base_rel, "w").write(src)
    d = subprocess.run(["diff", "-u", base_rel, dst_rel], capture_output=True, text=True)
    # 把临时路径改回 a/ b/ 形式
    patch = d.stdout.replace(base_rel, f"a/{rel}").replace(dst_rel, f"b/{rel}")
    p = os.path.join(OUT, name)
    open(p, "w").write(patch)
    print(f"写出 {name}（{len(patch.splitlines())} 行）")
    return new, p


# P1：中段区全满 → 有界搜索，绝不死循环
p1_old = '''    var cursor = (spec.midZone.0, 1)
    for mv in evict {
        while occupied.contains("\\(cursor.0),\\(cursor.1)") {
            cursor.1 += 1
            if cursor.1 > spec.rows { cursor.1 = 1; cursor.0 += 1 }
            if cursor.0 > spec.midZone.1 { cursor.0 = spec.midZone.0 }
        }'''
p1_new = '''    var cursor = (spec.midZone.0, 1)
    // ⭐ 有界搜索（第01次轮检 deepseek R20 实测：中段区被占满时旧实现无限循环，
    //   主线程/同步线程直接卡死）。容量 = 列数 × 行数，走满一圈还没空格就放弃搬移。
    let midCapacity = (spec.midZone.1 - spec.midZone.0 + 1) * spec.rows
    for mv in evict {
        var probes = 0
        while occupied.contains("\\(cursor.0),\\(cursor.1)") && probes < midCapacity {
            cursor.1 += 1
            if cursor.1 > spec.rows { cursor.1 = 1; cursor.0 += 1 }
            if cursor.0 > spec.midZone.1 { cursor.0 = spec.midZone.0 }
            probes += 1
        }
        if probes >= midCapacity {
            logLine("volumes: 中段区已满，\\((mv.name as NSString).lastPathComponent) 暂不搬移（避免死循环）")
            continue
        }'''
sw1, _ = make("QSpaceLayoutBar/main.swift", [(p1_old, p1_new)], "fix-P1-midzone-deadlock.patch")

# P2：VIP 第二遍归位改用 vipSlotRow（trim + 前缀统一判据）
p2_old = '''            let base = (n as NSString).lastPathComponent
            guard names.contains(n) || names.contains(base) else { continue }'''
p2_new = '''            let base = (n as NSString).lastPathComponent
            // ⭐ 归位判据与第一遍同源（第01次轮检 deepseek R20 实测：旧写法精确匹配，
            //   /Volumes/Jon113␣␣（QSpace 卷宗名常带尾随空格）永远匹配不上 → VIP 永不归位）
            guard vipSlotRow(n) == row else { continue }'''
sw2, _ = make("QSpaceLayoutBar/main.swift", [(p2_old, p2_new)], "fix-P2-vip-trim-relocate.patch")

# P2：hubServerTime 裸路径 → 走 hubGet（带 LAN→ZT 回退与令牌）
p3_old = '''func hubServerTime() -> Int? {
    guard let o = httpGetJSON("/status"), let t = o["serverTime"] as? Int else { return nil }
    return t
}'''
p3_new = '''func hubServerTime() -> Int? {
    // ⭐ 必须走 hubGet（拼 base + LAN→ZT 回退 + 令牌头）。旧实现 httpGetJSON("/status")
    //   是相对 URL → URLRequest 直接失败 → 恒返回 nil → 钟超前机器的自愈分支永不可达
    //   （第01次轮检 deepseek R18 实测：日志 GET /status -> 0，改动被永久吞）。
    guard let o = hubGet("/status"), let t = o["serverTime"] as? Int else { return nil }
    return t
}'''
sw3, _ = make("QSpaceLayoutBar/main.swift", [(p3_old, p3_new)], "fix-P2-hubservertime.patch")

# P3：syncAt 非法类型 → 400（现在 int("abc") 抛 ValueError 被兜底成 500）
h_old = '''        at = int(data.get('syncAt', 0) or 0)'''
h_new = '''        try:
            at = int(data.get('syncAt', 0) or 0)
        except (TypeError, ValueError):
            # 第01次轮检 deepseek R17 实测：syncAt="abc" 会抛 ValueError → 兜底 500。
            # 客户端只发整数，脏数据应显式 400（与其余字段校验口径一致）。
            logger.warning("POST /layout rejected: bad syncAt %r", data.get('syncAt'))
            self._json(400, {'error': 'syncAt must be an integer'})
            return'''
_, _ = make("hub/hub-server.py", [(h_old, h_new)], "fix-P3-syncat-type.patch")

# ---- 验证：P1+P2+P3 组合后编译 + 跑相关场景 ----
os.makedirs(WORK, exist_ok=True)
combined = sw1.replace(p1_old, p1_new)  # 已含 P1+P2+P3（make 顺序替换在同一份 src 上）
# make() 每次都从生产读，所以组合版要基于最后一次（P3）再补 P1/P2
combined = sw3
for a, b in ((p1_old, p1_new), (p2_old, p2_new)):
    assert a in combined
    combined = combined.replace(a, b, 1)
cp = os.path.join(WORK, "combined_main.swift")
open(cp, "w").write(combined)
exe = os.path.join(WORK, "combined")
r = subprocess.run(["swiftc", "-O", "-o", exe, cp], capture_output=True, text=True)
print("编译组合补丁:", "OK" if r.returncode == 0 else r.stderr[-500:])
if r.returncode == 0:
    t = subprocess.run([exe, "--selftest"], capture_output=True, text=True,
                       env={**os.environ, "DS_ROOT": "/tmp/ds_sandbox/runs/patchcheck"})
    print("补丁后 selftest:", t.stdout.strip())

hb = os.path.join(WORK, "hub_fixed.py")
open(hb, "w").write(open(os.path.join(PROD, "hub/hub-server.py")).read()
                    .replace("PORT = 18878", "PORT = 18892")
                    .replace(h_old, h_new))
os.makedirs("/tmp/ds_sandbox/datafix", exist_ok=True)
open("/tmp/ds_sandbox/datafix/hub-token.txt", "w").write("sandbox-token-123")
print("hub 补丁语法:", "OK" if subprocess.run(["python3", "-m", "py_compile", hb]).returncode == 0 else "FAIL")
