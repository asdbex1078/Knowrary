# 提示词 · Day-Info 周报（周日 20:00）

> 任务调度：`0 20 * * 0`（Asia/Shanghai），隔离会话（isolated），timeoutSeconds=300。
> 设计原则：每周唯一一次阅读推送，15 分钟可扫完；含「必须看 / 值得看 / 学习雷达 / 入库草稿」。

```text
Day-Info 周报任务（每周日 20:00；这是每周一次的阅读推送，务必精炼，目标 15 分钟内可扫完；全程中文）。

步骤：
1. exec 运行：python3 /root/.openclaw-autoclaw/workspace/Knowrary-dayinfo/day-info/scripts/weekly.py
2. 读取最新周材料：/root/.openclaw-autoclaw/workspace/Knowrary-dayinfo/day-info/digests/raw/ 下最新的 .md 文件（先 ls 找最新，再 cat 阅读）。
3. 精编周报（不要全量罗列，必须精选）：
   ① 标题「Day-Info 周报（YYYY-Www · 起止日期）」+ 一句本周概览；
   ②「⚡ 必须看」：通常 5-15 条，逐条「发生了什么 + 一句意义」，合并重复主题，按重要性排序；
   ③「🔖 值得看」：精选 20-40 条，按主题分组（论文 / 工具 / 工程 / 行业），每条一行：标题 + 链接 + 极短说明；
   ④「【学习雷达】」：结合用户个人知识库（Knowrary：计算机体系结构自底向上链 + AI-Agent 工程；如目录 /root/.openclaw-autoclaw/workspace/Knowrary/nodes/ 可访问，只读浏览节点名核对衔接点）分三档、每档 2-3 条、每条 ≤30 字最多两句话：🟢 值得学习（注明衔接的现有节点）/ 🟡 值得扩展眼界 / 🔴 从未涉及；结尾一句优先级建议；
   ⑤「📝 入库草稿」：3-5 个可入库节点草稿「- 域｜节点名：一句话描述」（域：硬件/软件/社区/安全/智能体/资本），与现有图谱互补不重复；
   ⑥「一句话总结」。
4. 成稿写入 /root/.openclaw-autoclaw/workspace/Knowrary-dayinfo/day-info/digests/<周标签>.md（周标签用 exec 运行 date +%G-W%V 获取），然后 exec 运行：bash /root/.openclaw-autoclaw/workspace/Knowrary-dayinfo/day-info/scripts/publish.sh "day-info: 周报 <周标签>"。
5. 最后把周报全文作为回复输出（这就是给用户看的周报）。只写材料中有信源的内容，不编造；不确定的标「待确认」。
```

## 说明

- 周报素材由 `day-info/scripts/weekly.py` 从最近 7 天收集池合并生成（`digests/raw/`）。
- 「学习雷达」「入库草稿」会与 Knowrary 图谱节点对照，确保建议能衔接现有知识结构。
- 成稿写入 `day-info/digests/<周标签>.md` 并调用 `publish.sh` 推送分支。
