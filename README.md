# QSpace 桌面布局跨机同步

> 三台 Mac 的桌面图标布局**自动同步**。谁最后改动，其他机器 10 秒内跟着变。
> 中枢跑在常开的 MINIPC 上，支持局域网 + ZeroTier 双网络。

---

## 一、这是什么

macOS 的桌面图标位置存在 QSpace Pro 的私有配置里（`desktop.conf`），换台机器就是另一套位置。
本项目做四件事：

1. **布局跨机同步** —— 任意一台拖动图标，其余机器自动跟着排好
2. **操作优先保护窗** —— 你在操作的机器不会被闲置机的联动上报覆盖
3. **屏幕自适应** —— 三台屏幕不同，用「相对坐标」映射（右侧 5 列固定区 + 左侧贴组件直通），不是死记像素
4. **中枢唯一真源** —— 布局只保存一份，不存在"哪台是权威机"的问题

| 痛点 | 解决 |
|---|---|
| 换台电脑桌面全乱，找不到文件 | 布局自动跟随，打开就是熟悉的排列 |
| 合盖/开盖换屏幕后图标重叠、歪斜 | 三套屏幕参数写死进软件，切套时参数+坐标一起写 |
| 多台互相覆盖，改着改着更乱 | 中枢「后改赢」+ 吸收期 + 操作保护窗 |
| iCloud 新增文件各机落位不同 | 名单对账：新增位置自动补报中枢，全网拉齐 |
| 笔记本带出门连不上家里中枢 | LAN 失败自动切 ZeroTier |

---

## 二、架构

> ⚠️ **跨套换算只允许单跳**（2026-11-12 mimo 轮检 R27 量化）：C→B 一跳与 C→A→B 两跳在映射区结果不一致（有损 14 格）。任何恢复/同步场景必须以中枢单一真源为一跳来源，禁止链式两跳换算。

```
        ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
        │  MacBook Pro │   │  MacBook Air │   │  Mac Studio  │
        │  客户端      │   │  客户端      │   │  客户端      │
        └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
               │                 │                 │
       LAN 192.168.113.x    （失败自动切）    ZT 192.168.191.x
               │                 │                 │
               └────────┬────────┴────────┬────────┘
                        ▼
              ┌──────────────────────┐
              │  中枢 MINIPC:18878   │
              │  hub-server.py       │
              │  layout.json 唯一真源 │
              │  archive/ 保留 20 份  │
              └──────────────────────┘
```

**三台完全对等**，没有权威机。中枢只做一件事：保存时间戳最新的那份布局。

### 同步节奏

| 机制 | 时长 | 作用 |
|---|---|---|
| 同步轮询 | **10 秒** | **先拉取应用 → 后广播上报**（防乱位置盖中枢） |
| 指纹轮询 | 30 秒 | 检测本机布局变化 |
| 拖动保护期 | **10 秒** | 拖完 10 秒内不应用远端，防止刚拖的图标被弹回 |
| **操作保护窗** | **10 分钟** | 谁在被手动操作听谁的；双方都在操作则后改者赢 |
| **吸收期** | 120 秒 | 应用远端后 QSpace 自己重排不算用户改动 |
| 切套冷静期 | **120 秒** | 开合盖换屏后重排余波不上报（采样/上报两侧一致） |
| **名单对账** | 实时 | 本机图标名单 ⊋ 中枢名单时强制补报（iCloud 新增落位同步） |
| **屏号自愈** | 实时 | 合盖残留旧屏号的项按坐标归入主屏（位置同步不漏项） |
| 网络退避 | 10→20→40→60 秒 | 中枢不可达时指数退避，恢复后自动归零 |

---

## 三、核心设计（细节见 docs/ARCHITECTURE.md）

### 1. 数格子，不是记像素

桌面被抽象成网格，每个图标只记录 `(列, 行)`。三套写死的屏幕参数（实测标定，`main.swift` → `SPEC_A/B/C`）：

| 套 | 对应屏幕 | 图标号 | 列距 | 行距 | 原点 | 网格 |
|---|---|---|---|---|---|---|
| A | Air 内屏 1710×1073 | 64 | 79 | 114 | 80,44 | 21×9 |
| B | MBP 内屏 1728×1084 | 64 | 90 | 114 | 53,43 | 19×9 |
| C | 外接/Studio 2560×1440 | 96 | 113 | 148 | 120,40 | 22×9 |

选哪套看**图标最多的那块屏**（不是面积最大的），避免双屏态用外接参数挤爆内屏。

### 2. 映射 v6：相对坐标（右 5 列固定区 + 左侧贴组件）

三台列数不同 + 小组件宽度不同，绝对列号互相换算必有损耗（曾实测 12% 格位往返不可逆 → 逐轮累积漂移）。
v6 规则两段纯平移直通，数学上完全可逆：

