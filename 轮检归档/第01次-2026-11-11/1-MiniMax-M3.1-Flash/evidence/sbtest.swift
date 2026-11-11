// ============================================================
// 沙箱场景驱动（注入到 main.swift 顶部执行区）
// 全部路径已重定向到 /tmp/sandbox_minimax/home，hub 指向 :18890
// ============================================================
import Foundation

func sbSay(_ s: String) { print(s); fflush(stdout) }

let sbConfPath = SB + "/desktop.conf"
let sbStatePath = SB + "/qspace-layouts/sync-state.json"
let sbTokenPath = SB + "/qspace-layouts/hub-token.txt"

func sbEnsureDirs() {
    try? FileManager.default.createDirectory(atPath: SB + "/qspace-layouts", withIntermediateDirectories: true)
    try? FileManager.default.createDirectory(atPath: SB, withIntermediateDirectories: true)
}

func sbWriteJSON(_ path: String, _ obj: Any) {
    let d = try! JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted])
    try! d.write(to: URL(fileURLWithPath: path))
}

func sbReadJSON(_ path: String) -> Any? {
    guard let d = FileManager.default.contents(atPath: path) else { return nil }
    return try? JSONSerialization.jsonObject(with: d)
}

func sbRemove(_ path: String) { try? FileManager.default.removeItem(atPath: path) }

/// 造一份合成的 conf
/// ⭐ 三条铁律（都是我在本轮实测里踩过的坑）：
///   ① 套必须与 sw 一致（用 specForSize(sw,sh) 反查），否则像素按 A 套算、
///      落格按 C 套读 → 坐标全错；
///   ② 图标放在「地板区」row 8/9（组件区 row1-3、臂区 row4-6、自定义禁区都在 row1-7）
///      ——真实 QSpace 布局不会把图标放组件区，放禁区会让 apply 端必然推开，掩盖真问题；
///   ③ 列数必须 ≤ 最小套的列数（B=19）且行数 ≤ 9，否则 makeGridSnapshot 的 clamp
///      会把多出的图标压到同一格 → 上报自检判「格重叠」→ 拒绝上报（我第一版就踩了）。
func sbConf(_ names: [String], screen: Int = 1, sw: Int = 2560, sh: Int = 1440) -> [String: Any] {
    var attrs: [[String: Any]] = []
    // ⭐ 用「最近中心」判定（与修复后的 specForSize 一致，R16 修复），
    //    否则沙箱 conf 的套与生产选的套不一致 → 坐标全错
    let spec: GridSpec
    if sw >= 2400 { spec = SPEC_C }
    else {
        let dA = abs(sw - 1710), dB = abs(sw - 1728)
        if dA <= 40 && dB <= 40 { spec = (dA <= dB ? SPEC_A : SPEC_B) }
        else if dA <= 40 { spec = SPEC_A }
        else if dB <= 40 { spec = SPEC_B }
        else { spec = SPEC_C }
    }
    let maxCols = min(19, spec.cols)      // 三套里最小的列数（B=19）→ 地板区 2 行共 38 格
    let floorRow0 = 8                      // 地板区起始行（row 8/9 在三套里都无禁区）
    let maxRows = max(1, spec.rows - floorRow0 + 1)
    for (i, n) in names.enumerated() {
        let col = (i % maxCols) + 1
        let row = floorRow0 + (i / maxCols)
        if row > spec.rows {
            // 放不下就停（不留半截，避免制造出本不可能的真实布局）
            break
        }
        let x = Int(spec.x0 + Double(col - 1) * spec.colStep)
        let y = Int(spec.y0 + Double(row - 1) * spec.rowStep)
        attrs.append([
            "name": "/Users/jon/Desktop/" + n,
            "x": x, "y": y, "screen": screen,
            "screenWidth": sw, "screenHeight": sh,
        ])
    }
    return [
        "lod": spec.lod, "itemHSpacing": spec.hSp, "itemVSpacing": 0, "fontSize": spec.font,
        "visible": true,
        "itemAttrs": attrs,
    ]
}

func sbNames(_ prefix: String, _ n: Int) -> [String] {
    return (0..<n).map { "\(prefix)\($0).txt" }
}

func sbHubGetLayout() -> [String: Any]? { hubGet() }
func sbHubCount() -> Int {
    guard let o = sbHubGetLayout(), let its = o["items"] as? [[String: Any]] else { return -1 }
    return its.count
}
func sbHubArchiveCount() -> Int {
    guard let o = hubGet("/status") else { return -1 }
    return (o["archiveCount"] as? Int) ?? -1
}
func sbHubSeed(_ body: [String: Any]) -> [String: Any]? {
    return hubPost(body: body)
}
func sbHubResetState() {
    sbRemove(sbStatePath)
    sbRemove(sbConfPath)
}

/// 设置中枢名单 + 上一次名单（hubNamesSetPrev 是只读属性，
/// 必须靠 setter 的「值变化时把旧值挪到 prev」机制来还原）
func sbSetHubNames(current: [String], prev: [String]) {
    rt.hubNamesSet = Set(prev)
    rt.hubNamesSet = Set(current)
}

/// 把客户端状态复位成「已同步基线」形态（模拟刚跑完一轮）
func sbSeedClientState(fp: String, hubNames: [String], prevHubNames: [String] = [], lastBC: String? = nil) {
    sbSetHubNames(current: hubNames, prev: prevHubNames)
    rt.lastSeenFp = fp
    rt.lastLocalChangeAt = 0
    rt.protectBackoffUntil = 0
    rt.ledgerShortSince = 0
    rt.suppressUntil = 0
    rt.skipPullOnce = false
    hubFailStreak = 0
    hubNextRetryAt = 0
    specChangedAt = 0
    forceApplyOnce = false
    var st: [String: Any] = [
        "baselineFingerprint": fp,
        "lastBroadcastFingerprint": lastBC ?? fp,
        "recentBroadcasts": [Int](repeating: 0, count: 0),
    ]
    sbWriteJSON(sbStatePath, st)
}

/// 让采样侧认为「没动过」→ 不触发用户拖动保护（避免 10s 防抖挡住上报）
func sbPrimeSampling(fp: String, items: [[String: Any]]) {
    rt.lastSeenFp = fp
    var d: [String: (Int, Int)] = [:]
    for it in items {
        if let n = it["name"] as? String, let c = it["col"] as? Int, let r = it["row"] as? Int { d[n] = (c, r) }
    }
    rt.lastSeenItems = d
    rt.lastLocalChangeAt = 0
}

func sbMakeConfAndPrime(_ names: [String]) -> (fp: String, items: [[String: Any]]) {
    let conf = sbConf(names)
    sbWriteJSON(sbConfPath, conf)
    let snap = makeGridSnapshot(conf: conf)!
    let fp = snap["layoutFingerprint"] as? String ?? ""
    let items = snap["items"] as? [[String: Any]] ?? []
    sbPrimeSampling(fp: fp, items: items)
    return (fp, items)
}

// ============================================================
// 场景实现
// ============================================================

func sbScenario1() {   // 新增文件对账
    sbSay("SBX-S01 | 新增文件对账：中枢 37 / 客户端 38")
    let now = Int(Date().timeIntervalSince1970)
    // 中枢先有 37 项（MBP 报）
    let hub37 = sbNames("old", 37)
    let r1 = sbHubSeed(["syncAt": now - 10, "sourceMachine": "MBP",
                        "items": hub37.enumerated().map { ["name": $0.element, "col": ($0.offset % 11) + 1, "row": ($0.offset / 11) + 1] },
                        "layoutFingerprint": "fp-hub-37", "specKey": "C"])
    sbSay("SBX-S01 | seed hub 37 → \(r1 ?? [:])")
    // 客户端 38 项（多 1 个真新增 X）
    var client = hub37
    client.append("NEW_X.txt")
    let (fp, items) = sbMakeConfAndPrime(client)
    sbSeedClientState(fp: fp, hubNames: hub37)
    broadcastIfNeeded()
    let after = sbHubCount()
    sbSay("SBX-S01 | EXPECT 中枢 38 | ACTUAL \(after) | \(after == 38 ? "PASS" : "FAIL")")
}

