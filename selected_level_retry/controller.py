from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

import numpy as np
import pyautogui

from tower_bot.capture import ScreenCapture
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
from tower_bot.window_locator import WindowLocator, enable_dpi_awareness

from .result_detector import ResultDetector
from .target_tracker import TargetCandidate, TargetSnapshot, TargetTracker


logger = get_logger(__name__)


@dataclass
class RetryStats:
    program_status: str = "已停止"
    target_status: str = "未记录"
    attempts: int = 0
    failures: int = 0
    recovery_scrolls: int = 0
    result_status: str = "-"
    last_action: str = "-"
    last_error: str = "-"


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

        self.stats = RetryStats()
        self._thread: Optional[threading.Thread] = None

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
                self.enabled.is_set() and not self.stopping.is_set()
            ),
            on_action=self._record_input_action,
        )

        self._target: Optional[TargetSnapshot] = None
        self._pending_confirm_since: Optional[float] = None
        self._battle_started_at: Optional[float] = None
        self._unknown_since: Optional[float] = None
        self._closing_result_since: Optional[float] = None
        self._selection_retries = 0
        self._recovery_scrolls = 0

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

    def emergency_pause(self, reason: str) -> None:
        self.enabled.clear()
        self.stats.program_status = "已触发紧急停止"
        self.stats.last_error = reason
        self._emit_stats()
        logger.warning("选定关卡重复挑战紧急暂停: %s", reason)

    def _emit_stats(self) -> None:
        """将精简后的运行状态安全地推送给界面。"""
        if self.on_stats is None:
            return
        try:
            self.on_stats(self.stats)
        except Exception:  # noqa: BLE001
            logger.exception("更新界面状态失败")

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
                if not self.enabled.is_set():
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
                    self.stats.last_error = str(exc)
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
                interval = float(self.cfg["capture"]["interval"])
                if self.state_machine.in_battle:
                    interval = float(self.cfg["capture"]["battle_interval"])
                time.sleep(interval)
        finally:
            self.capture.close()
            logger.info("选定关卡重复挑战视觉线程已结束")

    def _tick(self) -> None:
        if self.stopping.is_set() or not self.enabled.is_set():
            return
        window = self.locator.find()
        if window is None:
            raise WindowError("未找到目标窗口")
        self.locator.validate_geometry(window)
        frame = self.capture.grab_window(window.rect)
        analysis = self.state_machine.analyze_frame(frame)

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

        # 失败标题是比通用状态机更直接的结算信号。战斗结束动画期间，
        # 状态机可能暂时给出 UNKNOWN，此时也必须优先进入失败重试流程。
        if (
            self._battle_started_at is not None
            and outcome.failure_score >= self.result_detector.failure_threshold
            and analysis.state != BotState.SELECT_DIFFICULTY
        ):
            self._handle_result(window, frame, analysis, outcome=outcome)
            return

        # UNKNOWN 可能只出现在加载动画的一两帧；一旦恢复到任意已知界面，
        # 必须重新计算未知超时，不能把之前的短暂未知累计到 30 秒。
        if analysis.state != BotState.UNKNOWN:
            self._unknown_since = None

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
        self.stats.target_status = "当前难度已锁定"
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
        # 目标记录后，任何再次出现的选择列表都只允许找回并挑战这个目标；
        # 这既覆盖失败结算回列表，也覆盖战斗中途异常回到初始列表。
        if self._closing_result_since is not None:
            # 结算关闭后首次回到列表，允许开始目标搜索。
            self._closing_result_since = None
            self.state_machine.in_battle = False

        assert self._target is not None
        candidate = self.target_tracker.find(frame, analysis.difficulty, self._target)
        if candidate is None:
            self.stats.target_status = "找回当前难度中"
            self._recover_target(window, frame, analysis)
            return

        self.stats.target_status = "当前难度已找到"
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
        self._emit_stats()
        if outcome.status == "success":
            self._mark_success(outcome)
            return
        if outcome.status == "unknown" and self.result_detector.unknown_policy == "pause":
            raise BotError(
                f"无法判断成功/失败，已暂停；success={outcome.success_score:.3f} "
                f"failure={outcome.failure_score:.3f}"
            )
        close_point = analysis.close.center
        close_bounds = _match_click_bounds(analysis.close)
        close_detected = analysis.close.hit and close_point is not None
        if not close_detected:
            # 失败标题已命中但关闭文字可能被动画/遮罩影响时，使用失败页固定安全点。
            if outcome.failure_score < self.result_detector.failure_threshold:
                return
            close_point = Point(275, 760)
            close_bounds = Rect(205, 730, 350, 790)
        if self._closing_result_since is not None:
            retry_after = float(self.cfg["actions"].get("unknown_timeout", 30.0))
            if time.monotonic() - self._closing_result_since < retry_after:
                return
            self._closing_result_since = None
        if not self.input.can_act(ActionType.CLICK_CLOSE):
            return

        self.input.click_reference(
            window,
            close_point,
            action=ActionType.CLICK_CLOSE,
            reason=f"{outcome.reason}，关闭结算并找回原关卡",
            click_bounds=close_bounds,
            expected_hwnd=window.hwnd,
            frame_fresh=self.capture.is_fresh(),
            state_allows=(
                analysis.state == BotState.RESULT
                or outcome.failure_score >= self.result_detector.failure_threshold
            ),
            confidence_ok=close_detected or outcome.failure_score >= self.result_detector.failure_threshold,
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
        self.stats.last_action = "SUCCESS | 达成成功模板，停止重试"
        self._record_operation(
            f"SUCCESS_STOP success={outcome.success_score:.3f} attempts={self.stats.attempts}"
        )
        self._emit_stats()
        logger.info("目标关卡挑战成功，已停止重复挑战")

    # ---------- 列表找回 ----------
    def _prepare_recovery(self) -> None:
        self._recovery_scrolls = 0
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

        max_scrolls = int(recovery["max_scrolls"])
        if self._recovery_scrolls >= max_scrolls:
            raise BotError(f"向下滑动 {max_scrolls} 次仍未找回当前关卡，已暂停")

        scroll_x = int(recovery["scroll_x"])
        start = Point(scroll_x, int(recovery["down_start_y"]))
        end = Point(scroll_x, int(recovery["down_end_y"]))
        action = ActionType.SCROLL_DIFFICULTY_DOWN
        label = "RECOVERY_DOWN"

        if not self.input.can_act(action):
            return
        self.input.swipe_reference(
            window,
            start,
            end,
            action=action,
            reason="列表回到初始位置，向下滑动找回当前关卡",
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

    # ---------- 状态与超时 ----------
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
        self.stats.last_action = message
        logger.info("RETRY_OPERATION | %s", message)
        self._emit_stats()

    def _sleep_interruptible(self, seconds: float) -> bool:
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            if self.stopping.is_set() or not (
                self.enabled.is_set()
            ):
                return False
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
        return True
