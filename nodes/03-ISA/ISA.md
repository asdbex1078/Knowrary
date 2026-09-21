---
name: ISA
field: 计算机系统
year: 1964
layer: 体系结构
tags:
  - ISA
desc: "指令集架构。 cpu 控制计算机 控制了 cpu = 控制计算机 如何控制 cpu？ ==> ISA 指令架构"
source: 知识图谱zhis.jpg
---
# ISA

指令集架构。
1. cpu 控制计算机
2. 控制了 cpu = 控制计算机
3. 如何控制 cpu？ ==> ISA 指令架构

通过 ISA 控制 CPU 的"控制单元"。机器语言 ISA 指令是 CPU 生产厂商提供的用于操作控制器的。

两大流派：[[CISC]]、[[RISC]]；现代 Intel 是二者混合（[[现代Intel微架构]]）。
汇编在 ISA 之上做助记符 → [[汇编语言]]；编译器后端目标就是 ISA → [[编译器后端]]。

> **年份锚点：1964。** IBM System/360 第一次把「体系结构（architecture）」与「实现（implementation）」
> 分开——同一套指令集可以有快慢不同的多台机器，ISA 从此是一份契约，而不是某台机器的接线说明书。
>
> （`year` 取「历史视图锚点年」口径，不是唯一发明年。）

## 关系
- 实例:: [[CISC]]
- 实例:: [[现代Intel微架构]]
- 实例:: [[RISC]]
- 演化为:: [[RISC]] (1980) — Patterson 1980 正面针对的就是 ISA 一路复杂化（后来被回溯叫 CISC）
- 控制:: [[控制器]] — ISA 指令最终控制的对象就是控制单元
