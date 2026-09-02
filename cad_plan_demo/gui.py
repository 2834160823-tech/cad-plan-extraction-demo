from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False
    DND_FILES = None
    TkinterDnD = None

from .cross_view import apply_cross_view_opening_enrichment, match_project_openings
from .dxf_parser import parse_dxf
from .frames import detect_drawing_frames, filter_entities_to_frame
from .io_utils import ensure_dxf, find_oda_converter
from .pipeline import analyze_entities
from .railing_recognition import enrich_railings_with_section_height
from .standard_export import write_standard_project_outputs


class CadDemoApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("CAD 文件识别 - DXF")
        self.root.geometry("820x620")
        self.root.minsize(760, 560)

        self.files: list[Path] = []
        self.output_var = tk.StringVar(value=str(Path("outputs/gui_batch").resolve()))
        self.status_var = tk.StringVar()
        self.last_output_dir: Path | None = None
        self.messages: queue.Queue[str] = queue.Queue()

        self._build_ui()
        self._refresh_oda_status()
        self._poll_messages()

    def _build_ui(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        ttk.Label(root, text="CAD 文件识别", font=("Segoe UI", 17, "bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 4)
        )
        ttk.Label(
            root,
            text="批量识别 DXF 图纸中的轴线、墙体、门窗、标注和基础构件。",
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 12))

        toolbar = ttk.Frame(root)
        toolbar.grid(row=2, column=0, sticky="ew", padx=18)
        toolbar.columnconfigure(1, weight=1)

        ttk.Button(toolbar, text="添加 DXF 图纸", command=self.add_files).grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="Clear list", command=self.clear_files).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(toolbar, text="输出文件夹").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(toolbar, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(12, 0))
        ttk.Button(toolbar, text="选择文件夹", command=self.choose_output).grid(row=1, column=2, pady=(12, 0))

        files_frame = ttk.LabelFrame(root, text="待识别的 DXF 图纸")
        files_frame.grid(row=3, column=0, sticky="ew", padx=18, pady=12)
        files_frame.columnconfigure(0, weight=1)

        self.file_list = tk.Listbox(files_frame, height=6, selectmode="extended")
        self.file_list.grid(row=0, column=0, sticky="ew")
        list_scroll = ttk.Scrollbar(files_frame, orient="vertical", command=self.file_list.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=list_scroll.set)

        drop_text = "把 DXF 文件拖到这里，或点击“添加 DXF 图纸”。"
        if not DND_AVAILABLE:
            drop_text = "当前未安装拖拽组件，请点击“添加 DXF 图纸”。"
        self.drop = tk.Label(
            files_frame,
            text=drop_text,
            relief="groove",
            bd=2,
            height=3,
            bg="#f6f8fa",
            fg="#333333",
            font=("Segoe UI", 10),
        )
        self.drop.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        if DND_AVAILABLE:
            self.drop.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.drop.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]

        log_frame = ttk.LabelFrame(root, text="运行日志")
        log_frame.grid(row=4, column=0, sticky="nsew", padx=18, pady=(0, 12))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)

        bottom = ttk.Frame(root)
        bottom.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 16))
        bottom.columnconfigure(0, weight=1)

        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="开始识别", command=self.run).grid(row=0, column=1, padx=8)
        ttk.Button(bottom, text="打开输出文件夹", command=self.open_output).grid(row=0, column=2)

    def _refresh_oda_status(self) -> None:
        converter = find_oda_converter()
        if converter:
            self.status_var.set("请添加 DXF 文件进行识别。")
            self._log(f"ODA File Converter: {converter}")
        else:
            self.status_var.set("请添加 DXF 文件进行识别。")
            self._log("GUI 入口只接收 DXF 文件；如有 DWG，请先另存或转换为 DXF。")

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择 DXF 图纸",
            filetypes=[("DXF 图纸", "*.dxf")],
        )
        self._add_paths([Path(p) for p in paths])

    def clear_files(self) -> None:
        self.files.clear()
        self.file_list.delete(0, "end")
        self.status_var.set("文件列表已清空。")

    def choose_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出文件夹")
        if path:
            self.output_var.set(path)

    def _on_drop(self, event: object) -> None:
        raw = getattr(event, "data", "")
        files = [Path(p) for p in self.root.tk.splitlist(raw)]
        self._add_paths(files)

    def _add_paths(self, paths: list[Path]) -> None:
        added = 0
        existing = {p.resolve() for p in self.files if p.exists()}
        for path in paths:
            if path.suffix.lower() != ".dxf":
                continue
            resolved = path.resolve()
            if resolved in existing:
                continue
            self.files.append(path)
            self.file_list.insert("end", str(path))
            existing.add(resolved)
            added += 1
        if added:
            self.status_var.set(f"已添加 {added} 个 DXF 文件。")
        elif paths:
            self.status_var.set("没有添加新的 DXF 文件。")

    def run(self) -> None:
        valid_files = [p for p in self.files if p.exists() and p.suffix.lower() == ".dxf"]
        if not valid_files:
            messagebox.showwarning("没有 DXF 图纸", "请先添加一个或多个 DXF 文件。")
            return

        output_root = Path(self.output_var.get().strip('" '))
        self._log("")
        self._log(f"开始批量识别。文件数：{len(valid_files)}")
        self._log(f"输出文件夹：{output_root}")
        self.status_var.set("正在识别...")

        thread = threading.Thread(target=self._run_batch_worker, args=(valid_files, output_root), daemon=True)
        thread.start()

    def _run_batch_worker(self, input_files: list[Path], output_root: Path) -> None:
        try:
            output_root.mkdir(parents=True, exist_ok=True)
            self.last_output_dir = output_root.resolve()

            total_counts = {
                "walls": 0,
                "openings": 0,
                "axes": 0,
                "issues": 0,
            }
            type_counts: dict[str, int] = {}
            summary_rows: list[dict] = []
            workbook_results: list[tuple[str, dict]] = []
            for index, input_path in enumerate(input_files, start=1):
                self.messages.put(f"[{index}/{len(input_files)}] Processing: {input_path.name}")
                dxf_path = ensure_dxf(input_path)
                entities = parse_dxf(dxf_path)
                frames = detect_drawing_frames(entities)
                if frames:
                    self.messages.put(f"  -> found {len(frames)} drawing frame(s). Splitting this file.")
                    for frame_index, frame in enumerate(frames, start=1):
                        frame_entities = filter_entities_to_frame(entities, frame)
                        result = analyze_entities(frame_entities, input_path, dxf_path, frame)
                        frame_label = f"{input_path.stem}_{frame.id}"
                        workbook_results.append((frame_label, result))
                        self._record_result(
                            result,
                            input_path,
                            output_root,
                            frame_label,
                            total_counts,
                            type_counts,
                            summary_rows,
                        )
                        self.messages.put(self._format_result_log(frame_label, result))
                else:
                    result = analyze_entities(entities, input_path, dxf_path)
                    workbook_results.append((input_path.stem, result))
                    self._record_result(
                        result,
                        input_path,
                        output_root,
                        "",
                        total_counts,
                        type_counts,
                        summary_rows,
                    )
                    self.messages.put(self._format_result_log(input_path.stem, result))

            matches = match_project_openings(workbook_results)
            enriched_openings = apply_cross_view_opening_enrichment(workbook_results, matches)
            enriched_railings = enrich_railings_with_section_height(workbook_results)
            door_matches = [item for item in matches if item.get("opening_kind") == "door"]
            window_matches = [item for item in matches if item.get("opening_kind") == "window"]
            source_for_project = input_files[0] if input_files else output_root
            standard = write_standard_project_outputs(output_root, workbook_results, source_for_project, output_root.name)
            self.last_output_dir = standard.output_dir.resolve()
            self.messages.put(
                "Batch done. Total: walls {walls}, openings {openings}, axes {axes}, issues {issues}.".format(
                    **total_counts
                )
            )
            self.messages.put(f"Drawing type counts: {type_counts}")
            self.messages.put(f"Cross-view opening matches: {len(matches)}")
            self.messages.put(f"Cross-view door matches: {len(door_matches)}")
            self.messages.put(f"Cross-view window matches: {len(window_matches)}")
            self.messages.put(f"Plan openings enriched from elevations: {enriched_openings}")
            self.messages.put(f"Plan railings enriched from sections: {enriched_railings}")
            self.messages.put(f"Output package: {standard.output_dir}")
            self.messages.put(f"Human Excel report: {standard.human_report}")
            self.messages.put(f"Standard CSV folder: {standard.csv_dir}")
            self.messages.put(f"Detailed report: {standard.detailed_report}")
            self.messages.put("__DONE__")
        except Exception as exc:
            self.messages.put(f"Failed: {exc}")
            self.messages.put("__FAILED__")

    def _record_result(
        self,
        result: dict,
        input_path: Path,
        output_folder: Path,
        frame_id: str,
        total_counts: dict[str, int],
        type_counts: dict[str, int],
        summary_rows: list[dict],
    ) -> None:
        counts = result["counts"]
        drawing_type = str(result["notes"].get("drawing_type", "unknown"))
        type_counts[drawing_type] = type_counts.get(drawing_type, 0) + 1
        for key in total_counts:
            total_counts[key] += int(counts.get(key, 0))
        summary_rows.append(
            {
                "file": str(input_path),
                "frame": frame_id,
                "output_folder": str(output_folder),
                "drawing_type": drawing_type,
                "drawing_title": result["notes"].get("drawing_title") or "",
                "drawing_title_confidence": result["notes"].get("drawing_title_confidence", 0),
                "text_items": result["notes"].get("text_count", 0),
                "text_characters": result["notes"].get("text_char_count", 0),
                "walls": counts.get("walls", 0),
                "openings": counts.get("openings", 0),
                "axes": counts.get("axes", 0),
                "floor_height_candidates": len(result.get("plan_summary", {}).get("floor_heights", [])),
                "elevation_marks": len(result.get("plan_summary", {}).get("elevation_marks", [])),
                "issues": counts.get("issues", 0),
            }
        )

    def _format_result_log(self, folder: str, result: dict) -> str:
        counts = result["counts"]
        drawing_type = str(result["notes"].get("drawing_type", "unknown"))
        return (
            "  -> {folder}: type {drawing_type}, walls {walls}, openings {openings}, axes {axes}, "
            "heights {heights}, levels {levels}, issues {issues}"
        ).format(
            folder=folder,
            drawing_type=drawing_type,
            heights=len(result.get("plan_summary", {}).get("floor_heights", [])),
            levels=len(result.get("plan_summary", {}).get("elevation_marks", [])),
            **counts,
        )

    def open_output(self) -> None:
        path = self.last_output_dir or Path(self.output_var.get())
        if not path.exists():
            messagebox.showinfo("No output yet", "The output folder does not exist yet. Generate first.")
            return
        os.startfile(path)  # type: ignore[attr-defined]

    def _poll_messages(self) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if message == "__DONE__":
                self.status_var.set("Generation complete.")
                continue
            if message == "__FAILED__":
                self.status_var.set("Generation failed. Check the log.")
                continue
            self._log(message)
        self.root.after(120, self._poll_messages)

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    if DND_AVAILABLE and TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    CadDemoApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
