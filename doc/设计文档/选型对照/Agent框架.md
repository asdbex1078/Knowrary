# 选型对照：Agent 框架（LangChain / LangGraph）

> **结论：不用。自研 agent 循环，约 1500 行 Python，零 agent 框架。**
> 冲突：**约束 2**（能力边界写在服务端）、**约束 4**（消息列表只增不改）——冲两条。
> 触发线：工具数超过约 30 个（现在 11 个）而需要按场景动态装配，或者真要跑多 agent 编排。
> 代码：`server/chat.py`（`_run` / `_assemble` / `_invoke` / `_step_calls`）、
> `server/turns.py`（续接缓存）、`tools/knowrary/llm_backend.py`（`_cached` / `_chat_claude_cli`）。
> 判据与总表：《[选型判据](选型判据.md)》。相关：《Agent架构与选型》§3.2（同一结论的六行版）。

会话记录里出现过一次原话："**是否需要用 langchain？**"（2026-09-15）。结论是不用。
原文只有六行，"不用"写得像态度不像判断，所以这一篇把它展开成六处代码级对照。

---

## 0. 先说清楚它是哪一层的东西

**LangChain 不是"调模型的 SDK"，它是一层 agent 运行时。** 这一点决定了后面全部分歧：

引入它不是"多一个库"，是**把循环的所有权交出去**——循环归框架跑，你往它的钩子里塞东西。
如果你对循环里每一步都没有特殊要求，这笔交易非常划算；下面六处，每一处都是"这一步必须能改"。

> 下面的 LangChain 代码是**示意**，只用来说明"东西挂在哪一层"。
> 具体 API 随版本变（`AgentExecutor` → LCEL → LangGraph 已经变过几轮），
> 但"循环在库里、你在钩子里"这个形状没变过，分歧也在这个形状上。

---

## 1. 循环本身

**LangChain 版：**

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(model=ChatAnthropic(model="claude-opus-5"),
                           tools=[search_nodes, read_node, propose_changes, ...],
                           prompt=system_prompt)
result = agent.invoke({"messages": [...]})      # 循环在库里面转
```

**现在**（`server/chat.py:1601`）：

```python
for _ in range(MAX_STEPS):
    text, calls = "", []
    for ev in _stream(vault, turn["messages"], tools, op=..., session=...):
        if ev["type"] == "delta": yield ev
        else: text, calls, turn["usage"] = ev["text"], ev["calls"], ev["usage"]
    if not calls:
        turn["answer"], turn["last_raw"] = strip_tools(text), text
        break                                     # 不再调工具 = 这段就是答案
    turn["said"].append(strip_tools(text))
    results = yield from _step_calls(vault, req, calls, turn)
    turn["messages"] = turn["messages"] + [{"role": "assistant", ...}] + results
```

**差别不在行数**——两边都短。差别在于第二段里**每一行都是可以改的**，而后面五条正是"这一行必须改"。

顺带记两个只有自己写才顺手的决定：

- **没有 finish 工具、没有 final answer 标记**，"不再调工具"就是终止条件。
- **`said` 和 `answer` 分开**：中途那些"我先查一下"进 `trace`，界面折叠成过程条，不拼进正文。
  早先拼在一起，真实使用里最费时间的事变成了"在一堆过程里找那几句有营养的"。

---

## 2. 工具白名单：换一档口径

LangChain 的工具绑在 agent 对象上（`bind_tools` / 构造参数）。这里有三档口径 × 复习开关，于是：

```python
AGENTS = {("教练", True):  create_react_agent(model, all_tools, coach_prompt),
          ("教练", False): create_react_agent(model, tools_minus_quiz, coach_prompt_no_review),
          ("面试", True):  ...}        # 笛卡尔积，每加一个开关翻一倍
```

**现在**：`tools_of(stance, vault)` 返回一个元组（`server/chat.py:1200`），
同一份 `TOOLS_SPEC` 渲染出**说明书、JSON Schema、白名单**三样东西。
加一个开关 = 元组里少两个名字，没有第二个对象要造。

更要紧的是**调了表外工具时的行为**（`server/chat.py:1486` `_invoke`）：

```python
fn = TOOLS.get(name) if name in allowed else None
if fn is None:
    return f"这一档口径下没有 `{name}` 这个工具。可用的是：{'、'.join(sorted(allowed))}。", {}
```

不抛异常，把话说给模型听，对话继续往下走。同一个函数里还有两条也是这样：
同参数重复调用直接还回上次结果并明说别再调了；工具自己炸了要告诉模型它炸了、同时记进 `issues.jsonl`。

**三条非正常路径没有一条是"报错"。这是产品行为，不是异常处理。**
LangChain 那侧对应的是 `ToolException` / `handle_tool_error`，
默认要么往上冒、要么塞一句固定文案，做成"说人话 + 列出可用清单 + 不中断"得去改 executor 的错误分支。

> 这条直接对着**约束 2**：能力边界写在服务端。
> "表里写着能用、调了却说没有"是最让人发火的那种 bug，所以说明书和实际权限必须是同一份数据。

---

## 3. 缓存断点

这条最能说明问题。`tools/knowrary/llm_backend.py:731`：

```python
def _cached(messages):
    """在最后一条**非 system** 的消息上打缓存断点"""
    idx = next((i for i in range(len(messages) - 1, -1, -1)
                if messages[i].get("role") != "system"), None)
    ...
    out[idx] = {**out[idx], "content": [{"type": "text", "text": content,
                                         "cache_control": {"type": "ephemeral"}}]}
