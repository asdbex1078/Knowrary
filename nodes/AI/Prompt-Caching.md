---
name: Prompt-Caching
field: AI
year: 2024
layer: 系统软件
desc: 把 KV-Cache 跨请求持久化并计价——由此长出块边界、断点、TTL、写入溢价这套 KV-Cache 层不存在的规则
learned: 2026-09-17
---
# Prompt-Caching

## 描述

把 [[KV-Cache]] 从「一次推理内的显存结构」变成「跨请求、跨进程可复用且要计价的资源」。

多出来的只有两件事：**持久化**和**计价**。但正是这两件事，凭空长出了一整套 KV-Cache 层面根本不存在的规则——
块边界、断点、TTL、写入溢价、最小可缓存前缀。**烧钱全烧在这一层**，翻 KV-Cache 的原理一个字也解释不了。

> **年份锚点：2024。** Anthropic 2024-08 发布 prompt caching（需手动标断点），
> DeepSeek 同期上线硬盘上下文缓存，OpenAI 2024-10 DevDay 发布自动 prompt caching。
> 三家同年落地，不是某一家的专利。
>
> （`year` 取「历史视图锚点年」口径，不是唯一发明年。）

---

## 一、和 KV-Cache 的分界

| | KV-Cache | Prompt-Caching |
|---|---|---|
| 生命周期 | 一次 forward | 跨请求，5 分钟 / 1 小时 |
| 存在于 | 显存 | 服务端（各家自己管，可能落盘） |
| 关心什么 | 少算、少占 | **命中率**、写入溢价 |
| 出问题的症状 | 显存爆 / 变慢 | **账单变大，功能全对，不报错** |

最后一行是它最阴的地方：缓存失效**没有任何错误**，请求照样成功、结果照样对，只有账单知道。
所以它必须靠**监控**发现，不能靠测试发现。

---

## 二、先分清两条路：接入方式不同，能用的手段完全不同

**这一节是理解后面全部案例的前提。** 同样是 Claude 模型，接入方式不一样，能用的缓存手段
就完全不一样——同一个问题（「会变的东西放哪」）在两条路上甚至会得出**相反**的答案。

```
claude-cli（起一个 `claude -p` 子进程）
  我们 ──递一段纯文本──> claude -p ──它自己拼 JSON、自己打断点──> Anthropic API
       ↑ 只能给 stdin                ↑ 这一步我们完全够不着

anthropic（自己打 HTTP）
  我们 ──自己拼 JSON（含 cache_control）───────────────────────> Anthropic API
       ↑ 断点必须亲手标，不标就一个字都不缓存
```

| | anthropic | claude-cli |
|---|---|---|
| 我们能操作的 | JSON 里的每个 content block | 只有一段 stdin 文本 |
| 控制缓存的手段 | **标断点**（`cache_control`） | **控制位置**（让前缀别变） |
| 跨请求复用靠 | 断点 + 前缀匹配 | `--session-id` / `--resume` + 指纹校验 |
| 会变的东西放哪 | **最后**，扔在断点之后 | **最前**，钉死 |
| 图谱变了的代价 | 只重发那 261 字 | 整段重开 |

### 「claude-cli 没有断点」是错话

准确说法是：**断点存在，但不归我们管。**

账本里那个雷打不动的 `cache_read = 13103`，就是 Claude Code 自己打的断点在命中
（缓存它自己的系统提示 + 工具定义）：

```
cw=10870  cr=13103
cw=10942  cr=13103    ← 13103 从不变化 = 别人的断点在正常工作
cw=11014  cr=13103
```

断点机制一直在转，只是我们递进去的是一坨 stdin 文本，**连 content block 都摸不到**，
没有任何手段说「在我这段文本的第 X 处也打一个」。

所以两条路的手段是**互补**的，不是一个是另一个的简化版：

```
anthropic    有「断点之后」这个位置可以扔会变的东西
             → 把快照扔那儿，它怎么变都不动前面的缓存

claude-cli   没有这个位置，扔进去的一切都算进前缀
             → 只能让快照待在一个固定位置别动，真变了就认下整体重开
```

