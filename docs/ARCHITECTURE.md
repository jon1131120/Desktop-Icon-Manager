# 三 Mac 桌面布局同步 · v5 中枢架构

> 定稿：2026-11-05 凌晨｜实测通过｜取代 v2~v4 全部分机快照/权威机/iCloud 广播设计

## 一、架构

```
        ┌──────────────── MINIPC（常开值班机，不装 QSpace）────────────────┐
        │  ~/qspace-hub/hub.py   :18878   systemd qspace-hub.service       │
        │  layout.json（唯一真源）  archive/<日期>-<机器>.json（保留 20 份）│
        │  hub.log（5MB 轮转 3 份，记录机器名/网络/时间/图标数/接受拒绝）   │
        └──────────────────────────────────────────────────────────────────┘
              ▲ POST /layout（后改赢）        │ GET /layout（每 3 秒）
              │                              ▼
   ┌──────────┴──────────┬───────────────────┬───────────────────┐
   │  MacBook Pro        │  MacBook Air      │  Mac Studio       │
   │  LAN .113.14 / ZT .191.26               │  LAN .113.17 / ZT .191.17
   └─────────────────────┴───────────────────┴───────────────────┘
```

- **完全对等**：三台都能上报、都能拉取；没有权威机。Air 关机不影响 MBP ↔ Studio 互相同步。
- **谁最后改听谁的**：中枢按 `syncAt`（毫秒级）比较，旧的一律 REJECTED 并记日志。
- **双网络自动回退**：先试 LAN `192.168.113.11` → 失败自动切 ZT `192.168.191.9`；成功地址缓存复用（带出门只走 ZT）。

## 二、三套屏幕参数（写死进软件，应用时参数+位置一起写）

| 套 | 屏幕 | 逻辑分辨率 | lod | 列间距 | 行间距 | 字号 | 原点 | 步长 | 网格 | L 形禁区 |
|---|---|---|---|---|---|---|---|---|---|---|
| **A** | Air 内置 | 1710×1073 | 64 | 11 | 0 | 11 | 80,44 | 79/114 | 21×9 | 行1-4×列1-9 + 行5-7×列1-4 |
| **B** | MBP 内置 | 1728×1084 | 64 | **22** | 0 | 11 | 53,43 | **90/114** | 19×9 | 行1-4×列1-8 + 行5-7×列1-4 |
| **C** | 外接 Kuycon / Studio | 2560×1440 | 96 | 11 | 0 | 11 | 120,40 | 113/148 | 22×9 | 行1-3×列1-6 + 行4-5×列1-3 |

**选套规则**：按「**图标数量最多的屏**」判定 ——
- MBP 开盖（双屏，图标在内屏）→ **B**
- MBP 合盖（只剩外接）→ **C**
- Air → A；Studio → C

> 不能按面积/最宽屏选：双屏时外接面积更大，会错用 C 套参数把内屏图标挤溢出（历史 bug）。

**参数随套写盘**：应用布局时把该套的 `lod/itemHSpacing/itemVSpacing/fontSize` 一起写进 `desktop.conf`，彻底解决「QSpace 图标大小/间距是全局设置、不随屏幕切换」导致的溢出。

## 三、布局数据格式（屏幕无关）

中枢只存**格坐标**（第几列第几行），三台通用；每台用自己的套渲染成像素：

```json
{ "sourceMachine": "MBP", "syncAt": 1793811011, "specKey": "B",
  "layoutFingerprint": "fbed5efa9fd4577b",
  "items": [{"name":"/Users/jon/Desktop/xxx","col":12,"row":4}, ...] }
```

应用时：`x = x0 + (col-1)*colStep`，`y = y0 + (row-1)*rowStep`（用目标机自己那套的数值）。

## 四、收敛与防抖

| 机制 | 做法 |
|---|---|
| 应用后不回环 | 应用远端 → `baselineFingerprint` 设为**本地应用后**的指纹 → 下轮不再上报 |
| 当前生效指纹 | `lastAppliedFingerprint` 相同即跳过（不用历史指纹集合，否则 A→B→A 会卡死） |
| 忽略期 | 应用后 15 秒内不上报 |
| 风暴保护 | 60 秒内 ≥8 次上报则暂停 |
| 套变化 | 检测到 A/B/C 切换 → 清除 `lastApplied` → 下一轮强制重应用新套 |

