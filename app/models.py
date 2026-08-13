"""领域对象：试题、候选题对。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Question:
    # Excel 中的数据行号（1-based，方便对照原表）
    excel_row: int
    code: str
    qtype: str
    stem: str
    options: list[str] = field(default_factory=list)
    # 仅供人工复核展示，绝不进入 search_text
    answer: str = ""

    @property
    def search_text(self) -> str:
        # 查重文本 = 题干 + 非空选项；答案故意不拼进来
        parts = [self.stem.strip()]
        for letter, opt in zip("ABCDEF", self.options):
            text = (opt or "").strip()
            if text:
                parts.append(f"{letter}.{text}")
        return "\n".join(parts)

    @property
    def option_summary(self) -> str:
        bits = []
        for letter, opt in zip("ABCDEF", self.options):
            text = (opt or "").strip()
            if text:
                bits.append(f"{letter}.{text}")
        return " ".join(bits)

    @property
    def option_lines(self) -> str:
        # 复核窗分行展示，空选项不占行
        bits = []
        for letter, opt in zip("ABCDEF", self.options):
            text = (opt or "").strip()
            if text:
                bits.append(f"{letter}. {text}")
        return "\n".join(bits)


@dataclass
class PairResult:
    i: int
    j: int
    cosine: float | None
    bm25_norm: float

    def score(self, alpha: float, has_vectors: bool) -> float:
        """滑条卡的分。有向量：α·余弦 + (1-α)·BM25；无向量：只用 BM25。α 默认 0.7。"""
        if has_vectors and self.cosine is not None:
            return alpha * self.cosine + (1.0 - alpha) * self.bm25_norm
        return self.bm25_norm
