from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Optional

import cv2
import numpy as np
import pyautogui

from .capture import ScreenCapture
from .config import debug_dir
from .coords import assess_window_geometry, scale_factors
from .input_controller import InputController
from .logger import get_logger
from .models import (
    ActionType,
    AspectRatioError,
    BotError,
    BotState,
    CaptureError,
    FrameAnalysis,
    Point,
    Rect,
    RuntimeStats,
    VisionError,
    WindowError,
    WindowInfo,
)
from .state_machine import StateMachine, _row_click_bounds
from .vision import draw_debug_overlay
from .window_locator import WindowLocator, enable_dpi_awareness

logger = get_logger(__name__)


class TowerController:
    def __init__(
        self,
        cfg: Mapping,
        *,
        dry_run: bool = False,
        on_stats: Optional[Callable[[RuntimeStats], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.on_stats = on_stats

        self.enabled = threading.Event()
        self.stopping = threading.Event()
        self.debug_mode = threading.Event()
        if cfg["debug"].get("enabled"):
            self.debug_mode.set()
        self.step_once = threading.Event()

        self.stats = RuntimeStats()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        self.dpi_mode = enable_dpi_awareness()
        logger.info("DPI 模式: %s", self.dpi_mode)

        self.locator = WindowLocator(cfg)
        self.capture = ScreenCapture(cfg)
        self.state_machine = StateMachine(cfg)
        self.input = InputController(
            cfg,
            self.locator,
            dry_run=dry_run,
            enabled_check=lambda: (
                (self.enabled.is_set() or self.step_once.is_set())
                and not self.stopping.is_set()
            ),
            on_action=self._record_input_action,
        )

        self._operation_history = deque(maxlen=12)
        self._last_vision_record = None

        self._select_retries = 0
        self._scroll_attempts = 0
        self._all_unlocked_down_streak = 0
        self._unchanged_list_scrolls = 0
        # 滑动后列表位置变了，但游戏仍可能保持旧难度选中；必须先点选最高层再挑战。
        self._force_select_before_challenge = False
        # 已确认在解锁/灰色交界：战后不要再下滑探一遍（避免一滑就全灰再滑回）。
        self._known_at_frontier = False
        # 已确认满级（如 50）：列表滑不动且全解锁后，直接挑战最高可见层。
        self._at_max_floor = False
        self._pre_scroll_list_fp: Optional[bytes] = None
        self._battle_started_at: Optional[float] = None
        self._unknown_since: Optional[float] = None
        self._last_window: Optional[WindowInfo] = None
        self._pending_select_verify = False
        self._pending_confirm_since: Optional[float] = None
        self._start_clicked_at: Optional[float] = None

    # ---------- 控制 ----------
    def start(self) -> None:
        if self.stopping.is_set():
            return
        self.enabled.set()
        self.stats.program_status = "运行中(dry-run)" if self.dry_run else "运行中"
        self.stats.last_error = "-"
        self._emit_stats()
        self._ensure_worker()
        logger.info("自动化已开始")

    def pause(self) -> None:
        self.enabled.clear()
        self.stats.program_status = "已暂停"
        self._emit_stats()
        logger.info("自动化已暂停")

    def toggle(self) -> None:
        if self.enabled.is_set():
            self.pause()
        else:
            self.start()

    def stop(self) -> None:
        # 先置停止位，使进行中的点击/等待能尽快中断。
        self.stopping.set()
        self.enabled.clear()
        self.step_once.clear()
        self.stats.program_status = "正在停止"
        self._emit_stats()
        logger.info("自动化已停止")

    def request_step(self) -> None:
        self.step_once.set()
        self._ensure_worker()

    def toggle_debug(self) -> None:
        if self.debug_mode.is_set():
            self.debug_mode.clear()
            logger.info("调试模式关闭")
        else:
            self.debug_mode.set()
            logger.info("调试模式开启")

    def emergency_pause(self, reason: str) -> None:
        self.enabled.clear()
        self.stats.program_status = "已触发紧急停止"
        self.stats.last_error = reason
        self._emit_stats()
        logger.warning("紧急暂停: %s", reason)

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def refresh_window_info(self) -> None:
        """供 UI 定时刷新窗口尺寸与建议，不执行点击。"""
        try:
            window = self.locator.find()
            if window is None:
                self.stats.window_status = "未找到目标窗口"
                self.stats.window_size = "-"
                self.stats.window_advice = (
                    "请打开标题包含「英雄没有闪」的微信小程序独立窗口（勿最小化）"
                )
                self._emit_stats()
                return
            self._last_window = window
            self._apply_window_geometry_stats(window)
            self._emit_stats()
        except Exception as exc:  # noqa: BLE001
            self.stats.window_status = "窗口检测异常"
            self.stats.window_size = "-"
            self.stats.window_advice = f"检测失败: {exc}"
            self._emit_stats()

    def _apply_window_geometry_stats(self, window: WindowInfo) -> None:
        ok, size_text, advice = assess_window_geometry(window.rect, self.cfg)
        self.stats.window_status = f"已找到 hwnd={window.hwnd}"
        self.stats.window_size = size_text
        self.stats.window_advice = ("合适 · " if ok else "建议调整 · ") + advice

    def shutdown(self) -> None:
        self.stop()
        self.join(timeout=3.0)
        self.capture.close()

    def _ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="tower-bot-loop", daemon=True)
        self._thread.start()

    def _emit_stats(self) -> None:
        if self.on_stats is not None:
            try:
                self.on_stats(self.stats)
            except Exception as exc:  # noqa: BLE001
                logger.debug("统计回调失败: %s", exc)

    # ---------- 主循环 ----------
    def _record_input_action(self, message: str) -> None:
        self._record_operation(message)

    def _record_operation(self, message: str) -> None:
        entry = f"{datetime.now():%H:%M:%S}  {message}"
        self._operation_history.append(entry)
        self.stats.operation_history = "\n".join(self._operation_history)
        self.stats.last_action = message
        logger.info("OPERATION | %s", entry)
        self._emit_stats()

    def _loop(self) -> None:
        logger.info("视觉工作线程已启动")
        try:
            while not self.stopping.is_set():
                active = self.enabled.is_set() or self.step_once.is_set()
                if not active:
                    time.sleep(0.1)
                    continue
                try:
                    self._tick()
                except pyautogui.FailSafeException:
                    self.emergency_pause("已触发紧急停止（鼠标角落 FailSafe）")
                except AspectRatioError as exc:
                    self._handle_recoverable(str(exc), enter_error=True)
                    time.sleep(1.0)
                except WindowError as exc:
                    self.stats.window_status = "未找到目标窗口"
                    self.stats.window_size = "-"
                    self.stats.window_advice = (
                        "请打开标题包含「英雄没有闪」的微信小程序独立窗口"
                    )
                    self.stats.vision_state = BotState.LOCATING_WINDOW.name
                    self.stats.last_error = str(exc)
                    self._emit_stats()
                    logger.warning("%s", exc)
                    time.sleep(float(self.cfg["capture"]["locate_retry_interval"]))
                except (CaptureError, VisionError, BotError) as exc:
                    if self.stopping.is_set():
                        break
                    # 暂停/停止过程中取消点击，不算错误。
                    if not self.enabled.is_set() and "自动化未启用" in str(exc):
                        continue
                    self._handle_recoverable(str(exc), enter_error=False)
                    time.sleep(0.5)
                except Exception as exc:  # noqa: BLE001
                    if self.stopping.is_set():
                        break
                    logger.exception("未处理异常: %s", exc)
                    self._handle_recoverable(f"未处理异常: {exc}", enter_error=True)
                    time.sleep(1.0)
                finally:
                    if self.step_once.is_set():
                        self.step_once.clear()
                        if not self.enabled.is_set():
                            self.stats.program_status = "单步完成/已暂停"
                            self._emit_stats()

                interval = float(self.cfg["capture"]["interval"])
                if self.state_machine.in_battle:
                    interval = float(self.cfg["capture"]["battle_interval"])
                time.sleep(interval)
        finally:
            self.capture.close()
            logger.info("视觉工作线程已结束")

    def _handle_recoverable(self, message: str, *, enter_error: bool) -> None:
        logger.error("%s", message)
        self.stats.last_error = message
        if enter_error:
            self.enabled.clear()
            self.stats.program_status = "错误/已暂停"
            self.stats.vision_state = BotState.ERROR.name
        self._emit_stats()

    def _tick(self) -> None:
        if self.stopping.is_set():
            return
        if not (self.enabled.is_set() or self.step_once.is_set()):
            return
        window = self.locator.find()
        if window is None:
            raise WindowError("未找到目标窗口")
        self._last_window = window
        self._apply_window_geometry_stats(window)
        self.locator.validate_geometry(window)

        frame = self.capture.grab_window(window.rect)
        analysis = self.state_machine.analyze_frame(frame)
        self._update_stats_from_analysis(analysis, window)

        # 其他设备登录：立即停止爬塔，不点击、不继续循环动作。
        if (
            analysis.state == BotState.OTHER_DEVICE_LOGIN
            or analysis.next_action == ActionType.STOP_FOR_OTHER_DEVICE
        ):
            self._handle_other_device_login(frame, analysis, window)
            return

        # 视觉锚点：选择界面要求 challenge 或 select_anchor。
        if analysis.state == BotState.SELECT_DIFFICULTY:
            if not (analysis.challenge.hit or analysis.select_anchor.hit):
                raise WindowError("选择界面视觉锚点未通过")

        if self.debug_mode.is_set():
            self._save_debug(frame, analysis, window, tag="latest")

        self._check_timeouts(analysis)
        self._maybe_save_special_debug(frame, analysis, window)
        self._execute_action(window, analysis)

    def _handle_other_device_login(
        self,
        frame,
        analysis: FrameAnalysis,
        window: WindowInfo,
    ) -> None:
        message = (
            "检测到「该账号在其他设备登录」，已自动停止爬塔。"
            f" score={analysis.other_device.score:.3f}"
        )
        logger.warning("%s", message)
        self.enabled.clear()
        self.stats.program_status = "已暂停：其他设备登录"
        self.stats.vision_state = BotState.OTHER_DEVICE_LOGIN.name
        self.stats.last_error = message
        self.stats.last_action = "STOP_FOR_OTHER_DEVICE | 停止点击与循环"
        self._save_debug(
            frame,
            analysis,
            window,
            tag="other_device",
            timestamped=True,
        )
        self._emit_stats()

    def _update_stats_from_analysis(self, analysis: FrameAnalysis, window: WindowInfo) -> None:
        sx, sy = scale_factors(
            window.rect,
            reference_width=int(self.cfg["window"]["reference_width"]),
            reference_height=int(self.cfg["window"]["reference_height"]),
        )
        analysis.scale_x, analysis.scale_y = sx, sy
        analysis.window_size = (window.rect.width, window.rect.height)

        self.stats.vision_state = analysis.state.name
        self.stats.challenge_score = analysis.challenge.score
        self.stats.start_score = analysis.start.score
        self.stats.close_score = analysis.close.score
        highest = analysis.difficulty.highest_unlocked_index
        selected = analysis.difficulty.selected_index
        self.stats.highest_unlocked_row = "-" if highest is None else str(highest)
        self.stats.selected_row = "-" if selected is None else str(selected)
        vision_record = (
            analysis.state.name,
            analysis.next_action.name,
        )
        if vision_record != self._last_vision_record:
            self._last_vision_record = vision_record
            self._record_operation(
                f"VISION state={analysis.state.name} "
                f"challenge={analysis.challenge.score:.3f} "
                f"start={analysis.start.score:.3f} "
                f"next={analysis.next_action.name}"
            )
        if analysis.difficulty.at_unlock_frontier:
            self._scroll_attempts = 0
            self._all_unlocked_down_streak = 0
            self._unchanged_list_scrolls = 0
            # 看见真实灰色交界 → 绝不是误判的“满级中途停住”。
            if not analysis.difficulty.all_unlocked_visible:
                self._at_max_floor = False
        self._emit_stats()

    def _check_timeouts(self, analysis: FrameAnalysis) -> None:
        now = time.monotonic()
        if analysis.state == BotState.UNKNOWN:
            if self._unknown_since is None:
                self._unknown_since = now
            elif now - self._unknown_since > float(self.cfg["actions"]["unknown_timeout"]):
                self._unknown_since = now
                raise BotError("连续无法识别界面超时")
        else:
            self._unknown_since = None

        if analysis.state == BotState.IN_BATTLE:
            if self._battle_started_at is None:
                self._battle_started_at = now
            elif now - self._battle_started_at > float(self.cfg["actions"]["battle_timeout"]):
                self._battle_started_at = None
                raise BotError("战斗等待超时，已进入错误暂停（不盲点）")
        elif analysis.state in (BotState.SELECT_DIFFICULTY, BotState.RESULT, BotState.CONFIRM_CHALLENGE):
            self._battle_started_at = None

    def _execute_action(self, window: WindowInfo, analysis: FrameAnalysis) -> None:
        action = analysis.next_action
        if action == ActionType.STOP_FOR_OTHER_DEVICE:
            return

        # 点击“挑战深渊”后等待弹窗动画，期间禁止再次点击底层挑战按钮。
        if self._pending_confirm_since is not None:
            self._execute_pending_confirm(window, analysis)
            return

        if (
            analysis.state == BotState.CONFIRM_CHALLENGE
            and self._start_clicked_at is not None
            and time.monotonic() - self._start_clicked_at
            < float(self.cfg["actions"].get("start_retry_wait", 1.5))
        ):
            return

        # 最后一关专刷模式：确认列表到底之前，任何中途关卡都不能被点击或挑战。
        # 若当前已经位于底部，一次短上滑会保持列表不变，随后立即选择难度50。
        last_level_only = bool(
            self.cfg.get("difficulty", {}).get("last_level_only", False)
        )
        if last_level_only and analysis.state == BotState.SELECT_DIFFICULTY:
            if self._at_max_floor:
                self._redirect_to_select_or_challenge(
                    window,
                    analysis,
                    reason=self._target_level_reason("已确认列表底部，挑战"),
                )
                return
            if analysis.difficulty.highest_unlocked_index is not None:
                action = ActionType.SCROLL_DIFFICULTY_DOWN
                analysis.next_action = action
                analysis.action_point = None
                analysis.action_bounds = None
                analysis.reason = self._target_level_reason(
                    "尚未确认列表底部，继续下滑寻找"
                )

        # 看见灰色锁定交界时，取消错误的满级标记（中途全解锁≠满级50）。
        if (
            analysis.difficulty.at_unlock_frontier
            and not analysis.difficulty.all_unlocked_visible
        ):
            self._at_max_floor = False

        # 仅当「下滑后列表画面几乎不变」才判定满级；禁止用「滑几下全解锁」误判。
        if action == ActionType.SCROLL_DIFFICULTY_DOWN:
            frame = self.capture.last_frame
            if frame is not None:
                self._pre_scroll_list_fp = self._list_fingerprint(frame)

        if (
            action == ActionType.SCROLL_DIFFICULTY_DOWN
            and analysis.difficulty.all_unlocked_visible
            and self._at_max_floor
        ):
            logger.info("已确认满级(列表滑不动)，跳过下滑直接挑战")
            self._redirect_to_select_or_challenge(
                window, analysis, reason="已确认满级，直接挑战最高可见层"
            )
            return

        # 整页全解锁且未确认满级：禁止点选/挑战（防止弹回顶部后误打低难度）。
        if (
            analysis.state == BotState.SELECT_DIFFICULTY
            and analysis.difficulty.all_unlocked_visible
            and not self._at_max_floor
            and action
            in (ActionType.CLICK_CHALLENGE, ActionType.CLICK_DIFFICULTY, ActionType.NONE)
        ):
            action = ActionType.SCROLL_DIFFICULTY_DOWN
            analysis.next_action = action
            analysis.action_point = None
            analysis.action_bounds = None
            analysis.reason = "整页均为已解锁，禁止挑战，继续下滑寻找最高难度"
            logger.info(analysis.reason)

        if action == ActionType.SCROLL_DIFFICULTY_DOWN:
            if not self.input.can_act(action):
                return
            self._scroll_difficulty(window, analysis, direction="down")
            self._maybe_mark_max_floor_after_scroll(window)
            return
        if action == ActionType.SCROLL_DIFFICULTY_UP:
            if not self.input.can_act(action):
                return
            mode = "center" if analysis.difficulty.needs_center_up else "up"
            self._scroll_difficulty(window, analysis, direction=mode)
            self._unchanged_list_scrolls = 0
            self._pre_scroll_list_fp = None
            return

        # 滑动后禁止凭「画面上好像已选中」直接挑战：旧难度（如33）可能仍被游戏选中。
        if (
            action == ActionType.CLICK_CHALLENGE
            and self._force_select_before_challenge
        ):
            highest = analysis.difficulty.highest_unlocked_row
            if highest is None:
                return

            click_x = int(self.cfg["difficulty"]["click_x"])
            analysis.next_action = ActionType.CLICK_DIFFICULTY
            analysis.action_point = Point(click_x, highest.center_y)
            analysis.action_bounds = _row_click_bounds(self.cfg, highest.center_y)
            analysis.reason = (
                f"滑动后强制先点选最高难度行#{highest.row_index}，再挑战"
            )
            action = ActionType.CLICK_DIFFICULTY
            logger.info(analysis.reason)

        if action == ActionType.NONE or analysis.action_point is None:
            return
        if not self.input.can_act(action):
            return

        # 选择难度后的验证流程。
        if action == ActionType.CLICK_DIFFICULTY:
            self._click_and_verify_select(window, analysis)
            return

        screen = self.input.click_reference(
            window,
            analysis.action_point,
            action=action,
            reason=analysis.reason,
            click_bounds=analysis.action_bounds,
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
            state_allows=analysis.state
            in (
                BotState.SELECT_DIFFICULTY,
                BotState.CONFIRM_CHALLENGE,
                BotState.RESULT,
            ),
            confidence_ok=True,
        )
        self.stats.last_action = (
            f"{action.name} screen=({screen.x},{screen.y}) | {analysis.reason}"
        )
        if action == ActionType.CLICK_CHALLENGE:
            self._mark_challenge_clicked()
        elif action == ActionType.CLICK_START:
            self._start_clicked_at = time.monotonic()
            self.stats.challenges_started += 1
            self.state_machine.mark_battle_started()
            self._battle_started_at = time.monotonic()
        elif action == ActionType.CLICK_CLOSE:
            self.stats.results_closed += 1
            # 战后列表可能弹回顶部：不假设仍在交界，必须重新下滑找灰色最高层。
            self._known_at_frontier = False
        self._emit_stats()
        self._sleep_interruptible(float(self.cfg["actions"]["post_click_wait"]))

    def _list_fingerprint(self, frame: np.ndarray) -> bytes:
        """难度列表区域缩略图，用于判断下滑后画面是否变化。"""
        roi = self.cfg.get("rois", {}).get("difficulty_list", [70, 320, 480, 805])
        x1, y1, x2, y2 = [int(v) for v in roi]
        h, w = frame.shape[:2]
        x1, x2 = max(0, min(x1, w - 1)), max(0, min(x2, w))
        y1, y2 = max(0, min(y1, h - 1)), max(0, min(y2, h))
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return b""
        small = cv2.resize(region, (48, 64), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        # 量化一点，降低压缩噪声敏感度。
        return (gray // 8).tobytes()

    def _target_level_reason(self, prefix: str) -> str:
        target = int(self.cfg.get("difficulty", {}).get("target_level", 50))
        return f"{prefix}唯一目标难度{target}"

    def _mark_challenge_clicked(self) -> None:
        self._pending_confirm_since = time.monotonic()
        # 挑战按钮点击后立即作废“已在50底部”的缓存。若游戏异常跳回初始
        # 列表，下一轮必须重新下滑寻找50，绝不能复用旧状态挑战低难度。
        self._at_max_floor = False
        wait = float(self.cfg["actions"].get("confirm_open_wait", 1.0))
        self.stats.last_action = f"WAIT_CONFIRM | 等待 {wait:.1f}s 后点击开始挑战"
        self._emit_stats()

    def _execute_pending_confirm(
        self,
        window: WindowInfo,
        analysis: FrameAnalysis,
    ) -> None:
        assert self._pending_confirm_since is not None
        wait = float(self.cfg["actions"].get("confirm_open_wait", 1.0))
        if time.monotonic() - self._pending_confirm_since < wait:
            return
        if not self.input.can_act(ActionType.CLICK_START):
            return

        popup_visible = (
            analysis.state == BotState.CONFIRM_CHALLENGE
            or analysis.start.hit
            or analysis.start.score >= 0.50
        )
        if not popup_visible:
            self._pending_confirm_since = None
            self._at_max_floor = False
            self._force_select_before_challenge = False
            self._scroll_attempts = 0
            self._record_operation(
                "CONFIRM_MISSING | 弹窗消失或游戏跳回列表，禁止点击并重新寻找难度50"
            )
            return

        point_values = self.cfg["actions"].get("confirm_start_point", [277, 707])
        bounds_values = self.cfg["actions"].get(
            "confirm_start_bounds", [205, 675, 350, 740]
        )
        point = Point(int(point_values[0]), int(point_values[1]))
        bounds = Rect(*[int(value) for value in bounds_values])
        screen = self.input.click_reference(
            window,
            point,
            action=ActionType.CLICK_START,
            reason="挑战按钮已点击，等待弹窗动画后固定点击开始挑战",
            click_bounds=bounds,
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
            state_allows=True,
            confidence_ok=True,
            allow_jitter=False,
        )
        self._pending_confirm_since = None
        self._at_max_floor = False
        self._start_clicked_at = time.monotonic()
        self.stats.challenges_started += 1
        self.stats.last_action = (
            f"CLICK_START screen=({screen.x},{screen.y}) | 固定安全点击"
        )
        self.state_machine.mark_battle_started()
        self._battle_started_at = time.monotonic()
        self._emit_stats()
        self._sleep_interruptible(float(self.cfg["actions"]["post_click_wait"]))

    @staticmethod
    def _list_fingerprints_similar(
        before: bytes,
        after: bytes,
        *,
        mean_diff_threshold: float,
        equal_ratio_threshold: float,
    ) -> bool:
        if not before or len(before) != len(after):
            return False
        left = np.frombuffer(before, dtype=np.uint8).astype(np.int16)
        right = np.frombuffer(after, dtype=np.uint8).astype(np.int16)
        mean_diff = float(np.mean(np.abs(left - right)))
        equal_ratio = float(np.mean(left == right))
        return (
            mean_diff <= mean_diff_threshold
            and equal_ratio >= equal_ratio_threshold
        )

    def _maybe_mark_max_floor_after_scroll(self, window: WindowInfo) -> None:
        """下滑后若仍全解锁且列表几乎不动 → 已到列表底部（满级如50）。"""
        pre = self._pre_scroll_list_fp
        self._pre_scroll_list_fp = None
        if pre is None:
            return
        live = self.locator.current_rect(window.hwnd)
        frame = self.capture.grab_window(live)
        after = self.state_machine.analyze_frame(frame, debounce=False)
        if not after.difficulty.all_unlocked_visible:
            self._unchanged_list_scrolls = 0
            return
        # 已看到灰色交界则不是满级误判路径。
        if after.difficulty.at_unlock_frontier and not after.difficulty.all_unlocked_visible:
            self._unchanged_list_scrolls = 0
            self._at_max_floor = False
            return

        post = self._list_fingerprint(frame)
        scroll_cfg = self.cfg["difficulty"].get("scroll", {})
        unchanged = self._list_fingerprints_similar(
            pre,
            post,
            mean_diff_threshold=float(
                scroll_cfg.get("fingerprint_mean_diff_threshold", 0.75)
            ),
            equal_ratio_threshold=float(
                scroll_cfg.get("fingerprint_equal_ratio_threshold", 0.50)
            ),
        )
        if unchanged:
            self._unchanged_list_scrolls += 1
        else:
            self._unchanged_list_scrolls = 0

        need = int(scroll_cfg.get("unchanged_scrolls_for_max", 2))
        if self._unchanged_list_scrolls >= need:
            logger.info(
                "下滑 %d 次后难度列表画面不变且仍全解锁，判定已到满级底部，之后直接挑战",
                self._unchanged_list_scrolls,
            )
            self._at_max_floor = True
            self._unchanged_list_scrolls = 0
            self._challenge_highest_visible(window, after)

    def _redirect_to_select_or_challenge(
        self,
        window: WindowInfo,
        analysis: FrameAnalysis,
        *,
        reason: str,
    ) -> None:
        """在不应下滑时，改为点选/挑战当前最高已解锁行。"""
        highest = analysis.difficulty.highest_unlocked_row
        if highest is None:
            return
        from .state_machine import _match_click_bounds, _row_click_bounds

        if highest.selected and analysis.challenge.center is not None:
            if self._force_select_before_challenge:
                analysis.next_action = ActionType.CLICK_DIFFICULTY
                analysis.action_point = Point(
                    int(self.cfg["difficulty"]["click_x"]), highest.center_y
                )
                analysis.action_bounds = _row_click_bounds(self.cfg, highest.center_y)
                analysis.reason = reason + f"（先确认行#{highest.row_index}）"
                self._click_and_verify_select(window, analysis)
                return
            if not self.input.can_act(ActionType.CLICK_CHALLENGE):
                self.input.last_action_at = 0.0
            screen = self.input.click_reference(
                window,
                analysis.challenge.center,
                action=ActionType.CLICK_CHALLENGE,
                reason=reason,
                click_bounds=_match_click_bounds(analysis.challenge),
                expected_hwnd=window.hwnd,
                frame_fresh=self.capture.is_fresh(),
                state_allows=True,
                confidence_ok=analysis.challenge.hit,
            )
            self._mark_challenge_clicked()
            self.stats.last_action = (
                f"CLICK_CHALLENGE screen=({screen.x},{screen.y}) | {reason}"
            )
            self._emit_stats()
            self._sleep_interruptible(float(self.cfg["actions"]["post_click_wait"]))
            return

        analysis.next_action = ActionType.CLICK_DIFFICULTY
        analysis.action_point = Point(
            int(self.cfg["difficulty"]["click_x"]), highest.center_y
        )
        analysis.action_bounds = _row_click_bounds(self.cfg, highest.center_y)
        analysis.reason = reason + f"（点选行#{highest.row_index}）"
        self._click_and_verify_select(window, analysis)

    def _sleep_interruptible(self, seconds: float) -> bool:
        """可被暂停/停止打断的等待。返回 False 表示已被中断。"""
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            if self.stopping.is_set() or not (
                self.enabled.is_set() or self.step_once.is_set()
            ):
                return False
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
        return True

    def _scroll_difficulty(
        self,
        window: WindowInfo,
        analysis: FrameAnalysis,
        *,
        direction: str,
    ) -> None:
        scroll = self.cfg["difficulty"].get("scroll", {})
        if not bool(scroll.get("enabled", True)):
            raise BotError("需要滑动寻找最高难度，但 difficulty.scroll.enabled=false")

        max_attempts = int(scroll.get("max_attempts", 25))
        self._scroll_attempts += 1
        if self._scroll_attempts > max_attempts:
            self._scroll_attempts = 0
            frame = self.capture.last_frame
            if frame is None:
                frame = np.zeros((1020, 550, 3), dtype=np.uint8)
            self._save_debug(
                frame,
                analysis,
                window,
                tag="scroll_exhausted",
                timestamped=True,
            )
            raise BotError(
                f"滑动 {max_attempts} 次仍未稳定找到与灰色锁定相接的最高难度，已暂停"
            )

        # 列表滑动仍走列表中部，避免和选关点击区域混淆。
        scroll_x = int(scroll.get("scroll_x", 275))
        click_x = scroll_x
        if direction in ("up", "center"):
            # 手指下滑：露出上方内容，把贴顶的最高层移到中间。
            if direction == "center":
                start = Point(click_x, int(scroll.get("center_up_start_y", 480)))
                end = Point(click_x, int(scroll.get("center_up_end_y", 580)))
                label = "SCROLL_CENTER"
            else:
                start = Point(click_x, int(scroll.get("up_start_y", 420)))
                end = Point(click_x, int(scroll.get("up_end_y", 560)))
                label = "SCROLL_UP"
            action = ActionType.SCROLL_DIFFICULTY_UP
        else:
            # 手指上滑：露出下方更高难度。
            start = Point(click_x, int(scroll.get("down_start_y", 680)))
            end = Point(click_x, int(scroll.get("down_end_y", 540)))
            action = ActionType.SCROLL_DIFFICULTY_DOWN
            label = "SCROLL_DOWN"

        duration = float(scroll.get("duration", 0.35))
        post_wait = float(scroll.get("post_wait", 0.55))

        self.input.swipe_reference(
            window,
            start,
            end,
            action=action,
            reason=analysis.reason,
            duration=duration,
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
        )
        self._force_select_before_challenge = True
        self.stats.last_action = (
            f"{label} #{self._scroll_attempts}/{max_attempts} | {analysis.reason}"
        )
        self._emit_stats()
        logger.info(
            "难度列表%s %d/%d，等待界面稳定…",
            {
                "up": "上滑回看",
                "center": "上滑居中",
                "down": "下滑前进",
            }.get(direction, direction),
            self._scroll_attempts,
            max_attempts,
        )
        self._sleep_interruptible(post_wait)

    def _challenge_highest_visible(
        self,
        window: WindowInfo,
        analysis: FrameAnalysis,
    ) -> None:
        """满级无灰色时：必须先点选最高可见层并验证，再挑战（禁止跳过选中）。"""
        highest = analysis.difficulty.highest_unlocked_row
        if highest is None:
            raise BotError("判定满级后仍无已解锁行")
        click_x = int(self.cfg["difficulty"]["click_x"])
        from .state_machine import _row_click_bounds

        point = Point(click_x, highest.center_y)
        bounds = _row_click_bounds(self.cfg, highest.center_y)
        analysis.next_action = ActionType.CLICK_DIFFICULTY
        analysis.action_point = point
        analysis.action_bounds = bounds
        analysis.reason = f"满级无灰色，先点选最高可见行#{highest.row_index}再挑战"
        self._force_select_before_challenge = True
        self._click_and_verify_select(window, analysis)

    def _click_and_verify_select(self, window: WindowInfo, analysis: FrameAnalysis) -> None:
        assert analysis.action_point is not None
        if self.stopping.is_set() or not (
            self.enabled.is_set() or self.step_once.is_set()
        ):
            return
        max_retries = int(self.cfg["actions"]["max_select_retries"])
        self.input.click_reference(
            window,
            analysis.action_point,
            action=ActionType.CLICK_DIFFICULTY,
            reason=analysis.reason,
            click_bounds=analysis.action_bounds,
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
            state_allows=True,
            confidence_ok=True,
            allow_jitter=False,
        )
        self.stats.last_action = f"CLICK_DIFFICULTY | {analysis.reason}"
        self._emit_stats()
        if not self._sleep_interruptible(float(self.cfg["actions"]["post_select_wait"])):
            return

        # 重新截图验证：必须是「当前最高已解锁行」出现金边，才能挑战。
        live = self.locator.current_rect(window.hwnd)
        frame = self.capture.grab_window(live)
        verify = self.state_machine.analyze_frame(frame, debounce=False)
        highest = verify.difficulty.highest_unlocked_row
        if highest is not None and highest.selected:
            self._select_retries = 0
            self._force_select_before_challenge = False
            logger.info("难度选中验证成功: row=%d", highest.row_index)
            if verify.challenge.center is not None:
                self.input.last_action_at = 0.0
                from .state_machine import _match_click_bounds

                screen = self.input.click_reference(
                    window,
                    verify.challenge.center,
                    action=ActionType.CLICK_CHALLENGE,
                    reason="选中验证通过后点击挑战深渊",
                    click_bounds=_match_click_bounds(verify.challenge),
                    expected_hwnd=window.hwnd,
                    frame_fresh=self.capture.is_fresh(),
                    state_allows=True,
                    confidence_ok=verify.challenge.hit,
                )
                self._mark_challenge_clicked()
                self.stats.last_action = (
                    f"CLICK_CHALLENGE screen=({screen.x},{screen.y}) | 选中后挑战"
                )
                self._emit_stats()
                self._sleep_interruptible(float(self.cfg["actions"]["post_click_wait"]))
            return

        self._select_retries += 1
        self._save_debug(frame, verify, window, tag="select_verify_fail", timestamped=True)
        if self._select_retries > max_retries:
            self._select_retries = 0
            raise BotError(
                f"点击最高难度后未出现黄色选中框，已重试 {max_retries} 次；"
                "已暂停（避免误挑战旧难度）"
            )
        logger.warning(
            "选中验证失败（最高行未出现金边），将重试 (%d/%d)",
            self._select_retries,
            max_retries,
        )

    def _maybe_save_special_debug(
        self,
        frame: np.ndarray,
        analysis: FrameAnalysis,
        window: WindowInfo,
    ) -> None:
        if not self.cfg["debug"].get("save_unknown_frames", True):
            return
        near = float(self.cfg["matching"]["near_threshold_delta"])
        if analysis.state == BotState.UNKNOWN:
            self._save_debug(frame, analysis, window, tag="unknown", timestamped=True)
        if (
            analysis.state == BotState.SELECT_DIFFICULTY
            and analysis.difficulty.highest_unlocked_index is None
        ):
            self._save_debug(frame, analysis, window, tag="no_unlocked", timestamped=True)
        for match, th_key in (
            (analysis.challenge, "challenge_threshold"),
            (analysis.start, "start_threshold"),
            (analysis.close, "close_threshold"),
        ):
            th = float(self.cfg["matching"][th_key])
            if (not match.hit) and match.score >= th - near:
                self._save_debug(frame, analysis, window, tag=f"near_{match.template_name}", timestamped=True)

    def _save_debug(
        self,
        frame: np.ndarray,
        analysis: FrameAnalysis,
        window: WindowInfo,
        *,
        tag: str,
        timestamped: bool = False,
    ) -> None:
        try:
            out_dir = debug_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            matches = {
                "select_anchor": analysis.select_anchor,
                "challenge_button": analysis.challenge,
                "start_button": analysis.start,
                "close_prompt": analysis.close,
                "other_device_message": analysis.other_device,
            }
            overlay = draw_debug_overlay(
                frame,
                matches,
                rows=analysis.difficulty.rows,
                state_name=analysis.state.name,
                action_name=analysis.next_action.name,
                highest_index=analysis.difficulty.highest_unlocked_index,
                scale=(analysis.scale_x, analysis.scale_y),
                window_size=analysis.window_size,
            )
            latest = out_dir / "latest.png"
            cv2.imwrite(str(latest), overlay)
            if timestamped or tag != "latest":
                if timestamped:
                    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    path = out_dir / f"{stamp}_{tag}.png"
                    cv2.imwrite(str(path), overlay)
            self._prune_debug_files(out_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("保存调试图失败: %s", exc)

    def _prune_debug_files(self, out_dir: Path) -> None:
        max_files = int(self.cfg["debug"]["max_files"])
        files = sorted(
            [p for p in out_dir.glob("*.png") if p.name != "latest.png"],
            key=lambda p: p.stat().st_mtime,
        )
        while len(files) > max_files:
            old = files.pop(0)
            try:
                old.unlink(missing_ok=True)
            except OSError:
                break

    # 供离线分析直接使用
    def analyze_image(self, image: np.ndarray) -> FrameAnalysis:
        return self.state_machine.analyze_frame(image, debounce=False)