## 五、已彻底删除的旧设计（不要再写回来）

| 删除项 | 原因 |
|---|---|
| `authorityMachine` 权威机单向 | Air 不是 24 小时开机，MBP/Studio 改动没人接 |
| `autoSwitchIfNeeded` 屏幕变化恢复本地快照 | 会用旧快照冲掉同步结果（实测 16/45 被覆盖） |
| `applyBoundLayout` sleep/wake 绑定快照 | 与中枢布局抢改 |
| `monitorLayoutChanges` 自动存档 | 分电脑快照不通用，改为只手动保存 |
| iCloud `sync/` 目录广播 + `[机名] sync.json` | 分钟级，慢；已由中枢秒级取代 |
| SSH `sync-inbox` 直推 / pullFromPeers | 维护成本高，已被 HTTP 中枢取代 |
| 从坐标反推步长 `calibrateGrid` | 反推误差导致「步长 113 vs 90」误判，改为写死 |
| 旧快照 `[MBP]/[Air]/[Studio] *.json` | 已归档到 `~/qspace-layouts/archive-old/` |

## 六、⚠️ 关键坑：macOS 本地网络 TCC

**症状**：app 内 URLSession 访问局域网 IP 静默失败 —— 不发请求、无错误、无日志；同一条命令在终端 curl 完全正常。

**根因**：macOS 15+ 对「本地网络」有 TCC 门禁。由 launchd 启动 + ad-hoc 重新签名的 app，授权记录随 cdhash 变化而失效 → 被静默拦截。

**解法**：
1. `Info.plist` 加 `NSLocalNetworkUsageDescription`（缺了不弹窗、直接拒）
2. 部署后 `tccutil reset LocalNetwork com.jon1131120.qspacelayoutbar` → app 首次访问弹「允许访问本地网络」→ 用户在**桌面点允许**
3. 每次 ad-hoc 重签都会重置 → 长期免授权需自签名证书；但 codesign 非交互访问私钥常报 `errSecInternalComponent`，实操上 ad-hoc + 每次点允许更省事

**诊断法**：`--pulldebug` 在终端能通 + 常驻进程日志完全没有请求 = TCC 拦截。

## 七、实测记录（2026-11-05 00:50）

```
00:50:11  POST /layout  MBP @ MBP/LAN  syncAt=1793811011 items=45 -> ACCEPTED (fp=fbed5efa)
00:50:13  GET  /layout  Air/LAN     <- hub[MBP ...]
00:50:13  GET  /layout  Studio/LAN  <- hub[MBP ...]

Air:    应用 source=MBP spec=B matched=45 moved=1 kept=3   校验 ok=true 45/45
Studio: 应用 source=MBP spec=B matched=45 moved=1 kept=17  校验 ok=true 45/45
```

**从拖动到三台一致 = 2 秒**；ZT 链路三台均 HTTP 200（3~16ms）。

## 八、运维命令

```bash
# 中枢状态 / 日志
ssh minipc 'systemctl status qspace-hub --no-pager | head -5; tail -20 ~/qspace-hub/hub.log'
# 中枢重启
ssh minipc 'sudo -n systemctl restart qspace-hub'
# 归档查看（回滚用）
ssh minipc 'ls -lt ~/qspace-hub/archive | head'
# 客户端日志（三台）
tail -20 ~/qspace-layouts/app.log
ssh air 'tail -20 ~/qspace-layouts/app.log'
ssh studio 'tail -20 ~/qspace-layouts/app.log'
# 双网络连通性
curl -s http://192.168.113.11:18878/status; curl -s http://192.168.191.9:18878/status
```

## 九、相关文件

- 中枢：MINIPC `~/qspace-hub/hub.py`、`~/qspace-hub/layout.json`、`~/qspace-hub/hub.log`、systemd `/etc/systemd/system/qspace-hub.service`
- 客户端：`~/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift`（仓库 commit `67a4e1f`）
- 部署产物：三台 `/Applications/QSpaceLayoutBar.app`，LaunchAgent `com.jon1131120.qspacelayoutbar`
- 状态文件：`~/qspace-layouts/sync-state.json`


