---
name: DeepSeek-V3
field: AI
type: stub
year: 2024
tags:
  - 大模型
  - 长上下文
  - 推理优化
desc: DeepSeek 于 2024 年发布的开源大语言模型，支持 128K 上下文，引入 engram 等推理优化机制
layer: AI应用
---
# DeepSeek-V3

DeepSeek-V3 是 DeepSeek 于 2024 年发布的开源大语言模型，主打长上下文（128K）和高效推理。

## 关键特性
- 支持 128K token 上下文长度
- 推理加速技术：engram（动态 KV Cache 压缩）、flash attention 优化等
- 开源权重与推理代码

## 线头
- 架构基础：Transformer → 「Transformer」节点（已存在）
- 对比模型：LLaMA-3、Qwen2 → 待建
- 优化机制：engram → 本节点下「系统优化」小节

## 系统优化

### engram
engram 是 DeepSeek-V3 引入的动态 KV Cache 压缩机制，用于长上下文推理：
- **目的**：降低显存占用（KV Cache 从 O(L) 降至 O(√L) 量级）和计算量；
- **原理**：不按位置丢弃（如 sliding window），而是根据当前 token 对历史各位置的 attention score 动态判断重要性；score 高的 KV 视为“记忆痕迹（engram）”保留，低的批量压缩或丢弃；
- **注意**：engram 与分词（tokenization）无关 —— 它工作在已 tokenized 的 ID 序列之后，只操作 KV Cache，不触碰输入层。

## 容易搞混的
- 和「n-gram」：名字含 gram 纯属巧合；n-gram 是统计语言模型（AI应用层），engram 是系统级推理优化（系统软件层）；二者无演化、无依赖、无共同目标。
- 和「sliding window」：前者是注意力驱动的软裁剪，后者是位置驱动的硬截断。

## 关系