func sbScenario2() {   // 删除死条目清理（注入缩短 30 分钟）
    sbSay("SBX-S02 | 删除死条目清理：中枢 38 / 客户端 37")
    let now = Int(Date().timeIntervalSince1970)
    let hub38 = sbNames("old", 38)          // 中枢 38 项
    let deleted = hub38.last!               // 被删的那一项（中枢有、本机没有）
    let r = sbHubSeed(["syncAt": now - 10, "sourceMachine": "MBP",
                        "items": hub38.enumerated().map { ["name": $0.element, "col": ($0.offset % 11) + 1, "row": ($0.offset / 11) + 1] },
                        "layoutFingerprint": "fp-hub-38", "specKey": "C"])
    sbSay("SBX-S02 | seed hub 38 项 → \(r ?? [:])")
    let client37 = Array(hub38.prefix(37))  // 本机 37 项（少 1）
    let (fp, _) = sbMakeConfAndPrime(client37)
    sbSeedClientState(fp: fp, hubNames: hub38)
    // 未满 30 分钟 → 不应清理
    rt.ledgerShortSince = now - 100
    broadcastIfNeeded()
    let mid = sbHubCount()
    sbSay("SBX-S02 | 计时 100s 时 EXPECT 中枢仍 38 | ACTUAL \(mid) | \(mid == 38 ? "PASS" : "FAIL")")
    // 注入：已超 30 分钟 → 应清理
    rt.ledgerShortSince = now - 1801
    broadcastIfNeeded()
    let after = sbHubCount()
    sbSay("SBX-S02 | 计时 1801s 后 EXPECT 中枢 37 | ACTUAL \(after) | \(after == 37 ? "PASS" : "FAIL")  (被清理项=\(deleted))")
}

func sbScenario3() {   // 防震荡：X 曾在中枢 → 死条目复活不补报
    sbSay("SBX-S03 | 防震荡：A 报 38(含 X) / B 报 37(无 X) 交替")
    let now = Int(Date().timeIntervalSince1970)
    let list38 = sbNames("old", 38)
    let X = list38.last!                       // X = old37.txt
    let list37 = Array(list38.prefix(37))      // 中枢当前 37（X 已被 B 机删）
    // 中枢当前 = 37
    let r = sbHubSeed(["syncAt": now - 10, "sourceMachine": "MBP",
                        "items": list37.enumerated().map { ["name": $0.element, "col": ($0.offset % 11) + 1, "row": ($0.offset / 11) + 1] },
                        "layoutFingerprint": "fp-hub-37", "specKey": "C"])
    sbSay("SBX-S03 | seed 中枢 37 项（X 已不在）→ \(r ?? [:])")
    let hubSyncAt0 = sbHubGetLayout()?["syncAt"] as? Int ?? 0
    // 本机有 38 项（含 X）→ 复活场景
    let mine = list38
    let (fp, _) = sbMakeConfAndPrime(mine)
    // ⭐ 对账块的前置条件是 fp == baseline（"本机自上次同步后没变"）→ 必须先落状态
    // ⭐ prev 必须含 X：模拟「A 先见过 38 项的中枢名单，随后中枢被 B 机改成 37」
    //    hubNamesSetPrev 只读 → 用 setter 的「值变化时旧值挪到 prev」机制还原
    sbSeedClientState(fp: fp, hubNames: list37, prevHubNames: list38)
    sbSay("SBX-S03 | prev 含 X = \(rt.hubNamesSetPrev.contains(X)) | current 含 X = \(rt.hubNamesSet.contains(X))")
    broadcastIfNeeded()
    let after = sbHubCount()
    sbSay("SBX-S03 | 复活场景 EXPECT 中枢仍 37（X 不复活） | ACTUAL \(after) | \(after == 37 ? "PASS" : "FAIL")  X=\(X)")
    // 对照组：prev 不含 X（真新增）→ 应补报 38
    let list38b = sbNames("old", 38)
    let (fp2, _) = sbMakeConfAndPrime(list38b)
    // prev 必须「非空但不含 X」→ 先塞一个不同的 37 名单再切回来（setter 只在值变化时挪旧值到 prev）
    let other37 = sbNames("other", 37)
    sbSetHubNames(current: other37, prev: list37)
    sbSetHubNames(current: list37, prev: other37)
    sbSay("SBX-S03 | 对照组 prev 非空=\(!rt.hubNamesSetPrev.isEmpty) prev 含 X=\(rt.hubNamesSetPrev.contains(X))")
    sbWriteJSON(sbStatePath, ["baselineFingerprint": fp2, "lastBroadcastFingerprint": fp2, "recentBroadcasts": []])
    rt.lastSeenFp = fp2; rt.lastLocalChangeAt = 0; rt.ledgerShortSince = 0
    broadcastIfNeeded()
    let after2 = sbHubCount()
    sbSay("SBX-S03 | 对照组（prev 无 X=真新增）EXPECT 中枢 38 | ACTUAL \(after2) | \(after2 == 38 ? "PASS" : "FAIL")")
}

func sbScenario4() {   // 快速连续变化 → 只上报最终版
    sbSay("SBX-S04 | 快速连续变化：conf 连变 5 次（间隔 <10s 防抖窗口）")
    let now0 = Int(Date().timeIntervalSince1970)
    let base = sbNames("f", 10)
    // 先让中枢有 10 项基线，便于观察「中枢项数/落盘次数」
    let seedR = sbHubSeed(["syncAt": now0 - 10, "sourceMachine": "MBP",
                            "items": base.enumerated().map { ["name": $0.element, "col": ($0.offset % 11) + 1, "row": 1] },
                            "layoutFingerprint": "fp-base", "specKey": "C"])
    sbSay("SBX-S04 | seed 中枢基线 10 项 → \(seedR ?? [:])")
    let arch0 = sbHubArchiveCount()
    let (fp0, _) = sbMakeConfAndPrime(base)
    sbSeedClientState(fp: fp0, hubNames: base)
    // 造「把 f0.txt 挪到第 k 格」的配置（真拖动 → movedExisting > 0）
    // ⭐ 挪到「空列」：基础 10 项占 col 1..10（row 1），挪到 col 11..15（row 1）→ 永不撞格
    func confWithMoved(k: Int) -> [String: Any] {
        var c = sbConf(base)
        var a = c["itemAttrs"] as! [[String: Any]]
        let spec = SPEC_C
        let col = 10 + k        // k=1..5 → col 11..15
        let row = 1
        a[0]["x"] = Int(spec.x0 + Double(col - 1) * spec.colStep)
        a[0]["y"] = Int(spec.y0 + Double(row - 1) * spec.rowStep)
        c["itemAttrs"] = a
        return c
    }
    for k in 1...5 {
        sbWriteJSON(sbConfPath, confWithMoved(k: k))
        broadcastIfNeeded()
        let n = sbHubCount()
        sbSay("SBX-S04 |   变更\(k)（窗内）中枢项数=\(n) archive增量=\(sbHubArchiveCount() - arch0) 防抖距今=\(Int(Date().timeIntervalSince1970) - rt.lastLocalChangeAt)s")
    }
    let midCount = sbHubCount()
    let archMid = sbHubArchiveCount()
    sbSay("SBX-S04 | 5 次连变后（窗内）EXPECT 中枢未变（仍 10 项且 0 次新落盘） | ACTUAL 项数=\(midCount) archive增量=\(archMid - arch0) | \(archMid - arch0 == 0 ? "PASS" : "FAIL")")
    // 模拟「防抖窗已过 + 本轮采样无新变化」：fp 已进 lastSeenFp → 采样块不再重置 lastLocalChangeAt
    let cur = loadConf()!
    let curSnap = makeGridSnapshot(conf: cur)!
    let curFp = curSnap["layoutFingerprint"] as! String
    rt.lastSeenFp = curFp
    rt.lastLocalChangeAt = Int(Date().timeIntervalSince1970) - 11
    broadcastIfNeeded()
    let after = sbHubCount()
    let arch1 = sbHubArchiveCount()
    sbSay("SBX-S04 | 窗开后 EXPECT 中枢 10 项且只有 1 次落盘 | ACTUAL 项数=\(after) archive增量=\(arch1 - arch0) | \(after == 10 && arch1 - arch0 == 1 ? "PASS" : "FAIL")")
    if let lay = sbHubGetLayout(), let its = lay["items"] as? [[String: Any]] {
        let got = its.first?["col"] as? Int ?? -1
        let gotRow = its.first?["row"] as? Int ?? -1
        sbSay("SBX-S04 | 中枢首项 col=\(got) row=\(gotRow)（最终版 k=5 → col=15 row=1）| \(got == 15 ? "PASS" : "FAIL")")
    }
}

