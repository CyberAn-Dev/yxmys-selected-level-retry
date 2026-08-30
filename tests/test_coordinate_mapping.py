from __future__ import annotations

import random
import time
from types import SimpleNamespace

import numpy as np
import pytest

from tower_bot.config import load_config
from tower_bot.controller import TowerController
from tower_bot.coords import reference_to_screen, screen_to_reference, validate_aspect_ratio
from tower_bot.input_controller import (
    DryRunBackend,
    InputController,
    jitter_reference_point,
    point_in_bounds,
    shrink_click_bounds,
)
from tower_bot.models import ActionType, AspectRatioError, BotState, Point, Rect, WindowInfo
from tower_bot.window_locator import WindowLocator


def test_reference_to_screen_mapping():
    rect = Rect(100, 200, 650, 1220)
    assert reference_to_screen(275, 555, rect) == Point(375, 755)

    big = Rect(0, 0, 1100, 2040)
    assert reference_to_screen(275, 555, big) == Point(550, 1110)

    weird = Rect(10, 20, 835, 1550)
    p = reference_to_screen(100, 200, weird)
    assert p == Point(10 + round(100 * 1.5), 20 + round(200 * 1.5))
    back = screen_to_reference(p.x, p.y, weird)
    assert abs(back.x - 100) <= 1
    assert abs(back.y - 200) <= 1


def test_aspect_ratio_validation():
    cfg = load_config()
    validate_aspect_ratio(Rect(0, 0, 550, 1020), cfg)
    with pytest.raises(AspectRatioError):
        validate_aspect_ratio(Rect(0, 0, 900, 1020), cfg)
    with pytest.raises(AspectRatioError):
        validate_aspect_ratio(Rect(0, 0, 100, 100), cfg)

    from tower_bot.coords import assess_window_geometry

    ok, size, advice = assess_window_geometry(Rect(0, 0, 550, 1020), cfg)
    assert ok
    assert "550" in size
    assert "合适" in advice or "可用" in advice

    ok2, _, advice2 = assess_window_geometry(Rect(0, 0, 900, 1020), cfg)
    assert not ok2
    assert "建议" in advice2 or "比例" in advice2


def test_dry_run_never_clicks():
    cfg = load_config()
    cfg["actions"]["click_jitter"]["enabled"] = False
    backend = DryRunBackend()
    locator = WindowLocator(cfg)
    controller = InputController(cfg, locator, backend=backend, dry_run=True)
    window = WindowInfo(hwnd=1, title="英雄没有闪", rect=Rect(0, 0, 550, 1020))
    locator.current_rect = lambda hwnd: window.rect  # type: ignore[method-assign]
    locator.validate_geometry = lambda info: None  # type: ignore[method-assign]

    screen = controller.click_reference(
        window,
        Point(275, 900),
        action=ActionType.CLICK_CHALLENGE,
        reason="unit-test",
        expected_hwnd=1,
        frame_fresh=True,
        state_allows=True,
        confidence_ok=True,
    )
    assert screen == Point(275, 900)
    assert backend.clicks == [(275, 900)]
    assert isinstance(controller.backend, DryRunBackend)


def test_action_cooldown():
    cfg = load_config()
    cfg["actions"]["click_jitter"]["enabled"] = False
    locator = WindowLocator(cfg)
    backend = DryRunBackend()
    controller = InputController(cfg, locator, backend=backend, dry_run=True)
    window = WindowInfo(hwnd=1, title="英雄没有闪", rect=Rect(0, 0, 550, 1020))
    locator.current_rect = lambda hwnd: window.rect  # type: ignore[method-assign]
    locator.validate_geometry = lambda info: None  # type: ignore[method-assign]

    controller.click_reference(
        window,
        Point(100, 100),
        action=ActionType.CLICK_CLOSE,
        reason="first",
    )
    assert controller.can_act(ActionType.CLICK_CLOSE) is False
    controller.last_action_at = 0.0
    assert controller.can_act(ActionType.CLICK_CLOSE) is True


def test_pending_confirm_clicks_fixed_start_button_once():
    cfg = load_config()
    controller = TowerController(cfg, dry_run=True)
    controller.enabled.set()
    window = WindowInfo(hwnd=1, title="英雄没有闪", rect=Rect(0, 0, 550, 1020))
    controller.locator.current_rect = lambda hwnd: window.rect  # type: ignore[method-assign]
    controller.locator.validate_geometry = lambda info: None  # type: ignore[method-assign]
    controller.capture.last_frame = np.zeros((1020, 550, 3), dtype=np.uint8)
    controller.capture.last_capture_at = time.monotonic()
    controller.input.last_action_at = 0.0
    controller._pending_confirm_since = time.monotonic() - 2.0

    confirm_analysis = SimpleNamespace(
        state=BotState.CONFIRM_CHALLENGE,
        start=SimpleNamespace(hit=True, score=0.80),
    )
    controller._execute_pending_confirm(window, confirm_analysis)

    assert isinstance(controller.input.backend, DryRunBackend)
    assert controller.input.backend.clicks == [(277, 707)]
    assert controller._pending_confirm_since is None
    assert controller.stats.challenges_started == 1
    assert "CLICK_START ref=(277,707)" in controller.stats.operation_history


def test_pending_confirm_never_clicks_after_game_returns_to_level_list():
    cfg = load_config()
    controller = TowerController(cfg, dry_run=True)
    controller.enabled.set()
    controller._at_max_floor = True
    window = WindowInfo(hwnd=1, title="英雄没有闪", rect=Rect(0, 0, 550, 1020))
    controller.locator.current_rect = lambda hwnd: window.rect  # type: ignore[method-assign]
    controller.locator.validate_geometry = lambda info: None  # type: ignore[method-assign]
    controller.capture.last_frame = np.zeros((1020, 550, 3), dtype=np.uint8)
    controller.capture.last_capture_at = time.monotonic()
    controller.input.last_action_at = 0.0
    controller._pending_confirm_since = time.monotonic() - 2.0
    select_analysis = SimpleNamespace(
        state=BotState.SELECT_DIFFICULTY,
        start=SimpleNamespace(hit=False, score=0.28),
    )

    controller._execute_pending_confirm(window, select_analysis)

    assert isinstance(controller.input.backend, DryRunBackend)
    assert controller.input.backend.clicks == []
    assert controller._pending_confirm_since is None
    assert controller._at_max_floor is False
    assert "CONFIRM_MISSING" in controller.stats.operation_history


def test_click_jitter_stays_inside_bounds():
    cfg = load_config()
    bounds = Rect(180, 875, 370, 925)
    center = Point(275, 900)
    rng = random.Random(42)
    for _ in range(200):
        p = jitter_reference_point(center, bounds, cfg, rng=rng)
        assert point_in_bounds(p, bounds)
        safe = shrink_click_bounds(
            bounds,
            inset_px=int(cfg["actions"]["click_jitter"]["inset_px"]),
            inset_ratio=float(cfg["actions"]["click_jitter"]["inset_ratio"]),
        )
        assert safe.left <= p.x < safe.right
        assert safe.top <= p.y < safe.bottom


def test_click_jitter_disabled_returns_center():
    cfg = load_config()
    cfg["actions"]["click_jitter"]["enabled"] = False
    bounds = Rect(180, 875, 370, 925)
    center = Point(275, 900)
    assert jitter_reference_point(center, bounds, cfg) == center
