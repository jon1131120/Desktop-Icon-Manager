#!/usr/bin/env python3
"""轮检台架生成器（第01次轮检 · 3-deepseek-flash）

把生产 QSpaceLayoutBar/main.swift 复制成沙箱台架：
  1) 所有真机路径改为 $DS_ROOT 下（DS_ROOT 环境变量，默认 /tmp/ds_sandbox）→ 真机零触碰
  2) 所有 Int(Date().timeIntervalSince1970) 改为 nowTs()（可注入时间）
  3) QSpace 强杀/重启、通知、弹窗、微拖拽 打桩为纯写盘/日志
  4) 末尾插 --engine <script.json> 脚本解释器（驱动真实同步函数）

生产文件零改动：只读 main.swift，产物写 evidence/harness/main_harness.swift
"""
import os, re, sys

PROD = "/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_harness.swift")

src = open(PROD).read()
orig_len = len(src)

# ---------- 1. 路径重定向 ----------
pairs = [
    ('NSString(string: "~/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf").expandingTildeInPath',
     'dsRoot + "/conf/desktop.conf"'),
    ('NSString(string: "~/Library/Mobile Documents/com~apple~CloudDocs/qspace-layouts").expandingTildeInPath',
     'dsRoot + "/layouts"'),
    ('NSString(string: "~/qspace-layouts/app.log").expandingTildeInPath',
     'dsRoot + "/app.log"'),
    ('NSString(string: "~/qspace-layouts/machine-tag.txt").expandingTildeInPath',
     'dsRoot + "/machine-tag.txt"'),
    ('NSString(string: "~/qspace-layouts/hub-token.txt").expandingTildeInPath',
     'dsRoot + "/hub-token.txt"'),
    ('NSString(string: "~/qspace-layouts/sync-state.json").expandingTildeInPath',
     'dsRoot + "/sync-state.json"'),
    ('NSString(string: "~/qspace-layouts").expandingTildeInPath',
     'dsRoot'),
    ('NSString(string: "~/Desktop/").expandingTildeInPath + "/"',
     'dsRoot + "/Desktop/"'),
]
for a, b in pairs:
    assert a in src, a
    src = src.replace(a, b)

# hub 地址 → 沙箱
old_hub = '''let hubCandidates = [
    "http://192.168.113.11:18878",   // 局域网（MINIPC 有线）
    "http://192.168.191.9:18878"     // ZeroTier（异地/带出门）
]'''
assert old_hub in src
src = src.replace(old_hub, 'let hubCandidates = [dsRoot.contains("403") ? "http://127.0.0.1:18891" : "http://127.0.0.1:18890"]')

# 注入 dsRoot + nowTs（放在 import 之后）
head = "import AppKit\n"
assert src.startswith(head)
inject = head + '''
// ════════ 轮检台架注入（沙箱专用，生产源码无此段） ════════
let dsRoot: String = ProcessInfo.processInfo.environment["DS_ROOT"] ?? "/tmp/ds_sandbox"
var nowOverride: Int = 0
func nowTs() -> Int { return nowOverride > 0 ? nowOverride : Int(Date().timeIntervalSince1970) }
'''
src = src.replace(head, inject, 1)

# ---------- 2. 时间注入 ----------
n_time = src.count("Int(Date().timeIntervalSince1970)")
src = src.replace("Int(Date().timeIntervalSince1970)", "nowTs()")
# 修正注入的 nowTs 自身（上面的批量替换会把它的回退实现也改成 nowTs → 无限递归）
src = src.replace("func nowTs() -> Int { return nowOverride > 0 ? nowOverride : nowTs() }",
                  "func nowTs() -> Int { return nowOverride > 0 ? nowOverride : Int(Date().timeIntervalSince1970) }")

# ---------- 3. 打桩 ----------
def replace_func(text, signature, new_body):
    i = text.index(signature)
    j = text.index("{", i)
    depth, k, in_str, esc = 0, j, False, False
    while k < len(text):
        ch = text[k]
        if in_str:
            if esc: esc = False
            elif ch == "\\\\": esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0: break
        k += 1
    return text[:j] + "{\n" + new_body + "\n}" + text[k + 1:]

src = replace_func(src, "func atomicWriteConfAndRestartQSpace(_ conf: [String: Any]) {",
    '    writeConf(conf)\n    logLine("STUB atomicWrite: conf written (QSpace kill/restart 打桩)")')
src = replace_func(src, "func microNudge(conf: [String: Any]) {",
    '    logLine("STUB microNudge skipped")')
src = replace_func(src, "func alert(_ msg: String) {",
    '    logLine("STUB alert: \\(msg)")')
src = replace_func(src, "func notifyRaw(_ msg: String) {",
    '    logLine("STUB notify: \\(msg)")')
src = replace_func(src, "func qspaceRunning() -> Bool {",
    '    return false')

