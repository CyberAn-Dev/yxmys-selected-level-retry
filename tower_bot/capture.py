from __future__ import annotations

import time
from typing import Mapping, Optional

import cv2
import mss
import numpy as np

from .logger import get_logger
from .models import CaptureError, Rect
from .vision import to_reference_size

logger = get_logger(__name__)


class ScreenCapture:
    def __init__(self, cfg: Mapping) -> None:
        self.cfg = cfg
        self._sct: Optional[mss.mss] = None
        self.last_capture_at: float = 0.0
        self.last_frame: Optional[np.ndarray] = None

    def open(self) -> None:
        if self._sct is None:
            self._sct = mss.mss()

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("关闭 mss 时异常: %s", exc)
            self._sct = None

    def grab_window(self, rect: Rect) -> np.ndarray:
        self.open()
        assert self._sct is not None
        if rect.width <= 0 or rect.height <= 0:
            raise CaptureError(f"无效截图区域: {rect}")

        region = {
            "left": int(rect.left),
            "top": int(rect.top),
            "width": int(rect.width),
            "height": int(rect.height),
        }
        try:
            shot = np.asarray(self._sct.grab(region), dtype=np.uint8)
            frame = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"mss 截图失败: {exc}") from exc

        ref_w = int(self.cfg["window"]["reference_width"])
        ref_h = int(self.cfg["window"]["reference_height"])
        resized = to_reference_size(frame, ref_w, ref_h)
        self.last_frame = resized
        self.last_capture_at = time.monotonic()
        return resized

    def is_fresh(self) -> bool:
        max_age = float(self.cfg["capture"]["max_frame_age"])
        if self.last_frame is None or self.last_capture_at <= 0:
            return False
        return (time.monotonic() - self.last_capture_at) <= max_age