## 十、30 轮白纸审计与修复（2026-11-05）

审计对象：main.swift(1576 行) + hub-server.py + 三台部署态 + 网络/权限/持久化。
方法：白纸心态、不沿用旧清单、每项带文件:行号或线上数据。**16 项全部修复，三台自动验证 PASS。**

### 修复的 4 个 P0

| 编号 | 问题 | 根因 | 修复 |
|---|---|---|---|
| P0-1 | **同步永不收敛**（三台互相覆盖） | 应用后 QSpace 重启自行重排 → 指纹变化被误判"用户拖动" → 上报覆盖中枢 → 死循环 | 应用后 **60 秒吸收期**（`suppressBroadcastUntil` 持久化），期内持续把当前指纹写回 baseline 且不上报 |
| P0-2 | **外接屏右手边重叠/歪斜** | ①「原位保留」漏 `!used.contains` → 同格两图标坐标完全相同（实测 6 组）②B 套遗留 90px 列距被固化，C 套图标宽 96 → 压 6px | 像素偏差 ≤3px 才保留原位 + `!used.contains` + 冲突右推/行末下行 + 越界左移 |
| P0-3 | **中枢可被永久锁死** | 无 `syncAt` 上限，未来时间戳被接受后所有正常上报判 older | `at > now+300 → 400` + items 非空 + body ≤1MB + 来源网段白名单 |
| P0-4 | **三套列数超出屏幕物理宽度** | 标定按格数未按"图标宽"校验 | A 21→20 / B 19→18 / C 22→21；`floor((屏宽-x0-lod)/step)+1` 核算；selftest 跳过超界点 |

### 修复的 6 个 P1 + 5 个 P2

- 切套判定：`specForSize` 改容差 ±40，兜底不再用 `NSScreen.screens.count`（双屏会误判 C）
- `writeConf` 原子写（临时文件 + replaceItemAt）+ 失败日志
- LaunchAgent 三台加 `KeepAlive` + `ThrottleInterval=10`
- `app.log` 5MB 轮转保留 1 份
- `verifyAfterApply` 增加同格重叠检测并计入失败
- 菜单下线 6 项已废弃功能（自动切换/合盖/开盖/时光机/立即监测/自动备份）
- 删死代码 `calibrateGrid`/`changeRatio`/`pruneAutoSnapshots`/`restartQSpace`
- 中枢加来源网段白名单（本机/LAN/ZT）
- 一键回滚 `~/qspace-layouts/qspace-rollback.sh`（列归档 / 按编号回滚 / 自动回退 ZT / 过滤污染归档）
- `isSingleScreenConf` 改用"有图标的屏组数"

### 新增预防措施

**上报前自检**：`broadcastIfNeeded` 在提交前统计同格重叠，有重叠直接拒绝上报并记日志——防止某台把脏数据推给全机队后又被"中枢即本机"锁死（该链条真实发生过一次）。

### 三套参数最终值（已实测验证）

| 套 | 列数 | 最右列 x | +图标宽 | 屏宽 | 余量 |
|---|---|---|---|---|---|
| A | **20** | 1581 | 1645 | 1710 | +65 |
| B | **18** | 1583 | 1647 | 1728 | +81 |
| C | **21** | 2380 | 2476 | 2560 | +84 |

步长（79/90/113）未改——由 QSpace 按 lod+间距固定。

### 独立验证脚本（部署验收必备）

`/tmp/check.py`（可长期放在 `~/qspace-layouts/check-layout.py`）：读 conf 直接算
越界 / 同格重叠 / 列位偏差(>3px) / 行内间距(<图标宽)，三台各跑一遍。

> **为什么必须独立**：app 自己的 `verifyAfterApply` 在 QSpace 未排完盘时会报通过；
> 且「修复是否真生效」也只能靠它抓（实测 `!used.contains` 补丁未落盘，就是外部脚本抓出来的）。


### ⚠️ P0-4 撤回（误判）

