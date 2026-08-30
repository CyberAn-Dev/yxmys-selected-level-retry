from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Mapping

from .config import debug_dir
from .controller import TowerController
from .logger import get_logger
from .models import RuntimeStats

logger = get_logger(__name__)


class TowerUI:
    def __init__(self, cfg: Mapping, controller: TowerController) -> None:
        self.cfg = cfg
        self.controller = controller
        self.root = tk.Tk()
        self.root.title("yxmys自动爬绝境")
        self.root.geometry("720x820")
        self.root.minsize(620, 720)
        # 启动时把窗口提到前面，避免被挡住看起来像“没启动”。
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
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)

        ttk.Button(btn_frame, text="开始", command=self.controller.start).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="暂停", command=self.controller.pause).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="停止", command=self._stop_and_quit).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="单步识别", command=self.controller.request_step).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="打开调试目录", command=self._open_debug).pack(
            side="left", padx=4
        )

        fields = [
            ("program_status", "程序状态"),
            ("window_status", "窗口状态"),
            ("window_size", "小程序窗口大小"),
            ("window_advice", "尺寸建议"),
            ("vision_state", "当前视觉状态"),
            ("highest_unlocked_row", "最高可用难度行"),
            ("selected_row", "当前选中行"),
            ("challenge_score", "challenge 匹配分数"),
            ("start_score", "start 匹配分数"),
            ("close_score", "close 匹配分数"),
            ("challenges_started", "已发起挑战次数"),
            ("results_closed", "已关闭结算次数"),
            ("last_action", "最近一次动作"),
            ("last_error", "最近一次错误"),
        ]
        form = ttk.Frame(self.root)
        form.pack(fill="both", expand=True, **pad)
        for i, (key, label) in enumerate(fields):
            ttk.Label(form, text=label + "：").grid(row=i, column=0, sticky="nw", pady=3)
            var = tk.StringVar(value="-")
            self._vars[key] = var
            wrap = 360 if key in ("window_advice", "last_action", "last_error", "window_size") else 320
            ttk.Label(form, textvariable=var, wraplength=wrap, justify="left").grid(
                row=i, column=1, sticky="w", pady=3
            )

        history_frame = ttk.LabelFrame(self.root, text="操作记录（最近 12 条）")
        history_frame.pack(fill="both", padx=10, pady=6)
        self._history_text = tk.Text(
            history_frame,
            height=8,
            wrap="none",
            state="disabled",
            font=("Consolas", 9),
        )
        self._history_text.pack(fill="both", expand=True, padx=6, pady=6)

        tip = (
            "热键：F8 开始/暂停 | F9 停止退出 | F10 调试\n"
            "运行中控制面板会置顶，便于点「停止」；也可直接按 F9\n"
            "自动寻找最高已解锁难度；满级(如50)后直接反复挑战\n"
            "战后不会误打弹回顶部的低难度；必须滑到灰色交界或满级底部再挑战\n"
            "鼠标移到屏幕角落可 FailSafe 紧急停止"
        )
        ttk.Label(self.root, text=tip, foreground="#444").pack(fill="x", **pad)

    def _on_stats_threadsafe(self, stats: RuntimeStats) -> None:
        self.root.after(0, lambda: self._apply_stats(stats))

    def _apply_stats(self, stats: RuntimeStats) -> None:
        mapping = {
            "program_status": stats.program_status,
            "window_status": stats.window_status,
            "window_size": stats.window_size,
            "window_advice": stats.window_advice,
            "vision_state": stats.vision_state,
            "highest_unlocked_row": stats.highest_unlocked_row,
            "selected_row": stats.selected_row,
            "challenge_score": f"{stats.challenge_score:.3f}",
            "start_score": f"{stats.start_score:.3f}",
            "close_score": f"{stats.close_score:.3f}",
            "challenges_started": str(stats.challenges_started),
            "results_closed": str(stats.results_closed),
            "last_action": stats.last_action,
            "last_error": stats.last_error,
        }
        for key, value in mapping.items():
            if key in self._vars:
                self._vars[key].set(value)
        self._history_text.configure(state="normal")
        self._history_text.delete("1.0", "end")
        self._history_text.insert("1.0", stats.operation_history)
        self._history_text.see("end")
        self._history_text.configure(state="disabled")
        running = (
            self.controller.enabled.is_set()
            and not self.controller.stopping.is_set()
        )
        try:
            self.root.attributes("-topmost", running)
        except tk.TclError:
            pass

    def _poll(self) -> None:
        if self.controller.stopping.is_set():
            try:
                self.root.attributes("-topmost", False)
            except tk.TclError:
                pass
            self.root.quit()
            return
        try:
            self.controller.refresh_window_info()
        except Exception as exc:  # noqa: BLE001
            logger.debug("刷新窗口信息失败: %s", exc)
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
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass
        self.root.after(200, self.root.quit)

    def _on_close(self) -> None:
        self.controller.shutdown()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
