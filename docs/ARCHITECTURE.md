# 三 Mac 桌面布局同步 · 中枢架构

> 最后校准：2026-11-12（第01次四模型轮检后）。**唯一真源是代码**：`QSpaceLayoutBar/main.swift` 的 `SPEC_A/B/C`
> 与 `hub-server.py`。本文若与代码冲突，以代码为准并回来改本文。

## 一句话

MINIPC 上的中枢（`:18878`）保存唯一一份布局；三台 Mac 每 10 秒轮询，本机布局变了就上报，
中枢按 `syncAt` 后改赢，其余两台拉到后按自己的屏幕套重新换算像素。

## 架构

```
 MacBook Pro ─┐                    ┌─ LAN 192.168.113.x（优先）
 MacBook Air ─┼── 客户端 ──────────┤
 Mac Studio  ─┘                    └─ ZT  192.168.191.x（LAN 失败自动切）
                        ↓
        MINIPC 中枢 hub-server.py :18878
        layout.json（唯一真源）+ archive/（保留 20 份）
```

三台**完全对等**，没有权威机（任何一台都可能关机/带出门）。

## 三套屏幕参数（写死，应用时参数+位置一起写）

| 套 | 屏幕 | lod | 列距 | 行距 | 原点 | 网格 |
|---|---|---|---|---|---|---|
| A | Air 内屏 1710×1073 | 64 | 79 | 114 | 80,44 | **21**×9 |
| B | MBP 内屏 1728×1084 | 64 | 90 | 114 | 53,43 | **19**×9 |
| C | 外接 / Studio 2560×1440 | 96 | 113 | 148 | 120,40 | **22**×9 |

选套规则：**按「图标最多的那块屏」**（不是面积最大），否则双屏态会用外接参数挤爆内屏。

## 同步节奏（实测值）