# ---------- 4. 引擎脚本解释器 ----------
engine = r'''
// ════════ 轮检台架：--engine <script.json> ════════
func heSpec(_ k: String) -> GridSpec { return SPEC_ALL[k] ?? SPEC_B }

func heWriteConf(_ step: [String: Any]) {
    let spec = heSpec(step["spec"] as? String ?? "B")
    let items = step["items"] as? [[String: Any]] ?? []
    var attrs: [[String: Any]] = []
    for it in items {
        let name = it["name"] as? String ?? "x"
        let c = it["col"] as? Int ?? 1
        let r = it["row"] as? Int ?? 1
        let x = it["x"] as? Int ?? Int(spec.x0 + Double(c - 1) * spec.colStep)
        let y = it["y"] as? Int ?? Int(spec.y0 + Double(r - 1) * spec.rowStep)
        var a: [String: Any] = ["name": name, "x": x, "y": y,
                                "screen": it["screen"] as? Int ?? 1]
        if let w = it["screenWidth"] as? Int { a["screenWidth"] = w }
        if let h = it["screenHeight"] as? Int { a["screenHeight"] = h }
        attrs.append(a)
    }
    var conf: [String: Any] = ["itemAttrs": attrs]
    if step["nolod"] as? Bool != true {
        for (k, v) in specSettings(spec) { conf[k] = v }
        conf["snapToGrid"] = true
        conf["labelPosition"] = 1
        conf["useStacks"] = false
        conf["previewIcons"] = true
    }
    if let extra = step["extra"] as? [String: Any] {
        for (k, v) in extra { conf[k] = v }
    }
    writeConf(conf)
    print("HARNESS conf written items=\(attrs.count) spec=\(spec.key) nolod=\(step["nolod"] as? Bool ?? false)")
}

func heSetState(_ step: [String: Any]) {
    var st = loadSyncState()
    if let set = step["set"] as? [String: Any] { for (k, v) in set { st[k] = v } }
    if let del = step["del"] as? [String] { for k in del { st.removeValue(forKey: k) } }
    saveSyncState(st)
    print("HARNESS state=\(st)")
}

func heSetRT(_ step: [String: Any]) {
    guard let set = step["set"] as? [String: Any] else { return }
    for (k, v) in set {
        switch k {
        case "lastLocalChangeAt": rt.lastLocalChangeAt = v as? Int ?? 0
        case "lastSeenFp": rt.lastSeenFp = v as? String ?? ""
        case "suppressUntil": rt.suppressUntil = v as? Int ?? 0
        case "protectBackoffUntil": rt.protectBackoffUntil = v as? Int ?? 0
        case "lastRemoteApplyAt": rt.lastRemoteApplyAt = v as? Int ?? 0
        case "ledgerShortSince": rt.ledgerShortSince = v as? Int ?? 0
        case "skipPullOnce": rt.skipPullOnce = v as? Bool ?? false
        case "hubFailStreak": hubFailStreak = v as? Int ?? 0
        case "hubNextRetryAt": hubNextRetryAt = v as? Int ?? 0
        case "applyingRemote": rt.applyingRemote = v as? Bool ?? false
        case "hubNamesSet":
            let arr = v as? [String] ?? []
            rt.hubNamesSet = Set(arr)
        default: print("HARNESS rt-unknown \(k)")
        }
    }
    print("HARNESS rt set \(set.keys.sorted())")
}

func heReport() {
    let conf = loadConf() ?? [:]
    let attrs = conf["itemAttrs"] as? [[String: Any]] ?? []
    var items: [[String: Any]] = []
    for a in attrs {
        items.append(["name": (a["name"] as? String ?? "").split(separator: "/").last.map(String.init) ?? "",
                      "x": a["x"] as? Int ?? -1, "y": a["y"] as? Int ?? -1,
                      "screen": a["screen"] ?? -1])
    }
    let st = loadSyncState()
    let out: [String: Any] = ["items": items,
                              "lod": conf["lod"] ?? "MISSING",
                              "hSp": conf["itemHSpacing"] ?? "MISSING",
                              "font": conf["fontSize"] ?? "MISSING",
                              "previewIcons": conf["previewIcons"] ?? "MISSING",
                              "state": st]
    if let d = try? JSONSerialization.data(withJSONObject: out, options: [.sortedKeys]),
       let s = String(data: d, encoding: .utf8) { print("HARNESS report \(s)") }
}

func heSnap() -> [String: Any]? {
    guard let conf = loadConf(), let snap = makeGridSnapshot(conf: conf) else {
        print("HARNESS snap NIL"); return nil
    }
    let items = snap["items"] as? [[String: Any]] ?? []
    print("HARNESS snap spec=\(snap["specKey"] ?? "?") items=\(items.count) fp=\(snap["layoutFingerprint"] ?? "?")")
    return snap
}

func runEngineScript(_ path: String) {
    guard let d = FileManager.default.contents(atPath: path),
          let steps = (try? JSONSerialization.jsonObject(with: d)) as? [[String: Any]] else {
        print("HARNESS engine: 脚本读不到/不合法 \(path)"); return
    }
    for (i, step) in steps.enumerated() {
        let op = step["op"] as? String ?? "?"
        print("HARNESS step[\(i)] op=\(op)")
        switch op {
        case "now": nowOverride = step["t"] as? Int ?? 0
        case "conf": heWriteConf(step)
        case "rawconf":
            let s = step["json"] as? String ?? "{}"
            try? s.write(toFile: confPath, atomically: true, encoding: .utf8)
            print("HARNESS rawconf written \(s.count) bytes")
        case "state": heSetState(step)
        case "rt": heSetRT(step)
        case "bc": broadcastIfNeeded(); print("HARNESS bc done")
        case "pull":
            if let r = fetchRemote() { applyRemoteIfNewer(r) } else { print("HARNESS pull NIL") }
        case "cycle":
            var stv = loadSyncState()
            if (stv["applyLogicVersion"] as? String) != applyLogicVersion {
                forceApplyOnce = true
                stv["applyLogicVersion"] = applyLogicVersion
                saveSyncState(stv)
                logLine("sync: 应用逻辑升级 v\(applyLogicVersion) → 首轮强制重应用（重算全部位置）")
            }
            checkSpecChange()
            _ = fixOverlapsIfNeeded()
            if let r = fetchRemote() {
                if !rt.skipPullOnce { applyRemoteIfNewer(r) } else { rt.skipPullOnce = false }
            }
            broadcastIfNeeded()
            print("HARNESS cycle done")
        case "map":
            let fk = step["from"] as? String ?? "C"
            let tk = step["to"] as? String ?? "A"
            let fs = heSpec(fk), ts = heSpec(tk)
            var res: [String: Any] = [:]
            for c in 1...fs.cols {
                for r in 1...fs.rows {
                    let (dc, dr) = mapCell(from: fs, to: ts, col: c, row: r)
                    res["\(c),\(r)"] = [dc, dr]
                }
            }
            if let d3 = try? JSONSerialization.data(withJSONObject: res, options: [.sortedKeys]),
               let s3 = String(data: d3, encoding: .utf8) {
                print("HARNESS map \(fk)->\(tk) \(s3)")
            }
        case "snap": _ = heSnap()
        case "applyfile":
            let p = step["path"] as? String ?? ""
            if let d2 = FileManager.default.contents(atPath: p),
               let snap = (try? JSONSerialization.jsonObject(with: d2)) as? [String: Any],
               let conf = loadConf(), let r = applyGridSnapshot(snap, to: conf) {
                print("HARNESS applyfile matched=\(r.matched) kept=\(r.kept) moved=\(r.moved)")
                if step["write"] as? Bool == true { writeConf(r.newConf) }
            } else { print("HARNESS applyfile NIL") }
        case "relocate":
            if let conf = loadConf(), let fixed = relocateStrayVolumes(conf) {
                writeConf(fixed)
                print("HARNESS relocate CHANGED")
            } else { print("HARNESS relocate nil") }
        case "overlap":
            print("HARNESS overlap fixed=\(fixOverlapsIfNeeded())")
        case "save": saveLayout(named: step["name"] as? String ?? "t", isAuto: step["auto"] as? Bool ?? false)
        case "autosnap":
            if let t = step["last"] as? Double { lastAutoSnapshotAt = t }
            autoSnapshotIfNeeded()
        case "prune": pruneAutoSnapshots()
        case "restore":
            restoreLayout(at: URL(fileURLWithPath: step["path"] as? String ?? ""), restart: step["restart"] as? Bool ?? false)
        case "ls":
            let fs = (try? FileManager.default.contentsOfDirectory(atPath: step["path"] as? String ?? layoutDir)) ?? []
            print("HARNESS ls \(fs.sorted())")
        case "report": heReport()
        case "print": print("HARNESS note: \(step["msg"] as? String ?? "")")
        case "rm":
            try? FileManager.default.removeItem(atPath: step["path"] as? String ?? "")
        default: print("HARNESS unknown op \(op)")
        }
    }
}
'''
anchor = "// 单实例保护：已在运行则直接退出"
assert anchor in src
engine_mode = '''if let ei = CommandLine.arguments.firstIndex(of: "--engine"), ei + 1 < CommandLine.arguments.count {
    runEngineScript(CommandLine.arguments[ei + 1])
    exit(0)
}
if CommandLine.arguments.contains("--healcheck") {
    guard let conf = loadConf() else { print("HEAL NIL"); exit(1) }
    print("HEAL lod=\\(conf["lod"] ?? "MISSING") hSp=\\(conf["itemHSpacing"] ?? "MISSING") font=\\(conf["fontSize"] ?? "MISSING")")
    exit(0)
}

'''
src = src.replace(anchor, engine + "\n" + engine_mode + anchor, 1)

open(OUT, "w").write(src)
print(f"生成 {OUT}: {orig_len} → {len(src)} 字节；时间注入点 {n_time} 处")
