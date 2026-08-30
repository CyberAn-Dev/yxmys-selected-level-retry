from __future__ import annotations

import pytest

from tower_bot.config import load_config, reference_dir
from tower_bot.models import ActionType, BotState
from tower_bot.state_machine import StateMachine
from tower_bot.vision import TemplateMatcher, load_bgr, to_reference_size


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


def test_template_match_challenge(cfg, select_frame):
    matcher = TemplateMatcher(cfg)
    m = matcher.match(
        select_frame,
        "challenge_button",
        "challenge_button",
        float(cfg["matching"]["challenge_threshold"]),
    )
    assert m.hit


def test_template_match_start(cfg, confirm_frame):
    matcher = TemplateMatcher(cfg)
    m = matcher.match(
        confirm_frame,
        "start_button",
        "start_button",
        float(cfg["matching"]["start_threshold"]),
    )
    assert m.hit


def test_template_match_close(cfg, result_frame):
    matcher = TemplateMatcher(cfg)
    m = matcher.match(
        result_frame,
        "close_prompt",
        "close_prompt",
        float(cfg["matching"]["close_threshold"]),
    )
    assert m.hit
