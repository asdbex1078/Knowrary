"""把模型那段话解析成 JSON，以及解析不出来时怎么留痕。

**为什么在 core 而不是服务层**：CLI（`knowrary.py article`）和网页
（`/api/import/propose`）问的是同一个模型、拿的是同一种回答，容错却曾经是两套——
CLI 用 `extract_json`（失败直接 `SystemExit`，不留痕），服务端用 `parse_json`
（失败记 `issues.jsonl`）。同一次模型抽风，命令行那条只剩一句报错，
下次遇到照样两眼一抹黑。所以只留这一份。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .issues import record as record_issue

log = logging.getLogger(__name__)


def carve(text: str) -> str:
    """从一段话里抠出第一个完整的 JSON 对象。

    模型经常在 JSON 前后加一句「好的，这是题目：」或者半个围栏，只剥首尾围栏不够用。
    按花括号配对扫一遍（跳过字符串里的括号和转义），比正则可靠。
    """
    start = text.find("{")
    if start < 0:
        return text
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def parse_json(raw: str, context: str = "", vault: Path | None = None) -> dict:
    """把回答解析成 dict。模型偶尔会套 ``` 围栏或直接答非所问，解析失败只警告不抛。

    解析不出来时**必须留痕**：调用已经花了钱和时间，界面上只剩一句「没出出题来」的话，
    下次遇到照样两眼一抹黑。原文前 600 字进问题流（`.knowrary/issues.jsonl`）。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    for candidate in (text, carve(text)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    log.warning("LLM 返回的不是合法 JSON%s，raw=%s", f"（{context}）" if context else "", (raw or "")[:200])
    if vault is not None:
        try:
            record_issue(vault, "llm", f"模型没给出合法 JSON（{context}）", where="parse_json",
                         detail=(raw or "")[:600])
        except OSError as exc:
            log.warning("问题流没记上：%s", exc)
    return {}