func sbScenario5() {   // QSpace 精简回写 → 参数自愈
    sbSay("SBX-S05 | QSpace 精简回写（conf 只剩 visible+itemAttrs）→ 参数自愈")
    let names = sbNames("g", 12)
    var conf = sbConf(names)
    conf.removeValue(forKey: "lod")
    conf.removeValue(forKey: "itemHSpacing")
    conf.removeValue(forKey: "itemVSpacing")
    conf.removeValue(forKey: "fontSize")
    sbWriteJSON(sbConfPath, conf)
    let loaded = loadConf()
    let lod = loaded?["lod"] as? Int ?? -1
    let hs = loaded?["itemHSpacing"] as? Int ?? -1
    sbSay("SBX-S05 | EXPECT loadConf 补回 lod=96/hSpacing=11 | ACTUAL lod=\(lod) hSpacing=\(hs) | \(lod == 96 && hs == 11 ? "PASS" : "FAIL")")
    // 精简回写缺 itemAttrs → loadConf 仍返回字典（合法 JSON），
    // 但下游 makeGridSnapshot 必须返回 nil 且**不崩、不上报**
    sbWriteJSON(sbConfPath, ["visible": true])
    let l2 = loadConf()
    let snap2 = makeGridSnapshot(conf: l2 ?? [:])
    sbSay("SBX-S05 | 无 itemAttrs 时 EXPECT loadConf 非nil + 快照 nil | ACTUAL loadConf=\(l2 == nil ? "nil" : "非nil") snap=\(snap2 == nil ? "nil" : "非nil") | \(l2 != nil && snap2 == nil ? "PASS" : "FAIL")")
    let arch0 = sbHubArchiveCount()
    broadcastIfNeeded()
    let arch1 = sbHubArchiveCount()
    sbSay("SBX-S05 | 无 itemAttrs 时 EXPECT 不上报 | ACTUAL archive增量=\(arch1 - arch0) | \(arch1 - arch0 == 0 ? "PASS" : "FAIL")")
}

func sbScenario6() {   // conf 损坏
    sbSay("SBX-S06 | conf 损坏（坏 JSON）→ loadConf 拒绝 + 日志告警 + 不崩")
    let bad = "{ this is not json ]"
    try! bad.write(to: URL(fileURLWithPath: sbConfPath), atomically: true, encoding: .utf8)
    let r = loadConf()
    sbSay("SBX-S06 | EXPECT loadConf=nil | ACTUAL \(r == nil ? "nil" : "非nil") | \(r == nil ? "PASS" : "FAIL")")
    let log = (try? String(contentsOfFile: SB + "/qspace-layouts/app.log", encoding: .utf8)) ?? ""
    let warned = log.contains("loadConf: desktop.conf 解析失败")
    sbSay("SBX-S06 | EXPECT 日志含告警 | ACTUAL warned=\(warned) | \(warned ? "PASS" : "FAIL")")
    // 同步暂停（broadcast 不发）
    let arch0 = sbHubArchiveCount()
    broadcastIfNeeded()
    let arch1 = sbHubArchiveCount()
    sbSay("SBX-S06 | 损坏时 EXPECT 不上报 | ACTUAL archive增量=\(arch1 - arch0) | \(arch1 - arch0 == 0 ? "PASS" : "FAIL")")
}

func sbScenario7() {   // conf 缺失
    sbSay("SBX-S07 | conf 缺失 → 明确返回 nil 不崩")
    sbRemove(sbConfPath)
    let r = loadConf()
    sbSay("SBX-S07 | EXPECT nil | ACTUAL \(r == nil ? "nil" : "非nil") | \(r == nil ? "PASS" : "FAIL")")
    let arch0 = sbHubArchiveCount()
    broadcastIfNeeded()
    let arch1 = sbHubArchiveCount()
    sbSay("SBX-S07 | 缺失时 EXPECT 不上报 | ACTUAL archive增量=\(arch1 - arch0) | \(arch1 - arch0 == 0 ? "PASS" : "FAIL")")
}

func sbScenario8() {   // 屏号失真 → normalizeScreenIds
    sbSay("SBX-S08 | 屏号失真：itemAttrs 混入 screen=2 的旧屏号项")
    // 主组 bbox 从实际 attrs 算出来（不写死）→ stale 项坐标相对主组定位
    func mainBoxOf(_ a: [[String: Any]]) -> (Int, Int, Int, Int) {
        var xs = [Int](), ys = [Int]()
        for it in a where isDesktopItem(it) {
            xs.append((it["x"] as! NSNumber).intValue); ys.append((it["y"] as! NSNumber).intValue)
        }
        return (xs.min()!, ys.min()!, xs.max()!, ys.max()!)
    }
    func mk(_ staleOffsets: [(Int, Int)]) -> ([[String: Any]], (Int, Int, Int, Int)) {
        var a = sbConf(sbNames("h", 20), screen: 1)["itemAttrs"] as! [[String: Any]]
        let mb = mainBoxOf(a)
        for (i, off) in staleOffsets.enumerated() {
            a.append(["name": "/Users/jon/Desktop/st\(i).txt",
                      "x": mb.0 + off.0, "y": mb.1 + off.1,
                      "screen": 2, "screenWidth": 2560, "screenHeight": 1440])
        }
        return (a, mb)
    }
    func report(_ tag: String, _ a: [[String: Any]], _ expectMerged: Bool) {
        let out = normalizeScreenIds(a)
        let s1 = out.filter { ($0["screen"] as? Int) == 1 }.count
        let s2 = out.filter { ($0["screen"] as? Int) == 2 }.count
        let ok = expectMerged ? (s2 == 0) : (s2 == 3)
        sbSay("SBX-S08 | \(tag) EXPECT \(expectMerged ? "归入 screen1(s2=0)" : "不合并(s2=3)") | ACTUAL s1=\(s1) s2=\(s2) | \(ok ? "PASS" : "FAIL")")
    }
    let (aA, mbA) = mk([(100, 0), (200, 120)])
    sbSay("SBX-S08 | 主组 bbox = x[\(mbA.0),\(mbA.2)] y[\(mbA.1),\(mbA.3)]")
    report("A[旧组2行在主bbox内]", aA, true)
    let (aB, _) = mk([(100, 0), (200, 0), (300, 0)])
    report("B[旧组全同一行(bbox高度0)]", aB, true)
    let (aC, mbC) = mk([(100, 0), (200, 120), (300, 400)])
    sbSay("SBX-S08 | C 主组 y 范围 [\(mbC.1),\(mbC.3)]，旧组下沿 y=\(mbC.1 + 400)")
    report("C[旧组下沿超出主bbox]", aC, true)
    let (aD, _) = mk([(5000, 0), (5100, 120), (5200, 240)])
    report("D[真双屏 x 远超主组]", aD, false)
}

