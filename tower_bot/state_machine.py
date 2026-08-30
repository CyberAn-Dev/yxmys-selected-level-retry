from __future__ import annotations

from collections import deque
from typing import Deque, Mapping, Optional

import cv2
import numpy as np

from .difficulty_detector import DifficultyDetector
from .models import (
    ActionType,
    BotState,
    DifficultyAnalysis,
    FrameAnalysis,
    Point,
    Rect,
    VisionMatch,
)
from .vision import TemplateMatcher


def _match_click_bounds(match: VisionMatch) -> Optional[Rect]:
    """优先使用模板匹配框作为可点击范围。"""
    if match.box is not None:
        return match.box
    if match.center is None:
        return None
    c = match.center
    return Rect(c.x - 20, c.y - 12, c.x + 20, c.y + 12)


def _row_click_bounds(cfg: Mapping, center_y: int) -> Rect:
    """仅允许点击左侧「难度 XX」徽章中心附近，避免点到相邻行或中间 BUFF logo。"""
    d = cfg["difficulty"]
    # 高度收紧，降低 43/44 这类相邻难度误点。
    half_h = max(6, min(14, int(d["row_half_height"]) - 14))
    click_x = int(d["click_x"])
    half_w = max(8, int(d.get("click_half_width", 35)))
    left = max(40, click_x - half_w)
    right = min(200, click_x + half_w)
    if right <= left:
        left, right = click_x - 20, click_x + 20
    return Rect(left, center_y - half_h, right, center_y + half_h)


