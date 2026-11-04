import AppKit

// MARK: - 常量与路径

let confPath = NSString(string: "~/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf").expandingTildeInPath
let layoutDir = NSString(string: "~/Library/Mobile Documents/com~apple~CloudDocs/qspace-layouts").expandingTildeInPath
let logPath = NSString(string: "~/qspace-layouts/app.log").expandingTildeInPath
let qspaceApp = "/Applications/QSpace Pro.app"
let launchAgentPlist = NSString(string: "~/Library/LaunchAgents/com.jon1131120.qspacelayoutbar.plist").expandingTildeInPath
let autoPrefix = "自动 "
let maxAutoSnapshots = 10
// 机器标识：跨机共享快照目录时区分来源（文件名前缀 [MBP]/[Air]/[Studio]），自动清理/监测/绑定只认本机
// 注意：不能用 Host.current().name——它会做阻塞式 DNS 解析，间歇性卡死主线程（实测采样 781/781 卡在 NSHost blockingResolve）。
// 改用 sysctl 读内核 hostname，纯内存零网络 IO。
let machineTag: String = {
    var size = 0
    sysctlbyname("kern.hostname", nil, &size, nil, 0)
    var buf = [CChar](repeating: 0, count: size)
    sysctlbyname("kern.hostname", &buf, &size, nil, 0)
    let host = String(cString: buf).lowercased()
    if host.contains("macbookpro") || host.contains("mbp") { return "MBP" }
    if host.contains("macbookair") { return "Air" }
    if host.contains("macstudio") { return "Studio" }
    return host.split(separator: ".").first.map(String.init) ?? "Mac"
}()
let machinePrefix = "[\(machineTag)] "
// 主线程只读缓存，后台线程刷新——iCloud 目录枚举会阻塞主线程数分钟（实测 1519 采样点全卡在 contentsOfDirectory），绝不在主线程同步枚举
var cachedLayouts: [URL] = []
// 时光机监测参数（Desktop OK 式：拉开差距才记）
let monitorIntervalSec: TimeInterval = 1800   // 每 30 分钟监测一次
let changeThreshold = 0.20                    // 变化 ≥20% 视为大改
let redundantThreshold = 0.05                 // 差异 <5% 视为冗余不记
let minHoursBetweenSnapshots: TimeInterval = 24  // 两条自动快照最小间隔（保底触发）

var appDelegate: AppDelegate?
var currentLayoutName = "（未知）"
var lastKnownFingerprint = ""
var autoSwitchOn = UserDefaults.standard.object(forKey: "autoSwitchOn") == nil
    ? true
    : UserDefaults.standard.bool(forKey: "autoSwitchOn")
var autoMonitorOn = UserDefaults.standard.object(forKey: "autoMonitorOff") == nil
var microNudgeOn = UserDefaults.standard.object(forKey: "microNudgeOff") == nil
    ? true
    : UserDefaults.standard.bool(forKey: "microNudgeOff")

// MARK: - 基础工具

func currentFingerprint() -> String {
    let screens = NSScreen.screens.sorted { a, b in
        if a.frame.origin.x != b.frame.origin.x { return a.frame.origin.x < b.frame.origin.x }
        return a.frame.origin.y < b.frame.origin.y
    }
    return screens.map { "\(Int($0.frame.width))x\(Int($0.frame.height))" }.joined(separator: "+")
}

func loadConf() -> [String: Any]? {
    guard let data = FileManager.default.contents(atPath: confPath),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
    return obj
}

func writeConf(_ conf: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: conf, options: [.prettyPrinted]) else { return }
    try? data.write(to: URL(fileURLWithPath: confPath))
}

/// 同步枚举（只允许后台线程调用；iCloud 目录枚举可能阻塞主线程）
func enumerateLayoutsSync() -> [URL] {
    let fm = FileManager.default
    try? fm.createDirectory(atPath: layoutDir, withIntermediateDirectories: true)
    return ((try? fm.contentsOfDirectory(atPath: layoutDir)) ?? [])
        .filter { $0.hasSuffix(".json") }
        .map { URL(fileURLWithPath: layoutDir).appendingPathComponent($0) }
        .sorted { $0.lastPathComponent < $1.lastPathComponent }
}

/// 主线程用：只读缓存，绝不触发 IO
func layoutURLs() -> [URL] { cachedLayouts }

/// 后台刷新共享目录；列表有变化才重建菜单（避免 30 秒轮询反复闪烁）
func refreshLayoutsAsync() {
    DispatchQueue.global(qos: .utility).async {
        let urls = enumerateLayoutsSync()
        DispatchQueue.main.async {
            let changed = cachedLayouts.map { $0.lastPathComponent } != urls.map { $0.lastPathComponent }
            cachedLayouts = urls
            if changed { appDelegate?.rebuildMenu() }
        }
    }
}

func qspaceRunning() -> Bool {
    return !NSRunningApplication.runningApplications(withBundleIdentifier: "com.jinghaoshe.qspace.pro").isEmpty
}

func restartQSpace() {
    let config = NSWorkspace.OpenConfiguration()
    if let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.jinghaoshe.qspace.pro").first {
        app.terminate()
        for _ in 0..<25 {
            Thread.sleep(forTimeInterval: 0.2)
            if app.isTerminated { break }
        }
        if !app.isTerminated {
            // 优雅退出超时：强杀兜底（否则 open 会被系统忽略）
            logLine("restartQSpace: graceful quit timed out, force terminating")
            app.forceTerminate()
            for _ in 0..<25 {
                Thread.sleep(forTimeInterval: 0.2)
                if app.isTerminated { break }
            }
        }
    }
    Thread.sleep(forTimeInterval: 1)
    NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: qspaceApp),
                                       configuration: config,
                                       completionHandler: nil)
    Thread.sleep(forTimeInterval: 4)
    if !qspaceRunning() {
        logLine("restartQSpace: not running after first open, retrying")
        NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: qspaceApp),
                                           configuration: config,
                                           completionHandler: nil)
        Thread.sleep(forTimeInterval: 4)
    }
    logLine("restartQSpace: done, running=\(qspaceRunning())")
}

/// 原子写 conf：先退出 QSpace（防内存回写覆盖）→ 写 → 重启 QSpace → 轮询等起来
/// 全程快速轮询（0.15~0.2s 步进），总耗时压在 4 秒内
func atomicWriteConfAndRestartQSpace(_ conf: [String: Any]) {
    let config = NSWorkspace.OpenConfiguration()
    if let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.jinghaoshe.qspace.pro").first {
        app.terminate()
        for _ in 0..<12 {
            usleep(150_000)
            if app.isTerminated { break }
        }
        if !app.isTerminated {
            app.forceTerminate()
            for _ in 0..<12 {
                usleep(150_000)
                if app.isTerminated { break }
            }
        }
    }
    writeConf(conf)
    logLine("atomicWrite: conf written while QSpace stopped")
    NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: qspaceApp),
                                       configuration: config,
                                       completionHandler: nil)
    for _ in 0..<25 {
        usleep(200_000)
        if qspaceRunning() { break }
    }
    Thread.sleep(forTimeInterval: 0.6)  // 渲染稳定（原 2 秒，压缩以加快同步）
    logLine("atomicWrite: done, running=\(qspaceRunning())")
}

func logLine(_ msg: String) {
    let line = "\(Date()): \(msg)\n"
    if let handle = FileHandle(forWritingAtPath: logPath) {
        handle.seekToEndOfFile()   // FileHandle 默认 offset=0，必须 seek 到末尾，否则每次覆盖日志
        handle.write(line.data(using: .utf8)!)
        handle.closeFile()
    } else {
        try? line.write(to: URL(fileURLWithPath: logPath), atomically: true, encoding: .utf8)
    }
}

func alert(_ msg: String) {
    let a = NSAlert()
    a.messageText = msg
    a.runModal()
}

func notify(_ msg: String) {
    let n = NSUserNotification()
    n.title = "QSpace 布局"
    n.informativeText = msg
    NSUserNotificationCenter.default.deliver(n)
}

// MARK: - 布局保存

/// 当前状态的两态判定（杰哥的图标全在内置屏，外接屏只是扩展区——所以按内置屏在场与否区分即可）
func screenTypeName() -> String {
    var builtin = false
    for screen in NSScreen.screens {
        if let id = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID,
           CGDisplayIsBuiltin(id) != 0 {
            builtin = true
        }
    }
    return builtin ? "内置屏" : "外置屏"
}