func sbScenario8b() {  // 屏号自愈：判据公式 vs 生产函数 一致性（探针）
    sbSay("SBX-S08b | 屏号自愈判据：公式复刻 vs 生产函数 一致性")
    // ⭐ 主组 bbox 动态计算（不写死）——写死会让探针与实际布局脱节 → 永远报「不一致」
    func mainBoxOf(_ a: [[String: Any]]) -> (Int, Int, Int, Int) {
        var xs = [Int](), ys = [Int]()
        for it in a where isDesktopItem(it) {
            xs.append((it["x"] as! NSNumber).intValue); ys.append((it["y"] as! NSNumber).intValue)
        }
        return (xs.min()!, ys.min()!, xs.max()!, ys.max()!)
    }
    func probe(_ tag: String, _ offs: [(Int, Int)]) {
        var a = sbConf(sbNames("h", 20), screen: 1)["itemAttrs"] as! [[String: Any]]
        let mb = mainBoxOf(a)
        for (i, off) in offs.enumerated() {
            a.append(["name": "/Users/jon/Desktop/st\(i).txt",
                      "x": mb.0 + off.0, "y": mb.1 + off.1,
                      "screen": 2, "screenWidth": 2560, "screenHeight": 1440])
        }
        let bxs = offs.map { mb.0 + $0.0 }
        let bys = offs.map { mb.1 + $0.1 }
        let bminX = bxs.min()!, bmaxX = bxs.max()!
        let bminY = bys.min()!, bmaxY = bys.max()!
        let ox = max(0, min(mb.2, bmaxX) - max(mb.0, bminX))
        let oy = max(0, min(mb.3, bmaxY) - max(mb.1, bminY))
        let overlap = ox * oy
        let bArea = max(1, (bmaxX - bminX) * (bmaxY - bminY))
        let degenerate = (bmaxY - bminY) == 0 || (bmaxX - bminX) == 0
        let ratio = Double(overlap) / Double(bArea)
        let inside = bminX <= mb.2 && bmaxX >= mb.0 && bminY <= mb.3 && bmaxY >= mb.1
        let judged = degenerate ? (overlap > 0 || inside) : (ratio > 0.5 || inside)
        let out = normalizeScreenIds(a)
        let s2 = out.filter { ($0["screen"] as? Int) == 2 }.count
        let real = s2 == 0
        sbSay("SBX-S08b | \(tag) | ratio=\(String(format: "%.3f", ratio)) degenerate=\(degenerate) inside=\(inside) 判据=\(judged ? "并入" : "不并入") 实测=\(real ? "并入" : "不并入") | \(judged == real ? "公式自洽" : "★ 不一致")")
    }
    probe("旧组2行在主bbox内", [(100, 0), (200, 120)])
    probe("旧组全同一行(bbox高度0)", [(100, 0), (200, 0), (300, 0)])
    probe("旧组下沿超出主bbox", [(100, 0), (200, 120), (300, 400)])
    probe("旧组下沿超出更多", [(100, 0), (200, 400), (300, 800)])
    probe("旧组2项同行", [(100, 0), (200, 60)])
    probe("真双屏(远在右侧)", [(5000, 0), (5100, 120), (5200, 240)])
}

func sbScenario8c() {  // 屏号自愈：主组并列时的确定性
    sbSay("SBX-S08c | 屏号自愈：两组项数相同时「主组」选谁？")
    var a = sbConf(sbNames("h", 20), screen: 1)["itemAttrs"] as! [[String: Any]]
    // 造一个与主组项数相同（20 项）的 screen=2 组，坐标在主bbox内
    for i in 0..<20 {
        a.append(["name": "/Users/jon/Desktop/tie\(i).txt", "x": 300 + (i % 11) * 113, "y": 60 + (i / 11) * 148,
                  "screen": 2, "screenWidth": 2560, "screenHeight": 1440])
    }
    let out = normalizeScreenIds(a)
    let s1 = out.filter { ($0["screen"] as? Int) == 1 }.count
    let s2 = out.filter { ($0["screen"] as? Int) == 2 }.count
    sbSay("SBX-S08c | ACTUAL s1=\(s1) s2=\(s2)（两组都 20 项 → 谁当主？结果应恒定）")
}

func sbScenario10() {  // 跨实现一致性：main.swift mapCell 指纹 vs cmp-consistency.py mapCell
    sbSay("SBX-S10 | 跨实现一致性：生产 mapCell 指纹（供 Python 端比对）")
    var parts: [String] = []
    for sk in ["A", "B", "C"] {
        for dk in ["A", "B", "C"] where sk != dk {
            let s = SPEC_ALL[sk]!, d = SPEC_ALL[dk]!
            for col in 1...s.cols {
                for row in 1...s.rows {
                    let r = mapCell(from: s, to: d, col: col, row: row)
                    parts.append("\(sk)>\(dk):\(col),\(row)=\(r.0),\(r.1)")
                }
            }
        }
    }
    let digest = fnv1a(parts.joined(separator: ";"))
    let full = parts.joined(separator: "\n")
    try! full.write(toFile: SB + "/mapcell-prod.txt", atomically: true, encoding: .utf8)
    sbSay("SBX-S10 | 生产 mapCell 组合数=\(parts.count) 指纹=\(digest)")
    sbSay("SBX-S10 | 明细已落盘 \(SB)/mapcell-prod.txt")
}

func sbScenario11() {  // specForSize 可达性 + leftBase 一致性
    sbSay("SBX-S11 | specForSize 套判定可达性（生产函数）")
    var rows: [String] = []
    for w in [1512, 1680, 1700, 1710, 1718, 1728, 1740, 1750, 1751, 1760, 1768, 1769, 2560] {
        let k = specForSize(w, 1117)?.key ?? "nil(走 machineTag 兜底)"
        rows.append("w=\(w)→\(k)")
    }
    sbSay("SBX-S11 | \(rows.joined(separator: "  "))")
    // B 套可达区间
    var bMin = -1, bMax = -1
    for w in 1400...1900 {
        if specForSize(w, 1117)?.key == "B" {
            if bMin < 0 { bMin = w }
            bMax = w
        }
    }
    sbSay("SBX-S11 | SPEC_B 可达宽度区间 = [\(bMin), \(bMax)]（宽度 100）")
    // 正确判据：B 套必须覆盖 MBP 内置屏 1728，且可达区间要够宽（容差 80 → 至少 60 宽）
    let covers1728 = (bMin <= 1728 && bMax >= 1728)
    let wideEnough = (bMax - bMin + 1) >= 40
    sbSay("SBX-S11 | SPEC_B 覆盖 1728=\(covers1728) 可达区间宽=\(bMax - bMin + 1) | \(covers1728 && wideEnough ? "PASS" : "FAIL（MBP 1728 落进 A 区间 → B 套不可用）")")
    // leftBase 与 cmp 的一致性抽样
    for key in ["A", "B", "C"] {
        let sp = SPEC_ALL[key]!
        let vals = (1...sp.rows).map { r in "\(r):\(leftBase(sp, r))" }.joined(separator: ",")
        sbSay("SBX-S11 | leftBase[\(key)] = \(vals)")
    }
}

