"""桌面界面：选表、查重、滑条过滤、导出。"""

from __future__ import annotations

import shutil
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from app.config import AppConfig, load_config, save_config, template_path
from app.embed import EmbedError, probe_embed
from app.excel_io import TemplateError, export_pairs
from app.pipeline import RunResult, run_dedup

DEFAULT_THRESHOLD = 0.82
# 下载模板 / 导出：同一套次要色，表示输入输出
_IO_BTN = {
    "fg_color": ("gray70", "gray40"),
    "hover_color": ("gray60", "gray35"),
    "text_color": ("gray15", "gray90"),
}


def _short(text: str, limit: int = 36) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


class DedupApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("试题文义查重")
        self.geometry("1120x720")
        self.minsize(900, 600)

        self.cfg: AppConfig = load_config()
        self.result: RunResult | None = None
        self.excel_path = tk.StringVar(value="")
        self.url_var = tk.StringVar(value=self.cfg.embed_base_url)
        self.model_var = tk.StringVar(value=self.cfg.embed_model)
        self.key_var = tk.StringVar(value=self.cfg.embed_api_key)
        self.threshold = tk.DoubleVar(value=DEFAULT_THRESHOLD)
        self.status = tk.StringVar(value="请选择标准模板 Excel（须含工作表「正式题目」）")
        self.busy = False

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        pad = {"padx": 16, "pady": 8}
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=12, pady=12)

        # 窗口标题已说明用途，这里只放操作步骤
        steps = ctk.CTkFrame(root, fg_color="transparent")
        steps.pack(fill="x", padx=4, pady=(0, 8))
        step_font = ctk.CTkFont(size=13)
        for line in (
            "1、下载模板并按格式填写试题。",
            "2、使用语义比较时先配置向量模型，再选择 Excel 进行查重（支持文本 + 语义混合）。",
            "3、查重结束后，通过调节相似度滑轨得出理想的分界线。",
        ):
            ctk.CTkLabel(
                steps, text=line, font=step_font, text_color=("gray25", "gray75"), anchor="w"
            ).pack(anchor="w", pady=1)

        # 向量配置在上，选文件紧挨开始按钮
        emb = ctk.CTkFrame(root)
        emb.pack(fill="x", **pad)

        url_row = ctk.CTkFrame(emb, fg_color="transparent")
        url_row.pack(fill="x", padx=8, pady=(10, 4))
        ctk.CTkLabel(url_row, text="向量服务 URL", width=100, anchor="w").pack(side="left")
        ctk.CTkEntry(
            url_row,
            textvariable=self.url_var,
            placeholder_text="http://127.0.0.1:11434/v1 或 https://api.siliconflow.cn/v1",
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        mk_row = ctk.CTkFrame(emb, fg_color="transparent")
        mk_row.pack(fill="x", padx=8, pady=(4, 10))
        ctk.CTkLabel(mk_row, text="模型", width=100, anchor="w").pack(side="left")
        ctk.CTkEntry(
            mk_row, textvariable=self.model_var, placeholder_text="qwen3-embedding:0.6b", width=220
        ).pack(side="left", padx=(6, 16))
        ctk.CTkLabel(mk_row, text="API Key").pack(side="left")
        ctk.CTkEntry(
            mk_row,
            textvariable=self.key_var,
            placeholder_text="在线服务必填，本地可空",
            show="*",
            width=240,
        ).pack(side="left", padx=(6, 12))
        self.btn_save = ctk.CTkButton(mk_row, text="保存配置", width=90, command=self._save_cfg)
        self.btn_save.pack(side="left")
        self.btn_clear = ctk.CTkButton(
            mk_row, text="清空配置", width=90, command=self._clear_cfg, **_IO_BTN
        )
        self.btn_clear.pack(side="left", padx=(8, 0))

        file_row = ctk.CTkFrame(root)
        file_row.pack(fill="x", **pad)
        ctk.CTkButton(
            file_row,
            text="下载模板",
            width=110,
            command=self._download_template,
            **_IO_BTN,
        ).pack(side="left", padx=8, pady=10)
        ctk.CTkButton(file_row, text="选择 Excel", width=110, command=self._pick_file).pack(
            side="left", padx=(0, 8), pady=10
        )
        ctk.CTkEntry(file_row, textvariable=self.excel_path).pack(
            side="left", fill="x", expand=True, padx=(0, 8), pady=10
        )

        # 查重动作和阈值过滤同一色块
        action = ctk.CTkFrame(root)
        action.pack(fill="x", **pad)

        run_row = ctk.CTkFrame(action, fg_color="transparent")
        run_row.pack(fill="x", padx=8, pady=(10, 4))
        self.btn_run = ctk.CTkButton(
            run_row,
            text="开始查重",
            width=120,
            command=self._start,
            fg_color=("#2FA572", "#2FA572"),
            hover_color=("#258c60", "#258c60"),
        )
        self.btn_run.pack(side="left")
        self.progress = ctk.CTkProgressBar(run_row, width=280)
        self.progress.pack(side="left", padx=12)
        self.progress.set(0)
        ctk.CTkLabel(run_row, textvariable=self.status).pack(
            side="left", padx=4, fill="x", expand=True
        )

        filt = ctk.CTkFrame(action, fg_color="transparent")
        filt.pack(fill="x", padx=8, pady=(4, 10))
        ctk.CTkLabel(filt, text="相似度 ≥").pack(side="left")
        self.lbl_th = ctk.CTkLabel(filt, text="82%", width=48)
        self.lbl_th.pack(side="left")
        self.slider = ctk.CTkSlider(
            filt,
            from_=0.50,
            to=0.99,
            number_of_steps=49,
            variable=self.threshold,
            command=self._on_slide,
            width=360,
        )
        self.slider.pack(side="left", padx=8)
        self.lbl_hit = ctk.CTkLabel(filt, text="命中 0 对")
        self.lbl_hit.pack(side="left", padx=8)
        self.btn_export = ctk.CTkButton(
            filt,
            text="导出当前结果",
            width=120,
            command=self._export,
            state="disabled",
            **_IO_BTN,
        )
        self.btn_export.pack(side="right")

        table = ctk.CTkFrame(root)
        table.pack(fill="both", expand=True, padx=16, pady=(4, 8))
        cols = (
            "seq",
            "score",
            "row_a",
            "row_b",
            "code_a",
            "code_b",
            "type_a",
            "type_b",
            "stem_a",
            "stem_b",
            "opt_a",
            "opt_b",
        )
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=16)
        headings = {
            "seq": "序号",
            "score": "相似度",
            "row_a": "原表行A",
            "row_b": "原表行B",
            "code_a": "编号A",
            "code_b": "编号B",
            "type_a": "题型A",
            "type_b": "题型B",
            "stem_a": "题干A",
            "stem_b": "题干B",
            "opt_a": "选项A",
            "opt_b": "选项B",
        }
        widths = {
            "seq": 50,
            "score": 72,
            "row_a": 72,
            "row_b": 72,
            "code_a": 80,
            "code_b": 80,
            "type_a": 72,
            "type_b": 72,
            "stem_a": 300,
            "stem_b": 300,
            "opt_a": 180,
            "opt_b": 180,
        }
        for key, title in headings.items():
            self.tree.heading(key, text=title)
            self.tree.column(key, width=widths[key], minwidth=widths[key], stretch=False, anchor="w")
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        table.grid_rowconfigure(0, weight=1)
        table.grid_columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        yscroll.grid(row=0, column=1, sticky="ns", pady=(8, 0), padx=(0, 8))
        xscroll.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(0, 8))

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=26, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

    def _sync_cfg_from_form(self) -> None:
        self.cfg.embed_base_url = self.url_var.get().strip()
        self.cfg.embed_model = self.model_var.get().strip()
        self.cfg.embed_api_key = self.key_var.get().strip()

    def _save_cfg(self) -> None:
        if self.busy:
            messagebox.showwarning("请稍候", "正在处理，请完成后再保存配置。")
            return
        self._sync_cfg_from_form()
        url = self.cfg.embed_base_url
        model = self.cfg.embed_model
        # 三项都空：纯文本模式，直接存
        if not url and not model and not self.cfg.embed_api_key:
            self._write_cfg("已保存", "未配置向量服务，查重将只使用文本相似度。")
            return
        if not url or not model:
            messagebox.showwarning(
                "配置不完整",
                "请同时填写向量服务 URL 和模型名；若只用文本查重，请点「清空配置」。",
            )
            return
        self.busy = True
        self.btn_save.configure(state="disabled")
        self.btn_clear.configure(state="disabled")
        self.status.set("正在验证向量服务…")
        threading.Thread(target=self._validate_and_save, daemon=True).start()

    def _validate_and_save(self) -> None:
        try:
            dim = probe_embed(
                self.cfg.embed_base_url, self.cfg.embed_model, self.cfg.embed_api_key
            )
        except EmbedError as exc:
            msg = str(exc)
            self.after(0, lambda m=msg: self._save_failed(m))
            return
        except Exception as exc:  # noqa: BLE001
            msg = f"验证失败：{exc}"
            self.after(0, lambda m=msg: self._save_failed(m))
            return
        self.after(0, lambda d=dim: self._save_ok(d))

    def _save_ok(self, dim: int) -> None:
        self.busy = False
        self.btn_save.configure(state="normal")
        self.btn_clear.configure(state="normal")
        self._write_cfg("验证通过，已保存", f"向量服务可用，维度 {dim}。")
        self.status.set("向量服务验证通过")

    def _save_failed(self, msg: str) -> None:
        self.busy = False
        self.btn_save.configure(state="normal")
        self.btn_clear.configure(state="normal")
        self.status.set("向量服务验证失败，未保存")
        messagebox.showerror("无法保存", f"{msg}\n\n配置未写入，请检查地址、模型或 API Key。")

    def _write_cfg(self, title: str, body: str) -> None:
        try:
            save_config(self.cfg)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        messagebox.showinfo(title, body)

    def _clear_cfg(self) -> None:
        if self.busy:
            messagebox.showwarning("请稍候", "正在处理，请完成后再清空。")
            return
        self.url_var.set("")
        self.model_var.set("")
        self.key_var.set("")
        self._sync_cfg_from_form()
        self._write_cfg("已清空", "向量配置已清空，查重将只使用文本相似度。")
        self.status.set("向量配置已清空")

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择试题 Excel",
            filetypes=[("Excel", "*.xls *.xlsx"), ("所有文件", "*.*")],
        )
        if path:
            self.excel_path.set(path)

    def _download_template(self) -> None:
        """把内置 template.xls 另存到用户指定位置。"""
        src = template_path()
        if not src.is_file():
            messagebox.showerror("找不到模板", "程序未附带 template.xls，请重新打包或放到程序目录。")
            return
        dest = filedialog.asksaveasfilename(
            title="保存试题模板",
            defaultextension=".xls",
            initialfile="试题导入模板.xls",
            filetypes=[("Excel 97-2003", "*.xls"), ("所有文件", "*.*")],
        )
        if not dest:
            return
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        messagebox.showinfo("已保存", dest)

    def _on_slide(self, _value=None) -> None:
        th = float(self.threshold.get())
        self.lbl_th.configure(text=f"{th:.0%}")
        self._refresh_table()

    def _start(self) -> None:
        if self.busy:
            return
        path = self.excel_path.get().strip()
        if not path:
            messagebox.showwarning("提示", "请先选择 Excel 文件。")
            return
        self._sync_cfg_from_form()
        self.busy = True
        self.btn_run.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self.progress.set(0)
        self.status.set("正在查重…")
        threading.Thread(target=self._worker, args=(path,), daemon=True).start()

    def _worker(self, path: str) -> None:
        try:
            result = run_dedup(path, self.cfg, on_progress=self._progress_from_thread)
        except TemplateError as exc:
            msg = str(exc)
            self.after(0, lambda m=msg: self._fail(m))
            return
        except Exception as exc:  # noqa: BLE001 — 界面线程需要兜底
            msg = f"查重失败：{exc}"
            self.after(0, lambda m=msg: self._fail(m))
            return
        self.after(0, lambda r=result: self._ok(r))

    def _progress_from_thread(self, msg: str, cur: int, total: int) -> None:
        def apply() -> None:
            self.status.set(msg if not total else f"{msg}  {cur}/{total}")
            if total > 0:
                self.progress.set(cur / total)
            else:
                self.progress.set(0.15)

        self.after(0, apply)

    def _fail(self, msg: str) -> None:
        self.busy = False
        self.btn_run.configure(state="normal")
        self.progress.set(0)
        self.status.set(msg)
        messagebox.showerror("无法查重", msg)

    def _ok(self, result: RunResult) -> None:
        self.busy = False
        self.btn_run.configure(state="normal")
        self.progress.set(1)
        self.result = result
        mode = "文本 + 语义" if result.has_vectors else "仅文本"
        extra = ""
        if result.fallback_reason:
            extra = f"（向量失败已回退：{result.fallback_reason}）"
            messagebox.showwarning("已回退到 BM25", result.fallback_reason)
        self.status.set(f"完成 · {mode} 比较· 共 {len(result.questions)} 题 {extra}")
        self._refresh_table()

    def _refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self.result:
            self.lbl_hit.configure(text="命中 0 对")
            self.btn_export.configure(state="disabled")
            return
        th = float(self.threshold.get())
        rows = self.result.scored(th)
        qs = self.result.questions
        for seq, (pair, score) in enumerate(rows, start=1):
            a, b = qs[pair.i], qs[pair.j]
            self.tree.insert(
                "",
                "end",
                values=(
                    seq,
                    f"{score:.2%}",
                    a.excel_row,
                    b.excel_row,
                    a.code,
                    b.code,
                    a.qtype,
                    b.qtype,
                    _short(a.stem, 40),
                    _short(b.stem, 40),
                    _short(a.option_summary, 48),
                    _short(b.option_summary, 48),
                ),
            )
        self.lbl_hit.configure(text=f"命中 {len(rows)} 对")
        self.btn_export.configure(state="normal" if rows else "disabled")

    def _export(self) -> None:
        if not self.result:
            return
        th = float(self.threshold.get())
        rows = self.result.scored(th)
        if not rows:
            messagebox.showinfo("无结果", "当前阈值下没有题对。")
            return
        src = Path(self.excel_path.get().strip())
        stem = src.stem or "导出"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = filedialog.asksaveasfilename(
            title="导出查重结果",
            defaultextension=".xlsx",
            initialfile=f"{stem}-查重结果-{stamp}.xlsx",
            initialdir=str(src.parent) if src.parent.is_dir() else None,
            filetypes=[("Excel", "*.xlsx")],
        )
        if not dest:
            return
        try:
            export_pairs(
                dest,
                self.result.questions,
                [p for p, _ in rows],
                [s for _, s in rows],
            )
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        messagebox.showinfo("已导出", dest)

    def _on_close(self) -> None:
        self.destroy()


def run() -> None:
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    app = DedupApp()
    app.mainloop()
