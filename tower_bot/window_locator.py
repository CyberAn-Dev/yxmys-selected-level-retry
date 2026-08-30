from __future__ import annotations

import ctypes
import time
from typing import Callable, Mapping, Optional

import win32con
import win32gui

from .logger import get_logger
from .models import Rect, WindowError, WindowInfo
from .coords import validate_aspect_ratio

logger = get_logger(__name__)


def enable_dpi_awareness() -> str:
    """尽量启用 Per-Monitor DPI Aware V2，失败则回退。"""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        result = ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        if result:
            return "PER_MONITOR_AWARE_V2"
    except Exception as exc:  # noqa: BLE001 - 兼容旧系统 API
        logger.debug("SetProcessDpiAwarenessContext 不可用: %s", exc)

    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "SYSTEM_DPI_AWARE"
    except Exception as exc:  # noqa: BLE001
        logger.warning("无法设置 DPI 感知: %s", exc)
        return "UNAWARE"


class WindowLocator:
    def __init__(self, cfg: Mapping) -> None:
        self.cfg = cfg
        self._last_hwnd: Optional[int] = None

    def find(self) -> Optional[WindowInfo]:
        title_key = str(self.cfg["window"]["title_contains"])
        min_w = int(self.cfg["window"]["minimum_width"])
        min_h = int(self.cfg["window"]["minimum_height"])
        found: list[WindowInfo] = []

        def callback(hwnd: int, _: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.IsIconic(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if title_key not in title:
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            rect = Rect(left, top, right, bottom)
            if rect.width < min_w or rect.height < min_h:
                return
            found.append(WindowInfo(hwnd=hwnd, title=title, rect=rect))

        win32gui.EnumWindows(callback, None)
        if not found:
            self._last_hwnd = None
            return None

        best = max(found, key=lambda item: item.rect.area)
        self._last_hwnd = best.hwnd
        return best

    def validate_geometry(self, info: WindowInfo) -> None:
        validate_aspect_ratio(info.rect, self.cfg)

    def is_foreground(self, hwnd: int) -> bool:
        try:
            return win32gui.GetForegroundWindow() == hwnd
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取前台窗口失败: %s", exc)
            return False

    def focus(self, hwnd: int) -> bool:
        try:
            if not win32gui.IsWindow(hwnd):
                return False
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.25)
            if win32gui.GetForegroundWindow() != hwnd:
                # 尝试置前；部分系统可能因前台锁失败。
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("SetForegroundWindow 失败: %s", exc)
                time.sleep(0.15)
            return self.is_foreground(hwnd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("激活窗口失败: %s", exc)
            return False

    def current_rect(self, hwnd: int) -> Rect:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return Rect(left, top, right, bottom)

    def ensure_ready(
        self,
        visual_ok: Optional[Callable[[], bool]] = None,
    ) -> WindowInfo:
        info = self.find()
        if info is None:
            raise WindowError("未找到目标窗口")
        if win32gui.IsIconic(info.hwnd):
            raise WindowError("目标窗口已最小化")
        self.validate_geometry(info)
        if visual_ok is not None and not visual_ok():
            raise WindowError("窗口标题匹配，但视觉锚点验证失败")
        return info
