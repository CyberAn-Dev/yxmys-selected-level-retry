from __future__ import annotations

import cv2
import numpy as np

from tower_bot.config import load_config, reference_dir
from tower_bot.difficulty_detector import DifficultyDetector
from tower_bot.vision import load_bgr, to_reference_size

from selected_level_retry.config import validate_feature_config
from selected_level_retry.result_detector import ResultDetector
from selected_level_retry.target_tracker import TargetTracker


def test_tracker_picks_strongest_selected_row_and_finds_it_after_scroll():
    cfg = load_config()
    extra = validate_feature_config({})
    tracker = TargetTracker(cfg, extra)
    detector = DifficultyDetector(cfg)

    selected = to_reference_size(
        load_bgr(reference_dir() / "select_gold_selected.png"), 550, 1020
    )
    selected_analysis = detector.analyze(selected)
    target = tracker.capture(selected, selected_analysis)

    assert target is not None
    assert target.source_row_index == 1

    scrolled = to_reference_size(
        load_bgr(reference_dir() / "select_frontier_top.png"), 550, 1020
    )
    scrolled_analysis = detector.analyze(scrolled)
    found = tracker.find(scrolled, scrolled_analysis, target)

    assert found is not None
    assert found.row.row_index == 0
    assert found.good_matches >= extra["target"]["min_good_matches"]


def test_result_detector_stops_on_success_template(tmp_path):
    template = np.zeros((30, 80, 3), dtype=np.uint8)
    cv2.rectangle(template, (4, 4), (75, 25), (20, 200, 240), thickness=-1)
    path = tmp_path / "success.png"
    assert cv2.imwrite(str(path), template)

    cfg = validate_feature_config({
        "result": {
            "success_template": str(path),
            "success_roi": [100, 100, 300, 300],
            "success_threshold": 0.90,
        }
    })
    detector = ResultDetector(cfg)
    frame = np.zeros((1020, 550, 3), dtype=np.uint8)
    frame[150:180, 150:230] = template

    outcome = detector.detect(frame)
    assert detector.ready
    assert outcome.status == "success"
    assert outcome.success_score >= 0.90


def test_result_detector_requires_success_template():
    detector = ResultDetector(validate_feature_config({}))
    assert not detector.ready
    assert "success_template" in detector.missing_message()


def test_fingerprint_rejects_real_change_and_accepts_small_noise():
    from selected_level_retry.controller import SelectedLevelRetryController

    before = np.full(48 * 64, 12, dtype=np.uint8)
    after = before.copy()
    after[::8] += 1
    assert SelectedLevelRetryController._fingerprints_similar(
        before.tobytes(), after.tobytes()
    )

    changed = np.full(48 * 64, 16, dtype=np.uint8)
    assert not SelectedLevelRetryController._fingerprints_similar(
        before.tobytes(), changed.tobytes()
    )
