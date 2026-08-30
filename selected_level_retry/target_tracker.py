from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

import cv2
import numpy as np

from tower_bot.models import DifficultyAnalysis, RowAnalysis


@dataclass(frozen=True)
class TargetSnapshot:
    """用户启动时选中的关卡视觉指纹。"""

    source_row_index: int
    source_center_y: int
    patch_gray: np.ndarray
    keypoint_count: int
    descriptors: Optional[np.ndarray]


@dataclass(frozen=True)
class TargetCandidate:
    row: RowAnalysis
    score: float
    good_matches: int
    feature_ratio: float


class TargetTracker:
    """记录并在滚动列表中寻找用户最初选中的关卡。"""

    def __init__(self, cfg: Mapping, feature_cfg: Mapping) -> None:
        self.cfg = cfg
        self.feature_cfg = feature_cfg
        target_cfg = feature_cfg["target"]
        self.x1 = int(target_cfg["signature_x1"])
        self.x2 = int(target_cfg["signature_x2"])
        self.half_height = int(target_cfg["signature_half_height"])
        self.min_good_matches = int(target_cfg["min_good_matches"])
        self.min_feature_ratio = float(target_cfg["min_feature_ratio"])
        self.template_threshold = float(target_cfg["template_threshold"])

        try:
            self._detector = cv2.SIFT_create(nfeatures=500)
            self._norm = cv2.NORM_L2
            self._ratio = 0.75
            self._uses_float_descriptors = True
        except AttributeError:
            # 老版本 OpenCV 没有 SIFT 时仍可用 ORB；当前 requirements 通常会提供 SIFT。
            self._detector = cv2.ORB_create(nfeatures=600)
            self._norm = cv2.NORM_HAMMING
            self._ratio = 0.80

    def capture(
        self,
        frame: np.ndarray,
        difficulty: DifficultyAnalysis,
    ) -> Optional[TargetSnapshot]:
        candidates = [row for row in difficulty.rows if row.unlocked and row.selected]
        if not candidates:
            return None

        # 边缘检测偶尔会把相邻行也标成 selected，优先使用金边最强的那一行。
        row = max(candidates, key=self.selection_strength)
        patch = self._extract_patch(frame, row.center_y)
        keypoints, descriptors = self._describe(patch)
        return TargetSnapshot(
            source_row_index=row.row_index,
            source_center_y=row.center_y,
            patch_gray=patch,
            keypoint_count=keypoints,
            descriptors=descriptors,
        )

    @staticmethod
    def selection_strength(row: RowAnalysis) -> float:
        return float(row.yellow_border_ratio) + float(row.edge_yellow_coverage)

    def find(
        self,
        frame: np.ndarray,
        difficulty: DifficultyAnalysis,
        target: TargetSnapshot,
    ) -> Optional[TargetCandidate]:
        best: Optional[TargetCandidate] = None
        for row in difficulty.rows:
            if not row.unlocked:
                continue
            patch = self._extract_patch(frame, row.center_y)
            keypoint_count, descriptors = self._describe(patch)
            good, ratio = self._feature_match(target, descriptors, keypoint_count)
            corr = self._template_similarity(target.patch_gray, patch)

            accepted = (
                good >= self.min_good_matches and ratio >= self.min_feature_ratio
            ) or corr >= self.template_threshold
            if not accepted:
                continue

            # feature ratio 对真实滚动截图更稳定；相关性作为无特征图像的回退。
            score = ratio if good > 0 else corr
            candidate = TargetCandidate(
                row=row,
                score=float(score),
                good_matches=good,
                feature_ratio=ratio,
            )
            if best is None or candidate.score > best.score:
                best = candidate
        return best

    def _extract_patch(self, frame: np.ndarray, center_y: int) -> np.ndarray:
        y1 = max(0, int(center_y) - self.half_height)
        y2 = min(frame.shape[0], int(center_y) + self.half_height)
        x1 = max(0, min(self.x1, frame.shape[1] - 1))
        x2 = max(x1 + 1, min(self.x2, frame.shape[1]))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return np.zeros((max(2, self.half_height * 2), max(2, self.x2 - self.x1)), dtype=np.uint8)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return gray

    def _describe(self, patch: np.ndarray) -> tuple[int, Optional[np.ndarray]]:
        # 均衡亮度，降低滚动后背景亮度和截图压缩对特征的影响。
        normalized = cv2.equalizeHist(patch)
        keypoints, descriptors = self._detector.detectAndCompute(normalized, None)
        return len(keypoints), descriptors

    def _feature_match(
        self,
        target: TargetSnapshot,
        descriptors: Optional[np.ndarray],
        keypoint_count: int,
    ) -> tuple[int, float]:
        if target.descriptors is None or descriptors is None:
            return 0, 0.0
        if len(target.descriptors) < 2 or len(descriptors) < 2:
            return 0, 0.0

        matcher = cv2.BFMatcher(self._norm)
        raw = matcher.knnMatch(target.descriptors, descriptors, k=2)
        good = [first for first, second in raw if first.distance < self._ratio * second.distance]
        denominator = max(1, min(target.keypoint_count, keypoint_count))
        return len(good), len(good) / denominator

    @staticmethod
    def _template_similarity(left: np.ndarray, right: np.ndarray) -> float:
        if left.shape != right.shape:
            right = cv2.resize(right, (left.shape[1], left.shape[0]), interpolation=cv2.INTER_AREA)
        if left.size == 0 or right.size == 0:
            return 0.0
        result = cv2.matchTemplate(left, right, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])