> **本项目现状**（`.knowrary/llm.local.json`）：`learn` / `review` 两个角色都指 `claude-cli`，
> 没有配 `anthropic` 类型的 provider。所以**所有 `cache_control` 相关的代码目前只有测试在跑**——
> 它是为「哪天把某个角色切到 anthropic」准备的。真正在省钱的是 `--resume` 那条路（案例 1）。

---

## 三、匹配粒度：块边界，不是逐 token

Anthropic 的缓存键是「从开头到某个**块边界**为止的整体」，**不是**「前 N 个 token 的内容」。
渲染顺序固定：`tools → system → messages`。

这导致一个反直觉的结论：**历史一个字没改，照样全部失效。**

把多轮对话拍平成一个不断变长的 text block 时：

```
第 N 次请求：
  ├──── 固定前缀 13103 ────┤├──── block_A 10870 ────┤
                          ↑                        ↑
                      断点①(可查)              断点②(可查)
  写入缓存表：hash(前缀) 和 hash(前缀+block_A)

第 N+1 次请求（block_B 的前 10870 token 和 block_A 逐字节相同）：
  ├──── 固定前缀 13103 ────┤├───── block_B 10942 ─────┤
                          ↑            ╎             ↑
                      断点①(可查)      ╎         断点②(可查)
                                      ╎
                              位置 23973 在这里
                              ← 上一轮缓存的结束点
                              ← 但它落在 block_B 肚子里，不是块边界，算不出 key
```

`block_A` 那条缓存**就躺在表里、内容也完全对得上，但这个请求算不出它的 key**——
因为 `block_A` 在新请求里已经不是一个块，而是 `block_B` 的前半截。
缓存表是**按块建的目录，不是按字建的索引**。

比方：把一本书每次重排版成一整章重印。第 2 版的第 1-30 页和第 1 版逐字相同，
但第 1 版的「章末书签」在第 2 版里落到了正文中间——书签只能插在章的边界上。

补充两条边角规则：

- **最多 4 个断点**。超了报错。
- **20 块回看窗口**：系统会在显式断点之前自动往回找最多 20 个块位置。
  一轮里连着追加超过 20 个块（大量 tool_use / tool_result）会漏掉上一条命中点——
  连续的 tool_use 串和 tool_result 串各自折叠成一个位置，所以一般够用。

---

## 四、具体案例：Knowrary 教练对话（2026-09-16 事故 → 2026-09-17 修复）

> 每个案例标题上都标了**是哪条路的事**（见第二节）。案例 4 和案例 5 是同一个问题
> 在两条路上的**相反答案**——不先分清路，那两节一定看不懂。


### 案例 1【claude-cli 路】`claude -p` 每轮拍平重发

**症状**（账本 `.knowrary/llm-usage.json`，相邻三次调用）：

```
cw=10870  cr=13103
cw=10942  cr=13103   ← 只多了 72 个新 token
cw=11014  cr=13103   ← 又只多了 72 个
```

`cache_read` 死钉在 13103（Claude Code 自己那段固定前缀），我们的 11k **命中率 0%**：

```
实付：10942 × $10/M                        = $0.1094
应付：10870 读 × $0.5/M + 72 写 × $6.25/M  = $0.0059    → 18.5 倍
```

46 次调用 $10.575，其中 **cache write 占 75%（$7.96）**。
而且 Claude Code 默认 **1 小时 TTL**，写入价是基础输入价的 **2×**——
**这段 prompt 不是「没省到钱」，是比压根不缓存还贵一倍。**

**病根**（`tools/knowrary/llm_backend.py` 的 `_render_transcript`）：

```python
# 错：把 system + 全部历史 + 新问题拍平成一个字符串，
#     CLI 收到后当成一条 user 消息 = 一个 content block。
#     这个块每轮都变长 → 每轮都是新块 → 每轮全量重写。
prompt = _render_transcript(messages)          # ← 每轮 11k，其中 10.9k 和上轮一样
subprocess.run(["claude", "-p"], input=prompt)  # ← 全新进程，无服务端会话
```