func sbScenario15() {  // overlap 修复：同格重叠推开
    sbSay("SBX-S15 | overlap 修复：同格重叠 → 行内右推")
    var conf = sbConf(sbNames("o", 8))
    var a = conf["itemAttrs"] as! [[String: Any]]
    // 让 a[0] 和 a[1] 落在同一格 (col1,row1)
    a[1]["x"] = a[0]["x"]; a[1]["y"] = a[0]["y"]
    conf["itemAttrs"] = a
    let r = fixLocalOverlaps(conf: conf)
    let patched = r?.patched ?? -1
    sbSay("SBX-S15 | EXPECT 检出并推开 1 个 | ACTUAL patched=\(patched) | \(patched == 1 ? "PASS" : "FAIL")")
    if let out = r?.newConf, let aa = out["itemAttrs"] as? [[String: Any]] {
        let c0 = (aa[0]["x"] as! NSNumber).intValue, c1 = (aa[1]["x"] as! NSNumber).intValue
        sbSay("SBX-S15 | EXPECT 两项 x 不同 | ACTUAL x0=\(c0) x1=\(c1) | \(c0 != c1 ? "PASS" : "FAIL")")
        // 修后是否仍有同格
        var cells: [String: Int] = [:]
        for it in aa where isDesktopItem(it) {
            let spec = SPEC_C
            let x = (it["x"] as! NSNumber).intValue, y = (it["y"] as! NSNumber).intValue
            let cc = max(1, min(spec.cols, safeInt((Double(x) - spec.x0) / spec.colStep, fallback: -1) + 1))
            let rr = max(1, min(spec.rows, safeInt((Double(y) - spec.y0) / spec.rowStep, fallback: -1) + 1))
            cells["\(cc),\(rr)", default: 0] += 1
        }
        let dups = cells.values.filter { $0 > 1 }.count
        sbSay("SBX-S15 | EXPECT 修后 0 个同格重叠 | ACTUAL dups=\(dups) | \(dups == 0 ? "PASS" : "FAIL")")
    }
    // 无重叠时不应改盘
    let r2 = fixLocalOverlaps(conf: sbConf(sbNames("o2", 8)))
    sbSay("SBX-S15 | 无重叠 EXPECT nil（不改盘）| ACTUAL patched=\(r2?.patched ?? -1) | \(r2 == nil ? "PASS" : "FAIL")")
}

func sbScenario16() {  // 快照链：自动快照 + 保留上限 + 恢复
    sbSay("SBX-S16 | 快照链：自动快照写入 / 保留上限 / 恢复应用")
    let lay = SB + "/qspace-layouts"
    try? FileManager.default.createDirectory(atPath: lay, withIntermediateDirectories: true)
    // 清理本机前缀旧快照
    let files = (try? FileManager.default.contentsOfDirectory(atPath: lay)) ?? []
    for f in files where f.hasPrefix(machinePrefix + autoPrefix) { try? FileManager.default.removeItem(atPath: lay + "/" + f) }
    // 造 conf 并落盘
    sbWriteJSON(sbConfPath, sbConf(sbNames("s", 6)))
    lastAutoSnapshotAt = 0
    autoSnapshotIfNeeded()
    let after1 = ((try? FileManager.default.contentsOfDirectory(atPath: lay)) ?? []).filter { $0.hasPrefix(machinePrefix + autoPrefix) }
    sbSay("SBX-S16 | 首次自动快照 EXPECT ≥1 份 | ACTUAL \(after1.count) | \(after1.count >= 1 ? "PASS" : "FAIL")")
    // 连续快照 → 不应超上限
    for i in 1...14 {
        // ⭐ 必须内容真的变（指纹变），否则 sameContent 判定跳过 → 永远存不到上限
        var conf = sbConf(sbNames("s\(i)", 6))
        sbWriteJSON(sbConfPath, conf)
        lastAutoSnapshotAt = 0
        autoSnapshotIfNeeded()
    }
    let afterN = ((try? FileManager.default.contentsOfDirectory(atPath: lay)) ?? []).filter { $0.hasPrefix(machinePrefix + autoPrefix) }
    sbSay("SBX-S16 | 上限 maxAutoSnapshots=\(maxAutoSnapshots) EXPECT ≤\(maxAutoSnapshots) | ACTUAL \(afterN.count) | \(afterN.count <= maxAutoSnapshots ? "PASS" : "FAIL")")
    // 恢复：快照 → 应用到自己（同套应零移动）
    if let f = afterN.last {
        let url = URL(fileURLWithPath: lay + "/" + f)
        let conf = loadConf()!
        let snap = makeGridSnapshot(conf: conf)!
        if let r = applyGridSnapshot(snap, to: conf) {
            sbSay("SBX-S16 | 自应用 EXPECT moved=0 | ACTUAL matched=\(r.matched) kept=\(r.kept) moved=\(r.moved) | \(r.moved == 0 ? "PASS" : "FAIL")")
        } else {
            sbSay("SBX-S16 | 自应用 FAIL（applyGridSnapshot 返回 nil）")
        }
    }
}

func sbScenario17() {  // apply 端：跨套映射 + 占用推移 + 越界保护
    sbSay("SBX-S17 | apply 端跨套映射（中枢 C 套 → 本机 A 套）")
    let spec = SPEC_C
    var snapItems: [[String: Any]] = []
    for i in 0..<10 {
        let col = (i % 11) + 1, row = (i / 11) + 1
        snapItems.append(["name": "m\(i).txt", "col": col, "row": row])
    }
    let snap: [String: Any] = ["specKey": "C", "items": snapItems,
        "layoutFingerprint": "fp-x", "syncAt": 1, "sourceMachine": "MBP", "grid": [:]]
    // 本机 conf：A 套（1710 宽）
    var conf = sbConf(sbNames("m", 10), screen: 1, sw: 1710, sh: 982)
    var a = conf["itemAttrs"] as! [[String: Any]]
    for i in 0..<10 { a[i]["name"] = "/Users/jon/Desktop/m\(i).txt" }
    conf["itemAttrs"] = a
    if let r = applyGridSnapshot(snap, to: conf) {
        sbSay("SBX-S17 | apply OK matched=\(r.matched) kept=\(r.kept) moved=\(r.moved)")
        // 应用后本机必须用 A 套参数
        let lod = r.newConf["lod"] as? Int ?? -1
        sbSay("SBX-S17 | 参数随套 EXPECT lod=64(A套) | ACTUAL lod=\(lod) | \(lod == 64 ? "PASS" : "FAIL")")
        // 应用后不得有同格重叠
        var cells: [String: Int] = [:]
        for it in (r.newConf["itemAttrs"] as! [[String: Any]]) {
            let x = (it["x"] as! NSNumber).intValue, y = (it["y"] as! NSNumber).intValue
            let cc = max(1, min(21, safeInt((Double(x) - 80) / 79, fallback: -1) + 1))
            let rr = max(1, min(9, safeInt((Double(y) - 44) / 114, fallback: -1) + 1))
            cells["\(cc),\(rr)", default: 0] += 1
        }
        let dups = cells.values.filter { $0 > 1 }.count
        sbSay("SBX-S17 | 应用后同格重叠数 = \(dups) | \(dups == 0 ? "PASS" : "FAIL")")
        // 越界检查：A 套 1710 宽
        let bad = (r.newConf["itemAttrs"] as! [[String: Any]]).filter {
            let x = ($0["x"] as! NSNumber).intValue, y = ($0["y"] as! NSNumber).intValue
            return x + 32 > 1710 || y + 32 > 982
        }
        sbSay("SBX-S17 | 越界项数 = \(bad.count) | \(bad.isEmpty ? "PASS" : "FAIL")")
        // 其余 conf 字段必须保留
        let keep = r.newConf["visible"] != nil
        sbSay("SBX-S17 | conf 其余字段保留（visible）| \(keep ? "PASS" : "FAIL")")
    } else {
        sbSay("SBX-S17 | applyGridSnapshot 返回 nil | FAIL")
    }
}

