---
name: knowrary-import
description: 把一篇文章 / 一段笔记 / 一次学习心得拆成知识节点，存进 Knowrary 个人知识库 vault（第二版 Markdown 规范：一节点一 md、`- 类型:: [[目标]]` 带类型边、5 个关系族）。触发：用户说"导入到知识图谱"、"融入我的体系"、"把这篇文章拆成节点"、"knowrary import"、"存进 Knowrary"、"学到了 X，记进图谱"。
---

# knowrary-import：文章 → Knowrary 知识节点

> **这是三个入口里可选的那个。** 另外两个不依赖 Claude Code、也不依赖任何一家模型：
> 命令行 `knowrary.py article`（一次 LLM 调用，走配置里的 `roles.learn`，可以是 DeepSeek / 通义 / Ollama）、
> 网页活动栏「导入」面板（`POST /api/import/propose` → 审核卡 → `/api/import`）。
> 三条共用同一份提示词（`prompts/article.md`）、同一个长度闸、同一套三级匹配（`core.proposal`）、
> 同一份 JSON 容错（`core.llmjson`）、同一条写回通道（`core.translate` → `core.plan` → `core.commit`）。
> 用户在 Claude Code 里说导入时走本 skill；他自己点网页或敲命令行时不需要你。

## 固定路径

**下面全是仓库内相对路径，基准是本仓库根目录**——这是项目级 skill，触发时的工作目录就是它，
命令直接敲即可。万一不在（用户 cd 去了子目录），先回到仓库根再跑，别去拼绝对路径。

- 迁移/校验脚本：`tools/knowrary/knowrary.py`
- 规范原文：`doc/规范文档/Markdown文档规范.md`
- 提示词全文（拆分原则与输出 JSON 结构）：`tools/knowrary/prompts/article.md`
- 默认 vault：**不是仓库目录**（2026-09-22 程序与知识库拆分）。不写 `--vault` 时，脚本自己
  按「环境变量 `KNOWRARY_VAULT` → 设置页选中的库（`~/.knowrary/config.json` 的 `current`）」解析，
  所以**这里不写死任何一个库的路径**。节点在 `nodes/`，领域总览在 `fields/`，
  解析器只扫这两个目录。**拿不准就先跑一句 `knowrary.py check` 看它认的是哪个库**，别猜

## 流程

1. **确认输入**：文章路径（或用户直接贴的文本，先写到 scratchpad 一个 .md）、目标 vault、`field`（顶层领域，如 `计算机体系结构` / `AI-Agent` / `JVM`）。field 用户没说就从文章主题判断并在结果里说明。
   - **成品（HTML 深度文档，2026-10-22）**：文章也可以是 vault 成品目录（默认 `works/`）里的 `.html`——「成品即原文」，不往 `articles/` 存副本，拆出的节点 `sources` 直接链 `[[works/….html#页面锚点]]`（`--article` 传 html 路径即可，抽文本和锚点由 `core.plan_work` 管）。**html 必须先由用户自己整理进成品目录**，还在别的仓库里的先让用户拷进来，别替他拷。
2. **拿上下文**（不要自己遍历 vault）：
   ```bash
   python3 tools/knowrary/knowrary.py context --vault <vault> --article <文章> --field <领域>
   ```
   输出三段：可用关系类型、可链接的已有节点 id（不是全量：只有与文章相关的、它们一跳的邻居、
   和 `--field` 领域下的节点，封顶 200 个）、与文章最相关的已有节点及其边。
   图里别的节点不在列表里，不确定的概念不要猜 id，链到不存在的 id 会被补成 stub 等用户确认。
