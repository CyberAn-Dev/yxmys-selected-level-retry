from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping, Optional

import cv2
import numpy as np
import pyautogui

from tower_bot.capture import ScreenCapture
from tower_bot.coords import assess_window_geometry, scale_factors
from tower_bot.input_controller import InputController
from tower_bot.logger import get_logger
from tower_bot.models import (
    ActionType,
    AspectRatioError,
    BotError,
    BotState,
    CaptureError,
    FrameAnalysis,
    Point,
    Rect,
    VisionError,
    WindowError,
    WindowInfo,
)
from tower_bot.state_machine import StateMachine, _match_click_bounds, _row_click_bounds
from tower_bot.vision import draw_debug_overlay
from tower_bot.window_locator import WindowLocator, enable_dpi_awareness

from .result_detector import ResultDetector
from .target_tracker import TargetCandidate, TargetSnapshot, TargetTracker


logger = get_logger(__name__)


@dataclass
class RetryStats:
    program_status: str = "已停止"
    window_status: str = "未查找"
    window_size: str = "-"
    window_advice: str = "尚未检测到小程序窗口"
    vision_state: str = BotState.DISABLED.name
    target_status: str = "未记录"
    target_row: str = "-"
    target_visible: str = "-"
    attempts: int = 0
    failures: int = 0
    recovery_scrolls: int = 0
    result_status: str = "-"
    last_action: str = "-"
    operation_history: str = "-"
    last_error: str = "-"
    success_score: float = 0.0
    failure_score: float = 0.0


