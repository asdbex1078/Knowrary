# 我的知识库

这是一个 **Knowrary 知识库（vault）**：只有数据，没有程序。用
[Knowrary](https://github.com/asdbex1078/Knowrary) 打开它——启动服务后在
「设置 → 知识库」里选中这个目录。也可以用 Obsidian 直接打开本目录。

| 目录 | 放什么 |
| --- | --- |
| `nodes/` | 知识节点，一个知识点一个 md，按领域分子目录 |
| `fields/` | 领域总览，每个顶层领域一个文件 |
| `assets/` | 节点里引用的图片 |
| `relation-types.json` | 关系类型表：5 个族，具体类型可以自己加 |
| `.knowrary/` | 机器数据：图的摆位、项目、复习记录、LLM 配置 |

节点怎么写、关系怎么连，见 Knowrary 仓库里的 `doc/规范文档/Markdown文档规范.md`。

**密钥不会进这里**：LLM 配置写在 `.knowrary/llm.local.json`，被 `.gitignore` 挡着；
没有它也能跑（回落到本机 Claude Code 的 `claude -p`）。
