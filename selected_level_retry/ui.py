from __future__ import annotations

import tkinter as tk
import copy
import queue
from tkinter import font as tkfont

from .controller import RetryStats, SelectedLevelRetryController


class SelectedLevelRetryUI:
    """紧凑的暗色控制面板，只显示当前挑战所需的关键信息。"""

    COLORS = {
        "background": "#1d1d1f",
        "card": "#2c2c2e",
        "card_border": "#3a3a3c",
        "text": "#f5f5f7",
        "secondary": "#a1a1a6",
        "muted": "#6e6e73",
        "blue": "#0a84ff",
        "blue_hover": "#409cff",
        "green": "#30d158",
        "red": "#ff453a",
        "red_dark": "#4a2528",
        "button": "#3a3a3c",
        "button_hover": "#48484a",
    }

    def __init__(self, controller: SelectedLevelRetryController) -> None:
        self.controller = controller
        self._updates = queue.Queue(maxsize=1)
        self._closing = False
        self.root = tk.Tk()
        from . import __version__
        self.root.title(f"yxmys 当前难度重复挑战 v{__version__}")
        self.root.geometry("420x460")
        self.root.minsize(380, 420)
        self.root.configure(bg=self.COLORS["background"])
        self.root.option_add("*tearOff", False)
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(700, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

        self.family = self._pick_font()
        self._vars = {
            "program_status": tk.StringVar(value="已停止"),
            "target_status": tk.StringVar(value="等待手动选中难度"),
            "attempts": tk.StringVar(value="0"),
            "failures": tk.StringVar(value="0"),
            "recovery_scrolls": tk.StringVar(value="0"),
            "result_status": tk.StringVar(value="等待开始"),
            "last_action": tk.StringVar(value="暂无"),
            "last_error": tk.StringVar(value="无"),
        }
        self._attempt_detail = tk.StringVar(value="失败 0  ·  找回 0 次")
        self._status_pill: tk.Label | None = None

        self._build()
        self.controller.on_stats = self._on_stats_threadsafe
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._drain_updates)

    @staticmethod
    def _pick_font() -> str:
        families = set(tkfont.families())
        for family in ("SF Pro Display", "SF Pro Text", "Segoe UI"):
            if family in families:
                return family
        return "TkDefaultFont"

    def _build(self) -> None:
        c = self.COLORS
        body = tk.Frame(self.root, bg=c["background"])
        body.pack(fill="both", expand=True, padx=18, pady=16)

        header = tk.Frame(body, bg=c["background"])
        header.pack(fill="x", pady=(0, 14))
        title_box = tk.Frame(header, bg=c["background"])
        title_box.pack(side="left")
        tk.Label(
            title_box,
            text="yxmys",
            bg=c["background"],
            fg=c["text"],
            font=(self.family, 20, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="当前难度重复挑战",
            bg=c["background"],
            fg=c["secondary"],
            font=(self.family, 10),
        ).pack(anchor="w", pady=(1, 0))

        self._status_pill = tk.Label(
            header,
            textvariable=self._vars["program_status"],
            bg=c["button"],
            fg=c["secondary"],
            font=(self.family, 9, "bold"),
            padx=10,
            pady=5,
        )
        self._status_pill.pack(side="right", anchor="n", pady=3)

        actions = tk.Frame(body, bg=c["background"])
        actions.pack(fill="x", pady=(0, 14))
        self._button(actions, "开始", self.controller.start, primary=True).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._button(actions, "暂停", self.controller.pause).pack(
            side="left", fill="x", expand=True, padx=5
        )
        self._button(actions, "停止", self._stop_and_quit, danger=True).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        hero = self._card(body)
        hero.pack(fill="x", pady=(0, 10))
        tk.Label(
            hero,
            text="已挑战次数",
            bg=c["card"],
            fg=c["secondary"],
            font=(self.family, 10),
        ).pack(anchor="w", padx=16, pady=(13, 0))
        tk.Label(
            hero,
            textvariable=self._vars["attempts"],
            bg=c["card"],
            fg=c["text"],
            font=(self.family, 38, "bold"),
        ).pack(anchor="w", padx=16, pady=(0, 0))
        tk.Label(
            hero,
            textvariable=self._attempt_detail,
            bg=c["card"],
            fg=c["secondary"],
            font=(self.family, 10),
        ).pack(anchor="w", padx=16, pady=(0, 13))

        metrics = tk.Frame(body, bg=c["background"])
        metrics.pack(fill="x", pady=(0, 10))
        self._metric_card(metrics, "目标难度", "target_status", c["blue"]).pack(
            side="left", fill="both", expand=True, padx=(0, 5)
        )
        self._metric_card(metrics, "最近结算", "result_status", c["green"]).pack(
            side="left", fill="both", expand=True, padx=(5, 0)
        )

        detail = self._card(body)
        detail.pack(fill="x", pady=(0, 10))
        self._detail_row(detail, "最近动作", "last_action")
        self._detail_row(detail, "最近错误", "last_error", error=True)

        hotkeys = self.controller.cfg.get("hotkeys", {})
        toggle_key = self._display_key(hotkeys.get("toggle", "F8"))
        stop_key = self._display_key(hotkeys.get("stop", "F9"))
        tk.Label(
            body,
            text=(
                f"热键  {toggle_key} 开始/暂停  ·  {stop_key} 停止  ·  Esc 紧急暂停\n"
                "先手动选中难度，再点击开始。异常回到列表时会自动向下找回。"
            ),
            bg=c["background"],
            fg=c["secondary"],
            font=(self.family, 9),
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(1, 0))

    @staticmethod
    def _display_key(value: object) -> str:
        text = str(value).strip()
        if text.startswith("<") and text.endswith(">"):
            text = text[1:-1]
        return text.upper() or "未设置"

    def _card(self, parent: tk.Misc) -> tk.Frame:
        c = self.COLORS
        return tk.Frame(
            parent,
            bg=c["card"],
            highlightthickness=1,
            highlightbackground=c["card_border"],
            highlightcolor=c["card_border"],
        )

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        *,
        primary: bool = False,
        danger: bool = False,
    ) -> tk.Button:
        c = self.COLORS
        if primary:
            bg, active, fg = c["blue"], c["blue_hover"], "white"
        elif danger:
            bg, active, fg = c["red_dark"], "#663136", c["red"]
        else:
            bg, active, fg = c["button"], c["button_hover"], c["text"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            activebackground=active,
            fg=fg,
            activeforeground=fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=(self.family, 10, "bold"),
            padx=8,
            pady=8,
        )

    def _metric_card(
        self,
        parent: tk.Misc,
        title: str,
        variable: str,
        accent: str,
    ) -> tk.Frame:
        c = self.COLORS
        card = self._card(parent)
        tk.Label(
            card,
            text=title,
            bg=c["card"],
            fg=c["secondary"],
            font=(self.family, 9),
        ).pack(anchor="w", padx=13, pady=(11, 2))
        tk.Label(
            card,
            textvariable=self._vars[variable],
            bg=c["card"],
            fg=accent,
            font=(self.family, 11, "bold"),
            anchor="w",
            justify="left",
            wraplength=160,
        ).pack(fill="x", padx=13, pady=(0, 11))
        return card

    def _detail_row(
        self,
        parent: tk.Misc,
        title: str,
        variable: str,
        *,
        error: bool = False,
    ) -> None:
        c = self.COLORS
        row = tk.Frame(parent, bg=c["card"])
        row.pack(fill="x", padx=13, pady=(10 if title == "最近动作" else 0, 0))
        tk.Label(
            row,
            text=title,
            bg=c["card"],
            fg=c["secondary"],
            font=(self.family, 9),
            width=7,
            anchor="w",
        ).pack(side="left", anchor="n")
        tk.Label(
            row,
            textvariable=self._vars[variable],
            bg=c["card"],
            fg=c["red"] if error else c["text"],
            font=(self.family, 9),
            anchor="w",
            justify="left",
            wraplength=300,
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))
        if error:
            row.pack(pady=(7, 10))

    def _on_stats_threadsafe(self, stats: RetryStats) -> None:
        # Never call Tk from the worker/hotkey thread, including root.after().
        if self._closing:
            return
        snapshot = copy.deepcopy(stats)
        try:
            self._updates.put_nowait(snapshot)
        except queue.Full:
            try:
                self._updates.get_nowait()
            except queue.Empty:
                pass
            try:
                self._updates.put_nowait(snapshot)
            except queue.Full:
                pass

    def _drain_updates(self) -> None:
        if self._closing:
            return
        try:
            self._apply_stats(self._updates.get_nowait())
        except queue.Empty:
            pass
        if self.controller.stopping.is_set():
            self._on_close()
            return
        self.root.after(80, self._drain_updates)

    def _apply_stats(self, stats: RetryStats) -> None:
        self._vars["program_status"].set(stats.program_status)
        self._vars["target_status"].set(stats.target_status)
        self._vars["attempts"].set(str(stats.attempts))
        self._vars["failures"].set(str(stats.failures))
        self._vars["recovery_scrolls"].set(str(stats.recovery_scrolls))
        self._vars["result_status"].set(stats.result_status or "-")
        self._vars["last_action"].set(stats.last_action or "暂无")
        self._vars["last_error"].set("无" if stats.last_error in {"", "-"} else stats.last_error)
        self._attempt_detail.set(
            f"失败 {stats.failures}  ·  找回 {stats.recovery_scrolls} 次"
        )
        self._update_status_color(stats.program_status)

    def _update_status_color(self, status: str) -> None:
        if self._status_pill is None:
            return
        c = self.COLORS
        if "成功" in status:
            bg, fg = "#203b28", c["green"]
        elif "错误" in status or "紧急" in status:
            bg, fg = c["red_dark"], c["red"]
        elif "运行" in status:
            bg, fg = "#173451", c["blue_hover"]
        else:
            bg, fg = c["button"], c["secondary"]
        self._status_pill.configure(bg=bg, fg=fg)

    def _stop_and_quit(self) -> None:
        self._on_close()

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.controller.on_stats = None
        self.controller.stop()
        # run_app's finally joins the worker after Tk has closed.
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
