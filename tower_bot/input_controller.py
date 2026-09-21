from __future__ import annotations

import random
import time
from typing import Callable, Mapping, Optional, Protocol

import pyautogui

from .coords import reference_to_screen
from .logger import get_logger
from .models import ActionType, BotError, Point, Rect, WindowInfo
from .window_locator import WindowLocator

logger = get_logger(__name__)


class ClickBackend(Protocol):
    def click(self, x: int, y: int) -> None: ...

    def move_to(self, x: int, y: int, duration: float) -> None: ...

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None: ...

    def scroll(self, x: int, y: int, clicks: int) -> None: ...


class PyAutoGuiBackend:
    def click(self, x: int, y: int) -> None:
        pyautogui.click(x, y)

    def move_to(self, x: int, y: int, duration: float) -> None:
        pyautogui.moveTo(x, y, duration=max(0.0, float(duration)))

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None:
        pyautogui.moveTo(x1, y1, duration=0.05)
        pyautogui.dragTo(x2, y2, duration=max(0.1, float(duration)), button="left")

    def scroll(self, x: int, y: int, clicks: int) -> None:
        pyautogui.moveTo(x, y, duration=0.05)
        pyautogui.scroll(int(clicks))


class DryRunBackend:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.moves: list[tuple[int, int]] = []
        self.drags: list[tuple[int, int, int, int]] = []
        self.scrolls: list[tuple[int, int, int]] = []

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))
        logger.info("[DRY-RUN] 本应点击 screen=(%d,%d)，已跳过", x, y)

    def move_to(self, x: int, y: int, duration: float) -> None:
        self.moves.append((x, y))
        logger.info(
            "[DRY-RUN] 本应移动鼠标到 screen=(%d,%d) duration=%.2f，已跳过",
            x,
            y,
            duration,
        )

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None:
        self.drags.append((x1, y1, x2, y2))
        logger.info(
            "[DRY-RUN] 本应拖拽 screen=(%d,%d)->(%d,%d) duration=%.2f，已跳过",
            x1,
            y1,
            x2,
            y2,
            duration,
        )

    def scroll(self, x: int, y: int, clicks: int) -> None:
        self.scrolls.append((x, y, int(clicks)))
        logger.info(
            "[DRY-RUN] 本应在 screen=(%d,%d) 滚轮 clicks=%d，已跳过",
            x,
            y,
            clicks,
        )


