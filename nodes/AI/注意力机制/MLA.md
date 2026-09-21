---
name: 多头潜在注意力MLA
field: AI
year: 2024
desc: 不共用 K/V，改成把 K/V 压成低维 latent 存进 cache——换了一条路，cache 更小且质量不降
layer: AI应用
---
# 多头潜在注意力MLA

## 描述
不共用 K/V，改成把 K/V 压成低维 latent 存进 cache——换了一条路，cache 更小且质量不降

## 关系
- 基于:: [[KV-Cache]]
- 对比:: [[MQA]] — 同样为了砍 KV cache，走的是两条路：MQA 砍 head 数，MLA 压成低维 latent
- 部件:: [[Transformer]] — 同上：砍 KV cache 的三条路都长在注意力这一层里
