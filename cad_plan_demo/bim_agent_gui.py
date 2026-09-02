from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from tkinter import END, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from .bim.agent_controller import run_bim_agent


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"


class BimAgentGui:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("BIM 智能体 - Excel 生成 JSON")
        self.root.geometry("780x560")
        self.root.minsize(720, 520)

        self.excel_path = StringVar()
        self.notes_path = StringVar()
        self.output_dir = StringVar(value=str(PROJECT_ROOT / "outputs" / "bim_agent_gui"))
        self.api_key = StringVar(value=os.getenv("DEEPSEEK_API_KEY", ""))
        self.base_url = StringVar(value=os.getenv("BIM_LLM_BASE_URL", DEFAULT_BASE_URL))
        self.model = StringVar(value=os.getenv("BIM_LLM_MODEL", DEFAULT_MODEL))
        self.last_output_dir: Path | None = None

        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)

        title = ttk.Label(frame, text="Excel 到 Revit JSON 一键生成", font=("", 15, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

        self._path_row(frame, 1, "Excel 报告", self.excel_path, self._pick_excel)
        self._path_row(frame, 2, "设计总说明", self.notes_path, self._pick_notes, optional=True)
        self._path_row(frame, 3, "输出文件夹", self.output_dir, self._pick_output_dir)

        ttk.Label(frame, text="API Key").grid(row=4, column=0, sticky="w", pady=6)
        key_entry = ttk.Entry(frame, textvariable=self.api_key, show="*")
        key_entry.grid(row=4, column=1, sticky="ew", padx=(10, 8), pady=6)
        ttk.Label(frame, text="只用于本次运行").grid(row=4, column=2, sticky="w", pady=6)

        advanced = ttk.LabelFrame(frame, text="模型设置")
        advanced.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 8))
        advanced.columnconfigure(1, weight=1)
        advanced.columnconfigure(3, weight=1)

        ttk.Label(advanced, text="Base URL").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        ttk.Entry(advanced, textvariable=self.base_url).grid(row=0, column=1, sticky="ew", padx=(8, 12), pady=8)
        ttk.Label(advanced, text="模型").grid(row=0, column=2, sticky="w", padx=(4, 8), pady=8)
        ttk.Entry(advanced, textvariable=self.model, width=20).grid(row=0, column=3, sticky="ew", padx=(0, 10), pady=8)

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 10))
        buttons.columnconfigure(0, weight=1)

        self.run_button = ttk.Button(buttons, text="生成 JSON", command=self._start_run)
        self.run_button.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(buttons, text="打开输出文件夹", command=self._open_output_dir).grid(row=0, column=2)

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(frame, text="运行日志").grid(row=8, column=0, sticky="nw")
        log_frame = ttk.Frame(frame)
        log_frame.grid(row=8, column=1, columnspan=2, sticky="nsew", padx=(10, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log = self._create_log(log_frame)
        self._log("请选择 Excel 报告，填写 DeepSeek API Key，然后点击“生成 JSON”。")
        self._log("设计总说明可以不选；不选时会自动生成一份临时说明，并把缺失信息交给人工确认。")

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: StringVar, command, optional: bool = False) -> None:
        suffix = "（可选）" if optional else ""
        ttk.Label(parent, text=f"{label}{suffix}").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(10, 8), pady=6)
        ttk.Button(parent, text="选择", command=command).grid(row=row, column=2, sticky="ew", pady=6)

    def _create_log(self, parent: ttk.Frame):
        text = __import__("tkinter").Text(parent, height=12, wrap="word")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        return text

    def _pick_excel(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 CAD 识别导出的 Excel",
            filetypes=[("Excel 文件", "*.xlsx"), ("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if path:
            self.excel_path.set(path)
            if self.output_dir.get().endswith("bim_agent_gui"):
                stem = Path(path).stem
                self.output_dir.set(str(PROJECT_ROOT / "outputs" / f"{stem}_agent_json"))

    def _pick_notes(self) -> None:
        path = filedialog.askopenfilename(
            title="选择设计总说明",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if path:
            self.notes_path.set(path)

    def _pick_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择 JSON 输出文件夹")
        if path:
            self.output_dir.set(path)

    def _start_run(self) -> None:
        excel = Path(self.excel_path.get().strip('" '))
        output = Path(self.output_dir.get().strip('" '))
        key = self.api_key.get().strip()

        if not excel.exists():
            messagebox.showerror("缺少 Excel", "请先选择一个存在的 Excel 或 CSV 文件。")
            return
        if not key:
            messagebox.showerror("缺少 API Key", "请填写 DeepSeek API Key。")
            return
        if not output:
            messagebox.showerror("缺少输出位置", "请选择输出文件夹。")
            return

        self.run_button.configure(state="disabled")
        self.progress.start(12)
        self._log("")
        self._log("开始运行 BIM 智能体。")
        self._log(f"输入 Excel：{excel}")
        self._log(f"输出文件夹：{output}")

        thread = threading.Thread(target=self._run_agent, args=(excel, output, key), daemon=True)
        thread.start()

    def _run_agent(self, excel: Path, output: Path, key: str) -> None:
        try:
            output.mkdir(parents=True, exist_ok=True)
            notes = self._resolve_notes(output)
            result = run_bim_agent(
                excel,
                notes,
                output,
                memory_dir=PROJECT_ROOT / "agent_memory",
                api_key=key,
                base_url=self.base_url.get().strip() or DEFAULT_BASE_URL,
                model=self.model.get().strip() or DEFAULT_MODEL,
            )
            counts = {name: len(items) for name, items in result.get("components", {}).items() if isinstance(items, list)}
            self.last_output_dir = output
            self.root.after(0, self._finish_success, output, counts)
        except Exception as exc:  # noqa: BLE001 - show GUI-friendly error text.
            self.root.after(0, self._finish_error, str(exc))

    def _resolve_notes(self, output: Path) -> Path:
        notes_text = self.notes_path.get().strip('" ')
        if notes_text:
            notes = Path(notes_text)
            if notes.exists():
                return notes

        notes = output / "auto_design_notes.txt"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        notes.write_text(
            "\n".join(
                [
                    "建筑或结构设计总说明：",
                    f"本说明由 BIM 智能体界面在 {now} 自动生成。",
                    "输入来自 CAD 图纸识别后的 Excel 报告。",
                    "如果 Excel 中缺少楼层、层高、门窗高度、楼板边界、材料等信息，不得自动编造。",
                    "缺失、冲突或不确定的数据应标记为 needs_review，等待人工确认。",
                ]
            ),
            encoding="utf-8",
        )
        self.notes_path.set(str(notes))
        return notes

    def _finish_success(self, output: Path, counts: dict) -> None:
        self.progress.stop()
        self.run_button.configure(state="normal")
        self._log("运行完成。")
        self._log(f"构件数量：{counts}")
        self._log(f"标准 JSON：{output / 'standard_model.json'}")
        self._log(f"Revit 输入 JSON：{output / 'revit_model_input.json'}")
        messagebox.showinfo(
            "完成",
            "JSON 已生成。\n\n"
            f"Revit 外部工具请选择：\n{output / 'revit_model_input.json'}",
        )

    def _finish_error(self, error: str) -> None:
        self.progress.stop()
        self.run_button.configure(state="normal")
        self._log(f"运行失败：{error}")
        messagebox.showerror("运行失败", error)

    def _open_output_dir(self) -> None:
        path = self.last_output_dir or Path(self.output_dir.get().strip('" '))
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _log(self, message: str) -> None:
        self.log.insert(END, message + "\n")
        self.log.see(END)


def main() -> int:
    root = Tk()
    BimAgentGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
