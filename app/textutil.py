"""查重用分词：汉字 2/3-gram + 英文/数字整词。"""

from __future__ import annotations

import re

_LATIN = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """近重复用字 n-gram，比 jieba 稳：改一两个字仍共享大量 2/3-gram。"""
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []
    # Python、O(n) 这类整词单独保留
    for m in _LATIN.finditer(text):
        tokens.append(m.group(0))
    for m in _CJK.finditer(text):
        chunk = m.group(0)
        n = len(chunk)
        if n == 1:
            tokens.append(chunk)
            continue
        if n >= 2:
            tokens.extend(chunk[i : i + 2] for i in range(n - 1))
        if n >= 3:
            tokens.extend(chunk[i : i + 3] for i in range(n - 2))
    return tokens