原始判断「三套列数超出屏幕物理宽度」**错误**：把 `x/y` 当成图标左上角，实际是**中心点**。
- A 第21列 x=1660 → 1660+32=1692 < 1710 **不越界**
- B 第19列 x=1673 → 1673+32=1705 < 1728 **不越界**
- C 第22列 x=2493 → 2493+48=2541 < 2560 **不越界**

错误修补（减列数 + 越界左移）导致合法最后一列被推到倒数第二列，与原有图标撞车，
连锁推移 39 个图标。**已全部撤回**：列数恢复 21/19/22，越界判断改用 `lod/2`。

**恢复用户原版**：用 `archive-old/[Air] 内置屏-261104.json` 作真源，三台 PASS，
42 个图标逐个比对映射正确。

**结论：用户逐格标定的参数不动；判断越界前先确认坐标系语义。**


---

## 十一、同步节奏与防抖（实测值，2026-11-05 校准）

> 本节补文档滞后：前文只写了架构，没写**具体节奏参数**，排障时对不上号。

### 定时器一览

| 定时器 | 间隔 | 作用 | 代码位置 |
|---|---|---|---|
| **同步轮询** | **10 秒** | `runSyncCycle()`：切套 → 广播 → 拉取 → 应用 | `Timer.scheduledTimer(withTimeInterval: 10, repeats: true)` |
| **指纹轮询** | 30 秒 | 检测本机布局变化 + 刷新菜单 | `Timer.scheduledTimer(withTimeInterval: 30, repeats: true)` |
| **屏幕变化防抖** | 3 秒 | 屏幕参数变化后延迟跑一轮同步（避免切屏瞬间连发） | `debounceTimer` |
| **拖动保护期** | **10 秒** | 用户拖动后 10 秒内**不应用远端**（防止把刚拖的图标弹回） | `DEBOUNCE` |
| **吸收期** | **60 秒** | 应用远端后 60 秒内，QSpace 自行重排**只吸收不上报** | `suppressBroadcastUntil` |
| **网络退避** | 5s → 10s → 20s → ... 上限 300s | 中枢不可达时指数退避 | `hubNextRetryAt` / `hubFailStreak` |
| **休眠唤醒** | 立即 | `didWakeNotification` → 直接跑 `runSyncCycle()` | 睡眠挂起期轮询失效，靠这个补 |

### 令牌（第三次审计新增）

**为什么需要**：MINIPC 上跑着 8+ 个 Docker 容器，host 网络模式的容器**源 IP = 宿主 IP**，
网段白名单挡不住 → 容器内任意进程可 `POST /layout` 覆盖全机队桌面布局（实测 HTTP 200）。

**做法**：网段白名单**之外**再叠一层共享密钥
- 令牌文件：`~/qspace-layouts/hub-token.txt`（三台 Mac）+ `~/qspace-hub/hub-token.txt`（MINIPC）
- 权限：**600**（仅属主可读写）
- 客户端读本地令牌 → 请求头 `X-Hub-Token`
- 中枢用 `hmac.compare_digest` 恒定时间比较（防时序攻击）
- 无/错令牌 → `401`；网段外 → `403`

**四态实测**：无 token 401 ✓｜错 token 401 ✓｜对 token 200 ✓｜ZT + token 200 ✓

**⚠️ 坑（第四次审计抓到）**：加令牌时**必须同步改回滚脚本** `qspace-rollback.sh`，
否则 5 处 curl/urllib 全被 401 挡住，**回滚功能整体失效**（`--check` 返回 `unauthorized`）。

### HTTP 状态码可见性（第四次审计修复）

客户端 `logHTTPCode()` 会把非 200 翻译成人话写进日志：
```
401 → 令牌失效（hub-token.txt 与中枢不一致）
403 → 网段外被拒
400 → 数据被拒：<error>
404 → 中枢无该接口
```
修复前 `{ data, _, _ in` 直接丢状态码，**401/403/400 全显示成"失败"**，排障分不清。

### 版本号

`CFBundleVersion = 2.0.<构建时间戳后4位>`，启动日志第一行打版本：
```
startup: v2.0.6134 pid=57804 hub=http://192.168.113.11:18878 -> http://192.168.191.9:18878 machine=MBP
```
排障时先看这行，确认三台跑的是不是同一版。