class SelectedLevelRetryController:
    """用户选中一关后，重复挑战直到成功的独立控制器。"""

    def __init__(
        self,
        cfg: Mapping,
        feature_cfg: Mapping,
        *,
        dry_run: bool = False,
        on_stats: Optional[Callable[[RetryStats], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.feature_cfg = feature_cfg
        self.dry_run = dry_run
        self.on_stats = on_stats

        self.enabled = threading.Event()
        self.stopping = threading.Event()
        self.step_once = threading.Event()
        self.debug_mode = threading.Event()
        if cfg["debug"].get("enabled"):
            self.debug_mode.set()

        self.stats = RetryStats()
        self._thread: Optional[threading.Thread] = None
        self._history = deque(maxlen=12)
        self._last_vision_record: Optional[tuple[str, str]] = None

        self.dpi_mode = enable_dpi_awareness()
        logger.info("选定关卡重复挑战 DPI 模式: %s", self.dpi_mode)
        self.locator = WindowLocator(cfg)
        self.capture = ScreenCapture(cfg)
        self.state_machine = StateMachine(cfg)
        self.target_tracker = TargetTracker(cfg, feature_cfg)
        self.result_detector = ResultDetector(feature_cfg)
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

        self._target: Optional[TargetSnapshot] = None
        self._pending_confirm_since: Optional[float] = None
        self._battle_started_at: Optional[float] = None
        self._unknown_since: Optional[float] = None
        self._closing_result_since: Optional[float] = None
        self._selection_retries = 0
        self._recovery_direction = "down"
        self._recovery_scrolls = 0
        self._pending_scroll_fp: Optional[bytes] = None
        self._unchanged_scrolls = 0
        self._last_window: Optional[WindowInfo] = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self.stopping.is_set():
            return
        if not self.result_detector.ready:
            self._handle_recoverable(self.result_detector.missing_message(), enter_error=True)
            return
        self.enabled.set()
        self.stats.program_status = "运行中(dry-run)" if self.dry_run else "运行中"
        self.stats.last_error = "-"
        self._emit_stats()
        self._ensure_worker()
        logger.info("选定关卡重复挑战已开始")

    def pause(self) -> None:
        self.enabled.clear()
        self.stats.program_status = "已暂停"
        self._emit_stats()
        logger.info("选定关卡重复挑战已暂停")

    def toggle(self) -> None:
        if self.enabled.is_set():
            self.pause()
        else:
            self.start()

    def stop(self) -> None:
        self.stopping.set()
        self.enabled.clear()
        self.step_once.clear()
        self.stats.program_status = "正在停止"
        self._emit_stats()
        logger.info("选定关卡重复挑战正在停止")

    def shutdown(self) -> None:
        self.stop()
        self.join(timeout=3.0)
        self.capture.close()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def request_step(self) -> None:
        if not self.result_detector.ready:
            self._handle_recoverable(self.result_detector.missing_message(), enter_error=True)
            return
        self.step_once.set()
        self._ensure_worker()

    def toggle_debug(self) -> None:
        if self.debug_mode.is_set():
            self.debug_mode.clear()
            logger.info("选定关卡重复挑战调试模式关闭")
        else:
            self.debug_mode.set()
            logger.info("选定关卡重复挑战调试模式开启")

    def emergency_pause(self, reason: str) -> None:
        self.enabled.clear()
        self.stats.program_status = "已触发紧急停止"
        self.stats.last_error = reason
        self._emit_stats()
        logger.warning("选定关卡重复挑战紧急暂停: %s", reason)

    def refresh_window_info(self) -> None:
        try:
            window = self.locator.find()
            if window is None:
                self.stats.window_status = "未找到目标窗口"
                self.stats.window_size = "-"
                self.stats.window_advice = "请打开标题包含「英雄没有闪」的微信小程序独立窗口"
            else:
                self._last_window = window
                ok, size, advice = assess_window_geometry(window.rect, self.cfg)
                self.stats.window_status = f"已找到 hwnd={window.hwnd}"
                self.stats.window_size = size
                self.stats.window_advice = ("合适 · " if ok else "建议调整 · ") + advice
            self._emit_stats()
        except Exception as exc:  # noqa: BLE001
            self.stats.window_status = "窗口检测异常"
            self.stats.window_advice = f"检测失败: {exc}"
            self._emit_stats()

    # ---------- 工作线程 ----------
    def _ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="selected-level-retry-loop",
            daemon=True,
        )
        self._thread.start()

    def _loop(self) -> None:
        logger.info("选定关卡重复挑战视觉线程已启动")
        try:
            while not self.stopping.is_set():
                active = self.enabled.is_set() or self.step_once.is_set()
                if not active:
                    time.sleep(0.1)
                    continue
                try:
                    self._tick()
                except pyautogui.FailSafeException:
                    self.emergency_pause("已触发鼠标角落 FailSafe")
                except AspectRatioError as exc:
                    self._handle_recoverable(str(exc), enter_error=True)
                    time.sleep(1.0)
                except WindowError as exc:
                    self.stats.window_status = "未找到/不可用"
                    self.stats.last_error = str(exc)
                    self.stats.vision_state = BotState.LOCATING_WINDOW.name
                    self._emit_stats()
                    time.sleep(float(self.cfg["capture"]["locate_retry_interval"]))
                except (CaptureError, VisionError, BotError) as exc:
                    if self.stopping.is_set():
                        break
                    self._handle_recoverable(str(exc), enter_error=True)
                    time.sleep(0.5)
                except Exception as exc:  # noqa: BLE001
                    if self.stopping.is_set():
                        break
                    logger.exception("选定关卡重复挑战未处理异常")
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
            logger.info("选定关卡重复挑战视觉线程已结束")

    def _tick(self) -> None:
        if self.stopping.is_set() or not (self.enabled.is_set() or self.step_once.is_set()):
            return
        window = self.locator.find()
        if window is None:
            raise WindowError("未找到目标窗口")
        self._last_window = window
        self.locator.validate_geometry(window)
        frame = self.capture.grab_window(window.rect)
        analysis = self.state_machine.analyze_frame(frame)
        self._update_stats(analysis, window)
        if self.debug_mode.is_set():
            self._save_debug(frame, analysis, window, "latest")

        if analysis.state == BotState.OTHER_DEVICE_LOGIN:
            self.emergency_pause("检测到账号在其他设备登录，已停止重复挑战")
            return

        if self._target is None:
            if analysis.state != BotState.SELECT_DIFFICULTY:
                raise BotError("请先在游戏中手动选中一关，再点击开始")
            self._remember_target(frame, analysis)

        # 成功模板是正向停止信号，优先于 close/战斗状态。
        outcome = self.result_detector.detect(frame)
        if outcome.status == "success":
            self._mark_success(outcome)
            return

        if self._pending_confirm_since is not None:
            self._handle_pending_confirm(window, analysis)
            return

        if analysis.state == BotState.RESULT:
            self._handle_result(window, frame, analysis, outcome=outcome)
            return
        if analysis.state == BotState.CONFIRM_CHALLENGE:
            # 允许用户在启动前已手动打开确认弹窗；仍使用固定安全点。
            self._click_start(window, analysis, reason="检测到确认弹窗")
            return
        if analysis.state == BotState.SELECT_DIFFICULTY:
            self._handle_select(window, frame, analysis)
            return
        if analysis.state == BotState.IN_BATTLE:
            self._check_battle_timeout()
            return
        self._check_unknown_timeout(analysis)

    # ---------- 目标关卡 ----------
    def _remember_target(self, frame: np.ndarray, analysis: FrameAnalysis) -> None:
        target = self.target_tracker.capture(frame, analysis.difficulty)
        if target is None:
            raise BotError("未检测到明确的黄色选中关卡，请先手动选中一关")
        self._target = target
        self.stats.target_row = str(target.source_row_index)
        self.stats.target_status = f"已记录行#{target.source_row_index}"
        self.stats.target_visible = "是"
        self._record_operation(
            f"TARGET_CAPTURED row={target.source_row_index} y={target.source_center_y} "
            f"features={target.keypoint_count}"
        )

    def _handle_select(
        self,
        window: WindowInfo,
        frame: np.ndarray,
        analysis: FrameAnalysis,
    ) -> None:
        if self._closing_result_since is not None:
            # 结算关闭后首次回到列表，允许开始目标搜索。
            self._closing_result_since = None
            self.state_machine.in_battle = False

        assert self._target is not None
        candidate = self.target_tracker.find(frame, analysis.difficulty, self._target)
        if candidate is None:
            self.stats.target_visible = "否"
            self._recover_target(window, frame, analysis)
            return

        self.stats.target_visible = f"是(score={candidate.score:.3f}, row={candidate.row.row_index})"
        if candidate.row.selected:
            self._selection_retries = 0
            if analysis.challenge.hit:
                self._click_challenge(window, analysis, reason="已找到并确认原选中关卡")
            return

        self._click_target_and_verify(window, analysis, candidate)

    def _click_target_and_verify(
        self,
        window: WindowInfo,
        analysis: FrameAnalysis,
        candidate: TargetCandidate,
    ) -> None:
        if not self.input.can_act(ActionType.CLICK_DIFFICULTY):
            return
        point = Point(int(self.cfg["difficulty"]["click_x"]), candidate.row.center_y)
        bounds = _row_click_bounds(self.cfg, candidate.row.center_y)
        self.input.click_reference(
            window,
            point,
            action=ActionType.CLICK_DIFFICULTY,
            reason=f"找回原选中关卡 row={candidate.row.row_index} score={candidate.score:.3f}",
            click_bounds=bounds,
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
            state_allows=analysis.state == BotState.SELECT_DIFFICULTY,
            confidence_ok=True,
            allow_jitter=False,
        )
        self._selection_retries += 1
        self._record_operation(f"CLICK_TARGET row={candidate.row.row_index} retry={self._selection_retries}")
        if not self._sleep_interruptible(float(self.cfg["actions"]["post_select_wait"])):
            return

        live = self.locator.current_rect(window.hwnd)
        verify_frame = self.capture.grab_window(live)
        verify = self.state_machine.analyze_frame(verify_frame, debounce=False)
        verify_target = self.target_tracker.find(
            verify_frame,
            verify.difficulty,
            self._target,
        )
        if verify_target is not None and verify_target.row.selected:
            self._selection_retries = 0
            self._record_operation(f"TARGET_SELECTED_VERIFY row={verify_target.row.row_index}")
            if verify.challenge.hit:
                self._click_challenge(window, verify, reason="原选中关卡验证成功")
            return

        max_retries = int(self.cfg["actions"].get("max_select_retries", 3))
        if self._selection_retries >= max_retries:
            raise BotError(
                f"找回目标后选中验证失败，已重试 {max_retries} 次；已暂停避免挑战错误关卡"
            )
        logger.warning("目标关卡选中验证失败，将在下一轮重试 (%d/%d)", self._selection_retries, max_retries)

    # ---------- 挑战与结算 ----------
    def _click_challenge(self, window: WindowInfo, analysis: FrameAnalysis, *, reason: str) -> None:
        if analysis.challenge.center is None or not analysis.challenge.hit:
            return
        self._ensure_attempt_available()
        if not self.input.can_act(ActionType.CLICK_CHALLENGE):
            return
        self.input.click_reference(
            window,
            analysis.challenge.center,
            action=ActionType.CLICK_CHALLENGE,
            reason=reason,
            click_bounds=_match_click_bounds(analysis.challenge),
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
            state_allows=analysis.state == BotState.SELECT_DIFFICULTY,
            confidence_ok=analysis.challenge.hit,
        )
        self._pending_confirm_since = time.monotonic()
        self.stats.last_action = "CLICK_CHALLENGE | 等待确认弹窗"
        self._emit_stats()
        self._sleep_interruptible(float(self.cfg["actions"].get("post_click_wait", 0.2)))

    def _handle_pending_confirm(self, window: WindowInfo, analysis: FrameAnalysis) -> None:
        assert self._pending_confirm_since is not None
        wait = float(self.cfg["actions"].get("confirm_open_wait", 1.0))
        if time.monotonic() - self._pending_confirm_since < wait:
            return
        popup_visible = (
            analysis.state == BotState.CONFIRM_CHALLENGE
            or analysis.start.hit
            or analysis.start.score >= 0.50
        )
        if not popup_visible:
            self._pending_confirm_since = None
            self.state_machine.in_battle = False
            self._record_operation("CONFIRM_MISSING | 未检测到确认弹窗，重新搜索原关卡")
            self._prepare_recovery()
            return
        self._click_start(window, analysis, reason="挑战按钮已点击，确认弹窗稳定")

    def _click_start(self, window: WindowInfo, analysis: FrameAnalysis, *, reason: str) -> None:
        self._ensure_attempt_available()
        if not self.input.can_act(ActionType.CLICK_START):
            return
        point_values = self.cfg["actions"].get("confirm_start_point", [277, 707])
        bounds_values = self.cfg["actions"].get("confirm_start_bounds", [205, 675, 350, 740])
        point = Point(int(point_values[0]), int(point_values[1]))
        bounds = Rect(*[int(value) for value in bounds_values])
        self.input.click_reference(
            window,
            point,
            action=ActionType.CLICK_START,
            reason=reason,
            click_bounds=bounds,
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
            state_allows=analysis.state == BotState.CONFIRM_CHALLENGE,
            confidence_ok=True,
            allow_jitter=False,
        )
        self._pending_confirm_since = None
        self._battle_started_at = time.monotonic()
        self.stats.attempts += 1
        self.stats.result_status = "战斗中"
        self.stats.last_action = "CLICK_START | 开始本次挑战"
        self.state_machine.mark_battle_started()
        self._record_operation(f"ATTEMPT_START #{self.stats.attempts}")
        self._emit_stats()
        max_attempts = int(self.feature_cfg["result"].get("max_attempts", 0))
        if max_attempts > 0 and self.stats.attempts >= max_attempts:
            logger.info("已达到配置的最大挑战次数: %d", max_attempts)

    def _handle_result(
        self,
        window: WindowInfo,
        frame: np.ndarray,
        analysis: FrameAnalysis,
        *,
        outcome=None,
    ) -> None:
        if outcome is None:
            outcome = self.result_detector.detect(frame)
        self.stats.result_status = outcome.status.upper()
        self.stats.success_score = outcome.success_score
        self.stats.failure_score = outcome.failure_score
        self._emit_stats()
        if outcome.status == "success":
            self._mark_success(outcome)
            return
        if outcome.status == "unknown" and self.result_detector.unknown_policy == "pause":
            raise BotError(
                f"无法判断成功/失败，已暂停；success={outcome.success_score:.3f} "
                f"failure={outcome.failure_score:.3f}"
            )
        if not analysis.close.hit or analysis.close.center is None:
            return
        if self._closing_result_since is not None:
            retry_after = float(self.cfg["actions"].get("unknown_timeout", 30.0))
            if time.monotonic() - self._closing_result_since < retry_after:
                return
            self._closing_result_since = None
        if not self.input.can_act(ActionType.CLICK_CLOSE):
            return

        self.input.click_reference(
            window,
            analysis.close.center,
            action=ActionType.CLICK_CLOSE,
            reason=f"{outcome.reason}，关闭结算并找回原关卡",
            click_bounds=_match_click_bounds(analysis.close),
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
            state_allows=analysis.state == BotState.RESULT,
            confidence_ok=analysis.close.hit,
        )
        self.stats.failures += 1
        self._closing_result_since = time.monotonic()
        self.stats.last_action = "CLICK_CLOSE | 失败，准备找回原关卡"
        self._prepare_recovery()
        self._record_operation(
            f"FAILURE_RETRY #{self.stats.failures} | success={outcome.success_score:.3f}"
        )
        self._emit_stats()
        self._sleep_interruptible(float(self.cfg["actions"].get("post_click_wait", 0.2)))

    def _ensure_attempt_available(self) -> None:
        max_attempts = int(self.feature_cfg["result"].get("max_attempts", 0))
        if max_attempts > 0 and self.stats.attempts >= max_attempts:
            raise BotError(f"已达到最大挑战次数 {max_attempts}，已暂停")

    def _mark_success(self, outcome) -> None:
        self.enabled.clear()
        self.state_machine.in_battle = False
        self.stats.program_status = "成功，已暂停"
        self.stats.result_status = "SUCCESS"
        self.stats.success_score = outcome.success_score
        self.stats.failure_score = outcome.failure_score
        self.stats.last_action = "SUCCESS | 达成成功模板，停止重试"
        self._record_operation(
            f"SUCCESS_STOP success={outcome.success_score:.3f} attempts={self.stats.attempts}"
        )
        self._emit_stats()
        logger.info("目标关卡挑战成功，已停止重复挑战")

    # ---------- 列表找回 ----------
    def _prepare_recovery(self) -> None:
        self._recovery_direction = "down"
        self._recovery_scrolls = 0
        self._pending_scroll_fp = None
        self._unchanged_scrolls = 0
        self._selection_retries = 0

    def _recover_target(
        self,
        window: WindowInfo,
        frame: np.ndarray,
        analysis: FrameAnalysis,
    ) -> None:
        recovery = self.feature_cfg["recovery"]
        if not bool(recovery.get("enabled", True)):
            raise BotError("找不到原关卡且 recovery.enabled=false")

        if self._pending_scroll_fp is not None:
            current = self._list_fingerprint(frame)
            similar = self._fingerprints_similar(self._pending_scroll_fp, current)
            self._pending_scroll_fp = None
            if similar:
                self._unchanged_scrolls += 1
            else:
                self._unchanged_scrolls = 0
            reverse_after = int(recovery["unchanged_scrolls_before_reverse"])
            if self._unchanged_scrolls >= reverse_after:
                self._recovery_direction = "up" if self._recovery_direction == "down" else "down"
                self._unchanged_scrolls = 0
                self._record_operation(
                    f"RECOVERY_REVERSE | 列表滑动后无变化，切换方向={self._recovery_direction}"
                )

        if analysis.difficulty.highest_unlocked_index is None:
            self._recovery_direction = "up"

        max_scrolls = int(recovery["max_scrolls"])
        if self._recovery_scrolls >= max_scrolls:
            self._save_debug(self.capture.last_frame, analysis, window, "recovery_exhausted")
            raise BotError(f"滑动 {max_scrolls} 次仍未找回原选中关卡，已暂停")

        scroll_x = int(recovery["scroll_x"])
        if self._recovery_direction == "up":
            start = Point(scroll_x, int(recovery["up_start_y"]))
            end = Point(scroll_x, int(recovery["up_end_y"]))
            action = ActionType.SCROLL_DIFFICULTY_UP
            label = "RECOVERY_UP"
        else:
            start = Point(scroll_x, int(recovery["down_start_y"]))
            end = Point(scroll_x, int(recovery["down_end_y"]))
            action = ActionType.SCROLL_DIFFICULTY_DOWN
            label = "RECOVERY_DOWN"

        if not self.input.can_act(action):
            return
        self._pending_scroll_fp = self._list_fingerprint(frame)
        self.input.swipe_reference(
            window,
            start,
            end,
            action=action,
            reason="找回用户最初选中的关卡",
            duration=float(recovery["duration"]),
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
        )
        self._recovery_scrolls += 1
        self.stats.recovery_scrolls += 1
        self.stats.target_status = f"找回中 {self._recovery_scrolls}/{max_scrolls}"
        self.stats.last_action = f"{label} {self._recovery_scrolls}/{max_scrolls}"
        self._emit_stats()
        self._sleep_interruptible(float(recovery["post_wait"]))

    def _list_fingerprint(self, frame: Optional[np.ndarray]) -> bytes:
        if frame is None:
            return b""
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
        return (gray // 8).tobytes()

    @staticmethod
    def _fingerprints_similar(before: bytes, after: bytes) -> bool:
        if not before or len(before) != len(after):
            return False
        left = np.frombuffer(before, dtype=np.uint8).astype(np.int16)
        right = np.frombuffer(after, dtype=np.uint8).astype(np.int16)
        mean_diff = float(np.mean(np.abs(left - right)))
        equal_ratio = float(np.mean(left == right))
        return mean_diff <= 0.75 and equal_ratio >= 0.50

    # ---------- 状态、超时、调试 ----------
    def _update_stats(self, analysis: FrameAnalysis, window: WindowInfo) -> None:
        sx, sy = scale_factors(
            window.rect,
            reference_width=int(self.cfg["window"]["reference_width"]),
            reference_height=int(self.cfg["window"]["reference_height"]),
        )
        self.stats.window_status = f"已找到 hwnd={window.hwnd}"
        self.stats.window_size = f"{window.rect.width}×{window.rect.height}（scale={sx:.2f},{sy:.2f}）"
        self.stats.vision_state = analysis.state.name
        record = (analysis.state.name, analysis.next_action.name)
        if record != self._last_vision_record:
            self._last_vision_record = record
            self._record_operation(
                f"VISION state={analysis.state.name} challenge={analysis.challenge.score:.3f} "
                f"start={analysis.start.score:.3f} close={analysis.close.score:.3f}"
            )
        self._emit_stats()

    def _check_battle_timeout(self) -> None:
        now = time.monotonic()
        if self._battle_started_at is None:
            self._battle_started_at = now
        elif now - self._battle_started_at > float(self.cfg["actions"]["battle_timeout"]):
            raise BotError("战斗等待超时，已暂停避免盲目重试")

    def _check_unknown_timeout(self, analysis: FrameAnalysis) -> None:
        now = time.monotonic()
        if self._unknown_since is None:
            self._unknown_since = now
        elif now - self._unknown_since > float(self.cfg["actions"]["unknown_timeout"]):
            raise BotError(f"连续无法识别界面超时: {analysis.state.name}")

    def _handle_recoverable(self, message: str, *, enter_error: bool) -> None:
        logger.error("选定关卡重复挑战: %s", message)
        self.stats.last_error = message
        if enter_error:
            self.enabled.clear()
            self.stats.program_status = "错误/已暂停"
        self._emit_stats()

    def _record_input_action(self, message: str) -> None:
        self._record_operation(message)

    def _record_operation(self, message: str) -> None:
        entry = f"{datetime.now():%H:%M:%S}  {message}"
        self._history.append(entry)
        self.stats.operation_history = "\n".join(self._history)
        self.stats.last_action = message
        logger.info("RETRY_OPERATION | %s", entry)
        self._emit_stats()

    def _save_debug(
        self,
        frame: Optional[np.ndarray],
        analysis: FrameAnalysis,
        window: WindowInfo,
        tag: str,
    ) -> None:
        if frame is None:
            return
        try:
            from tower_bot.config import debug_dir

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
                scale=scale_factors(
                    window.rect,
                    reference_width=int(self.cfg["window"]["reference_width"]),
                    reference_height=int(self.cfg["window"]["reference_height"]),
                ),
                window_size=(window.rect.width, window.rect.height),
            )
            cv2.imwrite(str(out_dir / "selected_retry_latest.png"), overlay)
            if tag != "latest":
                stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                cv2.imwrite(str(out_dir / f"{stamp}_selected_retry_{tag}.png"), overlay)
        except Exception as exc:  # noqa: BLE001
            logger.warning("保存选定关卡调试图失败: %s", exc)

    def _sleep_interruptible(self, seconds: float) -> bool:
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            if self.stopping.is_set() or not (
                self.enabled.is_set() or self.step_once.is_set()
            ):
                return False
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
        return True