func saveLayout(named name: String, isAuto: Bool = false) {
    guard var conf = loadConf(),
          let attrs = conf["itemAttrs"] as? [[String: Any]] else {
        logLine("saveLayout: 读不到 conf，跳过")
        return
    }
    let fp = currentFingerprint()
    let screenType = screenTypeName()
    let df = DateFormatter()
    df.dateFormat = "MM-dd_HHmm"
    let safeStamp = df.string(from: Date())  // 文件名安全格式（无 / : 等非法字符）
    let displayStamp = DateFormatter.localizedString(from: Date(), dateStyle: .short, timeStyle: .short)
    let rawName = isAuto ? "\(machinePrefix)\(autoPrefix)[\(screenType)] \(safeStamp)" : "\(machinePrefix)\(name)"
    let finalName = rawName.replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: "-")
    // 全量收录桌面显示设置（图标大小 lod、行列间距、文字大小、标签位置、硬盘/外置盘/服务器显示等 15 键）
    var desktopSettings: [String: Any] = [:]
    for (k, v) in conf where k != "itemAttrs" { desktopSettings[k] = v }
    let snapshot: [String: Any] = [
        "name": finalName,
        "savedAt": displayStamp,
        "screenType": screenType,
        "screens": [fp],
        "fingerprint": fp,
        "iconCount": attrs.count,
        "isAuto": isAuto,
        "itemAttrs": attrs,
        "desktopSettings": desktopSettings,
        "itemVSpacing": conf["itemVSpacing"] ?? 0,
        "showItemBriefInfo": conf["showItemBriefInfo"] ?? true,
    ]
    try? FileManager.default.createDirectory(atPath: layoutDir, withIntermediateDirectories: true)
    let url = URL(fileURLWithPath: layoutDir).appendingPathComponent("\(finalName).json")
    guard let data = try? JSONSerialization.data(withJSONObject: snapshot, options: [.prettyPrinted]) else {
        logLine("saveLayout: 序列化失败")
        return
    }
    do {
        try data.write(to: url)
        logLine("saveLayout: 已写入「\(finalName)」(\(attrs.count) 图标)")
    } catch {
        logLine("saveLayout: 写入失败 \(error)")
    }
    currentLayoutName = finalName
    refreshLayoutsAsync()
    if !isAuto { notify("已保存布局「\(name)」（\(fp)）") }
}

func pruneAutoSnapshots() {
    let autos = layoutURLs().compactMap { url -> (URL, Date)? in
        guard let d = FileManager.default.contents(atPath: url.path),
              let s = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              (s["isAuto"] as? Bool) == true,
              (s["name"] as? String)?.hasPrefix(machinePrefix) == true else { return nil }  // 跨机共享目录：只清理本机的自动快照
        let mtime = (try? FileManager.default.attributesOfItem(atPath: url.path)[.modificationDate] as? Date) ?? nil
        return (url, mtime ?? Date.distantPast)
    }
    guard autos.count > maxAutoSnapshots else { return }
    let sorted = autos.sorted { $0.1 < $1.1 }
    for (url, _) in sorted.prefix(autos.count - maxAutoSnapshots) {
        try? FileManager.default.removeItem(at: url)
    }
    refreshLayoutsAsync()
}

// MARK: - 布局恢复（容错铁律：绝不报错刷屏，绝不因文件增删停工）


// MARK: - 时光机（Desktop OK 式自动快照）

/// 计算两组图标属性的差异率（0.0~1.0）：位置变化 + 新增 + 删除，除以总数
func changeRatio(between a: [[String: Any]], and b: [[String: Any]]) -> Double {
    func posMap(_ arr: [[String: Any]]) -> [String: [Int]] {
        var m: [String: [Int]] = [:]
        for item in arr {
            guard let n = item["name"] as? String else { continue }
            m[n] = [item["x"] as? Int ?? -1, item["y"] as? Int ?? -1]
        }
        return m
    }
    let am = posMap(a), bm = posMap(b)
    let all = Set(am.keys).union(bm.keys)
    guard !all.isEmpty else { return 0 }
    var changed = 0
    for n in all {
        switch (am[n], bm[n]) {
        case (.some(let x), .some(let y)): if x != y { changed += 1 }
        default: changed += 1  // 一方缺失 = 新增或删除
        }
    }
    return Double(changed) / Double(all.count)
}

/// 时光机监测：三规则 + 防冗余，决定是否自动存一条新快照
func monitorLayoutChanges() {
    // 已废弃：自动存档属旧「分电脑快照」体系，改为只在手动保存时存档
    return
}

func restoreLayout(at url: URL, restart: Bool = true) {
    guard let snapData = FileManager.default.contents(atPath: url.path),
          let snap = try? JSONSerialization.jsonObject(with: snapData) as? [String: Any],
          let snapAttrs = snap["itemAttrs"] as? [[String: Any]] else {
        logLine("restoreLayout: 布局文件读取失败，静默跳过")
        return
    }
    guard var conf = loadConf(), let curAttrs = conf["itemAttrs"] as? [[String: Any]] else {
        logLine("restoreLayout: 读不到 conf，静默跳过")
        return
    }
    let snapIndex = Dictionary(snapAttrs.map { ($0["name"] as? String ?? "", $0) },
                               uniquingKeysWith: { _, new in new })

    // 跨机应用防错位：目标机只有一块屏时，快照的屏幕编号（本机显示器序号，跨机语义不同）全部归一化为本机屏幕编号
    // 例：MBP 快照含 screen 1/2/3（历史多屏编号），单屏 Mac Studio 不认 2/3，图标会被指到不存在的屏幕导致乱排
    let singleScreenTarget = NSScreen.screens.count == 1
    let localScreenMode: String = {
        let vals = curAttrs.compactMap { $0["screen"].map { "\($0)" } }
        let counts = vals.reduce(into: [String: Int]()) { $0[$1, default: 0] += 1 }
        return counts.max { $0.value < $1.value }?.key ?? "1"
    }()
    var matched = 0, keptNew = 0, screenFixed = 0
    var newAttrs: [[String: Any]] = []
    for item in curAttrs {
        let key = item["name"] as? String ?? ""
        if let s = snapIndex[key] {
            var ni = item
            ni["x"] = s["x"]; ni["y"] = s["y"]
            if singleScreenTarget {
                if let sv = s["screen"], "\(sv)" != localScreenMode { screenFixed += 1 }
                ni["screen"] = localScreenMode
            } else {
                ni["screen"] = s["screen"]
            }
            newAttrs.append(ni)
            matched += 1
        } else {
            newAttrs.append(item)  // 快照之后新增/改名的文件：保留现位置
            keptNew += 1
        }
    }
    conf["itemAttrs"] = newAttrs
    // 全量还原桌面显示设置（图标大小/行列间距/标签位置/显示类别等）；旧快照无该字段则只还原原有两项
    if let ds = snap["desktopSettings"] as? [String: Any] {
        for (k, v) in ds where k != "itemAttrs" { conf[k] = v }
    } else {
        if let sp = snap["itemVSpacing"] { conf["itemVSpacing"] = sp }
        if let sb = snap["showItemBriefInfo"] { conf["showItemBriefInfo"] = sb }
    }
    currentLayoutName = (snap["name"] as? String) ?? url.deletingPathExtension().lastPathComponent

    if restart {
        // 关键竞态修复：QSpace 运行中会周期性用内存覆盖 conf——必须先退出 QSpace
        // 再写 conf，最后启动 QSpace 读取，否则写入会被内存回写覆盖
        appDelegate?.setBusy(true)
        atomicWriteConfAndRestartQSpace(conf)
        microNudge(conf: conf)  // 重启后微拖拽 1px，触发 QSpace snapToGrid 自动纠错
        appDelegate?.setBusy(false)
        appDelegate?.rebuildMenu()
        let fixInfo = screenFixed > 0 ? "，屏幕编号归一 \(screenFixed) 个" : ""
        notify("已恢复「\(currentLayoutName)」：匹配 \(matched) 个, 保留新增 \(keptNew) 个（设置已还原\(fixInfo)）")
    } else {
        writeConf(conf)
    }
}

func autoSwitchIfNeeded() {
    // 已废弃：分电脑快照自动切换已由「中枢统一布局 + 屏幕切套」取代
    return
}

// MARK: - 微拖拽纠错（恢复重启后模拟 1px 拖拽，触发 QSpace snapToGrid 自动对齐）

