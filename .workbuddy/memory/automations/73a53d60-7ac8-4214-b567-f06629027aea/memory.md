# Day-Info 每日收集 自动化执行记录

## 2026-09-14 19:01
- daily.sh 执行成功（Exit 0）。
- 新增 51 条，池内累计 87 条（必须看 5 / 值得看 69 / 仅存档 13；critical 1）。
- GitHub 源当天已抓过自动跳过；HN 源 SSL 握手超时（源告警 1 条）。
- HTTPS 推送失败后自动切换 SSH，推送成功（day-info-for-autoclaw 分支，提交 ebf4d36）。
- 已按 8 行内格式汇报，含 1 条即时提醒（critical）。

## 2026-09-16 20:04
- daily.sh 执行成功（Exit 0）。
- 新增 84 条，池内累计 84 条（必须看 8 / 值得看 69 / 仅存档 7；critical 0）。
- 源告警 4 条：GitHub releases 限流（antvis/X6、G6、G6-extension-3d）+ HN SSL 超时。
- HTTPS 推送失败后自动切 SSH 推送成功（提交 ef2008f）。
- 新情况：publish.sh 未能切回 main，仓库停留在 day-info 分支，已在输出中提醒用户手动切回。

## 2026-09-17 19:00
- 硬失败（Exit 1，硬失败分支「需要在」）：main 上有未提交改动 .knowrary/layouts/ai.json（4 行变更），daily.sh 拒绝切到 day-info（脚本绝不用 -f 强切）。
- 未重试（重试无效，非网络问题）；未触碰该改动，未切分支。
- 本次采集与推送均未发生，池内数据无变化。需用户自行提交/stash 该文件后下次运行可恢复。
