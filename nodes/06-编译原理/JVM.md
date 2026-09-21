---
name: JVM
field: 计算机系统
year: 1995
layer: 系统软件
tags:
  - JVM
  - 编译原理
desc: "Java / Scala / Kotlin → 字节码（IR）→ JVM → OS。 JVM 是\"后端复用\"思想的典型：不同前端语言共享同一份 IR 与运行时"
source: 知识图谱zhis.jpg
---
# JVM

Java / Scala / Kotlin → 字节码（IR）→ JVM → OS。
JVM 是"后端复用"思想的典型：不同前端语言共享同一份 IR 与运行时。

## 关系
- 演化为:: [[GraalVM]] (2018) — Graal 换掉 C2、Truffle 接进多语言
- 依赖:: [[OS]] — JVM 的运行基座
- 依赖:: [[字节码]] — 不同前端语言共享同一份 IR 与运行时
- 对比:: [[LLVM]] — 同是「后端复用」，JVM 的 IR 活在运行时（字节码 + GC），LLVM 的活在编译期