func microNudge(conf: [String: Any]) {
    guard microNudgeOn else { logLine("nudge: 已关闭,跳过"); return }
    guard AXIsProcessTrusted() else {
        // 防弹框轰炸：每 6 小时最多触发一次系统授权弹窗+设置面板，其余时间只写日志跳过
        let now = Date().timeIntervalSince1970
        let last = UserDefaults.standard.double(forKey: "nudgePromptedAt")
        if now - last > 6 * 3600 {
            UserDefaults.standard.set(now, forKey: "nudgePromptedAt")
            let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(opts)
            DispatchQueue.main.async {
                subprocessRun(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
            }
            notify("微拖拽需一次性授权：系统设置→隐私与安全性→辅助功能→勾选 QSpaceLayoutBar")
        }
        logLine("nudge: 未授权,跳过(授权后需重启工具生效)")
        return
    }
    guard let attrs = conf["itemAttrs"] as? [[String: Any]],
          let mainScreen = NSScreen.screens.first else { return }
    // 只选主屏内、远离边缘的图标（CGEvent 与 QSpace 的逻辑点坐标同为左上原点）
    let mw = Double(mainScreen.frame.width), mh = Double(mainScreen.frame.height)
    var target: (x: Double, y: Double)?
    for a in attrs {
        guard let x = a["x"] as? Int, let y = a["y"] as? Int else { continue }
        let dx = Double(x), dy = Double(y)
        if dx > 16, dy > 48, dx < mw - 120, dy < mh - 120 { target = (dx, dy); break }
    }
    guard let t = target else { logLine("nudge: 主屏内无可用图标,跳过"); return }
    Thread.sleep(forTimeInterval: 2.0)  // 等 QSpace 渲染稳定
    let p = CGPoint(x: t.x + 8, y: t.y + 8)  // 96px 图标左上角 +8 必落在图标内
    let src = CGEventSource(stateID: .hidSystemState)
    func post(_ type: CGEventType, _ pt: CGPoint) {
        if let e = CGEvent(mouseEventSource: src, mouseType: type, mouseCursorPosition: pt, mouseButton: .left) {
            e.post(tap: .cghidEventTap)
        }
    }
    post(.mouseMoved, p)
    Thread.sleep(forTimeInterval: 0.2)
    post(.leftMouseDown, p)
    Thread.sleep(forTimeInterval: 0.15)
    post(.leftMouseDragged, CGPoint(x: p.x + 1, y: p.y))
    Thread.sleep(forTimeInterval: 0.15)
    post(.leftMouseDragged, p)  // 拖回原位再松手：位置不变，只触发对齐纠错
    Thread.sleep(forTimeInterval: 0.15)
    post(.leftMouseUp, p)
    logLine("nudge: 在 (\(Int(t.x)),\(Int(t.y))) 完成 1px 微拖拽纠错")
}

// MARK: - 合盖/开盖绑定应用

func applyBoundLayout(kind: String, restart: Bool) {
    // 已废弃：合盖/开盖绑定快照已由「自动识别当前套(A/B/C)」取代
    return
}

// MARK: - 开机自启（launchd agent，不依赖签名身份）

func loginItemEnabled() -> Bool {
    // 双重验证：plist 存在 + launchctl 真的在册（防止状态不同步导致开关反转）
    guard FileManager.default.fileExists(atPath: launchAgentPlist) else { return false }
    let out = subprocessOutput(["launchctl", "list"])
    return out.contains("com.jon1131120.qspacelayoutbar")
}

func setLoginItem(_ on: Bool) {
    let binPath = "/Applications/QSpaceLayoutBar.app/Contents/MacOS/QSpaceLayoutBar"
    if on {
        let plist: [String: Any] = [
            "Label": "com.jon1131120.qspacelayoutbar",
            "ProgramArguments": [binPath],
            "RunAtLoad": true,
        ]
        guard let data = try? PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0) else {
            alert("开机自启：序列化 plist 失败"); return
        }
        do {
            try data.write(to: URL(fileURLWithPath: launchAgentPlist))
        } catch {
            alert("开机自启：写入 plist 失败\n\(error)"); return
        }
        subprocessRun(["launchctl", "unload", launchAgentPlist])
        subprocessRun(["launchctl", "load", launchAgentPlist])
        if !loginItemEnabled() { alert("开机自启：launchctl 加载后状态异常，请检查") }
    } else {
        subprocessRun(["launchctl", "unload", launchAgentPlist])
        try? FileManager.default.removeItem(atPath: launchAgentPlist)
    }
}

func subprocessRun(_ args: [String]) {
    _ = try? Process.run(URL(fileURLWithPath: "/usr/bin/env"), arguments: args)
}

func subprocessOutput(_ args: [String]) -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/bin/sh")
    p.arguments = ["-c", "/usr/bin/env " + args.joined(separator: " ") + " 2>/dev/null"]
    let pipe = Pipe()
    p.standardOutput = pipe
    do { try p.run() } catch { return "" }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    return String(data: data, encoding: .utf8) ?? ""
}

// MARK: - 网格映射引擎 v3（2026-11-04，杰哥 40 点标定验证：120/120 吻合、六方向往返 0 漂移）
//
// 三套网格：A = Air 21×9；B = MBP 内屏 19×9；C = Studio / MBP 外接 22×9
// 大白话规则：
//   1. 贴着组件右边缘的图标 → 相对组件右缘位置不变
//   2. 贴着屏幕右边缘的图标 → 相对右边缘距离不变（距右 ≤5 格优先）
//   3. 中间的图标 → 按两侧宽度差线性均摊
//   4. 行：组件行带内、且非右锚段的图标跟着组件高度差平移（A/B 行5-7 ↔ C 行4-6）
//   5. 落点撞禁区（组件区 + 自定义禁区）→ 同行向右推、行末向下推，永不落禁区
// 三套屏幕参数（写死：每套 = 网格 + 禁区 + 像素原点/步长 + QSpace 显示设置）
// 2026-11-04 实测定稿：A=Air 内置，B=MBP 内置，C=外接屏/Studio（2560×1440）
struct GridSpec {
    let key: String
    let cols: Int
    let rows: Int
    let topRight: Int       // 上排组件右缘列
    let topRows: Int        // 上排组件行数
    let armRight: Int       // 下排组件右缘列
    let zone2: (Int, Int)   // 组件行带（行区间）
    let floorStart: Int     // 地板起始行
    // 固定像素参数（实测）
    let x0: Double
    let y0: Double
    let colStep: Double
    let rowStep: Double
    // 固定 QSpace 显示设置（每套屏幕专用）
    let lod: Int
    let hSp: Int
    let font: Int
}

let SPEC_A = GridSpec(key: "A", cols: 21, rows: 9, topRight: 9, topRows: 4, armRight: 4, zone2: (5, 7), floorStart: 8,
                       x0: 80, y0: 44, colStep: 79, rowStep: 114, lod: 64, hSp: 11, font: 11)
let SPEC_B = GridSpec(key: "B", cols: 19, rows: 9, topRight: 8, topRows: 4, armRight: 4, zone2: (5, 7), floorStart: 8,
                       x0: 53, y0: 43, colStep: 90, rowStep: 114, lod: 64, hSp: 22, font: 11)
let SPEC_C = GridSpec(key: "C", cols: 22, rows: 9, topRight: 6, topRows: 3, armRight: 3, zone2: (4, 6), floorStart: 7,
                       x0: 120, y0: 40, colStep: 113, rowStep: 148, lod: 96, hSp: 11, font: 11)
let SPEC_ALL: [String: GridSpec] = ["A": SPEC_A, "B": SPEC_B, "C": SPEC_C]

/// 该套对应的 QSpace 显示设置（应用布局时一起写，保证参数随屏切换）
func specSettings(_ s: GridSpec) -> [String: Any] {
    ["lod": s.lod, "itemHSpacing": s.hSp, "itemVSpacing": 0, "fontSize": s.font]
}

// 杰哥 2026-11-04 追加的自定义禁区（列,行）
let customBlocked: [String: [(Int, Int)]] = [
    "A": [(10, 4), (11, 4), (12, 4)],
    "B": [(9, 4), (10, 4), (11, 4), (12, 4)],
    "C": [(1, 6), (2, 6), (3, 6), (1, 7), (2, 7), (3, 7), (4, 7)],
]

func leftBase(_ s: GridSpec, _ r: Int) -> Int {
    if r <= s.topRows { return s.topRight }
    if r >= s.zone2.0 && r <= s.zone2.1 { return s.armRight }
    return 0
}

func cellBlocked(_ s: GridSpec, _ c: Int, _ r: Int) -> Bool {
    if c < 1 || c > s.cols || r < 1 || r > s.rows { return true }
    if c <= s.topRight && r <= s.topRows { return true }
    if c <= s.armRight && r >= s.zone2.0 && r <= s.zone2.1 { return true }
    if (customBlocked[s.key] ?? []).contains(where: { $0.0 == c && $0.1 == r }) { return true }
    return false
}

/// 网格映射（纯整数）：源格 → 目标格；落禁区自动推移到最近可用格
func mapCell(from s: GridSpec, to d: GridSpec, col: Int, row: Int) -> (Int, Int) {
    let Ls = leftBase(s, row)
    let Ws = s.cols - Ls
    let o = col - Ls
    let rightAnchor = (Ws - o) <= 5
    var dr = row
    if row >= s.zone2.0 && row <= s.zone2.1 && !rightAnchor {
        dr = row + (d.zone2.0 - s.zone2.0)
    }
    dr = max(1, min(d.rows, dr))
    let Ld = leftBase(d, dr)
    let Wd = d.cols - Ld
    var dc: Int
    if rightAnchor {
        dc = Ld + Wd - (Ws - o)
    } else if o <= 6 {
        dc = Ld + o
    } else {
        let x0 = 6.0, x1 = Double(Ws - 5), y0 = 6.0, y1 = Double(Wd - 5)
        let t = (Double(o) - x0) / max(1.0, x1 - x0)
        dc = Ld + Int((y0 + t * (y1 - y0)).rounded())
    }
    dc = max(1, min(d.cols, dc))
    return (dc, dr)   // 纯映射；禁区/占用推移在 apply 阶段处理
}