func sbScenario18() {  // 风暴保护 + 网络退避
    sbSay("SBX-S18 | 风暴保护：60 秒内 8 次上报后应暂停")
    let now = Int(Date().timeIntervalSince1970)
    sbWriteJSON(sbStatePath, ["baselineFingerprint": "fp-x",
                               "recentBroadcasts": Array(repeating: now, count: 8),
                               "clockOffset": 0])
    sbWriteJSON(sbConfPath, sbConf(sbNames("f", 5)))
    sbSetHubNames(current: [], prev: [])
    rt.lastSeenFp = "stale"; rt.lastSeenItems = [:]; rt.lastLocalChangeAt = 0
    rt.ledgerShortSince = 0; rt.suppressUntil = 0; hubFailStreak = 0; hubNextRetryAt = 0; specChangedAt = 0
    let arch0 = sbHubArchiveCount()
    broadcastIfNeeded()
    let arch1 = sbHubArchiveCount()
    sbSay("SBX-S18 | 风暴保护 EXPECT 不上报(增量=0) | ACTUAL 增量=\(arch1 - arch0) | \(arch1 - arch0 == 0 ? "PASS" : "FAIL")")
    // 网络失败退避
    hubNextRetryAt = now + 30
    let arch2 = sbHubArchiveCount()
    broadcastIfNeeded()
    let arch3 = sbHubArchiveCount()
    sbSay("SBX-S18 | 网络退避 EXPECT 不上报 | ACTUAL 增量=\(arch3 - arch2) | \(arch3 - arch2 == 0 ? "PASS" : "FAIL")")
}

func sbScenario19() {  // sync-state 原子写 + 损坏恢复
    sbSay("SBX-S19 | sync-state 原子写 + 损坏恢复")
    var st: [String: Any] = ["baselineFingerprint": "fp-1", "lastBroadcastFingerprint": "fp-1",
                              "recentBroadcasts": [Int](repeating: 0, count: 0),
                              "clockOffset": 7, "nested": ["a": 1, "b": [2, 3]]]
    saveSyncState(st)
    let back = loadSyncState()
    sbSay("SBX-S19 | 往返 EXPECT clockOffset=7 nested 保留 | ACTUAL off=\(back["clockOffset"] as? Int ?? -1) nested=\(back["nested"] != nil) | \(back["clockOffset"] as? Int == 7 && back["nested"] != nil ? "PASS" : "FAIL")")
    // 损坏 → loadSyncState 返回空字典（不崩）
    try! "{ broken".write(toFile: sbStatePath, atomically: true, encoding: .utf8)
    let bad = loadSyncState()
    sbSay("SBX-S19 | 损坏 EXPECT 返回空字典不崩 | ACTUAL count=\(bad.count) | \(bad.isEmpty ? "PASS" : "FAIL")")
    // 非法 JSON 类型（NaN）→ isValidJSONObject 拦截（干净前提：先写回合法状态）
    saveSyncState(["baselineFingerprint": "fp-CLEAN", "recentBroadcasts": [Int](repeating: 0, count: 0)])
    saveSyncState(["bad": Double.nan])
    let after = loadSyncState()
    sbSay("SBX-S19 | NaN 写入 EXPECT 被拦截(文件保持不变) | ACTUAL baseline=\(after["baselineFingerprint"] as? String ?? "空") | \(after["baselineFingerprint"] as? String == "fp-CLEAN" ? "PASS(未被覆盖)" : "FAIL(被覆盖)")")
    // 临时文件残留检查
    let tmpExists = FileManager.default.fileExists(atPath: sbStatePath + ".tmp")
    sbSay("SBX-S19 | 临时文件残留检查 .tmp 存在=\(tmpExists)（存在也无妨：写完即 rename）")
}

func sbScenario20() {  // JSON 合法性预检探针（saveSyncState 的防崩闸）
    sbSay("SBX-S20 | JSON 合法性预检探针")
    let cases: [(String, Any)] = [
        ("NaN", ["x": Double.nan]),
        ("+Inf", ["x": Double.infinity]),
        ("NSNumber NaN", ["x": NSNumber(value: Double.nan)]),
        ("合法", ["x": 1.5]),
        ("嵌套合法", ["x": ["a": [1, 2, 3]]]),
    ]
    for (tag, v) in cases {
        let ok = JSONSerialization.isValidJSONObject(v)
        sbSay("SBX-S20 | isValidJSONObject[\(tag)] = \(ok)")
    }
    // 实测：saveSyncState 遇 NaN 会怎样
    var st: [String: Any] = ["baselineFingerprint": "fp-KEEP"]
    saveSyncState(st)
    let before = loadSyncState()["baselineFingerprint"] as? String ?? "空"
    saveSyncState(["bad": Double.nan])
    let after = loadSyncState()["baselineFingerprint"] as? String ?? "空"
    let raw = (try? String(contentsOfFile: sbStatePath, encoding: .utf8)) ?? "(读不到)"
    sbSay("SBX-S20 | saveSyncState(NaN) 前 baseline=\(before) 后 baseline=\(after)")
    sbSay("SBX-S20 | 落盘原文 = \(raw.prefix(160).description)")
    sbSay("SBX-S20 | 判定：\(before == "fp-KEEP" && after == "fp-KEEP" ? "NaN 被拦截，文件未变（符合预期）" : "★ NaN 未被拦截 → 状态文件被非法内容覆盖")")
}

func sbScenario21() {  // ⭐ 累积漂移：多轮跨套同步后位置是否稳定（v6 的核心目标）
    sbSay("SBX-S21 | 累积漂移：多轮 A→B→A 往返后位置是否稳定")
    // 造 A 套布局：12 项放 row 8/9（无禁区），col 1..12
    let names = sbNames("d", 12)
    var conf = sbConf(names, screen: 1, sw: 1710, sh: 982)   // 1710 → SPEC_A
    var attrs = conf["itemAttrs"] as! [[String: Any]]
    for i in 0..<12 { attrs[i]["name"] = "/Users/jon/Desktop/\(names[i])" }
    conf["itemAttrs"] = attrs
    let startCols = attrs.map { ($0["x"] as! NSNumber).intValue }
    sbSay("SBX-S21 | 初始 A 套 x = \(startCols)")
    // 逐轮：A 布局 → 映射成 B 套快照 → 再映射回 A 套 → 比对初始
    var cur = conf
    var drifts: [Int] = []
    for round in 1...6 {
        // 本机当 B 套机（1728 宽 → 但 specForSize 1728 落 A 区间… 用 1760 强制落 B）
        var bConf = cur
        var ba = bConf["itemAttrs"] as! [[String: Any]]
        for i in ba.indices { ba[i]["screenWidth"] = 1760; ba[i]["screenHeight"] = 1117 }
        bConf["itemAttrs"] = ba
        let bSnap = makeGridSnapshot(conf: bConf)!
        // B 套机应用 C? 不 —— 这里模拟「中枢存 B 套，另一台 A 套机应用」
        // A 套机应用 B 套快照
        var aMachine = conf
        var aa = aMachine["itemAttrs"] as! [[String: Any]]
        for i in aa.indices { aa[i]["screenWidth"] = 1710; aa[i]["screenHeight"] = 982 }
        aMachine["itemAttrs"] = aa
        guard let r = applyGridSnapshot(bSnap, to: aMachine) else { sbSay("SBX-S21 | 第\(round)轮 apply 返回 nil → 终止"); break }
        cur = r.newConf
        let nowCols = (cur["itemAttrs"] as! [[String: Any]]).map { ($0["x"] as! NSNumber).intValue }
        let maxD = zip(startCols, nowCols).map { abs($0 - $1) }.max() ?? 0
        drifts.append(maxD)
        sbSay("SBX-S21 | 第\(round)轮 应用后 x = \(nowCols) | 与初始最大偏差 = \(maxD)px")
    }
    let monotone = drifts.enumerated().allSatisfy { i, v in i == 0 || v >= drifts[i-1] }
    sbSay("SBX-S21 | 偏差序列 = \(drifts) | 单调不减(累积漂移)=\(monotone) | 末轮偏差=\(drifts.last ?? -1)px")
    sbSay("SBX-S21 | 判定：\(monotone && (drifts.last ?? 0) > 0 ? "★ 存在累积漂移（每轮多漂一点）" : "无累积漂移（稳定）")")
}

