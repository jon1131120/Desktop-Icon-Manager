# 交接书：第 1 位（MiniMax-M3.1-Flash）→ 第 2 位（glm-5.3-flash）

## 本项目主链路
QSpace 桌面图标跨机同步：Swift 菜单栏客户端（写 QSpace desktop.conf，A/B/C 三套屏幕参数映射，Air/MBP/Studio 三台）⇄ Python 中枢（MINIPC :18878，唯一真源，syncAt 后改赢 + 保护窗 + 吸收期）。今天项目刚完成大修（9 提交）+ 双实例事故处理。

## 第 1 位 MiniMax-M3.1-Flash 产出摘要（实测模式，53 分钟，30 轮+15 工况全跑）
**13 个发现全部有证据（主会话抽查 0 误报），11 个修复 patch 已全部 git apply 进基线**：
- ★P0 R12/R24：rollback.sh 的 sourceMachine="MBP(回滚)" ∉ 白名单 → 回滚 100% 不可用（已修：归一白名单+rollbackOf 字段）
- ★P0 R20 深挖：**仓库真源 hub-server.py 无 /reset → 从仓库建的沙箱清场静默失败**（已修：test-hub reset 失败显式报错；主会话另有更重磅发现见下方事故）
- ★ R16/R26：specForSize 容差区间重叠（B 套 [1688,1750] 被 A 先判吃掉 → MBP 内置屏潜伏误判）→ 已修（最近中心判定）
- ★ R7/R22：屏号自愈重叠率判据边界（单行分布/下沿超出时失效）→ 已修（公式重构，S08b 3FAIL→0）
- ★ R25：部署脚本无实例数强校验（双实例脚本层根因）→ 已修
- ★ R30：archive/*.json 未 chmod 600（低危信息泄露）→ 已修
- ★ R9/R23：自动快照命名分钟粒度冲突 → 已修（秒级戳）
- R27：接缝拥挤量化——确认既有设计（clamp+推移兜底）与注释一致，非缺陷
- 实测通过项：40 点 120/120、对账三态、保护窗、防抖、参数自愈、overlap、门禁 33 项、并发 10 POST、空布局拦截

## ⚠️ 重大事故（第 1 位验收期间发生，必须知道）
**沙箱假隔离 P0**：旧沙箱构建只隔离了端口，数据路径仍指生产 ~/qspace-hub/layout.json →
第 1 位跑 test-hub 期间写入用例全部打进生产中枢（污染成 1 项测试文件）。
**已恢复**（Air 基准 39 项 POST 回中枢，三台一致差 0）+ **已根治**（hub-server.py 数据目录
QSPACE_HUB_DIR 环境变量参数化 + 真隔离沙箱重建，test-hub 重跑后生产 layout.json 零变化实锤）。
commit f5cef04。**你跑测试必须用真隔离沙箱**（构建脚本 /tmp/build_sandbox.sh，或复制 22:52 后的
hub-sandbox.py 并设 QSPACE_HUB_DIR 环境变量）。

## 主会话追加改动（第 1 位验收后）
1. hub/hub-server.py：HUB 数据目录 QSPACE_HUB_DIR 环境变量参数化（沙箱隔离正解）
2. scripts/cmp-consistency.py：回滚 136 伪象基准 → 148（单实例实测 QSpace 网格=148）
3. scripts/test-hub.py：reset() 失败显式报错（R20 修复）
4. 以上均已提交（f5cef04 推 fork ✓，backup 遇 SSL 错误待补推）
5. Air 的 QSpace 已从 7.0.4 升级 7.0.4.015（三台版本对齐，A 套行距实测未变）

## 遗留问题
1. **Studio 桌面仍乱**（双实例时代产物+QSpace Pro 正跑杰哥任务）：38/39 项与基准一致，仅
   「逆向工程报告.md」1 项偏位。**杰哥指示：Studio 跑任务期间完全不动**（不许 killall/open/
   写 conf/重启任何进程），等任务跑完再修。你对 Studio 的检查仅限读 conf 与代码分析。
2. R18/R28 测试增强（idxR 变体断言、clamp 边界标定）未做——低优先，可顺手或跳过
3. 生产 hub-server.py 无 /reset（安全考量：token 持有者可清空中枢）——test-hub 已显式报错兜底，
   维持现状，不要给生产加 /reset
4. 参数自愈的长期观察（QSpace 是否还会精简回写）——轮检期间留意 app.log 告警

## 提醒
以下是第 1 位的有意修复（已 git apply 进基线），复核先理解意图，确认确属误修才可回滚：
fix-R12-rollback-src / fix-R16-spec-forsize / fix-R20-test-hub-reset / fix-R21-check-layout-spec /
fix-R24-rollback-local / fix-R25-deploy-instance-guard / fix-R28-calibration-coverage /
fix-R30-archive-perm / fix-R7-helper / fix-R7-screen-heal / fix-R9-snapshot-name（共 11 个，
evidence/ 下还有它的 15 工况输出与 1116 条 mapCell 明细，可作为你的测试数据但结论必重算）。
台账行内不要用半角竖线。**重点复核方向**：第 1 位的 11 个 patch 是否真修对了（尤其
fix-R7-screen-heal 的重叠率公式重构与 fix-R16 的最近中心判定——两处都改了关键判据）。