// ---- 40 点标定自检（--selftest 模式跑，不通过则拒绝启动自动同步）----
struct CalPoint { let n: Int; let a: (Int, Int); let b: (Int, Int); let c: (Int, Int) }
let calibrationPoints: [CalPoint] = [
    CalPoint(n: 1,  a: (1, 8),   b: (1, 8),   c: (1, 8)),
    CalPoint(n: 2,  a: (2, 8),   b: (2, 8),   c: (2, 8)),
    CalPoint(n: 3,  a: (3, 8),   b: (3, 8),   c: (3, 8)),
    CalPoint(n: 4,  a: (4, 8),   b: (4, 8),   c: (4, 8)),
    CalPoint(n: 5,  a: (5, 5),   b: (5, 5),   c: (4, 4)),
    CalPoint(n: 6,  a: (5, 6),   b: (5, 6),   c: (4, 5)),
    CalPoint(n: 7,  a: (5, 7),   b: (5, 7),   c: (4, 6)),
    CalPoint(n: 8,  a: (5, 8),   b: (5, 8),   c: (5, 8)),
    CalPoint(n: 9,  a: (6, 7),   b: (6, 7),   c: (5, 6)),
    CalPoint(n: 10, a: (6, 8),   b: (6, 8),   c: (6, 8)),
    CalPoint(n: 11, a: (10, 1),  b: (9, 1),   c: (7, 1)),
    CalPoint(n: 12, a: (10, 2),  b: (9, 2),   c: (7, 2)),
    CalPoint(n: 13, a: (10, 3),  b: (9, 3),   c: (7, 3)),
    CalPoint(n: 14, a: (6, 5),   b: (6, 5),   c: (5, 4)),
    CalPoint(n: 15, a: (7, 5),   b: (7, 5),   c: (6, 4)),
    CalPoint(n: 16, a: (8, 5),   b: (8, 5),   c: (7, 4)),
    CalPoint(n: 17, a: (9, 5),   b: (9, 5),   c: (8, 4)),
    CalPoint(n: 18, a: (10, 5),  b: (10, 5),  c: (9, 4)),
    CalPoint(n: 19, a: (11, 3),  b: (10, 3),  c: (8, 3)),
    CalPoint(n: 20, a: (21, 1),  b: (19, 1),  c: (22, 1)),
    CalPoint(n: 21, a: (21, 2),  b: (19, 2),  c: (22, 2)),
    CalPoint(n: 22, a: (21, 3),  b: (19, 3),  c: (22, 3)),
    CalPoint(n: 23, a: (21, 4),  b: (19, 4),  c: (22, 4)),
    CalPoint(n: 24, a: (21, 5),  b: (19, 5),  c: (22, 5)),
    CalPoint(n: 25, a: (21, 6),  b: (19, 6),  c: (22, 6)),
    CalPoint(n: 26, a: (21, 7),  b: (19, 7),  c: (22, 7)),
    CalPoint(n: 27, a: (21, 8),  b: (19, 8),  c: (22, 8)),
    CalPoint(n: 28, a: (21, 9),  b: (19, 9),  c: (22, 9)),
    CalPoint(n: 29, a: (20, 1),  b: (18, 1),  c: (21, 1)),
    CalPoint(n: 30, a: (20, 5),  b: (18, 5),  c: (21, 5)),
    CalPoint(n: 31, a: (20, 9),  b: (18, 9),  c: (21, 9)),
    CalPoint(n: 32, a: (19, 1),  b: (17, 1),  c: (20, 1)),
    CalPoint(n: 33, a: (19, 4),  b: (17, 4),  c: (20, 4)),
    CalPoint(n: 34, a: (19, 9),  b: (17, 9),  c: (20, 9)),
    CalPoint(n: 35, a: (18, 1),  b: (16, 1),  c: (19, 1)),
    CalPoint(n: 36, a: (18, 9),  b: (16, 9),  c: (19, 9)),
    CalPoint(n: 37, a: (17, 1),  b: (15, 1),  c: (18, 1)),
    CalPoint(n: 38, a: (17, 9),  b: (15, 9),  c: (18, 9)),
    CalPoint(n: 39, a: (16, 1),  b: (14, 1),  c: (17, 1)),
    CalPoint(n: 40, a: (16, 9),  b: (14, 9),  c: (17, 9)),
]

func runMappingSelfTest() -> Bool {
    var total = 0, pass = 0
    let pairs = [("A", "B"), ("A", "C"), ("B", "C")]
    for p in calibrationPoints {
        for (sk, dk) in pairs {
            guard let s = SPEC_ALL[sk], let d = SPEC_ALL[dk] else { continue }
            let src: (Int, Int) = sk == "A" ? p.a : (sk == "B" ? p.b : p.c)
            let dst: (Int, Int) = dk == "B" ? p.b : p.c
            let got = mapCell(from: s, to: d, col: src.0, row: src.1)
            total += 1
            if got == dst { pass += 1 }
            else { logLine("selftest FAIL \(sk)->\(dk) #\(p.n) \(src) -> \(got) expect \(dst)") }
        }
    }
    logLine("selftest: \(pass)/\(total) 吻合")
    return pass == total
}

// MARK: - 快照 v2：网格化（像素 ↔ 格）+ 布局指纹
//
// 像素 ↔ 格换算原则：
//   x0/y0 = 目标屏图标的最小坐标（三台桌面图标集合相同 → 最左图标 = 列1，语义一致）
//   step  = 从实际坐标反推（相邻差的最小值众数），差 >15% 才用实测值
//   保存：col = round((x-x0)/step)+1；应用：x = x0 + (col-1)*step

func fnv1a(_ s: String) -> String {
    var h: UInt64 = 0xcbf2_9ce4_8422_2325
    for b in s.utf8 { h ^= UInt64(b); h = h &* 0x0000_0100_0000_01b3 }
    return String(h, radix: 16)
}

struct ScreenGroup { let screenId: String; let width: Int; let height: Int; let count: Int }

func screenGroups(_ attrs: [[String: Any]]) -> [ScreenGroup] {
    var m: [String: (Int, Int, Int)] = [:]
    for it in attrs {
        let sid = (it["screen"].map { "\($0)" }) ?? "1"
        let w = (it["screenWidth"] as? NSNumber)?.intValue ?? 0
        let h = (it["screenHeight"] as? NSNumber)?.intValue ?? 0
        var e = m[sid] ?? (w, h, 0)
        e.2 += 1
        if e.0 == 0 { e.0 = w }
        if e.1 == 0 { e.1 = h }
        m[sid] = e
    }
    return m.map { ScreenGroup(screenId: $0.key, width: $0.value.0, height: $0.value.1, count: $0.value.2) }
        .sorted {
            if $0.width != $1.width { return $0.width > $1.width }
            return $0.count > $1.count   // 同宽按图标数，避免历史多屏编号残留组抢占
        }
}

func specForSize(_ w: Int, _ h: Int) -> GridSpec? {
    if w == 1710 && h == 1073 { return SPEC_A }   // Air 内置
    if w == 1728 && h == 1084 { return SPEC_B }   // MBP 内置
    if w >= 2400 { return SPEC_C }                 // 2560×1440 外接 / Studio 虚拟屏
    return nil
}

/// 本机当前网格角色：目标屏（多屏取外接）+ 对应 spec
func localGridContext(attrs: [[String: Any]]) -> (screen: ScreenGroup, spec: GridSpec)? {
    // 选「实际承载图标最多」的屏：开盖时图标在内屏 → B 套；合盖只剩外接 → C 套
    // （不能按面积/最宽选，否则双屏态会错用外接参数把内屏图标挤溢出）
    guard let g = screenGroups(attrs).max(by: { $0.count < $1.count }) else { return nil }
    if let s = specForSize(g.width, g.height) { return (g, s) }
    switch machineTag {
    case "Air": return (g, SPEC_A)
    case "Studio": return (g, SPEC_C)
    default:
        let hasBuiltin = NSScreen.screens.contains { s in
            guard let id = s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID else { return false }
            return CGDisplayIsBuiltin(id) != 0
        }
        return (g, (hasBuiltin && NSScreen.screens.count == 1) ? SPEC_B : SPEC_C)
    }
}

/// 单屏判断（不依赖 NSScreen，可在后台线程用）：有效尺寸的屏组只有一种 = 单屏态
/// 注意过滤 width/height=0 的异常组（如 QSpace 偶发的 screen=29 残留），否则会误判多屏
func isSingleScreenConf(_ attrs: [[String: Any]]) -> Bool {
    let groups = screenGroups(attrs).filter { $0.width > 0 && $0.height > 0 }
    return Set(groups.map { "\($0.width)x\($0.height)" }).count <= 1
}

/// 实测校准：x0/y0 = 目标屏最小坐标；step = 相邻差最小值众数（>20px 过滤杂值）
/// 只统计桌面文件（/Volumes/* 卷宗图标不受网格约束，会污染统计）
let desktopRootPath = NSString(string: "~/Desktop/").expandingTildeInPath + "/"
func isDesktopItem(_ it: [String: Any]) -> Bool {
    guard let n = it["name"] as? String else { return false }
    return n.hasPrefix(desktopRootPath)
}

func calibrateGrid(_ attrs: [[String: Any]], screenId: String) -> (x0: Double, y0: Double, stepX: Double, stepY: Double)? {
    let singleScreen = isSingleScreenConf(attrs)
    let items = attrs.filter {
        isDesktopItem($0) && (singleScreen || (($0["screen"].map { "\($0)" }) ?? "1") == screenId)
    }
    let xs = items.compactMap { ($0["x"] as? NSNumber)?.doubleValue }
    let ys = items.compactMap { ($0["y"] as? NSNumber)?.doubleValue }
    guard let minX = xs.min(), let minY = ys.min() else { return nil }
    func infer(_ v: [Double], fallback: Double) -> Double {
        let s = Array(Set(v.map { $0.rounded() })).sorted()
        guard s.count >= 2 else { return fallback }
        let ds = zip(s, s.dropFirst()).map { $1 - $0 }.filter { $0 > 20 }
        guard !ds.isEmpty else { return fallback }
        let mn = ds.min()!
        let cand = ds.filter { $0 <= mn * 1.2 }
        let avg = cand.reduce(0, +) / Double(cand.count)
        return avg > 20 ? avg : fallback
    }
    return (minX, minY, infer(xs, fallback: 90), infer(ys, fallback: 114))
}