**修法**：每段对话分配一个 uuid，首轮 `--session-id`，之后 `--resume` 且**只发新增的几条**。
关键是**别盲目续**——存指纹，每次校验前缀真的没变才续：

```python
def _cli_session(key, messages, model):
    """返回 (extra_args, start)：start > 0 表示续上了，只发 messages[start:]。"""
    have = _CLI_SESSIONS.get(key)
    if (have and have["model"] == model and have["sent"] <= len(messages)
            and have["fingerprint"] == _fingerprint(messages[:have["sent"]])):
        return ["--resume", have["uuid"]], have["sent"]      # 指纹对得上才续
    _CLI_SESSIONS[key] = {"uuid": str(uuid.uuid4()), "sent": 0, "fingerprint": "", "model": model}
    return ["--session-id", _CLI_SESSIONS[key]["uuid"]], 0   # 对不上就老实开新的
```

三条保命设计，缺一个都会把对话弄炸：

1. **指纹校验**：前缀改过（截断、插提醒、改口径）就不续，不能只看 key 相同。
2. **`model` 进校验**：缓存按模型隔离，换模型必须开新的。
3. **失败回退**：`SystemExit` 时 `drop_cli_session()` 然后重发全文。
   会话状态只放进程内存，服务重启即失效——**续错一段的代价（模型看着别人的上下文答题）
   远大于多花的那点钱**。

**实测**（haiku，约 40k token 上下文，第二轮）：

```
重发全文    $0.0673   cache_write 22242
--resume    $0.0061   cache_write   423      → 11 倍
```

> **为什么收益这么大**：一个用户回合里常常夹着三四次工具往返，每次工具结果都要再问一遍模型。
> **那几次才是账单大头**，而它们之间只差末尾几百个字。

### 案例 2【anthropic 路】不标 `cache_control` 就一个字都不缓存

原来的 `_chat_anthropic` 结构其实是对的（真 messages 数组、system 拆出去），
但**一个 `cache_control` 都没有 = 一个字都不缓存**，每轮全历史按全价算：

```python
# 错：能跑，结果全对，就是贵 10 倍，而且不报错
payload = {"model": model, "max_tokens": max_tokens, "messages": rest}
payload["system"] = system          # 纯字符串，没有 cache_control
```

```python
# 对：两个断点
def _cached_system(blocks):
    """断点打在最后一块之前——最后一块留给会变的东西。"""
    if len(blocks) == 1:
        return [{"type": "text", "text": blocks[0], "cache_control": {"type": "ephemeral"}}]
    *stable, volatile = blocks
    return [{"type": "text", "text": "\n\n".join(stable),
             "cache_control": {"type": "ephemeral"}},     # 断点①：9000 字静态指令
            {"type": "text", "text": volatile}]           # 会变的排在断点之后

def _cached(messages):
    """断点②：打在最后一条消息上，把到此为止的整段对话存进缓存。
    一轮里那三四次工具往返就都成了命中而不是重新计费。
    前缀不够长时 API 直接忽略这个标记，不报错，所以不用判长度。"""
    *head, last = messages
    return [*head, {**last, "content": [{"type": "text", "text": last["content"],
                                         "cache_control": {"type": "ephemeral"}}]}]
```

### 案例 3【两条路都吃亏】把会变的东西挪出前缀

系统提示词里原来嵌着图谱现状：

```
{{overview}} → "85 个节点、46 条关系，其中 3 个还只是壳。领域分布：AI(11)、…"
```

**这几个数字会变**——教练的整个用途就是聊着聊着把新点入库，采纳一张变更卡节点数就 +1。
而缓存认的是**逐字节前缀**：中间插一个会变的数字，等于图谱一动，
它**后面**那 9000 字静态指令（工具表、格式、关系类型表、教练侧写）全部作废重买。

修法是拆成两条 system，**静态在前、会变在后**，断点打在中间：

