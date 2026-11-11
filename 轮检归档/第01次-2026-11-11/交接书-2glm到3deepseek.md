# 交接书：第 2 位（glm-5.3-flash）→ 第 3 位（deepseek-flash）

## 项目主链路（不变）
QSpace 桌面图标跨机同步：Swift 菜单栏客户端 ⇄ Python 中枢（MINIPC :18878）。今天项目大修 9 提交 + 双实例事故处理 + 沙箱假隔离 P0 根治（见第 1 位交接书）。

## 第 2 位 glm-5.3-flash 产出摘要（实测模式，54 分钟，30 轮+15 工况全跑，校验器 PASS）
**复核专区 14 项，抓出第 1 位（MiniMax）3 个真问题 + 主会话设施 1 个 P1**：
- ★ R1：fix-R7-screen-heal 目标场景生效但引入 P1 回归——pointInsideMain「任意相交」判据在 B/C 套数值区间天然重叠时会把真双屏误并
- ★ R4：fix-R20 patch 名不符实（交接书声称「reset 失败显式报错」实际未落地）
- ★ R7：fix-R25 部署实例数校验自身边界缺陷（0 实例时 grep -c 退出码 1 + set -e 静默中止 all 部署）
- ★ R11：主会话写的 build_sandbox.sh token 路径失配 → 沙箱令牌校验整层失效（实测错/空 token 均 200 放行）
- 数字勘误：MiniMax 台账称 selftest 132/132，实为 126/126

**主会话对第 2 位产出的处置**（你复核的基线已含）：
- glm 的 P1-P7 patch 文件损坏（corrupt patch），按意图手工落实：
  - P1 → applyGridSnapshot 两段式预登记（kept 排序依赖同格重叠，实测 kept.md 队尾场景修复）
  - P2 → 屏号自愈判据重写（经 v3 尺寸判据→v4 门卫→**v5 主屏网格对齐率** 三次迭代，v3/v4 均被 snaptest 实测打回，见下「实测教训」）
  - P4/P5/P6 → test-hub reset 显式报错 / deploy 0 实例容错 / check-layout nil 兜底对齐
- **VIP 三格体系落地（杰哥定规，commit 4239482）**：右上列头三格=内置盘/Jon113固态/SynologyDrive，
  全网任何文件不许占/改；临时卷宗自动弹中段区（midZone A11-16/B10-14/C12-17）；
  VIP 三项不进快照不参与映射；relocateStrayVolumes 占位弹开+漂位归位
- 三台部署 2026.1112.0026，cmp 三台 ✓ 位置差 0，snaptest PASS（moved=0）

## ⚠️ 实测教训（写代码前必读）
1. **生产 conf 的 itemAttrs 没有 screenWidth/screenHeight 字段**（只有 x/y/screen/name）——
   任何依赖尺寸字段的判据在生产上永不触发（v3 判据死因）。glm 台架 fixture 自带 screenWidth，
   与生产不符 → 台架过了生产挂了。**你的 fixture 必须从真 conf 采样**。
2. **卷宗名带尾随空格**（/Volumes/Jon113␣␣）——名字匹配必须 trim+前缀，精确匹配会漏（实测 Jon113 被误弹）。
3. **isSingleScreenConf 按 screen 值分组判单屏**——失真 conf（混值）永远判非单屏，拿它当
   normalizeScreenIds 门卫=自残（v4 死因）。屏号自愈 v5 最终判据=非主屏组坐标对主屏网格的
   对齐率（>80% 归并），不依赖任何字段。
4. 真机零触碰照旧；Studio 正跑杰哥任务，完全不许动。
