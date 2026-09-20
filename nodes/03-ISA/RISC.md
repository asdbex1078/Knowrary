---
name: RISC
field: 计算机系统
type: 流派
year: 1980
start_year: 1980
tags:
  - ISA
desc: 精简指令集。一条指令完成一个操作，cpu 的压力变小，编译器的压力增大，可以提高 cpu 的主频
source: 知识图谱zhis.jpg
layer: 体系结构
color: "#3f8f6e"
---
# RISC

精简指令集。一条指令完成一个操作，cpu 的压力变小，编译器的压力增大，可以提高 cpu 的主频。

主要特点：定长指令、load/store 架构、软件/编译器做复杂操作。
1. ARM
2. RISC-V
3. MIPS

执行：多条简单指令 → CPU → 输出。

## 关系
- 演化为:: [[现代Intel微架构]] (1995) — RISC 的乱序流水反过来进了 CISC 的后端
