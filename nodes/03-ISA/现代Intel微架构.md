---
name: 现代Intel微架构
field: 计算机系统
year: 1995
layer: 体系结构
tags:
  - ISA
desc: 现代 Intel： 前端：CISC 指令集 后端：将复杂指令拆成类似 RISC 的 μops（微操作）
source: 知识图谱zhis.jpg
---
# 现代Intel微架构

现代 Intel：
- 前端：CISC 指令集
- 后端：将复杂指令拆成类似 RISC 的 μops（微操作）

> **年份锚点：1995。** Pentium Pro（P6）第一次把 x86 的 CISC 指令在前端译码成类 RISC 的 μops
> 再乱序执行——也就是上面这两行说的那件事。
>
> （`year` 取「历史视图锚点年」口径，不是唯一发明年。）

## 关系
- 实现:: [[x86]] — 前端吃 x86 指令，后端拆成 RISC 式 μops