```python
messages = [{"role": "system", "content": _system_prompt(...)},    # 9000 字，永不变
            {"role": "system", "content": _graph_snapshot(vault)}] # 261 字，随时变
```

这就是 `_cached_system` 里 `*stable, volatile = blocks` 的由来。数字怎么变都只影响它自己那 261 字。

### 案例 4【anthropic 路】会变的块该放哪：顶层 system 之外的第二次修正

> 下面这一节的结论是 **anthropic 专属**的。同一个问题在 claude-cli 上的答案正好相反，
> 见案例 5——照搬过去会出事，这正是 2026-09-17 那次回归。


案例 3 把图谱快照拆成**第二条顶层 system**、断点打在两者之间。看着对了，其实只修了一半：
**顶层 `system` 整体渲染在所有 `messages` 之前**。静态那 9000 字是保住了，
但图谱一动，**整个 messages 的缓存照样全丢**。

正解是 **mid-conversation system message**——把它作为 `{"role": "system"}` 追加到 `messages` **末尾**：

```python
messages = [{"role": "system", "content": 静态指令}] + history
messages.append({"role": "system", "content": 图谱快照})   # 坐在历史之后，变了只作废它自己
```

配套两处：顶层 `system` 只收**开头那一串** system（中途的留在原位），
缓存断点打在最后一条**非 system** 的消息上（会变的那条故意留在断点之后，
而且 mid-conversation system message 上打 `cache_control` 本身就会 400）。

**模型限制**：Opus 5 / Opus 4.8 / Fable 5 / Fable 5.1 支持，**Sonnet 5 不支持**
（400 `role 'system' is not supported on this model`）。
与其维护一张「哪个模型行」的表（一定会过期），不如撞上 400 再退一步——
折成 user 消息里的 `<system-reminder>` 重发一次：

```python
try:
    return _post_anthropic(messages, ...)
except SystemExit as exc:
    if "role" not in str(exc) or "system" not in str(exc) or not _has_mid_system(messages):
        raise                                    # 别的 400（比如余额不足）不能重发，白花钱
    return _post_anthropic(_fold_mid_system(messages), ...)
```

顺带一个安全性收益：user 文本里的「（系统提示）」谁都能伪造，`role: "system"` 不能，
它是**不可冒充的操作指令通道**。这也是为什么退化版要用 `<system-reminder>` 包起来——
退的是安全性，得说清楚退了什么。

### 案例 5【claude-cli 路】案例 4 的修法照搬过来，把 claude-cli 悄悄弄回了原点

> 案例 4 得出「会变的东西挂队尾」，那是**因为 anthropic 有断点、队尾在断点之后**。
> claude-cli 没有断点这个东西可用（第二节），照搬的结果是反着的。


改完案例 4，**测试全绿、功能全对、一个报错都没有**，但 claude-cli 那条路已经退回了
「每轮重发全文」——也就是这整篇笔记一开始要修的那个毛病。它藏了多久取决于谁去看账单。

#### 病灶：队尾是个**会动**的位置

服务端每次请求都重新拼消息列表：`[静态 system] + 历史 + [图谱快照]`。
所以快照永远在最后一个下标上，而最后一个下标每轮都往后跑：

```
        第 1 轮                          第 2 轮
      ┌──────────────────┐            ┌──────────────────┐
  [0] │ system  静态指令  │        [0] │ system  静态指令  │   ← 同
  [1] │ user    u1       │        [1] │ user    u1       │   ← 同
  [2] │ system  快照(85) │        [2] │ assistant  a1    │   ← ✗ 这里变了
      └──────────────────┘        [3] │ user    u2       │
                                  [4] │ system  快照(85) │   ← 被挤到这
                                      └──────────────────┘
```

快照本身**一个字都没改**（还是「85 个节点」），但它**换了位置**。
对前缀匹配来说，「同样的内容出现在不同下标」＝ 前缀变了。

#### 指纹校验是怎么判死的

claude-cli 续会话的条件是「**能证明**前缀没变」，靠一本进程内的记账本：