- **右侧 5 列固定区**：距右缘第 N 格 ↔ 距右缘第 N 格（三端同 5 列 → 完美双射，永不漂移）
- **左侧映射区**：贴组件带第 N 格 ↔ 贴组件带第 N 格（相对编号三端视觉一致）

摆错区域的后果：撞禁区或撞已占格 → 行内自动右推、行末折行，绝不丢图标。
应用 moved=0（布局等价）时跳过写盘，斩断回声源头。

### 3. 中枢安全：三层门禁

| 层 | 机制 |
|---|---|
| 网段白名单 | 仅 `127.0.0.1` / `192.168.113.0/24` / `192.168.191.0/24` |
| 共享密钥 | `X-Hub-Token` 头 + `hmac.compare_digest` 恒定时间比较 |
| 数据校验 | body ≤1MB、items 非空且 ≤500 项、`name:str` + `col/row:int 1~1000`、时间戳不许超前 5 分钟 |

> 为什么需要密钥：MINIPC 上跑着多个 Docker 容器，**host 网络模式的容器源 IP = 宿主 IP**，光靠网段白名单挡不住。

### 4. 客户端自检，不上报脏数据

上报前检查：格子越界？同格重叠？指纹为空？——任一不满足就拒绝上报。
应用后校验：重新读 conf 算一遍，比对期望位置，不一致就记日志告警。

---

## 四、目录结构

```
Desktop-Icon-Manager/
├── README.md                    ← 本文
├── QSpaceLayoutBar/             ← 客户端（菜单栏 app）
│   ├── main.swift               主程序（约 2200 行，随版本变动）
│   ├── Info.plist               bundle 配置（含 NSLocalNetworkUsageDescription）
│   └── build.sh                 构建脚本 → build/QSpaceLayoutBar.app
├── hub/                         ← 中枢（跑在 MINIPC）
│   ├── hub-server.py            HTTP 服务（:18878，systemd 自启）
│   └── qspace-hub.service       systemd 单元
├── scripts/                     ← 运维脚本
│   ├── qspace-deploy.sh         一键部署（all / 单台 / new <别名>）
│   ├── qspace-rollback.sh       布局回滚（列表 / --check / --local / 编号）
│   ├── qspace-uninstall.sh      完全卸载（--dry-run / --purge）
│   ├── check-layout.py          独立验证（越界/重叠/列位/行距）
│   ├── cmp-consistency.py       三台位置级一致性比对（v2：分屏面 + v6 归一）
│   └── test-hub.py              中枢契约测试 17 项（只打沙箱 18879）
└── docs/
    ├── ARCHITECTURE.md          完整架构与设计决策
    └── audits/                  问题总台账（十五轮审计按类型归档）
```

---

## 五、快速开始

### 前置

