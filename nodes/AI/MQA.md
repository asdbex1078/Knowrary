---
name: 多查询注意力MQA
field: AI
year: 2019
tags: AI应用
desc: 所有 head 共用一组 K/V，KV cache 直接缩到 1/h——砍得最狠，也最掉质量
layer: AI应用
---
# 多查询注意力MQA

## 描述
所有 head 共用一组 K/V，KV cache 直接缩到 1/h——砍得最狠，也最掉质量

## 关系
- 基于:: [[KV-Cache]]
- 演化为:: [[GQA]] (2023) — 从"全共用"退半步：分 g 组、组内共用