def shrink_click_bounds(
    bounds: Rect,
    *,
    inset_px: int,
    inset_ratio: float,
) -> Rect:
    """向内收缩可点区域，避免点到模板边缘外。"""
    dx = max(inset_px, int(bounds.width * inset_ratio))
    dy = max(inset_px, int(bounds.height * inset_ratio))
    # 至少保留中心 2x2 像素。
    max_dx = max(0, (bounds.width - 2) // 2)
    max_dy = max(0, (bounds.height - 2) // 2)
    dx = min(dx, max_dx)
    dy = min(dy, max_dy)
    return Rect(
        left=bounds.left + dx,
        top=bounds.top + dy,
        right=bounds.right - dx,
        bottom=bounds.bottom - dy,
    )


def point_in_bounds(point: Point, bounds: Rect) -> bool:
    return bounds.left <= point.x < bounds.right and bounds.top <= point.y < bounds.bottom


def jitter_reference_point(
    center: Point,
    bounds: Optional[Rect],
    cfg: Mapping,
    *,
    rng: Optional[random.Random] = None,
) -> Point:
    """
    在识别到的可点击范围内随机抖动。
    若未提供 bounds 或关闭抖动，则返回原中心点。
    """
    jitter_cfg = cfg.get("actions", {}).get("click_jitter", {})
    if not bool(jitter_cfg.get("enabled", True)):
        return center
    if bounds is None or bounds.width <= 0 or bounds.height <= 0:
        return center

    inset_px = int(jitter_cfg.get("inset_px", 4))
    inset_ratio = float(jitter_cfg.get("inset_ratio", 0.18))
    safe = shrink_click_bounds(bounds, inset_px=inset_px, inset_ratio=inset_ratio)
    if safe.width <= 0 or safe.height <= 0:
        return Point((bounds.left + bounds.right) // 2, (bounds.top + bounds.bottom) // 2)

    picker = rng or random
    # 若中心已在安全区内，以均匀分布覆盖安全区；否则仍在安全区内取样。
    x = picker.randint(safe.left, max(safe.left, safe.right - 1))
    y = picker.randint(safe.top, max(safe.top, safe.bottom - 1))
    point = Point(int(x), int(y))
    # 最终再夹紧到原始识别范围，确保绝不越界。
    clamped = Point(
        min(max(point.x, bounds.left), bounds.right - 1),
        min(max(point.y, bounds.top), bounds.bottom - 1),
    )
    return clamped


class InputController:
    def __init__(
        self,
        cfg: Mapping,
        locator: WindowLocator,
        *,
        backend: Optional[ClickBackend] = None,
        dry_run: bool = False,
        enabled_check: Optional[Callable[[], bool]] = None,
        on_action: Optional[Callable[[str], None]] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.cfg = cfg
        self.locator = locator
        self.dry_run = dry_run
        self.backend: ClickBackend = backend or (DryRunBackend() if dry_run else PyAutoGuiBackend())
        self.enabled_check = enabled_check or (lambda: True)
        self.on_action = on_action
        self.rng = rng or random.Random()
        self.last_action_at = 0.0
        self.last_action_type = ActionType.NONE
        self._configure_pyautogui()

    def _configure_pyautogui(self) -> None:
        pg = self.cfg.get("pyautogui", {})
        pyautogui.FAILSAFE = bool(pg.get("failsafe", True))
        pyautogui.PAUSE = float(pg.get("pause", 0.05))

    def can_act(self, action: ActionType) -> bool:
        if action == ActionType.NONE:
            return False
        cooldown = float(self.cfg["actions"]["cooldown"])
        return (time.monotonic() - self.last_action_at) >= cooldown

    def mark_action(self, action: ActionType) -> None:
        self.last_action_at = time.monotonic()
        self.last_action_type = action

    def click_reference(
        self,
        window: WindowInfo,
        point: Point,
        *,
        action: ActionType,
        reason: str,
        click_bounds: Optional[Rect] = None,
        expected_hwnd: Optional[int] = None,
        frame_fresh: bool = True,
        state_allows: bool = True,
        confidence_ok: bool = True,
        allow_jitter: bool = True,
    ) -> Point:
        if not self.enabled_check():
            raise BotError("自动化未启用，取消点击")
        if not state_allows:
            raise BotError(f"当前状态不允许动作 {action.name}")
        if not confidence_ok:
            raise BotError(f"置信度不足，取消动作 {action.name}")
        if not frame_fresh:
            raise BotError("截图已过期，取消点击")
        if expected_hwnd is not None and window.hwnd != expected_hwnd:
            raise BotError("窗口 hwnd 已变化，取消点击")
        if not self.can_act(action):
            raise BotError(f"动作冷却中，跳过 {action.name}")

        live_rect = self.locator.current_rect(window.hwnd)
        live_window = WindowInfo(hwnd=window.hwnd, title=window.title, rect=live_rect)
        self.locator.validate_geometry(live_window)

        if not self.dry_run:
            if not self.locator.focus(window.hwnd):
                raise BotError("无法将目标窗口切到前台，取消点击")
            if not self.locator.is_foreground(window.hwnd):
                raise BotError("目标窗口不是前台窗口，取消点击")

        # 点选难度时关闭抖动，避免点到相邻层（如 43/44 来回跳）。
        if allow_jitter and action != ActionType.CLICK_DIFFICULTY:
            jittered = jitter_reference_point(point, click_bounds, self.cfg, rng=self.rng)
        else:
            jittered = point
        if click_bounds is not None and not point_in_bounds(jittered, click_bounds):
            raise BotError(
                f"抖动后坐标越界: {jittered} bounds={click_bounds.as_tuple()}"
            )

        screen = reference_to_screen(
            jittered.x,
            jittered.y,
            live_rect,
            reference_width=int(self.cfg["window"]["reference_width"]),
            reference_height=int(self.cfg["window"]["reference_height"]),
        )
        logger.info(
            "执行动作 %s: reason=%s center_ref=(%d,%d) jitter_ref=(%d,%d) "
            "bounds=%s screen=(%d,%d) hwnd=%s dry_run=%s",
            action.name,
            reason,
            point.x,
            point.y,
            jittered.x,
            jittered.y,
            None if click_bounds is None else click_bounds.as_tuple(),
            screen.x,
            screen.y,
            window.hwnd,
            self.dry_run,
        )
        self.backend.click(screen.x, screen.y)
        self.mark_action(action)
        if self.on_action is not None:
            self.on_action(
                f"{action.name} ref=({jittered.x},{jittered.y}) "
                f"screen=({screen.x},{screen.y})"
            )
        return screen

    def move_reference(
        self,
        window: WindowInfo,
        point: Point,
        *,
        reason: str,
        expected_hwnd: Optional[int] = None,
        duration: float = 0.03,
    ) -> Point:
        """将鼠标移回参考坐标，不点击、不刷新动作冷却。"""
        if expected_hwnd is not None and window.hwnd != expected_hwnd:
            raise BotError("窗口 hwnd 已变化，取消鼠标定位")

        live_rect = self.locator.current_rect(window.hwnd)
        live_window = WindowInfo(hwnd=window.hwnd, title=window.title, rect=live_rect)
        self.locator.validate_geometry(live_window)
        screen = reference_to_screen(
            point.x,
            point.y,
            live_rect,
            reference_width=int(self.cfg["window"]["reference_width"]),
            reference_height=int(self.cfg["window"]["reference_height"]),
        )
        logger.info(
            "鼠标回到当前刻印位置: reason=%s ref=(%d,%d) screen=(%d,%d) hwnd=%s dry_run=%s",
            reason,
            point.x,
            point.y,
            screen.x,
            screen.y,
            window.hwnd,
            self.dry_run,
        )
        self.backend.move_to(screen.x, screen.y, float(duration))
        return screen

    def swipe_reference(
        self,
        window: WindowInfo,
        start: Point,
        end: Point,
        *,
        action: ActionType,
        reason: str,
        duration: float,
        expected_hwnd: Optional[int] = None,
        frame_fresh: bool = True,
    ) -> tuple[Point, Point]:
        """在参考坐标系内拖拽（用于难度列表下滑）。"""
        if not self.enabled_check():
            raise BotError("自动化未启用，取消滑动")
        if not frame_fresh:
            raise BotError("截图已过期，取消滑动")
        if expected_hwnd is not None and window.hwnd != expected_hwnd:
            raise BotError("窗口 hwnd 已变化，取消滑动")
        if not self.can_act(action):
            raise BotError(f"动作冷却中，跳过 {action.name}")

        live_rect = self.locator.current_rect(window.hwnd)
        live_window = WindowInfo(hwnd=window.hwnd, title=window.title, rect=live_rect)
        self.locator.validate_geometry(live_window)

        if not self.dry_run:
            if not self.locator.focus(window.hwnd):
                raise BotError("无法将目标窗口切到前台，取消滑动")
            if not self.locator.is_foreground(window.hwnd):
                raise BotError("目标窗口不是前台窗口，取消滑动")

        ref_w = int(self.cfg["window"]["reference_width"])
        ref_h = int(self.cfg["window"]["reference_height"])
        s1 = reference_to_screen(start.x, start.y, live_rect, reference_width=ref_w, reference_height=ref_h)
        s2 = reference_to_screen(end.x, end.y, live_rect, reference_width=ref_w, reference_height=ref_h)
        logger.info(
            "执行滑动 %s: reason=%s ref=(%d,%d)->(%d,%d) screen=(%d,%d)->(%d,%d) dry_run=%s",
            action.name,
            reason,
            start.x,
            start.y,
            end.x,
            end.y,
            s1.x,
            s1.y,
            s2.x,
            s2.y,
            self.dry_run,
        )
        self.backend.drag(s1.x, s1.y, s2.x, s2.y, duration)
        self.mark_action(action)
        if self.on_action is not None:
            self.on_action(
                f"{action.name} ref=({start.x},{start.y})->({end.x},{end.y}) "
                f"screen=({s1.x},{s1.y})->({s2.x},{s2.y})"
            )
        return s1, s2

    def scroll_reference(
        self,
        window: WindowInfo,
        point: Point,
        *,
        clicks: int,
        action: ActionType,
        reason: str,
        expected_hwnd: Optional[int] = None,
        frame_fresh: bool = True,
    ) -> Point:
        """在参考坐标系内把鼠标移到列表区域并滚动固定次数。"""
        if not self.enabled_check():
            raise BotError("自动化未启用，取消滚轮滚动")
        if not frame_fresh:
            raise BotError("截图已过期，取消滚轮滚动")
        if expected_hwnd is not None and window.hwnd != expected_hwnd:
            raise BotError("窗口 hwnd 已变化，取消滚轮滚动")
        if int(clicks) == 0:
            raise BotError("滚轮次数不能为 0")
        if not self.can_act(action):
            raise BotError(f"动作冷却中，跳过 {action.name}")

        live_rect = self.locator.current_rect(window.hwnd)
        live_window = WindowInfo(hwnd=window.hwnd, title=window.title, rect=live_rect)
        self.locator.validate_geometry(live_window)

        if not self.dry_run:
            if not self.locator.focus(window.hwnd):
                raise BotError("无法将目标窗口切到前台，取消滚轮滚动")
            if not self.locator.is_foreground(window.hwnd):
                raise BotError("目标窗口不是前台窗口，取消滚轮滚动")

        screen = reference_to_screen(
            point.x,
            point.y,
            live_rect,
            reference_width=int(self.cfg["window"]["reference_width"]),
            reference_height=int(self.cfg["window"]["reference_height"]),
        )
        logger.info(
            "执行滚轮 %s: reason=%s ref=(%d,%d) clicks=%d "
            "screen=(%d,%d) hwnd=%s dry_run=%s",
            action.name,
            reason,
            point.x,
            point.y,
            int(clicks),
            screen.x,
            screen.y,
            window.hwnd,
            self.dry_run,
        )
        self.backend.scroll(screen.x, screen.y, int(clicks))
        self.mark_action(action)
        if self.on_action is not None:
            self.on_action(
                f"{action.name} ref=({point.x},{point.y}) clicks={int(clicks)} "
                f"screen=({screen.x},{screen.y})"
            )
        return screen
