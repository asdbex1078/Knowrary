# 选型对照：LLM SDK

> **结论：不强依赖，装了就用。anthropic SDK 在就走 SDK，不在就走 `urllib` 打 REST。**
> 冲突：**0 条**。这是这个系列里唯一一条"两边都要"的选型。
> 触发线：无——已经是"有则用、无则兜底"，两条路都在用例里覆盖着。
> 代码：`tools/knowrary/llm_backend.py`（`_ask_anthropic` / `_ask_anthropic_sdk` /
> `_ask_anthropic_rest` / `_ask_openai` / `_post_json`）。
> 判据与总表：《[选型判据](选型判据.md)》。

---

## 1. 现在是什么

`llm_backend.py:233`：

```python
def _ask_anthropic(prompt, provider, model):
    ...
    if importlib.util.find_spec("anthropic") is None:
        return _ask_anthropic_rest(prompt, api_key, base, model, max_tokens)
    return _ask_anthropic_sdk(prompt, api_key, base, model, max_tokens)
```

三种 provider：

| type | 怎么发 | 密钥 |
|---|---|---|
| `claude-cli` | `subprocess` 跑 `claude -p`，复用本机 Claude Code 登录 | **不需要** |
| `anthropic` | 装了 SDK 走 `client.messages.stream`，没装走 `urllib` 打 `/v1/messages` | `api_key` 或 `ANTHROPIC_API_KEY` |
| `openai` | `urllib` 打 `/chat/completions`（OpenAI / DeepSeek / 通义 / Ollama / vLLM 都是这一路） | 可选（本地模型填 `none`） |

运行期依赖只有 FastAPI / uvicorn / pydantic / watchfiles，**LLM 后端本身零第三方依赖**。

---

## 2. 为什么要有 REST 那一路

**多一个必装的 SDK，就多一条"开源之后别人装不上"的路。**

而且 SDK 只覆盖一家。OpenAI 兼容协议那一侧要接 DeepSeek、通义、Ollama、vLLM，
真按 SDK 来就是装四个包、写四套 usage 字段映射；
走 `urllib` 是一个 `_post_json` 打所有人，usage 在一处归一化：

```python
u["input_tokens"]      = int(raw.get("prompt_tokens") or 0)
u["output_tokens"]     = int(raw.get("completion_tokens") or 0)
u["cache_read_tokens"] = int((raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
```

---

## 3. 为什么还留着 SDK 那一路

**它的流式实现比手写的好，装了不用是浪费。** 重连、分块边界、`stop_reason` 的处理都免费。
所以判断条件是 `find_spec` 而不是配置项——**装了就自动升级，不用改任何配置**。

---

## 4. 这一条和「不用 Agent 框架」不矛盾

这是这份系列里最容易被当成自相矛盾的一处，所以写清楚：

> **SDK 是"帮你发一个 HTTP 请求"，框架是"帮你跑一个循环"。**
> 前者不碰消息列表的形状，后者的全部价值就在于碰它。

判据 0.2 数的是**约束冲突**，不是依赖数量。SDK 冲 0 条，进；
框架冲约束 2 和 4，不进（见《[Agent框架](Agent框架.md)》）。

一个具体佐证：缓存断点那件事（`_cached`）是在 `llm_backend.py` 自己这一层做的，
**SDK 那条路和 REST 那条路拿到的是同一份已经摆好断点的消息列表**——
因为 SDK 只负责把它发出去。换成框架，这一步就得钻进它的格式化流程里改。

---

## 5. 代价

- **两条路要各测一遍。** 用例里两路都覆盖（装没装 SDK 的行为差异）。
- **REST 那一路的流式是自己写的**（`_sse`）。这是唯一真正手写的复杂部分，
  也是 SDK 那一路存在的理由——能用现成的就别用手写的。
- **新 API 特性要手动跟。** SDK 会自动带上，REST 那一路得自己补 header 和字段。