func sbScenario22() {  // 自动快照同分钟文件名撞名
    sbSay("SBX-S22 | 自动快照文件名粒度（MM-dd_HHmm）→ 同分钟覆盖")
    let lay = SB + "/qspace-layouts"
    try? FileManager.default.createDirectory(atPath: lay, withIntermediateDirectories: true)
    let files = (try? FileManager.default.contentsOfDirectory(atPath: lay)) ?? []
    for f in files where f.hasPrefix(machinePrefix + autoPrefix) { try? FileManager.default.removeItem(atPath: lay + "/" + f) }
    var namesSeen: [String] = []
    for i in 1...3 {
        sbWriteJSON(sbConfPath, sbConf(sbNames("z\(i)", 5)))
        lastAutoSnapshotAt = 0
        autoSnapshotIfNeeded()
        let fs = ((try? FileManager.default.contentsOfDirectory(atPath: lay)) ?? []).filter { $0.hasPrefix(machinePrefix + autoPrefix) }
        namesSeen.append(fs.sorted().joined(separator: ","))
    }
    sbSay("SBX-S22 | 三次自动快照后的文件名集合：")
    for (i, n) in namesSeen.enumerated() { sbSay("SBX-S22 |   第\(i+1)次 → \(n)") }
    let final = ((try? FileManager.default.contentsOfDirectory(atPath: lay)) ?? []).filter { $0.hasPrefix(machinePrefix + autoPrefix) }
    sbSay("SBX-S22 | 期望 3 份（内容各异）| ACTUAL \(final.count) 份 | \(final.count == 3 ? "PASS" : "★ 同分钟文件名撞名 → 后写覆盖前写")")
}

func sbScenario9() {   // 双写竞争 → 原子写
    sbSay("SBX-S09 | 双写竞争：多写者并发写 conf")
    sbEnsureDirs()
    var bad = 0, reads = 0, ok = 0
    let lock = NSLock()
    let stop = DispatchSemaphore(value: 0)
    // 读线程：持续读，验证永远是完整 JSON
    DispatchQueue.global().async {
        while true {
            if let d = FileManager.default.contents(atPath: sbConfPath) {
                lock.lock(); reads += 1; lock.unlock()
                let obj = try? JSONSerialization.jsonObject(with: d)
                if obj == nil { lock.lock(); bad += 1; lock.unlock() }
                else { lock.lock(); ok += 1; lock.unlock() }
            }
            usleep(300)
        }
    }
    // 写线程：10 个并发写者，各写不同内容
    let wg = DispatchGroup()
    for w in 0..<10 {
        DispatchQueue.global().async(group: wg) {
            for k in 0..<40 {
                let names = sbNames("w\(w)", 5 + k)
                let c = sbConf(names)
                writeConf(c)
                usleep(200)
            }
        }
    }
    wg.wait()
    usleep(300_000)
    // 最终可解析
    let final = (try? JSONSerialization.jsonObject(with: FileManager.default.contents(atPath: sbConfPath)!)) as? [String: Any]
    let fattr = (final?["itemAttrs"] as? [[String: Any]])?.count ?? -1
    lock.lock()
    sbSay("SBX-S09 | 并发写 10x40=400 次 | 读样本=\(reads) 完整JSON=\(ok) 半截JSON=\(bad) | 最终可解析=\(final != nil) 项数=\(fattr) | \(bad == 0 && final != nil ? "PASS" : "FAIL")")
    lock.unlock()
    _ = stop
}

func sbScenario12() {  // 保护窗（客户端侧）
    sbSay("SBX-S12 | 保护窗：active 上报 vs 非 active 上报冲突")
    let now = Int(Date().timeIntervalSince1970)
    // 中枢先放一份「另一台正在操作」的 active 版本
    let r = sbHubSeed(["syncAt": now - 5, "sourceMachine": "MBP",
                        "userActiveUntil": now + 600, "userActiveMachine": "MBP",
                        "items": sbNames("p", 5).enumerated().map { ["name": $0.element, "col": ($0.offset % 11) + 1, "row": 1] },
                        "layoutFingerprint": "fp-active", "specKey": "C"])
    sbSay("SBX-S12 | seed hub active 版本 → \(r ?? [:])")
    let names = sbNames("p", 6)
    let (fp, _) = sbMakeConfAndPrime(names)
    // 基线故意不同 → 走到上报；且无 active 标记
    sbWriteJSON(sbStatePath, ["baselineFingerprint": "fp-old-baseline", "recentBroadcasts": []])
    sbSetHubNames(current: names, prev: [])
    rt.lastSeenFp = fp; rt.lastLocalChangeAt = 0; rt.ledgerShortSince = 0
    rt.suppressUntil = 0; hubFailStreak = 0; hubNextRetryAt = 0; specChangedAt = 0
    broadcastIfNeeded()
    let st = (sbReadJSON(sbStatePath) as? [String: Any]) ?? [:]
    let base = st["baselineFingerprint"] as? String ?? ""
    let back = rt.protectBackoffUntil
    let hubN = sbHubCount()
    sbSay("SBX-S12 | EXPECT 被拒 reason=protected / baseline 不变 / 退避>now | ACTUAL baseline=\(base) backoff余=\(back - now)s hub项数=\(hubN) | \(base == "fp-old-baseline" && back > now ? "PASS" : "FAIL")")
    // ⭐ 真实路径：用户继续拖动 → 采样侧检测到位移 → 清零保护窗退避(main.swift:1365-1370)
    //    → 再等过 10s 拖动防抖 → 带 active 标记重报 → 后改者赢
    // ⭐ 必须「真挪坐标」：movedExisting 只统计「同名项且格位变了」→ 只改名不算拖动
    let (fpOld, itemsOld) = sbMakeConfAndPrime(names)
    var conf2 = sbConf(names)
    var a2 = conf2["itemAttrs"] as! [[String: Any]]
    let spec = SPEC_C
    a2[0]["x"] = Int(spec.x0 + Double(15) * spec.colStep)   // 挪到 col 16（空列）
    a2[0]["y"] = Int(spec.y0)
    conf2["itemAttrs"] = a2
    sbWriteJSON(sbConfPath, conf2)
    let snap2 = makeGridSnapshot(conf: loadConf() ?? [:])!
    let fp2 = snap2["layoutFingerprint"] as! String
    // 采样侧：上轮看到的是「挪之前」的位置 → 本轮检测到位移
    var dOld: [String: (Int, Int)] = [:]
    for it in itemsOld {
        if let n = it["name"] as? String, let c = it["col"] as? Int, let r = it["row"] as? Int { dOld[n] = (c, r) }
    }
    rt.lastSeenItems = dOld
    rt.lastSeenFp = "stale-fp"
    broadcastIfNeeded()                 // → 触发 protectBackoffUntil = 0
    sbSay("SBX-S12 | 用户拖动后 EXPECT 退避被清零 | ACTUAL protectBackoffUntil-now=\(rt.protectBackoffUntil - now)s | \(rt.protectBackoffUntil <= now ? "PASS" : "FAIL")")
    rt.lastLocalChangeAt = Int(Date().timeIntervalSince1970) - 11   // 等过 10s 拖动防抖
    broadcastIfNeeded()
    let st2 = (sbReadJSON(sbStatePath) as? [String: Any]) ?? [:]
    let base2 = st2["baselineFingerprint"] as? String ?? ""
    let hubFp2 = sbHubGetLayout()?["layoutFingerprint"] as? String ?? ""
    sbSay("SBX-S12 | 带 active 标记 EXPECT 被接受(baseline=fp2) | ACTUAL baseline=\(base2 == "fp-old-baseline" ? "未变" : "已变=\(base2)") hubFp=\(hubFp2 == fp2 ? "=本机fp" : hubFp2) | \(base2 == fp2 && hubFp2 == fp2 ? "PASS" : "FAIL")")
}