/// 生成网格化快照（v2）：只收目标屏图标（单屏机器：所有图标归一，忽略历史 screen 编号）
/// 用写死的 spec 参数换算（不再从坐标反推，消除步长误差）
func makeGridSnapshot(conf: [String: Any]) -> [String: Any]? {
    guard let attrs = conf["itemAttrs"] as? [[String: Any]],
          let ctx = localGridContext(attrs: attrs) else { return nil }
    let spec = ctx.spec
    let singleScreen = isSingleScreenConf(attrs)
    var gridItems: [[String: Any]] = []
    for it in attrs {
        let sid = (it["screen"].map { "\($0)" }) ?? "1"
        guard isDesktopItem(it),
              singleScreen || sid == ctx.screen.screenId,
              let name = it["name"] as? String,
              let x = (it["x"] as? NSNumber)?.doubleValue,
              let y = (it["y"] as? NSNumber)?.doubleValue else { continue }
        let col = Int(((x - spec.x0) / spec.colStep).rounded()) + 1
        let row = Int(((y - spec.y0) / spec.rowStep).rounded()) + 1
        gridItems.append(["name": name,
                          "col": max(1, min(spec.cols, col)),
                          "row": max(1, min(spec.rows, row))])
    }
    let fpStr = gridItems
        .sorted { ($0["name"] as? String ?? "") < ($1["name"] as? String ?? "") }
        .map { "\($0["name"] as? String ?? ""):\($0["col"] as? Int ?? 0),\($0["row"] as? Int ?? 0)" }
        .joined(separator: "|")
    var settings: [String: Any] = [:]
    for (k, v) in conf where k != "itemAttrs" { settings[k] = v }
    return [
        "formatVersion": 2,
        "specKey": ctx.spec.key,
        "screenId": ctx.screen.screenId,
        "screenSize": "\(ctx.screen.width)x\(ctx.screen.height)",
        "grid": ["cols": spec.cols, "rows": spec.rows,
                 "x0": spec.x0, "y0": spec.y0, "colStep": spec.colStep, "rowStep": spec.rowStep],
        "items": gridItems,
        "layoutFingerprint": fnv1a(fpStr),
        "syncAt": Int(Date().timeIntervalSince1970),
        "sourceMachine": machineTag,
        "desktopSettings": settings,
        "iconCount": gridItems.count,
    ]
}

/// 应用网格化快照：源格按映射换算成本机格 → 写像素；同名匹配，未知保留
func applyGridSnapshot(_ snap: [String: Any], to conf: [String: Any]) -> (newConf: [String: Any], matched: Int, kept: Int, moved: Int)? {
    guard let srcKey = snap["specKey"] as? String, let src = SPEC_ALL[srcKey],
          let attrs = conf["itemAttrs"] as? [[String: Any]],
          let ctx = localGridContext(attrs: attrs),
          let gridItems = snap["items"] as? [[String: Any]] else { return nil }
    let dst = ctx.spec
    let singleScreen = isSingleScreenConf(attrs)
    // 本机常用 screen 值（单屏归一化目标）
    let localScreenMode: String = {
        let vals = attrs.compactMap { $0["screen"].map { "\($0)" } }
        let counts = vals.reduce(into: [String: Int]()) { $0[$1, default: 0] += 1 }
        return counts.max { $0.value < $1.value }?.key ?? "1"
    }()
    var byName: [String: (Int, Int)] = [:]
    for g in gridItems {
        guard let n = g["name"] as? String, let c = g["col"] as? Int, let r = g["row"] as? Int else { continue }
        byName[n] = (c, r)
    }
    var out: [[String: Any]] = []
    var matched = 0, kept = 0, moved = 0
    var used = Set<String>()
    for it in attrs {
        var ni = it
        let name = it["name"] as? String ?? ""
        let sid = (it["screen"].map { "\($0)" }) ?? "1"
        guard isDesktopItem(it), (singleScreen || sid == ctx.screen.screenId), let (c, r) = byName[name] else {
            out.append(ni)
            kept += 1
            continue
        }
        if singleScreen { ni["screen"] = localScreenMode }
        let ox = (it["x"] as? NSNumber)?.intValue ?? -1
        let oy = (it["y"] as? NSNumber)?.intValue ?? -1
        let oc = ox >= 0 ? Int(((Double(ox) - dst.x0) / dst.colStep).rounded()) + 1 : -1
        let orow = oy >= 0 ? Int(((Double(oy) - dst.y0) / dst.rowStep).rounded()) + 1 : -1
        var (dc, dr) = mapCell(from: src, to: dst, col: c, row: r)
        if ox >= 0 && oc == dc && orow == dr {
            // 原格 == 计算格：原位不动（自定义禁区标定误差不应推移既有图标）
            used.insert("\(dc),\(dr)")
            ni["x"] = ox; ni["y"] = oy
        } else {
            // 目标格被占用 / 撞禁区 → 行内右推，行末下行
            var g = 0
            while used.contains("\(dc),\(dr)") || cellBlocked(dst, dc, dr) {
                dc += 1
                if dc > dst.cols { dc = 1; dr += 1 }
                if dr > dst.rows { break }
                g += 1
                if g > 500 { break }
            }
            if dr <= dst.rows {
                used.insert("\(dc),\(dr)")
                let nx = Int((dst.x0 + Double(dc - 1) * dst.colStep).rounded())
                let ny = Int((dst.y0 + Double(dr - 1) * dst.rowStep).rounded())
                ni["x"] = nx; ni["y"] = ny
                if oc != dc || orow != dr {
                    moved += 1
                    if CommandLine.arguments.contains("--snapshottest") {
                        print("MOVE \(name) (\(oc),\(orow)) -> (\(dc),\(dr))  px(\(ox),\(oy)) -> (\(nx),\(ny))")
                    }
                }
            }
        }
        matched += 1
        out.append(ni)
    }
    var nc = conf
    nc["itemAttrs"] = out
    // 参数随套应用：目标屏该用哪套就写哪套（开合盖自动切换，根治"合盖设置套到内屏溢出"）
    for (k, v) in specSettings(dst) { nc[k] = v }
    return (nc, matched, kept, moved)
}

// MARK: - 布局同步中枢客户端（MINIPC 常开，HTTP 18878）
// 地址优先级：局域网 → ZeroTier（MBP/Air 带出门时只走 ZT，自动回退）
// 直连 HTTP：首次访问会由系统弹「允许访问本地网络」窗口（无需进系统设置手动开）
let hubCandidates = [
    "http://192.168.113.11:18878",   // 局域网（MINIPC 有线）
    "http://192.168.191.9:18878"     // ZeroTier（异地/带出门）
]
let hubLayoutPath = "/layout"
let hubLock = NSLock()
var hubActiveBase: String? = nil

func hubBases() -> [String] {
    hubLock.lock(); defer { hubLock.unlock() }
    guard let active = hubActiveBase else { return hubCandidates }
    return [active] + hubCandidates.filter { $0 != active }
}

func markHubBase(_ base: String) {
    hubLock.lock()
    let changed = (hubActiveBase != base)
    hubActiveBase = base
    hubLock.unlock()
    if changed { logLine("hub: 使用地址 \(base)") }
}

func httpGetJSON(_ urlStr: String, timeout: TimeInterval = 3) -> [String: Any]? {
    guard let u = URL(string: urlStr) else { return nil }
    var req = URLRequest(url: u, timeoutInterval: timeout)
    req.httpMethod = "GET"
    let sem = DispatchSemaphore(value: 0)
    var result: [String: Any]?
    URLSession.shared.dataTask(with: req) { data, _, _ in
        if let d = data, let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] { result = o }
        sem.signal()
    }.resume()
    _ = sem.wait(timeout: .now() + timeout + 2)
    return result
}

func httpPostJSON(_ urlStr: String, body: [String: Any], timeout: TimeInterval = 3) -> [String: Any]? {
    guard let u = URL(string: urlStr),
          let bd = try? JSONSerialization.data(withJSONObject: body) else { return nil }
    var req = URLRequest(url: u, timeoutInterval: timeout)
    req.httpMethod = "POST"
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = bd
    let sem = DispatchSemaphore(value: 0)
    var result: [String: Any]?
    URLSession.shared.dataTask(with: req) { data, _, _ in
        if let d = data, let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] { result = o }
        sem.signal()
    }.resume()
    _ = sem.wait(timeout: .now() + timeout + 2)
    return result
}

/// 从中枢拉取（LAN 失败自动切 ZT）
func hubGet(_ path: String = hubLayoutPath) -> [String: Any]? {
    let bases = hubBases()
    for base in bases {
        if let o = httpGetJSON(base + path) {
            markHubBase(base)
            return o
        }
    }
    if bases.count > 1 { logLine("hub: 所有地址均不可达（LAN + ZT）") }
    return nil
}

/// 上报中枢（LAN 失败自动切 ZT）
func hubPost(_ path: String = hubLayoutPath, body: [String: Any]) -> [String: Any]? {
    let bases = hubBases()
    for base in bases {
        if let o = httpPostJSON(base + path, body: body) {
            markHubBase(base)
            return o
        }
    }
    if bases.count > 1 { logLine("hub: 上报失败（LAN + ZT 均不可达）") }
    return nil
}

// MARK: - 布局自动同步引擎（中枢模式：三台对等，后改赢）
let syncStatePath = NSString(string: "~/qspace-layouts/sync-state.json").expandingTildeInPath
var autoSyncOn = UserDefaults.standard.object(forKey: "autoSyncOff") == nil
var syncState: [String: Any] = [:]
let syncQueue = DispatchQueue(label: "qslb.sync")

func loadSyncState() -> [String: Any] {
    guard let d = FileManager.default.contents(atPath: syncStatePath),
          let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return [:] }
    return o
}

func saveSyncState(_ s: [String: Any]) {
    let dir = NSString(string: "~/qspace-layouts").expandingTildeInPath
    try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
    if let d = try? JSONSerialization.data(withJSONObject: s, options: [.prettyPrinted]) {
        try? d.write(to: URL(fileURLWithPath: syncStatePath))
    }
}