```
_CLI_SESSIONS["llm|s1|教练"] = {
    uuid        : 7f3a…                              ← CLI 那侧的会话 id
    sent        : 4                                  ← 它已经知道 4 条消息
    fingerprint : sha256([静态, u1, 快照, a1])        ← 那 4 条的指纹
}

第 2 轮进来，拿 messages[:4] 去对：
    实际  sha256([ 静态, u1,  a1 ,  u2  ])
    记账  sha256([ 静态, u1, 快照,  a1  ])
                        ────  ────
                     第 3、4 条对不上 → 指纹不等 → 不敢续 → 开新会话
```

**这一步本身是对的，不能改。** 「续错一段」意味着模型对着别人的上下文答题，
代价远大于多花的钱。错的是我们把一个会动的东西塞进了被校验的前缀里。

#### 代价（token 轴）

```
本该如此   ├── --resume，线上只发 u2 ──┤                     cache_write ≈ 几十
实际发生   ├──────── 11k 整段重新发一遍 ────────┤            cache_write ≈ 11k
                                                            ×  每一轮
```

实测复现（`_cli_session` 直接喂两轮）：

```
回归时          修好后
turn1: --session-id  start=0        turn1: --session-id  start=0     ← 首轮本来就该开新的
turn2: --session-id  start=0   ✗    turn2: --resume      start=4  ✓
```

#### 为什么 anthropic 不受影响：它的快照**从来没进过缓存**

两边对"前缀"的定义根本不是一回事：

```
anthropic —— 有断点，断点之前才是缓存条目
  第 1 轮   [顶层 system 静态] │ [u1]▲ [快照]
                              │     └── 断点。缓存条目到此为止
                              │            快照在断点之后，压根没进缓存
  第 2 轮   [顶层 system 静态] │ [u1] [a1] [u2]▲ [快照]
                              └───── 这一段命中 ─────┘   快照怎么动都无所谓

claude-cli —— 没有断点这回事，整个消息列表就是前缀本身
  第 1 轮   [静态] [u1] [快照]              → 指纹算的是全部，含快照
  第 2 轮   [静态] [u1] [a1] [u2] [快照]     → 前 4 条 ≠ 上面那 4 条 → 续不上
```

一句话：**anthropic 的「队尾」是「断点之后的垃圾桶」，claude-cli 没有垃圾桶，
你扔进去的一切都算进前缀。**

#### 修法：给别的后端钉回开头

```
            挪之前（队尾，会动）                  _hoist_system 之后（开头，钉死）
第 1 轮   [静态] [u1] [快照]                    [静态] [快照] [u1]
第 2 轮   [静态] [u1] [a1] [u2] [快照]           [静态] [快照] [u1] [a1] [u2]
                 └── 位置 2 变了 ✗ ──┘           └─ 前两条纹丝不动 ✓ ─┘ └ 只增不改 ┘
```

```python
def _hoist_system(messages):
    """把中途的 system 挪回开头那一串 system 的末尾——给 anthropic 以外的后端。"""
    head = 0
    while head < len(messages) and messages[head].get("role") == "system":
        head += 1
    moved = [m for m in messages[head:] if m.get("role") == "system"]
    return [*messages[:head], *moved,
            *(m for m in messages[head:] if m.get("role") != "system")] if moved else messages
```

钉回开头之后，快照进了被校验的前缀里**却不再移动**：
只有图谱**真的变了**（85 → 86）才换指纹，那时候前缀确实不一样了，本来就该重开一段。

```
图谱没变   [静态] [快照 85] [u1] [a1] [u2]   → --resume   ✓ 该续就续
图谱变了   [静态] [快照 86] [u1] [a1] [u2]   → --session-id ✓ 该断就断
```

#### 顺带查清的一件事（不是 bug，是设计）

**上一轮用过工具的话，下一轮本来就续不上**，和位置无关：

```
第 1 轮（带工具）  CLI 会话里最终有：[静态][快照][u1][a1][工具结果][a2]   6 条
第 2 轮 前端给的：                  [静态][快照][u1][a2][u2]             5 条
                                              ↑ 工具往返不进留档，前端不知道有过
                    记账本 sent=6 > 5 → 直接判定续不上
```

