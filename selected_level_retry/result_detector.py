from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

import cv2
import numpy as np

from tower_bot.logger import get_logger

from .config import resolve_feature_path


logger = get_logger(__name__)


@dataclass(frozen=True)
class ResultOutcome:
    status: str
    success_score: float
    failure_score: float
    reason: str


class ResultDetector:
    """通过可配置的成功/失败结算模板判断是否应停止。"""

    def __init__(self, feature_cfg: Mapping) -> None:
        result_cfg = feature_cfg["result"]
        self.success_threshold = float(result_cfg["success_threshold"])
        self.failure_threshold = float(result_cfg["failure_threshold"])
        self.assume_non_success_is_failure = bool(
            result_cfg.get("assume_non_success_is_failure", True)
        )
        self.unknown_policy = str(result_cfg.get("unknown_policy", "pause")).lower()
        self.success_roi = tuple(int(v) for v in result_cfg["success_roi"])
        self.failure_roi = tuple(int(v) for v in result_cfg["failure_roi"])
        self.success_path = self._load_path(result_cfg.get("success_template", ""))
        self.failure_path = self._load_path(result_cfg.get("failure_template", ""))
        self.success_template = self._load_template(self.success_path, "成功")
        self.failure_template = self._load_template(self.failure_path, "失败")

    @property
    def ready(self) -> bool:
        return self.success_template is not None

    def missing_message(self) -> str:
        return (
            "未配置成功结算模板；请在 selected_level_retry/default.yaml 中设置 "
            "result.success_template，避免把成功误判为失败"
        )

    def detect(self, frame: np.ndarray) -> ResultOutcome:
        success_score = self._match(frame, self.success_template, self.success_roi)
        failure_score = self._match(frame, self.failure_template, self.failure_roi)

        if success_score >= self.success_threshold:
            return ResultOutcome(
                status="success",
                success_score=success_score,
                failure_score=failure_score,
                reason="命中成功结算模板",
            )
        if failure_score >= self.failure_threshold:
            return ResultOutcome(
                status="failure",
                success_score=success_score,
                failure_score=failure_score,
                reason="命中失败结算模板",
            )
        if self.assume_non_success_is_failure:
            return ResultOutcome(
                status="failure",
                success_score=success_score,
                failure_score=failure_score,
                reason="结算已出现但未命中成功模板，按失败重试",
            )
        return ResultOutcome(
            status="unknown",
            success_score=success_score,
            failure_score=failure_score,
            reason="结算已出现但成功/失败模板均未命中",
        )

    def _load_path(self, value: object) -> Optional[Path]:
        text = str(value or "").strip()
        return resolve_feature_path(text) if text else None

    @staticmethod
    def _load_template(path: Optional[Path], label: str) -> Optional[np.ndarray]:
        if path is None:
            return None
        if not path.exists():
            logger.warning("%s结算模板不存在: %s", label, path)
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            logger.warning("无法读取%s结算模板: %s", label, path)
            return None
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _match(
        frame: np.ndarray,
        template: Optional[np.ndarray],
        roi: tuple[int, int, int, int],
    ) -> float:
        if template is None:
            return 0.0
        x1, y1, x2, y2 = roi
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w - 1))
        x2 = max(x1 + 1, min(x2, w))
        y1 = max(0, min(y1, h - 1))
        y2 = max(y1 + 1, min(y2, h))
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return 0.0
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        th, tw = template.shape[:2]
        if gray.shape[0] < th or gray.shape[1] < tw:
            return 0.0
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])

