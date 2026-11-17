"""Tkinter desktop frontend for UnityExtractor."""
from __future__ import annotations

import queue
import threading
import traceback
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ...adapters.registry import available_adapters
from .controller import DesktopController, DesktopOptions


class DesktopApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.controller = DesktopController()
        self.queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_options: DesktopOptions | None = None
        self.report: dict[str, Any] | None = None
        self.objects_by_id: dict[str, dict[str, Any]] = {}
        self.purpose = "scan"
        self._build()
        self.root.after(100, self._poll)

    def _build(self) -> None:
        self.root.title("UnityExtractor")
        self.root.geometry("1280x820")
        self.root.minsize(980, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        container = ttk.Frame(self.root, padding=8)
        container.pack(fill="both", expand=True)

        source = ttk.LabelFrame(container, text="Input", padding=6)
        source.pack(fill="x")
        self.input_var = tk.StringVar()
        ttk.Entry(source, textvariable=self.input_var).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ttk.Button(source, text="File...", command=self._pick_input_file).pack(side="left")
        ttk.Button(source, text="Directory...", command=self._pick_input_dir).pack(
            side="left", padx=(6, 0)
        )

        options = ttk.LabelFrame(container, text="Options", padding=6)
        options.pack(fill="x", pady=(6, 0))
        self.enumerate_var = tk.BooleanVar(value=True)
        self.references_var = tk.BooleanVar(value=False)
        self.dependencies_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options, text="Enumerate objects", variable=self.enumerate_var
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            options, text="Analyze references", variable=self.references_var
        ).grid(row=0, column=1, sticky="w", padx=12)
        ttk.Checkbutton(
            options, text="Analyze dependencies", variable=self.dependencies_var
        ).grid(row=0, column=2, sticky="w", padx=12)

        ttk.Label(options, text="Adapter:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.adapter_var = tk.StringVar(value="none")
        ttk.Combobox(
            options,
            textvariable=self.adapter_var,
            values=["none", *available_adapters()],
            state="readonly",
            width=16,
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))

        ttk.Label(options, text="Catalog:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.catalog_var = tk.StringVar()
        ttk.Entry(options, textvariable=self.catalog_var).grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=(6, 0)
        )
        ttk.Button(options, text="Browse...", command=self._pick_catalog).grid(
            row=2, column=3, sticky="w", padx=(6, 0), pady=(6, 0)
        )

        ttk.Label(options, text="Export dir:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.export_var = tk.StringVar()
        ttk.Entry(options, textvariable=self.export_var).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=(6, 0)
        )
        ttk.Button(options, text="Browse...", command=self._pick_export_dir).grid(
            row=3, column=3, sticky="w", padx=(6, 0), pady=(6, 0)
        )

        ttk.Label(options, text="Max container MiB:").grid(
            row=4, column=0, sticky="w", pady=(6, 0)
        )
        self.max_container_var = tk.StringVar(value="512")
        ttk.Spinbox(
            options,
            from_=1,
            to=8192,
            textvariable=self.max_container_var,
            width=10,
        ).grid(row=4, column=1, sticky="w", pady=(6, 0))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(2, weight=1)

        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(6, 0))
        self.scan_button = ttk.Button(actions, text="Scan", command=self._start_scan)
        self.scan_button.pack(side="left")
        self.cancel_button = ttk.Button(
            actions, text="Cancel", command=self._cancel, state="disabled"
        )
        self.cancel_button.pack(side="left", padx=(6, 0))
        self.export_selected_button = ttk.Button(
            actions,
            text="Export Selected...",
            command=self._export_selected,
            state="disabled",
        )
        self.export_selected_button.pack(side="left", padx=(18, 0))
        self.export_all_button = ttk.Button(
            actions,
            text="Export All Supported...",
            command=self._export_all,
            state="disabled",
        )
        self.export_all_button.pack(side="left", padx=(6, 0))

        progress_frame = ttk.Frame(container)
        progress_frame.pack(fill="x", pady=(6, 0))
        self.progress_label = ttk.Label(progress_frame, text="Ready")
        self.progress_label.pack(side="left")
        self.progress = ttk.Progressbar(progress_frame, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(8, 0))

        filters = ttk.Frame(container)
        filters.pack(fill="x", pady=(6, 0))
        ttk.Label(filters, text="Category:").pack(side="left")
        self.category_var = tk.StringVar(value="all")
        self.category_box = ttk.Combobox(
            filters, textvariable=self.category_var, values=["all"],
            state="readonly", width=14,
        )
        self.category_box.pack(side="left", padx=(4, 12))
        ttk.Label(filters, text="Type:").pack(side="left")
        self.type_var = tk.StringVar(value="all")
        self.type_box = ttk.Combobox(
            filters, textvariable=self.type_var, values=["all"],
            state="readonly", width=18,
        )
        self.type_box.pack(side="left", padx=(4, 12))
        ttk.Label(filters, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.search_var, width=28)
        search.pack(side="left", padx=(4, 12))
        self.count_label = ttk.Label(filters, text="No scan yet")
        self.count_label.pack(side="left")
        self.category_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_filters())
        self.type_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_filters())
        search.bind("<KeyRelease>", lambda _event: self._apply_filters())

        panes = ttk.PanedWindow(container, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(6, 0))

        left = ttk.Frame(panes)
        columns = ("type", "category", "name", "path_id", "size", "source")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        headings = {
            "type": ("Type", 120),
            "category": ("Category", 90),
            "name": ("Name", 180),
            "path_id": ("Path ID", 130),
            "size": ("Size", 90),
            "source": ("Source", 320),
        }
        for key, (title, width) in headings.items():
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w", stretch=key == "source")
        tree_scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._show_selection)
        panes.add(left, weight=3)

        right = ttk.Frame(panes)
        ttk.Label(right, text="Object details").pack(anchor="w")
        self.detail = scrolledtext.ScrolledText(right, width=50, height=20, wrap="word")
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")
        panes.add(right, weight=2)

        log_frame = ttk.LabelFrame(container, text="Report and log", padding=4)
        log_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.log = scrolledtext.ScrolledText(log_frame, height=9, wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        self._set_text(self.detail, "")
        self._set_text(self.log, "Select an APK, Unity directory, bundle, or SerializedFile.\n")

    def _set_text(self, widget: scrolledtext.ScrolledText, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _pick_input_file(self) -> None:
        value = filedialog.askopenfilename(
            title="Select APK, ZIP, bundle, or SerializedFile",
            filetypes=[
                ("Unity packages", "*.apk *.zip *.bundle *.assets *.unity3d"),
                ("All files", "*.*"),
            ],
        )
        if value:
            self.input_var.set(value)

    def _pick_input_dir(self) -> None:
        value = filedialog.askdirectory(title="Select unpacked game directory")
        if value:
            self.input_var.set(value)

    def _pick_catalog(self) -> None:
        value = filedialog.askopenfilename(
            title="Select Addressables catalog",
            filetypes=[("Catalog JSON", "*.json"), ("All files", "*.*")],
        )
        if value:
            self.catalog_var.set(value)

    def _pick_export_dir(self) -> None:
        value = filedialog.askdirectory(title="Select export directory")
        if value:
            self.export_var.set(value)

    def _options(self) -> DesktopOptions:
        input_path = self.input_var.get().strip()
        if not input_path:
            raise ValueError("Select an input path first.")
        try:
            max_container = int(self.max_container_var.get())
        except ValueError as exc:
            raise ValueError("Max container MiB must be an integer.") from exc
        if max_container < 1:
            raise ValueError("Max container MiB must be at least 1.")
        if self.dependencies_var.get() and not self.references_var.get():
            raise ValueError(
                "Dependency analysis requires reference analysis."
            )
        if not self.enumerate_var.get() and (
            self.references_var.get() or self.dependencies_var.get()
        ):
            raise ValueError("Reference analysis requires object enumeration.")
        adapter = self.adapter_var.get()
        catalog = self.catalog_var.get().strip()
        return DesktopOptions(
            input_path=input_path,
            enumerate_objects=self.enumerate_var.get(),
            analyze_references=self.references_var.get(),
            analyze_dependencies=self.dependencies_var.get(),
            export_dir=self.export_var.get().strip() or None,
            adapter_name=None if adapter in ("", "none") else adapter,
            catalog_paths=[catalog] if catalog else [],
            max_container_mb=max_container,
        )

    def _start_scan(
        self,
        *,
        selected_resource_ids: set[str] | None = None,
        purpose: str = "scan",
    ) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        try:
            options = self._options()
        except ValueError as exc:
            messagebox.showerror("Invalid options", str(exc))
            return
        if purpose.startswith("export") and not options.export_dir:
            self._pick_export_dir()
            options.export_dir = self.export_var.get().strip() or None
            if not options.export_dir:
                messagebox.showerror("Export", "Select an export directory.")
                return
        if purpose.startswith("export"):
            options.export_resources = True

        self.purpose = purpose
        self.last_options = options
        self.report = None
        self.objects_by_id = {}
        self.tree.delete(*self.tree.get_children())
        self._set_text(self.detail, "")
        self.cancel_event.clear()
        self.scan_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.export_selected_button.configure(state="disabled")
        self.export_all_button.configure(state="disabled")
        self.progress_label.configure(text="Scanning...")
        self.progress.start(12)
        self._append_log(f"Starting {purpose} for {options.input_path}")
        if selected_resource_ids is not None:
            self._append_log(f"Selected resources: {len(selected_resource_ids)}")

        self.worker = threading.Thread(
            target=self._worker,
            args=(options, selected_resource_ids),
            daemon=True,
        )
        self.worker.start()

    def _worker(
        self,
        options: DesktopOptions,
        selected_resource_ids: set[str] | None,
    ) -> None:
        try:
            report = self.controller.run(
                options,
                cancel_event=self.cancel_event,
                progress_callback=lambda stage, count, total: self.queue.put(
                    ("progress", stage, count)
                ),
                selected_resource_ids=selected_resource_ids,
            )
            self.queue.put(("done", report))
        except Exception as exc:
            self.queue.put(("error", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"))

    def _poll(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    self.progress_label.configure(
                        text=f"{item[1]}: {item[2]}"
                    )
                elif kind == "done":
                    self._finish(item[1])
                elif kind == "error":
                    self._fail(item[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self, report: dict[str, Any]) -> None:
        self.progress.stop()
        self.scan_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.report = report
        for line in self.controller.summarize(report):
            self._append_log(line)
        for warning in report.get("warnings", [])[:50]:
            self._append_log(f"Warning: {warning}")
        for error in report.get("errors", [])[:50]:
            self._append_log(f"Error: {error}")
        adapter = report.get("adapter")
        if adapter:
            for diagnostic in adapter.get("diagnostics", [])[:20]:
                self._append_log(f"Adapter diagnostic: {diagnostic}")
        export = report.get("resource_export")
        if export:
            self._append_log(f"Manifest: {export.get('manifest_path', '')}")

        objects = report.get("objects", [])
        self.objects_by_id = {
            str(obj["resource_id"]): obj for obj in objects if obj.get("resource_id")
        }
        values = self.controller.filter_values(objects)
        self.category_box.configure(values=["all", *values["categories"]])
        self.type_box.configure(values=["all", *values["types"]])
        self.category_var.set("all")
        self.type_var.set("all")
        self._apply_filters()
        if objects:
            self.export_selected_button.configure(state="normal")
            self.export_all_button.configure(state="normal")
        self.progress_label.configure(text=f"Finished: {report.get('scan_status')}")

    def _fail(self, message: str) -> None:
        self.progress.stop()
        self.scan_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.progress_label.configure(text="Failed")
        self._append_log(message)
        messagebox.showerror("Scan failed", message.splitlines()[0])

    def _apply_filters(self) -> None:
        if self.report is None:
            return
        objects = self.report.get("objects", [])
        rows, truncated = self.controller.filter_objects(
            objects,
            category=self.category_var.get(),
            type_name=self.type_var.get(),
            text=self.search_var.get(),
        )
        self.tree.delete(*self.tree.get_children())
        for obj in rows:
            resource_id = str(obj.get("resource_id") or "")
            if not resource_id:
                continue
            self.tree.insert("", "end", iid=resource_id, values=(
                obj.get("type_name", ""),
                obj.get("resource_category", ""),
                obj.get("name") or "<unnamed>",
                obj.get("path_id", ""),
                obj.get("size_bytes") if obj.get("size_bytes") is not None else "",
                obj.get("source_id", ""),
            ))
        suffix = " (truncated)" if truncated else ""
        self.count_label.configure(
            text=f"Showing {len(rows)} of {len(objects)} objects{suffix}"
        )

    def _show_selection(self, _event: Any = None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        obj = self.objects_by_id.get(selected[0])
        if obj is not None:
            self._set_text(self.detail, self.controller.object_detail(obj))

    def _export_selected(self) -> None:
        selected = set(self.tree.selection())
        if not selected:
            messagebox.showinfo("Export selected", "Select one or more objects first.")
            return
        self._start_scan(selected_resource_ids=selected, purpose="export-selected")

    def _export_all(self) -> None:
        self._start_scan(purpose="export-all")

    def _cancel(self) -> None:
        self.cancel_event.set()
        self._append_log("Cancellation requested; stopping at the next safe boundary.")
        self.progress_label.configure(text="Cancelling...")

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "Scan running", "Cancel the running scan and close?"
            ):
                return
            self.cancel_event.set()
        self.root.destroy()


def main() -> int:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Desktop UI requires a display: {exc}")
        return 2
    DesktopApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