class StateMachine:
    """纯视觉状态识别与下一动作决策。不执行点击。"""

    def __init__(self, cfg: Mapping) -> None:
        self.cfg = cfg
        self.matcher = TemplateMatcher(cfg)
        self.difficulty = DifficultyDetector(cfg)
        self._history: Deque[BotState] = deque(maxlen=int(cfg["actions"]["debounce_frames"]))
        self._stable_state = BotState.UNKNOWN
        self.in_battle = False

    def reset(self) -> None:
        self._history.clear()
        self._stable_state = BotState.UNKNOWN
        self.in_battle = False

    def analyze_frame(
        self,
        frame,
        *,
        debounce: bool = True,
        force_single_frame_result: bool = True,
    ) -> FrameAnalysis:
        matches = self.matcher.match_all(frame)
        select_anchor = matches["select_anchor"]
        challenge = matches["challenge_button"]
        start = matches["start_button"]
        if not start.hit and self._looks_like_confirm_dialog(frame):
            # 模板会受弹窗动画/缩放影响；羊皮纸弹窗结构明确时，只允许点击
            # 固定的红色“开始挑战”按钮区域，不能继续点击底层挑战按钮。
            fallback_box = Rect(205, 675, 350, 740)
            start = VisionMatch(
                hit=True,
                score=start.score,
                box=fallback_box,
                center=Point(277, 707),
                template_name=start.template_name,
                roi=start.roi,
            )
        close = matches["close_prompt"]
        other_device = matches["other_device_message"]

        diff = DifficultyAnalysis()
        raw_state = self._detect_raw_state(
            select_anchor, challenge, start, close, other_device
        )

        if raw_state in (BotState.SELECT_DIFFICULTY, BotState.CONFIRM_CHALLENGE):
            diff = self.difficulty.analyze(frame)

        if debounce:
            # 其他设备登录 / 点击关闭：高置信度可单帧触发。
            if (
                force_single_frame_result
                and raw_state == BotState.OTHER_DEVICE_LOGIN
                and other_device.hit
            ):
                stable = BotState.OTHER_DEVICE_LOGIN
                self._history.clear()
                self._history.append(stable)
            elif raw_state == BotState.RESULT and force_single_frame_result and close.hit:
                stable = BotState.RESULT
                self._history.clear()
                self._history.append(BotState.RESULT)
            else:
                self._history.append(raw_state)
                stable = self._debounced_state(raw_state)
        else:
            stable = raw_state

        self._stable_state = stable
        if stable == BotState.CONFIRM_CHALLENGE:
            self.in_battle = False
        if stable == BotState.IN_BATTLE:
            self.in_battle = True
        if stable in (BotState.SELECT_DIFFICULTY, BotState.RESULT):
            if stable == BotState.SELECT_DIFFICULTY:
                self.in_battle = False
        if stable == BotState.OTHER_DEVICE_LOGIN:
            self.in_battle = False

        action, point, bounds, reason = self._decide_action(
            stable, challenge, start, close, other_device, diff
        )

        return FrameAnalysis(
            state=stable,
            select_anchor=select_anchor,
            challenge=challenge,
            start=start,
            close=close,
            other_device=other_device,
            difficulty=diff,
            next_action=action,
            action_point=point,
            action_bounds=bounds,
            reason=reason,
        )

    @staticmethod
    def _looks_like_confirm_dialog(frame) -> bool:
        """识别中央米色羊皮纸确认弹窗，作为开始按钮模板的安全后备。"""
        if frame is None or frame.shape[0] < 760 or frame.shape[1] < 500:
            return False
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        def parchment_ratio(x1: int, y1: int, x2: int, y2: int) -> float:
            region = hsv[y1:y2, x1:x2]
            if region.size == 0:
                return 0.0
            saturation = region[:, :, 1]
            value = region[:, :, 2]
            return float(np.mean((saturation < 110) & (value > 120)))

        # 顶部标题纸带和按钮周围底纸必须同时存在，避免把普通选择页误判为弹窗。
        top_ratio = parchment_ratio(50, 290, 500, 345)
        bottom_ratio = parchment_ratio(50, 620, 500, 760)
        return top_ratio >= 0.55 and bottom_ratio >= 0.45

    def _detect_raw_state(
        self,
        select_anchor: VisionMatch,
        challenge: VisionMatch,
        start: VisionMatch,
        close: VisionMatch,
        other_device: VisionMatch,
    ) -> BotState:
        # 优先级：其他设备登录 > RESULT > CONFIRM > SELECT > IN_BATTLE/UNKNOWN
        if other_device.hit:
            return BotState.OTHER_DEVICE_LOGIN
        if close.hit:
            return BotState.RESULT
        if start.hit:
            return BotState.CONFIRM_CHALLENGE
        if challenge.hit or select_anchor.hit:
            return BotState.SELECT_DIFFICULTY
        if self.in_battle:
            return BotState.IN_BATTLE
        return BotState.UNKNOWN

    def _debounced_state(self, raw: BotState) -> BotState:
        required = int(self.cfg["actions"]["debounce_required"])
        if len(self._history) < required:
            return self._stable_state if self._history else raw
        counts: dict[BotState, int] = {}
        for state in self._history:
            counts[state] = counts.get(state, 0) + 1
        best_state, best_count = max(counts.items(), key=lambda item: item[1])
        if best_count >= required:
            return best_state
        return self._stable_state

    def _decide_action(
        self,
        state: BotState,
        challenge: VisionMatch,
        start: VisionMatch,
        close: VisionMatch,
        other_device: VisionMatch,
        diff: DifficultyAnalysis,
    ) -> tuple[ActionType, Optional[Point], Optional[Rect], str]:
        if state == BotState.OTHER_DEVICE_LOGIN:
            return (
                ActionType.STOP_FOR_OTHER_DEVICE,
                None,
                None,
                "检测到账号在其他设备登录，停止自动爬塔",
            )
        if state == BotState.UNKNOWN:
            return ActionType.NONE, None, None, "未知界面，禁止点击"
        if state == BotState.IN_BATTLE:
            return ActionType.NONE, None, None, "战斗中，等待结算"
        if state == BotState.RESULT:
            if close.center is None:
                return ActionType.NONE, None, None, "结算态但 close 中心缺失"
            return (
                ActionType.CLICK_CLOSE,
                close.center,
                _match_click_bounds(close),
                "检测到点击关闭",
            )
        if state == BotState.CONFIRM_CHALLENGE:
            if start.center is None:
                return ActionType.NONE, None, None, "确认弹窗但 start 中心缺失"
            return (
                ActionType.CLICK_START,
                start.center,
                _match_click_bounds(start),
                "检测到开始挑战",
            )
        if state == BotState.SELECT_DIFFICULTY:
            # 滑过头：全灰 → 向上回滑。
            if diff.needs_scroll_up:
                return (
                    ActionType.SCROLL_DIFFICULTY_UP,
                    None,
                    None,
                    "可见难度均为灰色锁定，向上回滑寻找最高已解锁",
                )
            # 交界在顶部只露一部分 → 先居中。
            if diff.needs_center_up:
                return (
                    ActionType.SCROLL_DIFFICULTY_UP,
                    None,
                    None,
                    f"最高难度行#{diff.highest_unlocked_index}贴在顶部，上滑居中后再选择",
                )
            # 列表偏上：下方看不到灰色锁定 → 必须向下滑动。
            # 整页全解锁时禁止挑战（含战后弹回顶部选中低难度的情况）。
            if diff.needs_scroll_down:
                return (
                    ActionType.SCROLL_DIFFICULTY_DOWN,
                    None,
                    None,
                    "下方未见灰色锁定，向下滑动寻找最高已解锁难度",
                )
            # 仅在「已解锁紧挨灰色」的真实交界处才允许点选/挑战。
            if not diff.at_unlock_frontier:
                return (
                    ActionType.SCROLL_DIFFICULTY_DOWN,
                    None,
                    None,
                    "未确认最高交界，继续下滑寻找",
                )
            highest = diff.highest_unlocked_row
            if highest is None:
                return ActionType.NONE, None, None, "选择界面但无 unlocked 行"
            click_x = int(self.cfg["difficulty"]["click_x"])
            # 最高层已出现金边 → 直接挑战（滑动后的强制重选由 controller 拦截）。
            if highest.selected:
                if challenge.center is None:
                    return ActionType.NONE, None, None, "最高难度已选中但挑战按钮中心缺失"
                return (
                    ActionType.CLICK_CHALLENGE,
                    challenge.center,
                    _match_click_bounds(challenge),
                    f"最高难度行#{highest.row_index}已选中，点击挑战深渊",
                )
            return (
                ActionType.CLICK_DIFFICULTY,
                Point(click_x, highest.center_y),
                _row_click_bounds(self.cfg, highest.center_y),
                f"最高难度行#{highest.row_index}未选中，先点击难度",
            )
        return ActionType.NONE, None, None, f"状态 {state.name} 无动作"

    def mark_battle_started(self) -> None:
        self.in_battle = True
        self._stable_state = BotState.IN_BATTLE