- macOS（三台已实测：MBP M4 Pro / Air / Mac Studio）
- [QSpace Pro](https://qspace.awehunt.com/)（本项目操作它的 `desktop.conf`）
- 一台常开机器跑中枢（任意 Linux / macOS，需 Python 3）
- SSH 免密到各台（`BatchMode=yes`）

### 1. 构建客户端

```bash
cd QSpaceLayoutBar && ./build.sh
# 产物：build/QSpaceLayoutBar.app
```

### 2. 部署中枢（MINIPC 或任意常开机器）

```bash
mkdir -p ~/qspace-hub ~/qspace-layouts
openssl rand -hex 32 > ~/qspace-layouts/hub-token.txt && chmod 600 ~/qspace-layouts/hub-token.txt

scp hub/hub-server.py  <中枢机>:~/qspace-hub/hub.py
scp ~/qspace-layouts/hub-token.txt <中枢机>:~/qspace-hub/hub-token.txt
scp hub/qspace-hub.service <中枢机>:/tmp/
ssh <中枢机> 'sudo cp /tmp/qspace-hub.service /etc/systemd/system/ && \
              sudo systemctl daemon-reload && \
              sudo systemctl enable --now qspace-hub && \
              systemctl is-active qspace-hub'

# 验证
curl -s -H "X-Hub-Token: $(cat ~/qspace-layouts/hub-token.txt)" http://<中枢IP>:18878/status
```

### 3. 部署客户端

```bash
./scripts/qspace-deploy.sh new mbp     # 首次（建目录+发令牌+装 launchd）
./scripts/qspace-deploy.sh all         # 日常更新（改了代码后）
```

### 4. 验证

```bash
python3 scripts/check-layout.py        # 期望 PASS 无越界/无同格重叠
python3 scripts/cmp-consistency.py     # 期望三台 ✓ 位置差 0
```

---

## 六、日常运维

```bash
./scripts/qspace-rollback.sh --check      # 中枢当前布局（谁上报的/几个图标）
./scripts/qspace-rollback.sh              # 归档列表（✅/❌ 可用性）
./scripts/qspace-rollback.sh 3            # 回滚到第 3 个，三台自动应用
tail -f ~/qspace-layouts/app.log          # 客户端日志
ssh <中枢机> 'tail -f ~/qspace-hub/hub.log'   # 中枢日志

./scripts/qspace-uninstall.sh --dry-run   # 卸载先看会删什么
./scripts/qspace-uninstall.sh all         # 卸三台（保留数据）
```

### 故障排查

| 现象 | 查什么 |
|---|---|
| 不同步 | `grep 'hub:' ~/qspace-layouts/app.log` — 401（令牌）/ 403（网段）/ 超时 |
| 图标重叠 | `python3 scripts/check-layout.py` |
| 各机位置不一致但脚本说零差异 | 确认用的是 `cmp-consistency.py` v2（旧版不比位置不分屏面） |
| 新增文件位置各机不同 | 名单对账自动补报；查日志 `名单对账`；本机比中枢少 = iCloud 未到货，等同步 |
| 合盖后部分图标位置乱 | 查日志 `屏号自愈`；确认部署版本含 normalizeScreenIds |
| 刚拖完又被弹回 | 防抖 10 秒内不该应用远端；若发生说明 `lastLocalChangeAt` 没刷新 |
| 开合盖乱位 | 切套冷静期 120 秒；查日志 `屏幕切换 X → Y` |
| 中枢 500 | `ssh <中枢机> 'tail -50 ~/qspace-hub/hub.log'` |

---

## 七、中枢协议

```http
POST /layout
X-Hub-Token: <共享密钥>
Content-Type: application/json

{
  "syncAt": 1793817197,          // 秒级时间戳（后改赢）
  "sourceMachine": "Studio",
  "layoutFingerprint": "5b8cbf1cbeb6e7b",
  "specKey": "C",                 // 上报方用的哪套
  "userActiveUntil": 1793817797,  // 操作保护窗截止（可选）
  "items": [
    {"name": "样片", "col": 22, "row": 6}     // 只存文件名，不存完整路径
  ]
}
```

**接受顺序**：先比 `syncAt`（旧版返回 `reason:"older"`），再查保护窗（对方操作中返回 `reason:"protected"`，客户端跟随、窗开后自动重报）。

```http
GET /layout   → 当前布局（同门禁）
GET /status   → {ok, hasLayout, syncAt, sourceMachine, iconCount, archiveCount, serverTime}
```

**存储**：`~/qspace-hub/layout.json`（唯一真源，600 权限）+ `archive/`（最近 20 份）+ `hub.log`（5MB×3 轮转）。

---

## 八、设计决策（为什么这样做）

| 决定 | 原因 |
|---|---|
| 中枢放常开机器，不做权威机 | 任何一台都可能关机/带出门 |
| 只存文件名不存完整路径 | 三台桌面根路径相同，文件名足够定位 |
| 三套参数写死进代码 | 从坐标反推步长有误差，实测导致重叠 |
| 映射用相对坐标不用插值 | 插值跨「格子数不等」的两套必有损耗，逐轮累积漂移 |
| 60 秒吸收期 | QSpace 应用新布局后会自己重排，不吸收会死循环 |
| 网段白名单 + 共享密钥 | Docker host 网络容器源 IP = 宿主 IP |
| 直连 HTTP 而非 SSH | SSH 每条命令 fork 进程，10 秒轮询代价高 |
| sync 循环先拉后报 | 快照口径变化时，先报会把修正前的乱位置盖上中枢 |

---

## 九、已验证的事实

- **三台二进制一致**：md5 相同，同一次构建部署
- **42 点标定 126/126 吻合**（第01次轮检实测，含 R18 新增的 41/42 两个 clamp 补点）；右锚段（距右 ≤5 格）往返完全可逆，左贴组件段在「右锚判定翻转接缝」与「zone2 行带成员不对称」处有损（第01次轮检 4-mimo 六方向实测：B→A 123/123 全可逆、A→B 123/137、C→B 122/167，合法格口径）
- **端到端同步**：拖动 → 其余两台 10 秒内应用，校验全过
- **收敛稳定**：v6 + moved=0 跳写盘上线后长时间静默零轮换（对比上线前 9 分钟震荡 2 轮）
- **灾难恢复**：中枢丢失不影响使用，任一台重新上报即可重建

问题与修复记录见 `docs/audits/问题总台账.md`（十五轮白纸审计，按类型归档）。

---

## 十、来源与许可

早期版本 fork 自 [Desktop Icon Manager](https://github.com/parker9/DIM)（MIT）。
当前代码已完全重写为面向 QSpace Pro 的跨机同步方案，上游源码不再包含在本仓库中。

本项目为个人自用工具，按现状提供，不保证适用其他环境。
