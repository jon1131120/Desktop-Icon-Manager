#!/usr/bin/env python3
"""
把 main.swift 复制成沙箱版：
  1) 所有路径重定向到 /tmp/sandbox_minimax/home（绝不碰真机 ~/Library/... ~/qspace-layouts）
  2) hub 指向沙箱 127.0.0.1:18890（绝不碰生产 18878）
  3) atomicWriteConfAndRestartQSpace 打桩 → 绝不 kill/open 真机 QSpace
  4) 注入 --sbtest 场景驱动（插在单实例保护之前，保证不启动菜单栏 App）
"""
import pathlib, re, sys

SRC = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift").read_text()
OUT = pathlib.Path("/tmp/sandbox_minimax/swift/sb/main.swift")
OUT.parent.mkdir(parents=True, exist_ok=True)

reps = [
    # ① 路径重定向
    ('let confPath = NSString(string: "~/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf").expandingTildeInPath',
     'let SB = ProcessInfo.processInfo.environment["QS_SB"] ?? "/tmp/sandbox_minimax/home"\n'
     'let confPath = SB + "/desktop.conf"'),
    ('let layoutDir = NSString(string: "~/Library/Mobile Documents/com~apple~CloudDocs/qspace-layouts").expandingTildeInPath',
     'let layoutDir = SB + "/qspace-layouts"'),
    ('let logPath = NSString(string: "~/qspace-layouts/app.log").expandingTildeInPath',
     'let logPath = SB + "/qspace-layouts/app.log"'),
    ('let tagPath = NSString(string: "~/qspace-layouts/machine-tag.txt").expandingTildeInPath',
     'let tagPath = SB + "/qspace-layouts/machine-tag.txt"'),
    ('let hubTokenPath = NSString(string: "~/qspace-layouts/hub-token.txt").expandingTildeInPath',
     'let hubTokenPath = SB + "/qspace-layouts/hub-token.txt"'),
    ('let syncStatePath = NSString(string: "~/qspace-layouts/sync-state.json").expandingTildeInPath',
     'let syncStatePath = SB + "/qspace-layouts/sync-state.json"'),
    # ② hub 指向沙箱
    ('''let hubCandidates = [
    "http://192.168.113.11:18878",   // 局域网（MINIPC 有线）
    "http://192.168.191.9:18878"     // ZeroTier（异地/带出门）
]''',
     '''let hubCandidates = [
    "http://127.0.0.1:18890"         // ⛔ 沙箱中枢（绝不指向生产 :18878）
]'''),
    # ③ atomicWrite 打桩：绝不触碰真机 QSpace
    ('''func atomicWriteConfAndRestartQSpace(_ conf: [String: Any]) {
    let config = NSWorkspace.OpenConfiguration()''',
     '''func atomicWriteConfAndRestartQSpace(_ conf: [String: Any]) {
    // 🛡️ 沙箱打桩：只写沙箱 conf，绝不 forceTerminate / open 真机 QSpace
    writeConf(conf)
    logLine("SBX: atomicWrite stubbed (no real QSpace touched)")
    return
    let config = NSWorkspace.OpenConfiguration()'''),
]
for a, b in reps:
    if a not in SRC:
        print("MISS:", a[:80]); sys.exit(1)
    SRC = SRC.replace(a, b, 1)

DRIVER = pathlib.Path("/tmp/sandbox_minimax/swift/sbtest.swift").read_text()
ANCHOR = '''// 自检模式：QSpaceLayoutBar --selftest → 只跑映射自检后退出（不建菜单、不碰 QSpace）'''
assert ANCHOR in SRC
SRC = SRC.replace(ANCHOR, DRIVER + "\n\n" + ANCHOR, 1)

OUT.write_text(SRC)
print("sandbox main.swift written:", OUT, len(SRC.splitlines()), "lines")