3. **读文章，写方案 JSON**：按 `prompts/article.md` 里的拆分原则和输出结构，直接在 scratchpad 写 `plan.json`。要点：
   - **粒度由用户定**（2026-10-13）：开拆前问一句「粗 / 中 / 细」，没说就用「中」。三档口径在 `tools/knowrary/core/article.py` 的 `GRAINS`
     （提示词里的 `{{grain}}` 就是它）：粗 2～4 个、中 3～8 个、细按机制拆 8～15 个；**应用场景、工具清单、案例默认写进所属概念的 `## 例子`**，不单独成点。
     一个节点 = 一个可独立复习的概念；文章章节不等于节点。
   - 正文写足不精简：以原文为主体，作者原话、例子、代码、数字一个不丢；原文一笔带过的前置概念和机制自己补足、段首标「（补充）」。每个节点都要有 `## 例子`（至少一个具体案例，原文没有就自己举并标「（补充）」）。从 `## 描述` 开始，不含 H1 / frontmatter / `## 关系`。
   - 关系优先连到**已有节点**，类型只从类型表选；方向从本节点指向目标；不要两侧重复写。
   - 正文里只能 `[[链接]]` 已有节点、本次新节点或 stubs；其余概念要么进 stubs，要么不链接。
   - 和已有节点是同一概念的，放 `enrich`（旧名 `merge_into` 仍认），只写已有节点**缺的**内容；它会被**追加**到老节点正文末尾并带"补充自《来源》（日期）"引言，绝不改原文。
     字段是 `existing`（目标节点 id）/ `content`（要追加的 Markdown）/ `why`（可选，一句话说明补的是什么，会拼进引言）——**不是 `target` / `body`**；写错了 dry-run 只报一句 `⚠ enrich 的目标 `` 不存在，跳过` 就过去了，很容易看漏。`content` 里不许带 `## 关系`（关系放 `relations`），带了整段跳过。
   - 每条关系给 `confidence`（0～1）。连到已有节点且低于 0.8 的边不进 md，落 `.knowrary/pending.json` 待审；新节点之间的边直接写。
   - `year` 只在有明确年代且值得进历史视图时填，不猜。
4. **先 dry-run 给用户看**：
   ```bash
   python3 tools/knowrary/knowrary.py apply plan.json --vault <vault> --field <field> --article <文章路径> --dry-run
   ```
   **原文默认保留**（2026-10-13 起和网页一致）：`--article` 给的那篇存进原文目录（库里原文目录下的就地引用、一模一样的复用、别处的存一份），
   节点 `sources` 链到原文，能对上 `from_section` 的链到那一节。文章名默认取文件名，`--source` 可改；
   用户贴的文本先存成 scratchpad 里以文章标题命名的 `.md` 再传 `--article`。用户明说不要原文才加 `--no-article`。
   同名不同文会直接退出——换个 `--source` 再来。
   脚本把方案翻成变更集（新建节点 / 补充老节点 / 待审边），校验 id、目标、类型，然后打印**每个文件的 diff**（新文件给全文，老节点给 unified diff）。把摘要（新建几个、补充了哪些老节点、直接写入几条边、几条待审、被丢弃的边）讲给用户，补充老节点的 diff 要让用户看过。
   dry-run 之后用户说「这几个合并」「这个再拆」：直接改 `plan.json`（合并时被并掉的名字进 `aliases`、别的点指向它的边和 `[[链接]]` 改指新点，
   正文全部保留不精简），再 dry-run 给他看。
5. **用户确认后**去掉 `--dry-run` 写入：原文先进库，落盘前自动备份；待审边记进 `.knowrary/pending.json`；方案与变更集存档到 `<vault>/.knowrary/imports/`。
6. **校验**：`python3 tools/knowrary/knowrary.py check <vault>`，错误必须为 0；警告（未登记类型、演化边缺 year）如实汇报。
7. 待审边不要替用户拍板：列出来即可，审核入口在网页端（后续阶段）。

## 不做的事

- 不改写已有节点正文（`enrich` 只追加）、不回填反向边（反链由解析器推导）。
- 不写坐标 / 分组进 frontmatter。
- 不为凑数拆节点；文章只有一个新概念就只建一个节点。