/// 上报本机布局到中枢（后改赢；被拒说明中枢有更新的版本，下一轮拉取）
func broadcastIfNeeded() {
    guard autoSyncOn else { return }
    guard let conf = loadConf(), var snap = makeGridSnapshot(conf: conf) else { return }
    let fp = snap["layoutFingerprint"] as? String ?? ""
    guard !fp.isEmpty else { return }
    let now = Int(Date().timeIntervalSince1970)
    var st = loadSyncState()
    let baseline = st["baselineFingerprint"] as? String ?? ""
    let lastBC = st["lastBroadcastFingerprint"] as? String ?? ""

    // ① 只在指纹「真正变化的那一刻」刷新计时：连续拖动时每次变化都重置，停手满 10 秒才上报。
    //    绝不能每轮无条件刷新（fp 会持续 != baseline，那样永远等不到 10 秒，实测导致永不同步）
    if fp != lastSeenFp {
        lastSeenFp = fp
        if now - specChangedAt >= 20 { lastLocalChangeAt = now }   // 切套冷静期内的变化不算用户拖动
    }

    if fp == baseline { return }
    if fp == lastBC { return }

    // ② 切套冷静期：屏幕刚变（开合盖/插拔外接），本机 fp 变化不是用户改布局 —— 绝不上报，
    //    否则会把"按新套反推的歪坐标"当成真源覆盖中枢（实测导致开合盖后不生效）
    if now - specChangedAt < 20 { return }

    // ③ 拖动防抖：停手不足 10 秒先不上报
    if now - lastLocalChangeAt < 10 { return }

    // ④ 刚应用过远端，给 15 秒忽略期
    if now - (st["lastAppliedAt"] as? Int ?? 0) < 15 { return }

    // ⑤ 网络失败退避
    if now < hubNextRetryAt { return }

    // ⑥ 风暴保护
    let recent = (st["recentBroadcasts"] as? [Int] ?? []).filter { now - $0 < 60 }
    if recent.count >= 8 { logLine("sync: 风暴保护，暂停上报"); return }

    snap["syncAt"] = now
    snap["sourceMachine"] = machineTag
    let items = (snap["items"] as? [[String: Any]])?.count ?? 0
    let resp = hubPost(body: snap)

    guard let resp = resp else {
        // 网络失败：不更新 baseline，下轮自动重试（指数退避 10/20/40/60 秒）
        hubFailStreak += 1
        let wait = min(60, 10 << min(3, hubFailStreak - 1))
        hubNextRetryAt = now + wait
        logLine("sync: 上报失败（网络不可达），\(wait) 秒后重试（连续 \(hubFailStreak) 次）")
        return
    }
    hubFailStreak = 0
    hubNextRetryAt = 0

    let accepted = (resp["accepted"] as? Bool) ?? false
    if accepted {
        logLine("sync: 上报中枢 ✅ 接受 fp=\(fp) items=\(items)")
        st["baselineFingerprint"] = fp
        st["lastBroadcastFingerprint"] = fp
        st["lastBroadcastAt"] = now
        st["recentBroadcasts"] = recent + [now]
    } else {
        logLine("sync: 上报被拒（中枢有更新版本 \(resp["latestSyncAt"] ?? "?")），等下一轮拉取")
        st["baselineFingerprint"] = fp   // 已知道中枢更新 → 交给拉取
    }
    saveSyncState(st)
}

/// 从中枢拉取最新布局并应用（主线程）
/// 后台线程：抓取中枢最新（网络操作不占主线程，ZT 慢时也不卡菜单）
func fetchRemote() -> [String: Any]? {
    guard autoSyncOn else { return nil }
    if let remote = hubGet() { return remote }
    // 网络失败退避
    let now = Int(Date().timeIntervalSince1970)
    if now >= hubNextRetryAt {
        hubFailStreak += 1
        let wait = min(60, 10 << min(3, hubFailStreak - 1))
        hubNextRetryAt = now + wait
        logLine("sync: 拉取失败（网络不可达），\(wait) 秒后重试（连续 \(hubFailStreak) 次）")
    }
    return nil
}

/// 主线程：按指纹/时间戳判断是否需要应用，需要则应用
func applyRemoteIfNewer(_ remote: [String: Any]) {
    guard autoSyncOn else { return }
    guard let at = remote["syncAt"] as? Int,
          let fp = remote["layoutFingerprint"] as? String,
          let items = remote["items"] as? [[String: Any]], !items.isEmpty else { return }
    let st = loadSyncState()
    let appliedAt = st["lastAppliedAt"] as? Int ?? 0
    let lastFp = st["lastAppliedFingerprint"] as? String ?? ""
    if fp == lastFp && !forceApplyOnce { return }
    if at <= appliedAt && !forceApplyOnce { return }
    // 中枢就是本机布局 —— 但切套后必须重算（不同套的像素映射不同），不能跳
    if !forceApplyOnce, let conf = loadConf(), let local = makeGridSnapshot(conf: conf),
       (local["layoutFingerprint"] as? String) == fp {
        return
    }
    let forced = forceApplyOnce
    forceApplyOnce = false
    logLine("sync: 拉取中枢 syncAt=\(at) source=\(remote["sourceMachine"] ?? "?") items=\(items.count) fp=\(fp)\(forced ? " [切套强制]" : "")")
    applyRemote(remote, fp: fp, file: "hub")
}

var applyingRemote = false

func applyRemote(_ snap: [String: Any], fp: String, file: String) {
    guard !applyingRemote else { return }
    guard let conf = loadConf(), let r = applyGridSnapshot(snap, to: conf) else {
        logLine("sync: 应用失败（applyGridSnapshot 返回 nil）")
        return
    }
    applyingRemote = true
    appDelegate?.setBusy(true)
    let src = snap["sourceMachine"] as? String ?? file
    var dstKey = "?"
    if let c0 = loadConf(), let a0 = c0["itemAttrs"] as? [[String: Any]], let ctx0 = localGridContext(attrs: a0) {
        dstKey = ctx0.spec.key
    }
    logLine("sync: 应用 source=\(src) 套 \(snap["specKey"] ?? "?")→\(dstKey) matched=\(r.matched) moved=\(r.moved) kept=\(r.kept)")
    atomicWriteConfAndRestartQSpace(r.newConf)
    microNudge(conf: r.newConf)
    let v = verifyAfterApply(snap)
    logLine("sync: 校验 ok=\(v.ok) checked=\(v.checked) bad=\(v.bad) total=\(v.total)")
    var st = loadSyncState()
    st["lastAppliedFingerprint"] = fp
    st["lastAppliedAt"] = Int(Date().timeIntervalSince1970)
    st["lastVerify"] = ["ok": v.ok, "checked": v.checked, "bad": v.bad, "total": v.total,
                        "at": Int(Date().timeIntervalSince1970), "source": src]
    if let c2 = loadConf(), let s2 = makeGridSnapshot(conf: c2) {
        st["baselineFingerprint"] = s2["layoutFingerprint"] as? String ?? ""
    }
    saveSyncState(st)
    applyingRemote = false
    appDelegate?.setBusy(false)
    appDelegate?.rebuildMenu()
    notify(v.ok
        ? "已同步「\(src)」布局（\(r.moved) 个归位，校验通过 \(v.checked) 个）"
        : "⚠️ 同步「\(src)」完成但校验不通过：\(v.bad)/\(v.total)（详见日志）")
}

/// 应用后校验：①格合法（不越界/不进禁区）②与纯映射期望偏差在容差内（禁区/冲突推移视为合理）
func verifyAfterApply(_ snap: [String: Any]) -> (ok: Bool, checked: Int, bad: Int, total: Int) {
    guard let srcKey = snap["specKey"] as? String, let src = SPEC_ALL[srcKey],
          let conf = loadConf(), let actual = makeGridSnapshot(conf: conf),
          let dstKey = actual["specKey"] as? String, let dst = SPEC_ALL[dstKey],
          let sitems = snap["items"] as? [[String: Any]],
          let aitems = actual["items"] as? [[String: Any]] else { return (false, 0, 0, 0) }
    var amap: [String: (Int, Int)] = [:]
    for a in aitems {
        if let n = a["name"] as? String, let c = a["col"] as? Int, let r = a["row"] as? Int { amap[n] = (c, r) }
    }
    var checked = 0, bad = 0
    for s in sitems {
        guard let n = s["name"] as? String, let c = s["col"] as? Int, let r = s["row"] as? Int else { continue }
        guard let got = amap[n] else { continue }   // 本机还没这个文件，不判错
        checked += 1
        if cellBlocked(dst, got.0, got.1) { bad += 1; continue }
        let (ec, er) = mapCell(from: src, to: dst, col: c, row: r)
        let dc = got.0 - ec, dr = got.1 - er
        let rowSame = dr == 0 && dc >= 0 && dc <= 12
        let colSame = dc == 0 && dr >= 0 && dr <= 3
        if !rowSame && !colSame { bad += 1 }
    }
    return (bad == 0, checked, bad, sitems.count)
}

/// 屏幕状态（开合盖/切屏）变化 → 用新套参数强制重应用中枢最新布局
var lastSpecKey = ""

