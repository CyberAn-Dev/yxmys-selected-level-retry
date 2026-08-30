from __future__ import annotations

import numpy as np
import pytest

from tower_bot.config import load_config, reference_dir
from tower_bot.models import ActionType, BotState
from tower_bot.state_machine import StateMachine
from tower_bot.vision import load_bgr, to_reference_size


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def select_frame():
    return to_reference_size(load_bgr(reference_dir() / "select_screen.png"), 550, 1020)


@pytest.fixture(scope="module")
def confirm_frame():
    return to_reference_size(load_bgr(reference_dir() / "confirm_screen.png"), 550, 1020)


@pytest.fixture(scope="module")
def result_frame():
    return to_reference_size(load_bgr(reference_dir() / "result_screen.png"), 550, 1020)


def test_select_state_priority(cfg, select_frame):
    analysis = StateMachine(cfg).analyze_frame(select_frame, debounce=False)
    assert analysis.state == BotState.SELECT_DIFFICULTY
    assert analysis.challenge.hit
    assert analysis.difficulty.highest_unlocked_index == 2
    assert analysis.difficulty.rows[2].selected
    assert analysis.difficulty.at_unlock_frontier
    assert not analysis.difficulty.needs_scroll_down
    assert analysis.next_action == ActionType.CLICK_CHALLENGE


def test_scrolled_top_needs_scroll_down(cfg):
    path = reference_dir() / "select_scrolled_top.png"
    assert path.exists()
    frame = to_reference_size(load_bgr(path), 550, 1020)
    analysis = StateMachine(cfg).analyze_frame(frame, debounce=False)
    assert analysis.state == BotState.SELECT_DIFFICULTY
    assert analysis.difficulty.needs_scroll_down
    assert not analysis.difficulty.needs_scroll_up
    assert not analysis.difficulty.at_unlock_frontier
    assert analysis.next_action == ActionType.SCROLL_DIFFICULTY_DOWN
    assert analysis.action_point is None


def test_scrolled_past_needs_scroll_up(cfg):
    path = reference_dir() / "select_scrolled_past.png"
    assert path.exists()
    frame = to_reference_size(load_bgr(path), 550, 1020)
    analysis = StateMachine(cfg).analyze_frame(frame, debounce=False)
    assert analysis.state == BotState.SELECT_DIFFICULTY
    assert analysis.difficulty.needs_scroll_up
    assert not analysis.difficulty.needs_scroll_down
    assert analysis.next_action == ActionType.SCROLL_DIFFICULTY_UP


def test_frontier_at_top_needs_center_before_click(cfg):
    path = reference_dir() / "select_frontier_top.png"
    assert path.exists()
    frame = to_reference_size(load_bgr(path), 550, 1020)
    analysis = StateMachine(cfg).analyze_frame(frame, debounce=False)
    assert analysis.state == BotState.SELECT_DIFFICULTY
    assert analysis.difficulty.at_unlock_frontier
    assert analysis.difficulty.needs_center_up
    assert analysis.difficulty.highest_unlocked_index == 0
    assert analysis.next_action == ActionType.SCROLL_DIFFICULTY_UP
    assert "居中" in analysis.reason


def test_confirm_state_priority(cfg, confirm_frame):
    analysis = StateMachine(cfg).analyze_frame(confirm_frame, debounce=False)
    assert analysis.state == BotState.CONFIRM_CHALLENGE
    assert analysis.start.hit
    assert analysis.next_action == ActionType.CLICK_START


def test_confirm_parchment_fallback_clicks_fixed_start_area(cfg, confirm_frame):
    fallback_cfg = dict(cfg)
    fallback_cfg["matching"] = dict(cfg["matching"])
    fallback_cfg["matching"]["start_threshold"] = 1.0
    analysis = StateMachine(fallback_cfg).analyze_frame(confirm_frame, debounce=False)

    assert analysis.state == BotState.CONFIRM_CHALLENGE
    assert analysis.start.hit
    assert analysis.next_action == ActionType.CLICK_START
    assert analysis.action_point is not None
    assert 205 <= analysis.action_point.x <= 350
    assert 675 <= analysis.action_point.y <= 740


def test_result_state_priority(cfg, result_frame):
    analysis = StateMachine(cfg).analyze_frame(result_frame, debounce=False)
    assert analysis.state == BotState.RESULT
    assert analysis.close.hit
    assert analysis.next_action == ActionType.CLICK_CLOSE


def test_other_device_login_stops_automation(cfg):
    path = reference_dir() / "other_device_screen.png"
    assert path.exists()
    frame = to_reference_size(load_bgr(path), 550, 1020)
    analysis = StateMachine(cfg).analyze_frame(frame, debounce=False)
    assert analysis.state == BotState.OTHER_DEVICE_LOGIN
    assert analysis.other_device.hit
    assert analysis.next_action == ActionType.STOP_FOR_OTHER_DEVICE
    assert analysis.action_point is None


def test_unknown_state_has_no_action(cfg):
    blank = np.zeros((1020, 550, 3), dtype=np.uint8)
    analysis = StateMachine(cfg).analyze_frame(blank, debounce=False)
    assert analysis.state == BotState.UNKNOWN
    assert analysis.next_action == ActionType.NONE
