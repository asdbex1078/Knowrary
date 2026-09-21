---
name: 分组查询注意力GQA
field: AI
year: 2023
desc: MHA 与 MQA 之间的插值：分 g 组、组内共用 K/V，且能从现成 MHA checkpoint 续训出来
layer: AI应用
---
# 分组查询注意力GQA

## 描述
MHA 与 MQA 之间的插值：分 g 组、组内共用 K/V，且能从现成 MHA checkpoint 续训出来

## 关系
- 基于:: [[KV-Cache]]
- 对比:: [[MLA]] — 同一堵墙的两条路：GQA 共用 K/V 少存几份，MLA 压成低维 latent；MLA 不是从 GQA 走出来的下一步
- 部件:: [[Transformer]] — 它是注意力那一层的一种实现，不是 Transformer 的后继