func checkSpecChange() {
    guard let conf = loadConf(), let attrs = conf["itemAttrs"] as? [[String: Any]],
          let ctx = localGridContext(attrs: attrs) else { return }
    let key = ctx.spec.key
    if lastSpecKey.isEmpty { lastSpecKey = key; return }
    if key != lastSpecKey {
        logLine("sync: 屏幕切换 \(lastSpecKey) → \(key)，冷静 20 秒并用新套强制重应用")
        lastSpecKey = key
        specChangedAt = Int(Date().timeIntervalSince1970)
        forceApplyOnce = true
        var st = loadSyncState()
        st.removeValue(forKey: "lastAppliedFingerprint")
        st.removeValue(forKey: "lastAppliedAt")
        // 关键：把"切套后本机 fp"直接记为已知，之后不再当成用户改动上报
        // （否则冷静期 20 秒一过，仍会把按新套反推的坐标当真源覆盖中枢）
        if let c2 = loadConf(), let s2 = makeGridSnapshot(conf: c2) {
            let f = s2["layoutFingerprint"] as? String ?? ""
            st["baselineFingerprint"] = f
            st["lastBroadcastFingerprint"] = f
        }
        saveSyncState(st)
    }
}

// 防抖 / 冷静期 / 重试状态
var lastLocalChangeAt = 0      // 本机布局最后变化时刻（只在 fp 真正变化时刷新）
var lastSeenFp = ""            // 上一轮看到的本机指纹（用于判断"这一刻变了"）
var specChangedAt = 0          // 屏幕/套切换时刻（冷静期内不上报，防止污染中枢）
var forceApplyOnce = false     // 切套后强制重应用一次（跨套像素不同，不能靠指纹跳过）
var hubFailStreak = 0          // 中枢连续失败次数
var hubNextRetryAt = 0         // 下次允许重试的时刻（指数退避）

var syncTickCount = 0

func runSyncCycle() {
    syncTickCount += 1
    syncQueue.async {
        checkSpecChange()
        broadcastIfNeeded()
        let remote = fetchRemote()          // 网络在后台线程
        guard let r = remote else { return }
        DispatchQueue.main.sync { applyRemoteIfNewer(r) }
    }
}

