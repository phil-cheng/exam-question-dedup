"""右侧 B 相对左侧 A 的字级差异。

只用标准库 difflib.SequenceMatcher（Ratcliff/Obershelp），
不自己实现 Myers。题干按字比；选项先按 A–F 对齐，再对比每一项。
"""

from __future__ import annotations

import difflib

EMPTY = "（空）"
_LETTERS = "ABCDEF"


def diff_spans(left: str, right: str) -> list[tuple[int, int]]:
    """right 上需要标红的半开区间 [start, end)。A 多出来的字在右侧没有对应，不画。"""
    sm = difflib.SequenceMatcher(None, left, right, autojunk=False)
    spans: list[tuple[int, int]] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or j1 == j2:
            continue
        spans.append((j1, j2))
    return spans


def field_display(left: str, right: str) -> tuple[str, list[tuple[int, int]]]:
    left = (left or "").strip()
    right = (right or "").strip()
    if not right:
        return EMPTY, [(0, len(EMPTY))] if left else []
    return right, diff_spans(left, right)


def _opt_map(options: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for letter, opt in zip(_LETTERS, options):
        text = (opt or "").strip()
        if text:
            out[letter] = text
    return out


def option_display(
    left_opts: list[str], right_opts: list[str]
) -> tuple[str, list[tuple[int, int]]]:
    """拼出 B 的选项正文，并给出相对 A 同序号选项的标红区间。"""
    left_map = _opt_map(left_opts)
    lines: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    for letter, opt in zip(_LETTERS, right_opts):
        text = (opt or "").strip()
        if not text:
            continue
        if lines:
            pos += 1
        prefix = f"{letter}. "
        line = prefix + text
        lines.append(line)
        base = pos + len(prefix)
        a_text = left_map.get(letter)
        if a_text is None:
            spans.append((base, pos + len(line)))
        else:
            for start, end in diff_spans(a_text, text):
                spans.append((base + start, base + end))
        pos += len(line)
    if not lines:
        return EMPTY, [(0, len(EMPTY))] if left_map else []
    return "\n".join(lines), spans
