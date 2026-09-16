# Day-Info 每日收集 自动化执行记录

## 2026-09-14 19:01
- daily.sh 执行成功（Exit 0）。
- 新增 51 条，池内累计 87 条（必须看 5 / 值得看 69 / 仅存档 13；critical 1）。
- GitHub 源当天已抓过自动跳过；HN 源 SSL 握手超时（源告警 1 条）。
- HTTPS 推送失败后自动切换 SSH，推送成功（day-info-for-autoclaw 分支，提交 ebf4d36）。
- 已按 8 行内格式汇报，含 1 条即时提醒（critical）。

## 2026-09-15 19:00
- daily.sh 硬失败（Exit 1）：当前在 main，工作区有大量未提交改动（.knowrary、server、tools、web 等），git 拒绝切到 day-info（无 -f 强切设计）。未采集、未推送。
- 已如实汇报，未自行切分支/stash/merge。

## 2026-09-15 23:46
- 连续第二次同样硬失败（Exit 1）：main 分支工作区有未提交改动，无法安全切到 day-info。未采集、未推送。
- 已如实汇报并提示用户先提交或 stash 工作区改动。

## 2026-09-16 19:00
- 第三次连续硬失败（Exit 1）：仍在 main，工作区有会被覆盖的本地改动，git 拒绝切到 day-info。未采集、未推送。
- 已如实汇报，未自行切分支/stash/merge/force push。

## 2026-09-16 20:02
- 第四次连续硬失败（Exit 1）：仍在 main，工作区未提交改动导致无法安全切到 day-info。未采集、未推送。
- 已如实汇报并再次提示用户先提交或 stash；未做任何分支操作。