```

要求是：挂在**倒着数第一条非 system 消息**的**最后一个 content block** 上。两个原因——

- 队尾那条 system 是会变的图谱快照，必须留在断点之后（它怎么变都不动前面整段对话的缓存）；
- mid-conversation system message 上打 `cache_control` 本身就会 400。

LangChain 里消息是 `HumanMessage` / `AIMessage` 对象，转成 Anthropic wire format
发生在 `ChatAnthropic` 内部。要做这件事得把 content 手写成 blocks 塞进 `additional_kwargs`
（版本相关，随 integration 升级会变），或者子类化覆盖格式化方法。
**然后你还得保证图的 state reducer 不在中间重排消息**——因为下一条。

---

## 4. 消息列表只增不改

`_assemble`（`server/chat.py:1467`）：

```python
prior = turns.resume(vault, llm_session_key(req), history)
messages = [*prior, history[-1]] if prior else _rebuild(vault, req, history, dropped)
snapshot = _graph_snapshot(vault)
if _last_snapshot(messages) != snapshot:      # 没变就不贴，贴一条就动一次前缀
    messages.append({"role": "system", "content": snapshot})
```

上一轮结束时的**完整内部列表**（含工具往返）存下来，下一轮原样接着发，只追加新的那句话。

LangChain 的 Memory 抽象（`ConversationBufferMemory` 一类，以及 LangGraph 的 checkpointer）
干的事是"把历史存起来，下一轮**重新组装**成 prompt"。**重新组装**正是问题所在：
它会摘掉或压缩 tool 往返、按自己的模板重排、`trim_messages` 会从中间删。
前缀一变，anthropic 的缓存断点和 openai 的自动前缀缓存**同时失效**。

> **这是四条约束里最晚想明白、代价最大的一条。** 一句话：
> **省钱的机制要的是字节级不变，而框架 Memory 的职责恰恰是重新生成。**
> 两者不是"配置一下就好"的关系，是职责本身对立。

---

## 5. 流式与自定义事件

SSE 现在有 9 种事件：`delta` `tool` `card` `project` `points` `list_edit` `review` `question` `done`。
循环里想推什么就 `yield` 什么，`_step_calls` 一边跑工具一边 `yield from _tool_events(...)`。

LangChain 的等价物是 `astream_events` + 自定义 `CallbackHandler`：
回调里能收到 `on_tool_start` / `on_tool_end`，再接到自己的 SSE 通道上。

但 `card` 这种事件**不是"工具开始/结束"**——它是 `_tool_propose` 内部调了
`curation.preview()` 算出 diff 之后的产物（**和 `/api/changes` 的 dry_run 是同一份代码**，
所以卡片上看到的就是会写进去的）。回调的粒度给不了，只能从工具函数里往外传
（全局队列 / contextvar）。**这就是"绕着框架写"的标准形态。**

---

## 6. claude-cli 那条后端

零配置那条路（复用本机 Claude Code 登录，不填任何密钥）是：
把整段对话渲染成文本 transcript、贴一段围栏协议说明、`claude -p` 跑、
再用正则把 ` ```knowrary {...}``` ` 解析回工具调用（`llm_backend.py:540`–`629`）。

LangChain 没有这个 provider。要接得自己写一个 `BaseChatModel` 子类，
实现 `_generate` / `_stream` / 工具协议翻译 / usage 统计 / 会话续接——
**比现在这份实现还长**，而且得长在框架的接口约定里。

> 砍掉这条路等于"想聊天先去开个 API key"。
> 2026-09-20 当天先砍掉了 claude-cli 的对话能力，当天又加回来（《Agent架构与选型》§9）：
> **能力该由配置决定，不该由一次架构选择替用户决定。**

---

## 7. 什么时候 LangChain 是对的

不是"框架没用"。它值钱的地方，逐条对一下这个项目：

| 它的强项 | 这个项目 |
|---|---|
| 一堆现成的 loader / splitter / retriever | 只有一个 vault，`core.index` 自己就是检索层 |
| RAG 那一整套 | 零向量库，`search_nodes` 是按词拆的字面匹配（见《[向量库与RAG](向量库与RAG.md)》） |
| LangSmith 追踪面板 | **这是真损失**，见 §8 |
| 多人团队要统一写法 | 单人项目 |
| 多 agent 编排、人在环审批节点 | 口径差异用提示词解决；审批是服务端的卡片机制 |

**触发线**：工具数超过约 30 个而需要按场景动态装配，或者真要跑多 agent 编排——
到那时"自己写的调度"会开始比框架的贵。

---

## 8. 代价，以及补了什么

诚实记两条：

- **没有开箱的追踪面板。** 补了两份更小的：用量账本 `llm-usage.json`
  （按 op / 按天 / 缓存命中）和出错流水 `issues.jsonl`（只留最近 500 条）。
- **没有现成的重试策略。** 现在是"工具炸了就把话说给模型听，让它自己决定要不要换个方式再来"，
  没有指数退避那一层。

> 一个旁证：这个 vault 的 Claude Code 会话目录里堆了 505 个 session，
> 其中 473 个是应用自己发起的调用。
> **473 段机器会话没有一次栽在协议本身**——自己写的那层协议翻译不是脆弱环节。