这是对的——CLI 那侧确实多知道几条我们这边没有的消息，不能装作一样。
**真正的大头本来也不在这里**：一个用户回合里那三四次工具往返是**同一个请求内**的连续调用，
它们之间一直是续着的（案例 1 实测的 11 倍就是这么省下来的）。跨回合续不上只损失一次。

> **提炼成通则：缓存友好的消息列表必须是 append-only（只增不改）的。**
>
> 而「把会变的东西放最后」和「只增不改」**天生打架**——最后一个位置会被后来的内容挤走。
> 三种化解方式：
>
> | 办法 | 谁能用 |
> |---|---|
> | mid-conversation system message（在历史之后插东西，不算改历史） | Anthropic（Opus 5 / 4.8 / Fable 5 系） |
> | 让会变的东西待在**固定**位置，靠「变了就整体重开」兜底 | 所有后端，本项目 claude-cli 走这条 |
> | `clear_at: "next_user_message"`：渲染一轮后留在原地被清空 | Anthropic beta，最完整的答案 |
>
> **没有第三种的时候，绝不能「插一条、下一轮删掉」——那是改历史，缓存从那一点之后全断。**
---

## 五、各家的处理方式（三种计费模型）

> **这一节讲的是「厂商」这个维度，和第二节的「接入方式」是两回事，别串。**
> claude-cli 和 anthropic 两条路最后都打到 Anthropic 的模型上，所以都适用下表的
> **Anthropic 那一列**；它们的区别在于「我们够不够得着那些旋钮」，不在于计费规则。
> 下表换的是**厂商**（换模型供应商），第二节换的是**接入方式**（同一个厂商的两种进门方式）。


| | Anthropic | OpenAI 系（含 DeepSeek / 通义） | Gemini |
|---|---|---|---|
| 怎么开启 | **手动** `cache_control` 断点 | **自动**，无需声明 | 隐式自动 + 显式 `cachedContents` 双轨 |
| 匹配粒度 | **内容块**边界 | **token 前缀**分片（OpenAI 128 / DeepSeek 64 对齐） | token 前缀 |
| 写入收费 | **有**：5m 1.25×，1h 2× | 无 | 显式缓存**按存储时长**计费（token·小时） |
| 命中收费 | 0.1× 输入价（Fable 5.1 是 0.025×） | 约 0.1–0.5× | 折扣价 |
| **没命中的代价** | **比不缓存更贵**（白付写入溢价） | 和不缓存一样，只是少个折扣 | 显式缓存**没命中也要付存储费** |
| 生命周期 | 你选 5m / 1h | 服务端定（分钟级不活跃即清） | 显式缓存你设 TTL |

三种心智模型完全不同，别串台：

- **Anthropic = 手动挡**：你标断点、你选 TTL、标错了罚钱。换来的是多层 TTL 和精确放置。
- **OpenAI 系 = 自动挡**：什么都不用管，不命中也不亏，但你控制不了。
- **Gemini 显式缓存 = 租仓库**：按占地面积 × 时长收租，**不用也照收**。适合一份大文档被反复问，
  不适合对话——对话每轮都在变，租了也白租。

### Anthropic 逐模型细节

**最小可缓存前缀（不够长就静默不缓存：不报错，`cache_creation_input_tokens: 0`）**

| 模型 | 最小前缀 |
|---|---:|
| Opus 5 / Fable 5 / Fable 5.1 | **512** |
| Opus 4.8 / Sonnet 5 / Sonnet 4.6 / Sonnet 4.5 | 1024 |
| Opus 4.7 / Haiku 3.5 | 2048 |
| **Opus 4.6 / Opus 4.5 / Haiku 4.5** | **4096** |

