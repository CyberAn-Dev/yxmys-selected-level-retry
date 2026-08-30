from __future__ import annotations

import pytest

from tower_bot.config import load_config, reference_dir
from tower_bot.difficulty_detector import DifficultyDetector
from tower_bot.vision import load_bgr, to_reference_size


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def select_frame():
    return to_reference_size(load_bgr(reference_dir() / "select_screen.png"), 550, 1020)


def test_unlocked_row_classifier(cfg, select_frame):
    analysis = DifficultyDetector(cfg).analyze(select_frame)
    assert analysis.rows[0].unlocked
    assert analysis.rows[1].unlocked
    assert analysis.rows[2].unlocked
    assert not analysis.rows[3].unlocked
    assert not analysis.rows[4].unlocked


def test_highest_unlocked_row(cfg, select_frame):
    analysis = DifficultyDetector(cfg).analyze(select_frame)
    assert analysis.highest_unlocked_index == 2
    assert analysis.at_unlock_frontier
    assert not analysis.needs_scroll_down


def test_scrolled_top_all_unlocked_needs_scroll(cfg):
    path = reference_dir() / "select_scrolled_top.png"
    frame = to_reference_size(load_bgr(path), 550, 1020)
    analysis = DifficultyDetector(cfg).analyze(frame)
    assert all(r.unlocked for r in analysis.rows)
    assert analysis.needs_scroll_down
    assert not analysis.needs_scroll_up
    assert not analysis.at_unlock_frontier


def test_scrolled_past_all_locked_needs_scroll_up(cfg):
    path = reference_dir() / "select_scrolled_past.png"
    frame = to_reference_size(load_bgr(path), 550, 1020)
    analysis = DifficultyDetector(cfg).analyze(frame)
    assert analysis.highest_unlocked_index is None
    assert all(not r.unlocked for r in analysis.rows)
    assert analysis.needs_scroll_up
    assert not analysis.needs_scroll_down


def test_locked_gray_rows_are_not_selected(cfg, select_frame):
    analysis = DifficultyDetector(cfg).analyze(select_frame)
    for row in analysis.rows:
        if not row.unlocked:
            assert row.selected is False


def test_selected_yellow_border(cfg, select_frame):
    analysis = DifficultyDetector(cfg).analyze(select_frame)
    assert analysis.rows[2].selected
    assert analysis.selected_index == 2
