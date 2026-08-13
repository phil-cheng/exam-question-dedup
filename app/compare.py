"""人工复核：左右对照窗，题型 / 题干 / 选项 / 答案横向对齐。"""

from __future__ import annotations

from collections.abc import Callable

import tkinter as tk

import customtkinter as ctk

from app.models import Question
from app.textdiff import EMPTY, field_display, option_display

_LABEL_FG = ("gray10", "gray90")
_DIFF_FG = "#C0392B"
_DIFF_TAG = "diff"


def _or_empty(text: str) -> str:
    text = (text or "").strip()
    return text if text else EMPTY


def _inner(box: ctk.CTkTextbox):
    return getattr(box, "_textbox", box)


def _set_box(box: ctk.CTkTextbox, text: str) -> None:
    # 不设 disabled：CTk 会把字发灰，复核时反而难读。编辑靠 Key 拦截。
    box.delete("1.0", "end")
    box.insert("1.0", _or_empty(text))
    box.see("1.0")


def _set_box_diff(box: ctk.CTkTextbox, text: str, spans: list[tuple[int, int]]) -> None:
    box.delete("1.0", "end")
    box.insert("1.0", text if text else EMPTY)
    inner = _inner(box)
    inner.tag_configure(_DIFF_TAG, foreground=_DIFF_FG)
    inner.tag_remove(_DIFF_TAG, "1.0", "end")
    for start, end in spans:
        if start < end:
            inner.tag_add(_DIFF_TAG, f"1.0+{start}c", f"1.0+{end}c")
    box.see("1.0")


def _set_label_diff(label: ctk.CTkLabel, left: str, right: str) -> None:
    text, spans = field_display(left, right)
    label.configure(text=text, text_color=_DIFF_FG if spans else _LABEL_FG)


class CompareDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        on_prev: Callable[[], None],
        on_next: Callable[[], None],
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self._on_prev = on_prev
        self._on_next = on_next
        self._on_close = on_close

        self.title("试题比对")
        self.geometry("980x640")
        self.minsize(760, 480)
        self.transient(master)

        body_font = ctk.CTkFont(family="Microsoft YaHei UI", size=13)
        head_font = ctk.CTkFont(family="Microsoft YaHei UI", size=15, weight="bold")
        dim_font = ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 8))
        self.lbl_title = ctk.CTkLabel(top, text="", font=head_font, anchor="w")
        self.lbl_title.pack(side="left", fill="x", expand=True)
        self.btn_next = ctk.CTkButton(top, text="下一对", width=90, command=self._on_next)
        self.btn_next.pack(side="right")
        self.btn_prev = ctk.CTkButton(top, text="上一对", width=90, command=self._on_prev)
        self.btn_prev.pack(side="right", padx=(0, 8))

        grid = ctk.CTkFrame(self)
        grid.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        grid.grid_columnconfigure(0, weight=0, minsize=52)
        grid.grid_columnconfigure(1, weight=1, uniform="ab")
        grid.grid_columnconfigure(2, weight=1, uniform="ab")
        grid.grid_rowconfigure(2, weight=3)
        grid.grid_rowconfigure(3, weight=2)

        ctk.CTkLabel(grid, text="").grid(row=0, column=0, padx=8, pady=(8, 4))
        self.lbl_ha = ctk.CTkLabel(grid, text="A", font=dim_font, anchor="w")
        self.lbl_hb = ctk.CTkLabel(grid, text="B", font=dim_font, anchor="w")
        self.lbl_ha.grid(row=0, column=1, sticky="ew", padx=8, pady=(8, 4))
        self.lbl_hb.grid(row=0, column=2, sticky="ew", padx=8, pady=(8, 4))

        self.lbl_type_a, self.lbl_type_b = self._dim_row(grid, 1, "题型", body_font)
        self.box_stem_a, self.box_stem_b = self._text_row(grid, 2, "题干", dim_font, body_font)
        self.box_opt_a, self.box_opt_b = self._text_row(grid, 3, "选项", dim_font, body_font)
        self.lbl_ans_a, self.lbl_ans_b = self._dim_row(grid, 4, "答案", body_font)

        foot = ctk.CTkLabel(
            self,
            text="Esc 关闭    ← → 上一对 / 下一对    右侧红色为与左侧不同的文字",
            text_color=("gray40", "gray70"),
            anchor="w",
        )
        foot.pack(fill="x", padx=20, pady=(0, 10))

        self.bind("<Escape>", lambda _e: self._close())
        self.bind("<Left>", lambda _e: self._on_prev())
        self.bind("<Right>", lambda _e: self._on_next())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(40, self._focus_self)

    def _dim_row(self, grid, row: int, title: str, font) -> tuple[ctk.CTkLabel, ctk.CTkLabel]:
        ctk.CTkLabel(grid, text=title, font=font, anchor="nw", width=48).grid(
            row=row, column=0, sticky="nw", padx=8, pady=6
        )
        left = ctk.CTkLabel(grid, text="", font=font, anchor="w", justify="left", wraplength=420)
        right = ctk.CTkLabel(grid, text="", font=font, anchor="w", justify="left", wraplength=420)
        left.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        right.grid(row=row, column=2, sticky="ew", padx=8, pady=6)
        return left, right

    def _text_row(
        self, grid, row: int, title: str, dim_font, body_font
    ) -> tuple[ctk.CTkTextbox, ctk.CTkTextbox]:
        ctk.CTkLabel(grid, text=title, font=dim_font, anchor="nw", width=48).grid(
            row=row, column=0, sticky="nw", padx=8, pady=6
        )
        left = self._box(grid, body_font)
        right = self._box(grid, body_font)
        left.grid(row=row, column=1, sticky="nsew", padx=8, pady=6)
        right.grid(row=row, column=2, sticky="nsew", padx=8, pady=6)
        return left, right

    def _box(self, parent, font) -> ctk.CTkTextbox:
        box = ctk.CTkTextbox(
            parent,
            font=font,
            wrap="word",
            activate_scrollbars=True,
            fg_color=("gray95", "gray20"),
            text_color=("gray10", "gray90"),
        )
        # 外壳和内部 Text 都要绑，否则焦点在正文时方向键不会翻对
        for widget in (box, getattr(box, "_textbox", None)):
            if widget is None:
                continue
            widget.bind("<Left>", lambda _e: self._on_prev() or "break")
            widget.bind("<Right>", lambda _e: self._on_next() or "break")
            widget.bind("<Escape>", lambda _e: self._close() or "break")
            widget.bind("<Key>", self._readonly_key)
            try:
                widget.configure(takefocus=0)
            except tk.TclError:
                pass
        return box

    @staticmethod
    def _readonly_key(event) -> str | None:
        # 允许滚动、复制、全选；其余按键丢掉，避免改到试题正文
        if event.keysym in {"Up", "Down", "Prior", "Next", "Home", "End"}:
            return None
        if event.state & 0x4 and event.keysym.lower() in {"c", "a"}:
            return None
        return "break"

    def render(
        self,
        seq: int,
        total: int,
        score: float,
        a: Question,
        b: Question,
        *,
        raise_window: bool = True,
    ) -> None:
        self.lbl_title.configure(
            text=f"{a.code}  ↔  {b.code}    相似度 {score:.2%}    第 {seq} / {total} 对"
        )
        self.lbl_ha.configure(text=f"A · {a.code}")
        self.lbl_hb.configure(text=f"B · {b.code}")
        self.lbl_type_a.configure(text=_or_empty(a.qtype), text_color=_LABEL_FG)
        _set_label_diff(self.lbl_type_b, a.qtype, b.qtype)
        _set_box(self.box_stem_a, a.stem)
        stem_text, stem_red = field_display(a.stem, b.stem)
        _set_box_diff(self.box_stem_b, stem_text, stem_red)
        _set_box(self.box_opt_a, a.option_lines)
        opt_text, opt_red = option_display(a.options, b.options)
        _set_box_diff(self.box_opt_b, opt_text, opt_red)
        self.lbl_ans_a.configure(text=_or_empty(a.answer), text_color=_LABEL_FG)
        _set_label_diff(self.lbl_ans_b, a.answer, b.answer)
        self.btn_prev.configure(state="normal" if seq > 1 else "disabled")
        self.btn_next.configure(state="normal" if seq < total else "disabled")
        if raise_window:
            self.deiconify()
            self.lift()
            self.after(20, self._focus_self)

    def _focus_self(self) -> None:
        try:
            self.focus_force()
        except Exception:  # noqa: BLE001 — 窗口已关时忽略
            pass

    def _close(self) -> None:
        self._on_close()
        self.destroy()