**这个数字不随代际单调下降**（4.6 是 4096，4.7 是 2048，4.8 是 1024，5 是 512）。
后果：一段 3K token 的 prompt 在 **Opus 5 上能缓存，在 Opus 4.6 / Haiku 4.5 上静默不缓存**。
换模型省钱的时候必须重新算这个，否则会出现「换了便宜模型反而更贵」。

**单价（$/MTok）**

| 模型 | 输入 | 输出 | cache read | write 5m | write 1h |
|---|---:|---:|---:|---:|---:|
| Opus 5 | 5 | 25 | 0.50 | 6.25 | 10.00 |
| Sonnet 5 | 2 | 10 | 0.20 | 2.50 | 4.00 |
| Haiku 4.5 | 1 | 5 | 0.10 | 1.25 | 2.00 |
| Fable 5.1 | 10 | 50 | **0.25**（0.025×，特例） | 12.50 | 20.00 |

**TTL 怎么选**（按「共享前缀的两次请求之间，起点到起点的间隔」）

| 间隔 | 选哪个 | 理由 |
|---|---|---|
| < 5 分钟 | **5 分钟** | 每次读都免费刷新计时器，永远热着；付 2× 是纯亏 |
| 5–60 分钟 | 1 小时 | 唯一值得付 2× 写入的窗口 |
| > 1 小时 | 都不行 | 定时预热（`max_tokens: 0` 重发）或接受冷启动 |

注意计时**从写/读那次请求的开始算起，生成时间算在里面**——一次生成跑了 4 分钟，
5 分钟的条目就只剩 1 分钟给下一次请求起跑。

**盈亏平衡**：5m TTL 两次请求回本（1.25× + 0.1× = 1.35× < 2×）；
1h TTL 要三次（2× + 0.2× = 2.2× < 3×）。**只问一次的东西别缓存。**

**Fable 5.1 特例**：read 只要 0.025×，所以 5–60 分钟那档**用保活比用 1h TTL 便宜**——
保持 5 分钟 TTL，快过期时用 `max_tokens: 0` 重发上一次请求刷新计时器，只花一次极便宜的读。

### 会悄悄作废整条链的操作

| 操作 | 后果 |
|---|---|
| 改 `tools`（增删、换顺序） | tools 渲染在位置 0 → **全部作废** |
| 换模型 | 缓存按模型隔离 → 全丢 |
| 改顶层 `output_config.effort` | messages 缓存作废（Opus 5 可用 per-message effort 绕开） |
| 改顶层 `system` | 它在 messages 之前 → 整段对话作废 |
| 换 workspace | 缓存按 workspace / org 隔离，不跨组织共享 |
| `speed: "fast"` 切换 | 也算换了配置，作废 |

---

## 六、怎么验（唯一的实锤）

响应 `usage` 三个字段，**它们的和才是真实 prompt 大小**：

| 字段 | 含义 |
|---|---|
| `cache_creation_input_tokens` | 这次写进缓存的（付了 1.25× / 2× 溢价） |
| `cache_read_input_tokens` | 这次从缓存读的（付了 0.1×） |
| `input_tokens` | **只是没命中的那点余数**，不是总量 |

**健康形状**——每轮读走此前全部，只写上一轮新增的：

```
第 1 轮：input=22   cache_creation=10870  cache_read=0
第 2 轮：input=0    cache_creation=72     cache_read=10870    ✅
第 3 轮：input=0    cache_creation=68     cache_read=10942    ✅
```

**事故形状**——`cache_read` 不动，`cache_write` 跟着对话涨：

```
cw=10870  cr=13103
cw=10942  cr=13103    ❌ 读的部分纹丝不动 = 只有别人的前缀在命中
```

读写比值：**健康 5–10×**，Knowrary 事故现场 **1.43×**。

### 常设监控怎么搭（测试够不着真实 API）

`cache_read_input_tokens > 0` 这件事**单测证明不了**——它要真的打一次 API。
所以拆成两层，各管一半：

