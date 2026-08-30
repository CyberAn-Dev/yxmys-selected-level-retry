from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Tuple

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


def draw_debug_overlay(
    frame: np.ndarray,
    matches: Mapping[str, VisionMatch],
    rows: Optional[list] = None,
    *,
    state_name: str = "",
    action_name: str = "",
    highest_index: Optional[int] = None,
    scale: Tuple[float, float] = (1.0, 1.0),
    window_size: Tuple[int, int] = (550, 1020),
) -> np.ndarray:
    canvas = frame.copy()
    colors = {
        "select_anchor": (255, 180, 80),
        "challenge_button": (80, 180, 255),
        "start_button": (80, 80, 255),
        "close_prompt": (180, 255, 80),
        "other_device_message": (0, 0, 255),
    }

    for name, match in matches.items():
        x1, y1, x2, y2 = match.roi
        color = colors.get(name, (200, 200, 200))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 1)
        label = f"{name}:{match.score:.3f}"
        cv2.putText(
            canvas,
            label,
            (x1 + 4, max(16, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )
        if match.box is not None:
            cv2.rectangle(
                canvas,
                (match.box.left, match.box.top),
                (match.box.right, match.box.bottom),
                color,
                2,
            )
            if match.center is not None:
                cv2.circle(canvas, (match.center.x, match.center.y), 5, color, -1)

    if rows:
        for row in rows:
            y1 = row.center_y - 28
            y2 = row.center_y + 28
            color = (0, 220, 0) if row.unlocked else (120, 120, 120)
            if row.selected:
                color = (0, 215, 255)
            if highest_index is not None and row.row_index == highest_index:
                color = (0, 255, 255)
            cv2.rectangle(canvas, (90, y1), (460, y2), color, 2)
            text = (
                f"#{row.row_index} S={row.mean_saturation:.0f} "
                f"U={int(row.unlocked)} Sel={int(row.selected)}"
            )
            cv2.putText(
                canvas,
                text,
                (95, y1 + 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                color,
                1,
                cv2.LINE_AA,
            )

    header = [
        f"state={state_name}",
        f"action={action_name}",
        f"win={window_size[0]}x{window_size[1]}",
        f"scale={scale[0]:.2f},{scale[1]:.2f}",
    ]
    for i, line in enumerate(header):
        cv2.putText(
            canvas,
            line,
            (12, 24 + i * 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )
    return canvas