// MARK: - AppDelegate

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var debounceTimer: Timer?
    var busy = false

    func applicationDidFinishLaunching(_ note: Notification) {
        logLine("startup: pid=\(getpid()) hub=\(hubCandidates.joined(separator: " -> ")) machine=\(machineTag)")
        let screenChangeNote = Notification.Name("NSWorkspaceDidChangeScreenParametersNotification")
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: screenChangeNote, object: nil, queue: .main) { [weak self] _ in
            logLine("event: screen parameters changed")
            self?.debounceTimer?.invalidate()
            self?.debounceTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: false) { _ in
                autoSwitchIfNeeded()
            }
        }
        // 轮询兜底：合盖=系统睡眠=进程挂起，屏幕变化通知会错过；唤醒后靠轮询补上
        lastKnownFingerprint = currentFingerprint()
        let poll = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { _ in
            let fp = currentFingerprint()
            if fp != lastKnownFingerprint {
                logLine("poll: fingerprint changed \(lastKnownFingerprint) -> \(fp)")
                lastKnownFingerprint = fp
                autoSwitchIfNeeded()
            }
            refreshLayoutsAsync()  // 顺带后台刷新共享快照目录（别机新快照 30 秒内进入本机视野）
        }
        RunLoop.main.add(poll, forMode: .common)
        // 合盖（睡眠前）：绑定方案则先退出 QSpace 再写 conf（防内存回写）
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { _ in
            applyBoundLayout(kind: "sleep", restart: false)
        }
        // 开盖（唤醒后 4 秒，等系统完成屏幕重构）：绑定方案优先，否则按指纹匹配，然后时光机存档
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
                if UserDefaults.standard.string(forKey: "wakeLayout") != nil {
                    applyBoundLayout(kind: "wake", restart: true)
                } else {
                    autoSwitchIfNeeded()
                }
                monitorLayoutChanges()
                lastKnownFingerprint = currentFingerprint()
            }
        }
        // 布局自动同步：每 3 秒一轮（广播本机 + 拉取其他机器）
        syncState = loadSyncState()
        let syncT = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { _ in runSyncCycle() }
        RunLoop.main.add(syncT, forMode: .common)

        // 时光机监测：启动 20 秒先跑一次，之后每 30 分钟
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { monitorLayoutChanges() }
        let t = Timer.scheduledTimer(withTimeInterval: monitorIntervalSec, repeats: true) { _ in
            monitorLayoutChanges()
        }
        RunLoop.main.add(t, forMode: .common)

        rebuildMenu()          // 先用缓存（可能为空）秒建菜单，绝不阻塞启动
        refreshLayoutsAsync()  // 后台拉取共享目录列表，完成后自动重建菜单
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.remindLegacySnapshots() }
    }

    func setBusy(_ b: Bool) {
        busy = b
        updateStatusIcon()
    }

    func updateStatusIcon() {
        guard let btn = statusItem?.button else { return }
        if busy {
            btn.title = "⏳"
        } else {
            btn.title = "🖥"
            btn.appearsDisabled = !autoSwitchOn
        }
    }

    func buildBindMenu(kind: String, current: String) -> NSMenu {
        let sub = NSMenu()
        let none = NSMenuItem(title: "不绑定", action: #selector(bindClicked(_:)), keyEquivalent: "")
        none.target = self
        none.representedObject = ["kind": kind, "name": ""]
        if current == "未绑定" { none.state = .on }
        sub.addItem(none)
        sub.addItem(.separator())
        for url in layoutURLs() {
            guard let d = FileManager.default.contents(atPath: url.path),
                  let snap = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  let name = snap["name"] as? String else { continue }
            guard name.hasPrefix(machinePrefix) else { continue }  // 合盖/开盖绑定只认本机方案（别机坐标恢复会错位）
            let it = NSMenuItem(title: name, action: #selector(bindClicked(_:)), keyEquivalent: "")
            it.target = self
            it.representedObject = ["kind": kind, "name": name]
            if name == current { it.state = .on }
            sub.addItem(it)
        }
        return sub
    }

    func rebuildMenu() {
        if statusItem == nil {
            statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        }
        updateStatusIcon()
        let menu = NSMenu()

        let cur = NSMenuItem(title: "当前布局：\(currentLayoutName)", action: nil, keyEquivalent: "")
        cur.isEnabled = false
        menu.addItem(cur)
        menu.addItem(.separator())

        // 布局自动同步区（三台互通）
        let syncItem = NSMenuItem(title: "🔄 三台布局自动同步", action: #selector(toggleAutoSync(_:)), keyEquivalent: "")
        syncItem.target = self
        syncItem.state = autoSyncOn ? .on : .off
        menu.addItem(syncItem)
        let syncNow = NSMenuItem(title: "📡 立即同步一次", action: #selector(syncNowClicked(_:)), keyEquivalent: "")
        syncNow.target = self
        menu.addItem(syncNow)
        let stNow = loadSyncState()
        if let at = stNow["lastAppliedAt"] as? Int, at > 0 {
            let df = DateFormatter(); df.dateFormat = "MM-dd HH:mm:ss"
            let info = NSMenuItem(title: "　最近应用：\(df.string(from: Date(timeIntervalSince1970: TimeInterval(at))))", action: nil, keyEquivalent: "")
            info.isEnabled = false
            menu.addItem(info)
        }
        if let bfp = stNow["lastBroadcastFingerprint"] as? String {
            let info = NSMenuItem(title: "　本机指纹：\(bfp.prefix(8))", action: nil, keyEquivalent: "")
            info.isEnabled = false
            menu.addItem(info)
        }
        menu.addItem(.separator())

        // 手动方案（子菜单：应用/重命名/删除）
        for url in layoutURLs() {
            guard let d = FileManager.default.contents(atPath: url.path),
                  let snap = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { continue }
            let name = snap["name"] as? String ?? url.deletingPathExtension().lastPathComponent
            let isAuto = (snap["isAuto"] as? Bool) == true
            if isAuto { continue }

            let it = NSMenuItem(title: name, action: nil, keyEquivalent: "")
            if name == currentLayoutName { it.state = .on }

            let sub = NSMenu()
            let apply = NSMenuItem(title: "⭐ 应用这个方案", action: #selector(restoreClicked(_:)), keyEquivalent: "")
            apply.target = self; apply.representedObject = url.path
            let ren = NSMenuItem(title: "重命名…", action: #selector(renameClicked(_:)), keyEquivalent: "")
            ren.target = self; ren.representedObject = url.path
            let del = NSMenuItem(title: "删除", action: #selector(deleteClicked(_:)), keyEquivalent: "")
            del.target = self; del.representedObject = url.path
            sub.addItem(apply); sub.addItem(.separator()); sub.addItem(ren); sub.addItem(del)
            it.submenu = sub
            menu.addItem(it)
        }

        // 自动备份区（直接点击快照名即应用——本来就是二级菜单，一步到位）
        let autos = layoutURLs().filter {
            guard let d = FileManager.default.contents(atPath: $0.path),
                  let s = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return false }
            return (s["isAuto"] as? Bool) == true
        }
        if !autos.isEmpty {
            let head = NSMenuItem(title: "🤖 自动备份（最近 \(autos.count) 个，点击即应用）", action: nil, keyEquivalent: "")
            let sub = NSMenu()
            for url in autos {
                guard let d = FileManager.default.contents(atPath: url.path),
                      let snap = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { continue }
                let name = snap["name"] as? String ?? "自动"
                let it = NSMenuItem(title: name, action: #selector(restoreClicked(_:)), keyEquivalent: "")
                it.target = self
                it.representedObject = url.path
                if name == currentLayoutName { it.state = .on }
                sub.addItem(it)
            }
            head.submenu = sub
            menu.addItem(head)
        }

        menu.addItem(.separator())

        let saveIt = NSMenuItem(title: "💾 保存当前为布局…", action: #selector(saveClicked(_:)), keyEquivalent: "s")
        saveIt.target = self
        menu.addItem(saveIt)

        let autoIt = NSMenuItem(title: "⚡️ 自动切换（屏幕变化时）", action: #selector(toggleAuto(_:)), keyEquivalent: "")
        autoIt.target = self
        autoIt.state = autoSwitchOn ? .on : .off
        menu.addItem(autoIt)

        let sleepBound = UserDefaults.standard.string(forKey: "sleepLayout") ?? "未绑定"
        let sleepIt = NSMenuItem(title: "🌙 合盖方案：\(sleepBound)", action: nil, keyEquivalent: "")
        sleepIt.submenu = buildBindMenu(kind: "sleep", current: sleepBound)
        menu.addItem(sleepIt)

        let wakeBound = UserDefaults.standard.string(forKey: "wakeLayout") ?? "未绑定"
        let wakeIt = NSMenuItem(title: "☀️ 开盖方案：\(wakeBound)", action: nil, keyEquivalent: "")
        wakeIt.submenu = buildBindMenu(kind: "wake", current: wakeBound)
        menu.addItem(wakeIt)

        let loginIt = NSMenuItem(title: "🚀 开机自启", action: #selector(toggleLoginItem(_:)), keyEquivalent: "")
        loginIt.target = self
        loginIt.state = loginItemEnabled() ? .on : .off
        menu.addItem(loginIt)

        let monitorIt = NSMenuItem(title: "⏱ 时光机监测（每 30 分钟）", action: #selector(toggleMonitor(_:)), keyEquivalent: "")
        monitorIt.target = self
        monitorIt.state = autoMonitorOn ? .on : .off
        menu.addItem(monitorIt)

        let nowIt = NSMenuItem(title: "⏱ 立即监测一次", action: #selector(monitorNow(_:)), keyEquivalent: "")
        nowIt.target = self
        menu.addItem(nowIt)

        let nudgeIt = NSMenuItem(title: "🔧 恢复后微拖拽纠错", action: #selector(toggleNudge(_:)), keyEquivalent: "")
        nudgeIt.target = self
        nudgeIt.state = microNudgeOn ? .on : .off
        menu.addItem(nudgeIt)

        menu.addItem(.separator())
        let quitIt = NSMenuItem(title: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quitIt)

        menu.autoenablesItems = false
        statusItem.menu = menu
    }

    // MARK: 动作

    @objc func toggleAutoSync(_ sender: NSMenuItem) {
        autoSyncOn = !autoSyncOn
        UserDefaults.standard.set(!autoSyncOn, forKey: "autoSyncOff")
        logLine("sync: 自动同步 \(autoSyncOn ? "开" : "关")")
        rebuildMenu()
        if autoSyncOn { syncState = loadSyncState(); runSyncCycle() }
    }

    @objc func syncNowClicked(_ sender: NSMenuItem) {
        syncState = loadSyncState()
        runSyncCycle()
        notify("已触发一次同步（8 秒内完成）")
    }

    @objc func restoreClicked(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        restoreLayout(at: URL(fileURLWithPath: path))
    }

    @objc func saveClicked(_ sender: NSMenuItem) {
        promptName(title: "保存当前桌面布局", info: "给这套图标摆法起个名字（如：内置屏 / 外接屏 / 双屏）：") { name in
            saveLayout(named: name)
            notify("已保存布局「\(name)」（\(currentFingerprint())）")
        }
    }

    @objc func renameClicked(_ sender: NSMenuItem) {
        guard let oldPath = sender.representedObject as? String,
              let d = FileManager.default.contents(atPath: oldPath),
              var snap = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              let oldName = snap["name"] as? String else { return }
        promptName(title: "重命名布局", info: "「\(oldName)」的新名字：", preset: oldName) { newName in
            let finalName = newName.hasPrefix("[") ? newName : machinePrefix + newName  // 保留机器归属前缀
            snap["name"] = finalName
            if let data = try? JSONSerialization.data(withJSONObject: snap, options: [.prettyPrinted]) {
                try? data.write(to: URL(fileURLWithPath: oldPath))
                let newPath = URL(fileURLWithPath: layoutDir).appendingPathComponent("\(finalName).json").path
                if oldPath != newPath {
                    try? FileManager.default.removeItem(atPath: oldPath)
                }
                if currentLayoutName == oldName { currentLayoutName = finalName }
                refreshLayoutsAsync()
            }
        }
    }

    @objc func deleteClicked(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        try? FileManager.default.removeItem(atPath: path)
        refreshLayoutsAsync()
    }

    @objc func toggleAuto(_ sender: NSMenuItem) {
        autoSwitchOn.toggle()
        UserDefaults.standard.set(autoSwitchOn, forKey: "autoSwitchOn")
        rebuildMenu()
    }

    @objc func toggleLoginItem(_ sender: NSMenuItem) {
        setLoginItem(!loginItemEnabled())
    }

    @objc func toggleMonitor(_ sender: NSMenuItem) {
        autoMonitorOn.toggle()
        UserDefaults.standard.set(!autoMonitorOn, forKey: "autoMonitorOff")
        rebuildMenu()
    }

    @objc func monitorNow(_ sender: NSMenuItem) {
        monitorLayoutChanges()
    }

    @objc func toggleNudge(_ sender: NSMenuItem) {
        microNudgeOn.toggle()
        UserDefaults.standard.set(!microNudgeOn, forKey: "microNudgeOff")
        rebuildMenu()
    }

    @objc func bindClicked(_ sender: NSMenuItem) {
        guard let info = sender.representedObject as? [String: String],
              let kind = info["kind"] else { return }
        let name = info["name"] ?? ""
        let key = kind == "sleep" ? "sleepLayout" : "wakeLayout"
        if name.isEmpty {
            UserDefaults.standard.removeObject(forKey: key)
        } else {
            UserDefaults.standard.set(name, forKey: key)
        }
        rebuildMenu()
    }

    func promptName(title: String, info: String, preset: String = "", completion: @escaping (String) -> Void) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = info
        let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 240, height: 24))
        tf.stringValue = preset
        a.accessoryView = tf
        a.addButton(withTitle: "确定")
        a.addButton(withTitle: "取消")
        a.window.initialFirstResponder = tf
        if a.runModal() == .alertFirstButtonReturn {
            let name = tf.stringValue.trimmingCharacters(in: .whitespaces)
            if !name.isEmpty { completion(name) }
        }
    }

    func remindLegacySnapshots() {
        let legacy = layoutURLs().filter { url -> Bool in
            guard let d = FileManager.default.contents(atPath: url.path),
                  let s = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return false }
            return s["fingerprint"] == nil
        }
        if !legacy.isEmpty {
            notify("发现 \(legacy.count) 个旧布局未绑定屏幕指纹，自动切换不识别——请在对应屏幕状态下用「保存当前为布局…」重新保存一次")
        }
    }
}

// MARK: - 启动

// 自检模式：QSpaceLayoutBar --selftest → 只跑映射自检后退出（不建菜单、不碰 QSpace）
if CommandLine.arguments.contains("--selftest") {
    let ok = runMappingSelfTest()
    print(ok ? "SELFTEST PASS" : "SELFTEST FAIL")
    exit(ok ? 0 : 1)
}

// 拉取调试：--pulldebug 打印中枢当前布局的解析/应用结果
if CommandLine.arguments.contains("--pulldebug") {
    syncState = loadSyncState()
    print("machineTag=\(machineTag) hub=\(hubCandidates.joined(separator: " -> "))")
    print("state:", syncState)
    guard let snap = hubGet(), !snap.isEmpty else {
        print("中枢无布局"); exit(1)
    }
    let its = snap["items"] as? [[String: Any]] ?? []
    print("spec=\(snap["specKey"] ?? "?") src=\(snap["sourceMachine"] ?? "?") fp=\(snap["layoutFingerprint"] ?? "?") at=\(snap["syncAt"] ?? "?") items=\(its.count)")
    guard let conf = loadConf() else { print("loadConf fail"); exit(1) }
    if let r = applyGridSnapshot(snap, to: conf) {
        print("APPLY OK matched=\(r.matched) kept=\(r.kept) moved=\(r.moved)")
    } else {
        print("APPLY NIL")
    }
    exit(0)
}

// 快照 v2 自检：读当前 conf → 生成网格快照 → 应用到自身（同一套 spec 应零移动）
if CommandLine.arguments.contains("--snapshottest") {
    guard let conf = loadConf(), let snap = makeGridSnapshot(conf: conf) else {
        print("SNAPTEST FAIL: 读不到 conf 或生成快照失败")
        exit(1)
    }
    let items = snap["items"] as? [[String: Any]] ?? []
    print("spec=\(snap["specKey"] ?? "?") screen=\(snap["screenSize"] ?? "?") icons=\(items.count) fp=\(snap["layoutFingerprint"] ?? "?")")
    guard let r = applyGridSnapshot(snap, to: conf) else {
        print("SNAPTEST FAIL: 应用失败")
        exit(1)
    }
    print("matched=\(r.matched) kept=\(r.kept) moved=\(r.moved)")
    let ok = r.moved == 0 && r.matched > 0
    print(ok ? "SNAPTEST PASS" : "SNAPTEST FAIL")
    exit(ok ? 0 : 1)
}

let app = NSApplication.shared
let delegate = AppDelegate()
appDelegate = delegate
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
