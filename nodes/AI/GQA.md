---
name: 分组查询注意力GQA
field: AI
year: 2023
tags: AI应用
desc: MHA 与 MQA 之间的插值：分 g 组、组内共用 K/V，且能从现成 MHA checkpoint 续训出来
layer: AI应用
---
# 分组查询注意力GQA

## 描述
MHA 与 MQA 之间的插值：分 g 组、组内共用 K/V，且能从现成 MHA checkpoint 续训出来

## 关系
- 基于:: [[KV-Cache]]
- 对比:: [[MLA]]
- 演化为:: [[MLA]] (2024) — 不再共用 K/V，改成压成低维 latent
