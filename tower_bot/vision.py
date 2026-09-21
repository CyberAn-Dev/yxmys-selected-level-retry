from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

import cv2
import numpy as np

from .config import templates_dir
from .logger import get_logger
from .models import Point, Rect, VisionError, VisionMatch, ensure_roi

logger = get_logger(__name__)


TEMPLATE_FILES = {
    "select_anchor": "select_anchor.png",
    "challenge_button": "challenge_button.png",
    "start_button": "start_button.png",
    "close_prompt": "close_prompt.png",
    "other_device_message": "other_device_message.png",
}


def load_bgr(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise VisionError(f"无法读取图像: {path}")
    return image


def to_reference_size(
    frame: np.ndarray,
    width: int = 550,
    height: int = 1020,
) -> np.ndarray:
    if frame.shape[1] == width and frame.shape[0] == height:
        return frame
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


class TemplateMatcher:
    def __init__(self, cfg: Mapping, template_root: Optional[Path] = None) -> None:
        self.cfg = cfg
        self.template_root = template_root or templates_dir()
        self.templates: dict[str, np.ndarray] = {}
        self.reload()

    def reload(self) -> None:
        loaded: dict[str, np.ndarray] = {}
        for name, filename in TEMPLATE_FILES.items():
            path = self.template_root / filename
            if not path.exists():
                logger.warning("模板缺失: %s", path)
                continue
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise VisionError(f"无法读取模板: {path}")
            loaded[name] = image
        self.templates = loaded

    def has(self, name: str) -> bool:
        return name in self.templates

    def match(
        self,
        frame: np.ndarray,
        template_name: str,
        roi_name: str,
        threshold: float,
    ) -> VisionMatch:
        roi = ensure_roi(self.cfg["rois"][roi_name], roi_name)
        template = self.templates.get(template_name)
        if template is None:
            return VisionMatch.miss(template_name, roi)

        x1, y1, x2, y2 = roi
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w - 1))
        x2 = max(0, min(x2, w))
        y1 = max(0, min(y1, h - 1))
        y2 = max(0, min(y2, h))
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return VisionMatch.miss(template_name, roi)

        th, tw = template.shape[:2]
        if region.shape[0] < th or region.shape[1] < tw:
            logger.warning(
                "模板 %s (%dx%d) 大于 ROI %s (%dx%d)",
                template_name,
                tw,
                th,
                roi_name,
                region.shape[1],
                region.shape[0],
            )
            return VisionMatch.miss(template_name, roi)

        gray_region = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(gray_region, gray_template, cv2.TM_CCOEFF_NORMED)
        _, max_score, _, max_loc = cv2.minMaxLoc(result)
        score = float(max_score)
        tx, ty = max_loc
        box = Rect(
            left=x1 + tx,
            top=y1 + ty,
            right=x1 + tx + tw,
            bottom=y1 + ty + th,
        )
        center = Point(box.left + tw // 2, box.top + th // 2)
        hit = score >= threshold
        return VisionMatch(
            hit=hit,
            score=score,
            box=box if hit else None,
            center=center if hit else center,
            template_name=template_name,
            roi=roi,
        )

    def match_all(self, frame: np.ndarray) -> dict[str, VisionMatch]:
        m = self.cfg["matching"]
        return {
            "select_anchor": self.match(
                frame,
                "select_anchor",
                "select_anchor",
                float(m["select_anchor_threshold"]),
            ),
            "challenge_button": self.match(
                frame,
                "challenge_button",
                "challenge_button",
                float(m["challenge_threshold"]),
            ),
            "start_button": self.match(
                frame,
                "start_button",
                "start_button",
                float(m["start_threshold"]),
            ),
            "close_prompt": self.match(
                frame,
                "close_prompt",
                "close_prompt",
                float(m["close_threshold"]),
            ),
            "other_device_message": self.match(
                frame,
                "other_device_message",
                "other_device_message",
                float(m["other_device_threshold"]),
            ),
        }