```
     离线（每次跑测试）                    线上（每次看账单）
  ┌────────────────────────┐        ┌────────────────────────┐
  │ 前缀逐字节稳定吗？       │        │ 真的命中了吗？          │
  │                        │        │                        │
  │ 发两轮相同开头的对话，   │        │ 账本里算读 ÷ 写，        │
  │ 断言第二轮**原样包含**   │        │ 低于 3× 就在界面上报警   │
  │ 第一轮，位置都不许动     │        │                        │
  └────────────────────────┘        └────────────────────────┘
       挡住"前缀被改坏"                   挡住"前缀没坏但就是不命中"
```

**离线那半**——这条用例如果早在，前面两次事故都会当场变红：

```python
第一轮 = 发一轮([u1])
第二轮 = 发一轮([u1, a1, u2])
for i, (a, b) in enumerate(zip(第一轮, 第二轮)):
    assert a["role"] == b["role"] and a["content"] == b["content"], \
        f"第 {i} 条变了，前缀断在这里"
```

它同时挡住两类病因：**内容变了**（system prompt 里混进时间 / 节点数 / 未排序 JSON）
和**位置变了**（案例 5 那种被挤走）。

**线上那半**——比值算在账本里，摆在花销面板上：

```python
HEALTHY_RATIO = 3.0      # 健康的多轮循环在 5-10×；留余量，低于 3 才报
MIN_CALLS = 5            # 样本太少的比值没意义
MULTI_TURN = ("chat",)   # 只看多轮的
```

**为什么要挑着看**：出题 / 关系建议那类一问一答每次都是新前缀，比值天然贴着 0。
把它们算进告警等于天天在响，而**一个天天响的告警等于没有告警**——真正该看的多轮对话反而被淹了。
所以只盯多轮的 op，而且要够样本量。（这条也单独有用例钉着。）

> **这个检查必须是常设的，不能只在上线时看一次。**
> 最贵的缓存故障都是**回归**：写的时候好好的，几个月后有人往 system prompt 里加了个动态字段，
> 从此每次全 miss，没有任何报错，只有账单在涨。
> 做法：加一条集成测试断言「第二次相同请求 `cache_read_input_tokens > 0`」，或者直接监控这两个数。

---

## 七、通用规则（换谁都躲不掉）

1. **前缀必须逐字节稳定**——`datetime.now()`、会变的统计数字、未排序的 `json.dumps`、
   `set` 迭代顺序、f-string 拼进 system 的 user id，全是杀手。
2. **稳定的和易变的不能粘在同一个块里**（伪共享）。会变的一律往后排。
3. **分叉调用要复用父调用的完整前缀**——子 agent / 摘要 / 压缩另起一个请求时，
   `system`/`tools`/`model` 必须逐字节照抄，只在末尾追加，否则完全命不中父缓存。
4. **自动断点不是万能**：prompt 以「每次都不一样的尾巴」结尾时（检索结果、一次性问题），
   自动断点落在唯一尾巴后面 = 每次都付写入溢价、永远读不回来。
   这时要**手动把断点打在共享部分的结尾**。

---

## 关系

- 基于:: [[KV-Cache]] — 把一次推理内的 K/V 复用，变成跨请求可复用且计价的资源
- 类比:: [[高速缓存]] — 块边界≈cache line 对齐、命中/未命中、TTL≈逐出策略；把稳定前缀和易变尾巴粘进同一个块，就是伪共享

## 参考资料

- Anthropic Prompt Caching：https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- 事故现场与修复：`tools/knowrary/llm_backend.py`（`_cli_session` / `_cached_system` / `_cached`）、
  `server/chat.py`（`_graph_snapshot` / `llm_session_key`）、账本 `.knowrary/llm-usage.json`

## 待办

- [x] 把 `_graph_snapshot` 改成 messages 末尾的 `role: "system"` + Sonnet 5 的 400 兜底（2026-09-17，见案例 4）
- [x] 顺带修掉它给 claude-cli 带来的静默回归（2026-09-17，见案例 5）
- [x] 常设断言，分两层落地（2026-09-17，见第五节「常设监控怎么搭」）
- [ ] 生产环境跑满一天后回填真实读写比值（现在的 11 倍来自 haiku 40k 的对照实验）
