# 提示词 · Day-Info 每日收集（静默入池）

> 调度：`0 9 * * *`（Asia/Shanghai）· 隔离会话 · timeoutSeconds=300
> 原则：收集频率高、推送频率低；本任务只静默入池 + 提交仓库，不向用户推送内容。

```text
Day-Info 每日静默收集任务：把今天的技术信息收集入池并提交仓库，不向用户推送内容；只输出简短状态（含 critical 即时提醒）。

步骤：
1. 用 exec 运行（超时给 240 秒）：bash /root/.openclaw-autoclaw/workspace/Knowrary-dayinfo/day-info/scripts/daily.sh
2. 依据脚本输出汇报（全中文，不超过 8 行）：
   - 若出现以「CRITICAL:」开头的行：整理为「⚡【即时提醒】」列表放在最前（每条：标题 + 链接 + 一句为什么重要）；
   - 一行统计：本次新增 N 条、池内累计（必须看 X / 值得看 Y / 仅存档 Z）；
   - 若脚本尾部有「源告警」：附一行告警概要（最多 3 条）；
   - 若脚本执行失败：重试一次；仍失败则输出「网络波动，本次收集跳过，明天自动重试」并结束。
3. 不要输出速览正文；不要联网补充内容；不要修改 day-info 以外的工作区文件。
```

## 说明

- 实际采集逻辑在 `day-info/scripts/collect.py`（零依赖），本提示词只负责调用 `day-info/scripts/daily.sh` 并按输出汇报。
- `CRITICAL:` 行会被整理成「⚡【即时提醒】」放在汇报最前。
- critical 判定规则见 `collect.py` 的 `classify()`：项目强相关（知识图谱 / Agent / X6 / G6 / AntV / MCP / Obsidian 等）或头部实验室重大发布。