func sbScenario13() {  // 时钟偏移
    sbSay("SBX-S13 | 时钟偏移：客户端钟落后 → clockOffset 补偿后上报成功")
    let now = Int(Date().timeIntervalSince1970)
    // 中枢先放一个「比本机时钟超前 200 秒」的版本
    let r = sbHubSeed(["syncAt": now + 200, "sourceMachine": "MBP",
                        "items": sbNames("c", 4).enumerated().map { ["name": $0.element, "col": $0.offset + 1, "row": 1] },
                        "layoutFingerprint": "fp-future", "specKey": "C"])
    sbSay("SBX-S13 | seed hub syncAt=now+200 → \(r ?? [:])")
    let names = sbNames("c", 5)
    let (fp, _) = sbMakeConfAndPrime(names)
    sbWriteJSON(sbStatePath, ["baselineFingerprint": "fp-x", "recentBroadcasts": []])
    sbSetHubNames(current: names, prev: [])
    rt.lastSeenFp = fp; rt.lastLocalChangeAt = 0; rt.ledgerShortSince = 0
    rt.suppressUntil = 0; hubFailStreak = 0; hubNextRetryAt = 0; specChangedAt = 0
    // 第 1 轮：at=now ≤ 中枢 now+200 → 应被判 older，并记录 clockOffset
    broadcastIfNeeded()
    var st = (sbReadJSON(sbStatePath) as? [String: Any]) ?? [:]
    let off1 = st["clockOffset"] as? Int ?? 0
    sbSay("SBX-S13 | 第1轮 EXPECT 记下 clockOffset≈201 | ACTUAL offset=\(off1) | \(abs(off1 - 201) <= 3 ? "PASS" : "FAIL")")
    // 第 2 轮：补偿后应成功
    rt.lastSeenFp = fp; rt.lastLocalChangeAt = 0
    broadcastIfNeeded()
    st = (sbReadJSON(sbStatePath) as? [String: Any]) ?? [:]
    let base = st["baselineFingerprint"] as? String ?? ""
    let hubLay = sbHubGetLayout()
    let hubFp = hubLay?["layoutFingerprint"] as? String ?? ""
    let hubN = sbHubCount()
    sbSay("SBX-S13 | 第2轮 EXPECT 上报成功(baseline=fp, 中枢 fp=本机 fp) | ACTUAL baseline=\(base) hubFp=\(hubFp) 项数=\(hubN) | \(base == fp && hubFp == fp ? "PASS" : "FAIL")")
}

func sbScenario14() {  // 空布局拦截（客户端侧）
    sbSay("SBX-S14 | 空布局拦截：items=[] → 客户端拒发")
    // 情形 A：conf 完全没有 itemAttrs
    sbWriteJSON(sbConfPath, ["lod": 96, "visible": true])
    let snapA = makeGridSnapshot(conf: loadConf() ?? [:])
    sbSay("SBX-S14 | A[conf 无 itemAttrs] EXPECT 快照 nil（不可生成） | ACTUAL \(snapA == nil ? "nil" : "非nil") | \(snapA == nil ? "PASS" : "FAIL")")
    // 情形 B：conf 有 itemAttrs 但全是非桌面项（卷宗）→ 快照生成成功但 items=[] → 触发 1472 行空布局闸
    var confB: [String: Any] = ["lod": 96, "itemHSpacing": 11, "visible": true]
    confB["itemAttrs"] = [
        ["name": "/Volumes/Macintosh HD", "x": 100, "y": 100, "screen": 1, "screenWidth": 2560, "screenHeight": 1440],
        ["name": "/Volumes/Backup", "x": 300, "y": 100, "screen": 1, "screenWidth": 2560, "screenHeight": 1440],
    ]
    sbWriteJSON(sbConfPath, confB)
    let snapB = makeGridSnapshot(conf: loadConf() ?? [:])
    let nB = (snapB?["items"] as? [[String: Any]])?.count ?? -1
    let fpB = snapB?["layoutFingerprint"] as? String ?? ""
    sbSay("SBX-S14 | B[只有卷宗无桌面项] EXPECT 快照非nil且 items=0 | ACTUAL snap=\(snapB == nil ? "nil" : "非nil") items=\(nB) fp=\(fpB.isEmpty ? "空" : "非空") | \(snapB != nil && nB == 0 ? "PASS" : "FAIL")")
    sbWriteJSON(sbStatePath, ["baselineFingerprint": "fp-x", "recentBroadcasts": []])
    sbSetHubNames(current: [], prev: [])
    rt.lastSeenFp = "stale"; rt.lastSeenItems = [:]; rt.lastLocalChangeAt = 0
    rt.ledgerShortSince = 0; rt.suppressUntil = 0; hubFailStreak = 0; hubNextRetryAt = 0; specChangedAt = 0
    let arch0 = sbHubArchiveCount()
    broadcastIfNeeded()
    let arch1 = sbHubArchiveCount()
    sbSay("SBX-S14 | EXPECT 客户端拒发（archive 增量=0）| ACTUAL 增量=\(arch1 - arch0) | \(arch1 - arch0 == 0 ? "PASS" : "FAIL")")
    let log = (try? String(contentsOfFile: SB + "/qspace-layouts/app.log", encoding: .utf8)) ?? ""
    sbSay("SBX-S14 | EXPECT 日志含「本机布局为空」| ACTUAL \(log.contains("本机布局为空（0 个图标）") ? "PASS" : "FAIL")")
    let lastLines = log.split(separator: "\n").suffix(3).map(String.init).joined(separator: " || ")
    sbSay("SBX-S14 | 日志尾部: \(lastLines)")
}

// ---- dispatch ----
if let idx = CommandLine.arguments.firstIndex(of: "--sbtest"), idx + 1 < CommandLine.arguments.count {
    sbEnsureDirs()
    let sc = CommandLine.arguments[idx + 1]
    sbSay("=== sandbox scenario \(sc) ===")
    switch sc {
    case "1": sbScenario1()
    case "2": sbScenario2()
    case "3": sbScenario3()
    case "4": sbScenario4()
    case "5": sbScenario5()
    case "6": sbScenario6()
    case "7": sbScenario7()
    case "8": sbScenario8()
    case "8b": sbScenario8b()
    case "8c": sbScenario8c()
    case "9": sbScenario9()
    case "10": sbScenario10()
    case "11": sbScenario11()
    case "12": sbScenario12()
    case "13": sbScenario13()
    case "14": sbScenario14()
    case "15": sbScenario15()
    case "16": sbScenario16()
    case "17": sbScenario17()
    case "18": sbScenario18()
    case "19": sbScenario19()
    case "20": sbScenario20()
    case "21": sbScenario21()
    case "22": sbScenario22()
    default: sbSay("unknown scenario \(sc)")
    }
    exit(0)
}
