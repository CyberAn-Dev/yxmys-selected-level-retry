from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from tower_bot.config import debug_dir
from tower_bot.logger import get_logger

from .controller import RetryStats, SelectedLevelRetryController


logger = get_logger(__name__)


class SelectedLevelRetryUI:
    def __init__(self, controller: SelectedLevelRetryController) -> None:
        self.controller = controller
        self.root = tk.Tk()
        self.root.title("yxmys·选定关卡重复挑战")
        self.root.geometry("700x700")
        self.root.minsize(620, 620)
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(800, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

        self._vars: dict[str, tk.StringVar] = {}
        self._build()
        self.controller.on_stats = self._on_stats_threadsafe
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="开始", command=self.controller.start).pack(side="left", padx=4)
        ttk.Button(buttons, text="暂停", command=self.controller.pause).pack(side="left", padx=4)
        ttk.Button(buttons, text="停止", command=self._stop_and_quit).pack(side="left", padx=4)
        ttk.Button(buttons, text="单步识别", command=self.controller.request_step).pack(side="left", padx=4)
        ttk.Button(buttons, text="调试", command=self.controller.toggle_debug).pack(side="left", padx=4)
        ttk.Button(buttons, text="打开调试目录", command=self._open_debug).pack(side="left", padx=4)

        fields = [
            ("program_status", "程序状态"),
            ("window_status", "窗口状态"),
            ("window_size", "窗口大小"),
            ("window_advice", "尺寸建议"),
            ("vision_state", "视觉状态"),
            ("target_status", "目标关卡"),
            ("target_visible", "目标是否可见"),
            ("attempts", "已开始挑战"),
            ("failures", "失败次数"),
            ("recovery_scrolls", "找回滑动次数"),
            ("result_status", "最近结算"),
            ("success_score", "成功模板分数"),
            ("failure_score", "失败模板分数"),
            ("last_action", "最近动作"),
            ("last_error", "最近错误"),
        ]
        form = ttk.Frame(self.root)
        form.pack(fill="x", **pad)
        for i, (key, label) in enumerate(fields):
            ttk.Label(form, text=label + "：").grid(row=i, column=0, sticky="nw", pady=3)
            var = tk.StringVar(value="-")
            self._vars[key] = var
            wrap = 400 if key in {"window_advice", "last_action", "last_error"} else 320
            ttk.Label(form, textvariable=var, wraplength=wrap, justify="left").grid(
                row=i, column=1, sticky="w", pady=3
            )

        history_frame = ttk.LabelFrame(self.root, text="操作记录（最近 12 条）")
        history_frame.pack(fill="both", expand=True, **pad)
        self._history = tk.Text(
            history_frame,
            height=8,
            wrap="none",
            state="disabled",
            font=("Consolas", 9),
        )
        self._history.pack(fill="both", expand=True, padx=6, pady=6)

        tip = (
            "先在游戏中手动选中目标关卡，再点击开始或按 F8。\n"
            "失败结算会自动关闭并重新搜索原关卡；成功模板命中后停止。\n"
            "F9 停止退出，F10 调试；鼠标移到屏幕角落可 FailSafe 紧急暂停。"
        )
        ttk.Label(self.root, text=tip, foreground="#444").pack(fill="x", **pad)

    def _on_stats_threadsafe(self, stats: RetryStats) -> None:
        self.root.after(0, lambda: self._apply_stats(stats))

    def _apply_stats(self, stats: RetryStats) -> None:
        mapping = {
            "program_status": stats.program_status,
            "window_status": stats.window_status,
            "window_size": stats.window_size,
            "window_advice": stats.window_advice,
            "vision_state": stats.vision_state,
            "target_status": stats.target_status,
            "target_visible": stats.target_visible,
            "attempts": str(stats.attempts),
            "failures": str(stats.failures),
            "recovery_scrolls": str(stats.recovery_scrolls),
            "result_status": stats.result_status,
            "success_score": f"{stats.success_score:.3f}",
            "failure_score": f"{stats.failure_score:.3f}",
            "last_action": stats.last_action,
            "last_error": stats.last_error,
        }
        for key, value in mapping.items():
            self._vars[key].set(value)
        self._history.configure(state="normal")
        self._history.delete("1.0", "end")
        self._history.insert("1.0", stats.operation_history)
        self._history.see("end")
        self._history.configure(state="disabled")

    def _poll(self) -> None:
        if self.controller.stopping.is_set():
            self.root.quit()
            return
        try:
            self.controller.refresh_window_info()
        except Exception as exc:  # noqa: BLE001
            logger.debug("刷新选定关卡窗口信息失败: %s", exc)
        self.root.after(500, self._poll)

    def _open_debug(self) -> None:
        path = debug_dir()
        path.mkdir(parents=True, exist_ok=True)
        try:
            import os

            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("错误", f"无法打开调试目录: {exc}")

    def _stop_and_quit(self) -> None:
        self.controller.stop()
        self.root.after(200, self.root.quit)

    def _on_close(self) -> None:
        self.controller.shutdown()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

