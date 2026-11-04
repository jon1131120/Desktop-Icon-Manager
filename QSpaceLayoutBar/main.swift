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
    Thread.sleep(forTimeInterval: 2)  // 渲染稳定
    logLine("atomicWrite: done, running=\(qspaceRunning())")
}

func logLine(_ msg: String) {
    let line = "\(Date()): \(msg)\n"
    if let handle = FileHandle(forWritingAtPath: logPath) {
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
    let snapshot: [String: Any] = [
        "name": finalName,
        "savedAt": displayStamp,
        "screenType": screenType,
        "screens": [fp],
        "fingerprint": fp,
        "iconCount": attrs.count,
        "isAuto": isAuto,
        "itemAttrs": attrs,
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
    guard autoMonitorOn else {
        logLine("monitor: off, skip")
        return
    }
    guard let conf = loadConf(), let cur = conf["itemAttrs"] as? [[String: Any]] else {
        logLine("monitor: conf unreadable, skip")
        return
    }

    var maxRatioToAny = 0.0        // 规则②：与所有已知条目的最大差异
    var lastAutoAttrs: [[String: Any]]?
    var lastAutoMtime = Date.distantPast

    for url in layoutURLs() {
        guard let d = FileManager.default.contents(atPath: url.path),
              let s = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              let attrs = s["itemAttrs"] as? [[String: Any]] else { continue }
        // 跨机共享目录：监测/锚点/最近自动快照只统计本机条目（别机布局分辨率不同，永远大差异会误触发存档）
        guard (s["name"] as? String)?.hasPrefix(machinePrefix) == true else { continue }
        let r = changeRatio(between: cur, and: attrs)
        maxRatioToAny = max(maxRatioToAny, r)
        let isAuto = (s["isAuto"] as? Bool) == true
        if isAuto {
            let mt = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            if mt > lastAutoMtime {
                lastAutoMtime = mt
                lastAutoAttrs = attrs
            }
        }
    }

    let ratioToLastAuto = lastAutoAttrs != nil ? changeRatio(between: cur, and: lastAutoAttrs!) : 1.0
    let hoursSince = Date().timeIntervalSince(lastAutoMtime) / 3600.0
    let rule1 = ratioToLastAuto >= changeThreshold                       // 漂移检测：相对上一条大改
    let rule2 = maxRatioToAny >= changeThreshold                         // 离锚检测：偏离所有手动方案锚点
    let rule3 = hoursSince >= minHoursBetweenSnapshots && ratioToLastAuto > 0  // 保底：每天至少一条
    // 防冗余只对比最近一条自动快照，且只压制大改触发；24h 保底不受冗余限制（每天至少一个还原点的承诺）
    let redundant = lastAutoAttrs != nil && ratioToLastAuto < redundantThreshold
    let shouldSave = (rule1 || rule2) && !redundant || rule3

    logLine("monitor: R1(\(rule1)) R2(\(rule2)) R3(\(rule3)) redundant(\(redundant)) -> save=\(shouldSave) lastAuto=\(Int(ratioToLastAuto * 100))% maxAny=\(Int(maxRatioToAny * 100))%")

    if shouldSave {
        saveLayout(named: "", isAuto: true)
        pruneAutoSnapshots()
        notify("⏱ 检测到布局大改（\(Int(ratioToLastAuto * 100))%），已自动存档")
    }
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

    var matched = 0, keptNew = 0
    var newAttrs: [[String: Any]] = []
    for item in curAttrs {
        let key = item["name"] as? String ?? ""
        if let s = snapIndex[key] {
            var ni = item
            ni["x"] = s["x"]; ni["y"] = s["y"]; ni["screen"] = s["screen"]
            newAttrs.append(ni)
            matched += 1
        } else {
            newAttrs.append(item)  // 快照之后新增/改名的文件：保留现位置
            keptNew += 1
        }
    }
    conf["itemAttrs"] = newAttrs
    if let sp = snap["itemVSpacing"] { conf["itemVSpacing"] = sp }
    if let sb = snap["showItemBriefInfo"] { conf["showItemBriefInfo"] = sb }
    currentLayoutName = (snap["name"] as? String) ?? url.deletingPathExtension().lastPathComponent

    if restart {
        // 关键竞态修复：QSpace 运行中会周期性用内存覆盖 conf——必须先退出 QSpace
        // 再写 conf，最后启动 QSpace 读取，否则写入会被内存回写覆盖
        appDelegate?.setBusy(true)
        atomicWriteConfAndRestartQSpace(conf)
        appDelegate?.setBusy(false)
        appDelegate?.rebuildMenu()
        notify("已恢复「\(currentLayoutName)」：匹配 \(matched) 个, 保留新增 \(keptNew) 个")
    } else {
        writeConf(conf)
    }
}

func autoSwitchIfNeeded() {
    guard autoSwitchOn else {
        logLine("autoSwitch: off, skip")
        return
    }
    let fp = currentFingerprint()
    logLine("autoSwitch: fp=\(fp), current=\(currentLayoutName)")
    refreshLayoutsAsync()  // 顺带后台刷新共享目录（不阻塞本次匹配，新同步来的快照下一轮生效）
    for url in layoutURLs() {
        guard let d = FileManager.default.contents(atPath: url.path),
              let snap = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              (snap["fingerprint"] as? String) == fp else { continue }
        let name = snap["name"] as? String ?? ""
        if name != currentLayoutName {
            logLine("autoSwitch: matched「\(name)」-> restoring")
            restoreLayout(at: url)
        } else {
            logLine("autoSwitch: matched「\(name)」== current, no-op")
        }
        return
    }
    logLine("autoSwitch: no layout matched fp=\(fp)")
    currentLayoutName = "（未匹配）"
    appDelegate?.rebuildMenu()
}

// MARK: - 合盖/开盖绑定应用

func applyBoundLayout(kind: String, restart: Bool) {
    guard let name = UserDefaults.standard.string(forKey: kind == "sleep" ? "sleepLayout" : "wakeLayout"),
          !name.isEmpty else { return }
    let url = URL(fileURLWithPath: layoutDir).appendingPathComponent("\(name).json")
    if FileManager.default.fileExists(atPath: url.path) {
        restoreLayout(at: url, restart: restart)
    }
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

// MARK: - AppDelegate

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var debounceTimer: Timer?
    var busy = false

    func applicationDidFinishLaunching(_ note: Notification) {
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

        menu.addItem(.separator())
        let quitIt = NSMenuItem(title: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quitIt)

        menu.autoenablesItems = false
        statusItem.menu = menu
    }

    // MARK: 动作

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

let app = NSApplication.shared
let delegate = AppDelegate()
appDelegate = delegate
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
