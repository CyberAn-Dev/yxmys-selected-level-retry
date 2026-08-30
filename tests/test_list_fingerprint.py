from __future__ import annotations

import numpy as np

from tower_bot.controller import TowerController


def test_list_fingerprint_tolerates_animation_noise():
    before = np.full(48 * 64, 12, dtype=np.uint8)
    after = before.copy()
    after[::8] += 1

    assert TowerController._list_fingerprints_similar(
        before.tobytes(),
        after.tobytes(),
        mean_diff_threshold=0.75,
        equal_ratio_threshold=0.50,
    )


def test_list_fingerprint_rejects_real_scroll():
    before = np.full(48 * 64, 12, dtype=np.uint8)
    after = np.full(48 * 64, 16, dtype=np.uint8)

    assert not TowerController._list_fingerprints_similar(
        before.tobytes(),
        after.tobytes(),
        mean_diff_threshold=0.75,
        equal_ratio_threshold=0.50,
    )