| 机制 | 时长 | 作用 |
|---|---|---|
| 同步轮询 | 10 秒 | 切套 → 重叠修复 → **拉取应用 → 广播上报**（先拉后报，防乱位置盖中枢）|
| 指纹轮询 | 30 秒 | 检测本机变化 + 刷新菜单 |
| 拖动防抖 | 10 秒 | 停手 10 秒才上报，且期内不应用远端 |
| 吸收期 | 60 秒 | 应用远端后 QSpace 自行重排不算用户改动 |
| 切套冷静 | 120 秒 | 开合盖/插屏后重排余波不上报（采样/上报两侧一致）|
| **操作保护窗** | **10 分钟** | 上报带 `userActiveUntil`（最后拖动+10min）；中枢拒绝「无标记上报」覆盖「保护窗内版本」。拖动判定=共有文件坐标位移数>0，文件增删/回声（120s 内）不带标记 |
| 网络退避 | 10→20→40→60 秒封顶 | 中枢不可达时指数退避，恢复后归零 |
| GET 日志节流 | 5 分钟/来源 | 否则每天 2.6MB 噪声 |
| **名单对账** | 实时 | 本机名单 ⊋ 中枢名单 → 补报新增位置（豁免 lastBC；名单未见过/我比中枢少时不报）|
| **屏号自愈** | 每次快照/应用 | v5 判据：非主屏组坐标对主屏网格对齐率 >80% = 失真残留 → 归入主组；<80% 判真双屏不动（合盖残留旧屏号自愈，真双屏不误并）|
| **应用逻辑版本** | 部署时 | applyLogicVersion 变化 → 首轮强制重应用（穿越 fp==lastApplied 短路；当前 "4"）|
| **VIP 三格** | 实时 | 右上列头三格绑定内置盘/Jon113 固态/SynologyDrive：被占先本机弹开再同步，VIP 漂位自动归位 |
| **卷宗专区** | 实时 | 临时卷宗（/Volumes/* 与裸名虚拟挂载）入右锚区/VIP → 自动弹中段区 midZone |

## 数据格式

```json
POST /layout   头: X-Hub-Token: <共享密钥>
{
  "syncAt": 1793912026,          // 秒级，后改赢
  "sourceMachine": "Studio",
  "layoutFingerprint": "dcab6a889ee8edf6",
  "specKey": "C",
  "items": [{"name": "样片", "col": 22, "row": 6}]   // 只存文件名，不存路径
}
```

`GET /layout`、`GET /status` 同门禁。返回 `accepted:false, reason:"older"` 表示中枢有更新的版本；
`reason:"protected"` 表示对方用户操作中（保护窗），客户端跟随并在窗开后重报。

**映射 v6（相对坐标，2026-11-11）**：三台列数不同（21/19/22）+ 小组件宽度不同，
绝对列号互相换算必有损耗（实测 12% 格位往返不可逆 → 逐轮累积成「慢慢漂移」）。
v6 规则（杰哥定规）改为两段纯平移直通：
- **右侧 5 列固定区**：距右缘第 N 格 ↔ 距右缘第 N 格（三端同 5 列 → 完美双射）
- **左侧映射区**：贴组件带第 N 格 ↔ 贴组件带第 N 格（相对编号三端一致 → 恒等）
中段不再插值；接缝/容量差由 clamp+占用推移兜底（确定性，不累积）。
另有第一刀：应用 moved=0（布局等价）时跳过写盘，斩断回声源头。
**可逆性实测口径**（第01次轮检 mimo 六方向）：右锚段完全可逆；左贴组件段在接缝处有损
（B→A 123/123 全可逆、A→B 123/137、C→B 122/167，合法格口径）——mapCell 非双射是已知特性，
比对/校验必须用正向映射，反推会产生假差异（cmp v3 据此重写）。

## VIP 三格与卷宗专区（杰哥定规，2026-11-12）

- **右上列头三格**绑定常驻项：`(cols,1)`=内置盘、`(cols,2)`=Jon113 固态（常驻，拔空插归）、
  `(cols,3)`=SynologyDrive。全网任何文件不许占/改；占位者先本机弹开（中段区）再同步；
  VIP 项漂位自动归位；三项不进快照不参与映射（位置各机固定，右锚直通天然保位）。
- **临时卷宗**（`/Volumes/*` 与裸名虚拟挂载如 DeviceFS）出现在右锚区/VIP → 自动弹中段区
  `midZone`（A 11-16 / B 10-14 / C 12-17），从上往下从左到右找空位；专区满则留原位+告警（不死循环）。
- 卷宗名常带尾随空格（`/Volumes/Jon113␣␣`），VIP/杂卷匹配一律 trim+前缀。

## 中枢四层门禁

| 层 | 规则 |
|---|---|
| 网段白名单 | `127.0.0.1/32` / `192.168.113.0/24` / `192.168.191.0/24` → 否则 403 |
| 共享密钥 | `X-Hub-Token` + `hmac.compare_digest` → 否则 401 |
| 数据校验 | body ≤1MB / items 非空且 ≤500 / `name:str` + `col,row:int 1~1000` / 时间戳不超前 300s / 元素结构逐项校验 |

> 为什么需要密钥：MINIPC 上跑着多个 Docker 容器，**host 网络模式容器源 IP = 宿主 IP**，
> 光靠网段白名单挡不住（实测容器内可 POST 200）。

## 硬坑清单（按类型，改代码前必读）

1. **macOS 本地网络 TCC 静默拦截** — 缺 `NSLocalNetworkUsageDescription` 时系统不弹窗、不写日志，
   直连 IP 的请求直接失败。补键后静默放行，**不需要用户授权**。
2. **Docker host 网络绕过 IP 白名单** — 见上，必须叠共享密钥。`curl --interface` 测不出这个问题
   （源 IP 不变），要从真容器内发起才能复现。
3. **互相同步覆盖** — 应用远端后 QSpace 会自行重排，若当成用户改动上报 → 覆盖中枢 → 三台死循环。
   用 **60 秒吸收期** 化解（期内只更新基线不上报）。
4. **时钟偏差** — 落后：`at <= cur_at` 永远被拒 → `clockOffset` 补偿（`latestSyncAt+1-now`）；
   超前 >300s：400 → `hubServerTime()` 校准重发**并同步更新 offset**（否则每轮都 400）。
5. **同秒碰撞** — 两台同秒上报只有第一个被接受。被拒后 `pendingRepushFp` 下轮重推、
   本轮 `skipPullOnce` 不拉远端（否则远端会盖掉本机刚改的）。
6. **回滚编号错位** — 列表与回滚**必须共用同一个选择器**（`select_archive`）。
   曾用两套排序规则 → 用户看到 #1 是 A，实际回滚到 B。
7. **状态文件非原子写** — `sync-state.json` 被截断 → 状态全丢 → 重启后把旧布局当新改动。
   用 `tmp + replaceItemAt`。
8. **假开关** — 菜单写 `microNudgeOff`、代码读 `microNudgeOn` → 点了重启即失效。
   写入的键必须与读取的键同名。
9. **中文全角括号紧跟变量名** — `$H（首次` 被 bash 当成变量名 → 空值。永远用 `${H}`。
10. **重叠自动推开只修「真同格」** — 把"撞禁区"也算推开理由会连锁改动用户布局
    （实测一次误推 7 个）。且连续修 3 次仍重叠就停手告警（防 QSpace 重启后又放重 → 死循环）。
11. **`applyingRemote` 必须 defer 复位** — 中途 return 会永久卡 true，
    表现为"同步完全不工作但日志无报错"（最难查的一类）。
12. **fuzz 测试会把中枢打脏** — 测试必须用**过去**的 `syncAt`（走 `older` 拒绝路径），
    否则正常请求会被接受并覆盖真源。
13. **conf 的 screen 字段会失真** — 合盖后残留旧屏号，坐标却在外接屏上（2026-11-11 实测 12 项）。
    按字段分组会把真屏上的图标排除在同步外 → 位置永不同步。判屏以**坐标包围盒重叠**为准。
14. **kept 项必须进 used 集合** — 应用时不登记本机保留项的格子，映射落点会撞上它们 →
    写盘重叠 → overlap 连锁推挤（实测一次推 5 个）。
15. **lastBC 短路挡不住「中枢被别人覆盖」** — 它只证明「我报过这个 fp」。
    名单对账必须豁免 lastBC，否则被覆盖后永久无法自愈（Air 43 项被 Studio 34 项覆盖卡死实测）。
16. **中段区满时 relocate 会死循环** — cursor 找不到空格重头再找 → 8 秒超时（mimo 轮检 R16 实测）。
    加步数保护：超区容量就放弃弹开、留原位+告警。
17. **卷宗名带尾随空格** — `/Volumes/Jon113␣␣` 精确匹配 `/Volumes/Jon113` 会漏 → 常驻盘被当
    临时卷宗弹走。匹配一律 trim+前缀。
18. **远程桌面每会话一块虚拟屏** — Studio 双会话时出现两块 5K RemoteDesktop，图标坐标混两屏
    网格 → conf 全乱 + QSpace 重算风暴卡死。只保留单一会话。
19. **外接线松会降级识别**（Kuycon 5K 识别成 1920×1080）— 无匹配套 → machineTag 兜底按本机套摆，
    不新增标定（曾误加 SPEC_D 后撤销）。重插线自动回正。
20. **比对必须用正向映射** — mapCell 非双射（六方向实测 A→B 损 14 格），反推比对产生假差异。
    cmp v3 与 applyGridSnapshot 同源（中枢→本机套正向）。

## 运维命令

```bash
# 部署（改完代码）
./scripts/qspace-deploy.sh all            # 三台
./scripts/qspace-deploy.sh new <ssh别名>  # 全新机器首次（建目录+发令牌+装plist+bootstrap）

# 验证
python3 scripts/check-layout.py           # 期望 PASS 无越界/无同格重叠/列位标准/行距足够

# 回滚
./scripts/qspace-rollback.sh              # 列表（✅=可用，标注项数与 fp）
./scripts/qspace-rollback.sh 3            # 回滚到第 3 个，三台 10~20 秒自动应用
./scripts/qspace-rollback.sh --local      # 本地手动快照

# 状态
./scripts/qspace-rollback.sh --check      # 中枢当前布局（谁上报/几项）
tail -f ~/qspace-layouts/app.log          # 客户端（本地时区）
ssh minipc 'tail -f ~/qspace-hub/hub.log' # 中枢

# 中枢维护
ssh minipc 'sudo -n systemctl restart qspace-hub'
ssh minipc 'ls -t ~/qspace-hub/archive | head'

# 卸载
./scripts/qspace-uninstall.sh --dry-run   # 先看会删什么
./scripts/qspace-uninstall.sh all
```

## 仓库结构

`jon1131120/desktop-icon-suite`（backup）+ `jon1131120/Desktop-Icon-Manager`（fork），两者 main 同步。

```
QSpaceLayoutBar/   客户端 main.swift + Info.plist + build.sh
hub/               中枢 hub-server.py + qspace-hub.service
scripts/           deploy / rollback / uninstall / check-layout / cmp-consistency v3 / test-hub
docs/              ARCHITECTURE.md + audits/问题总台账.md
```

**自包含铁律**：改完必须验证「全新 clone → build → md5 == 三台部署版」。

## 关键文件

| 路径 | 作用 |
|---|---|
| `~/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf` | QSpace 布局真源（我们读改写） |
| `~/qspace-layouts/hub-token.txt` | 共享密钥（600） |
| `~/qspace-layouts/sync-state.json` | 客户端同步状态（原子写） |
| `~/qspace-layouts/app.log` | 客户端日志（5MB 轮转） |
| `~/qspace-layouts/archive-old/` | 手动快照（`--local` 用） |
| MINIPC `~/qspace-hub/layout.json` | 中枢唯一真源（600） |
| MINIPC `~/qspace-hub/archive/` | 归档 20 份 |
