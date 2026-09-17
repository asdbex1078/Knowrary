---
name: 多头注意力MHA
field: AI
year: 2017
desc: Transformer 原版注意力：h 个 head 各有独立 K/V 投影——MQA/GQA/MLA 都是在砍它的 K/V
layer: AI应用
---
# 多头注意力MHA

## 描述
Transformer 原版注意力：h 个 head 各有独立 K/V 投影——MQA/GQA/MLA 都是在砍它的 K/V

## 它在这条线上的位置

Transformer 原版的注意力就是 MHA：h 个 head 各自有独立的 W_Q / W_K / W_V，各算各的注意力，最后 concat 过一次 W_O。

后面的 MQA / GQA / MLA **全在动 K/V 这一半，Q 那一半没人碰**——所以三个变体都以 MHA 为基线，而不是彼此的下一步。

## 为什么它会成为瓶颈

decode 每生成一个 token 都要把历史 KV 读一遍，而 MHA 的 KV cache 是

```
2 × 层数 × h × d_head × 上下文长度
```

`h` 在这个乘积里是**满的**。这就是内存墙里的容量墙，也是后面三条路存在的理由：

- **MQA**：h 份 K/V 压成 1 份，省 h 倍，掉质量
- **GQA**：分 g 组、组内共用，省 h/g 倍；还能把现成 MHA checkpoint 的 h 份 K/V 投影 mean pooling 合并后 uptraining 迁移过来
- **MLA**：不走「共用」这条路，改成把 K/V 压成低维 latent

## 待补

- **为什么非要「多头」不可**：单头差在哪、h 个头实际分工了什么——这次没聊。
- **减头之后 W_O 怎么办**：MQA/GQA 砍的是 K/V，Q 还是 h 份、concat 出来的维度没变，所以 W_O 大概不用动？没确认。
- 上一轮那个问题我还欠着没答：MQA 的 Q 还是 h 份吗，如果是，「多头」到底还剩什么。

## 关系
- 部件:: [[Transformer]] (2017)
- 演化为:: [[MQA]] (2019) — h 份 K/V 压成 1 份
- 演化为:: [[GQA]] (2023) — 分 g 组、组内共用 K/V
- 演化为:: [[MLA]] (2024) — K/V 压成低维 latent
