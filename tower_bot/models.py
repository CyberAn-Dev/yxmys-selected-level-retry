from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple


class BotState(Enum):
    DISABLED = auto()
    LOCATING_WINDOW = auto()
    UNKNOWN = auto()
    SELECT_DIFFICULTY = auto()
    CONFIRM_CHALLENGE = auto()
    IN_BATTLE = auto()
    RESULT = auto()
    OTHER_DEVICE_LOGIN = auto()
    ERROR = auto()


class ActionType(Enum):
    NONE = auto()
    CLICK_DIFFICULTY = auto()
    CLICK_CHALLENGE = auto()
    CLICK_START = auto()
    CLICK_CLOSE = auto()
    STOP_FOR_OTHER_DEVICE = auto()
    SCROLL_DIFFICULTY_DOWN = auto()


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def as_tuple(self) -> Tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    rect: Rect


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class VisionMatch:
    hit: bool
    score: float
    box: Optional[Rect]
    center: Optional[Point]
    template_name: str
    roi: Tuple[int, int, int, int]

    @classmethod
    def miss(cls, template_name: str, roi: Tuple[int, int, int, int], score: float = 0.0) -> "VisionMatch":
        return cls(
            hit=False,
            score=score,
            box=None,
            center=None,
            template_name=template_name,
            roi=roi,
        )


@dataclass
class RowAnalysis:
    row_index: int
    center_y: int
    mean_saturation: float
    high_saturation_ratio: float
    gray_ratio: float
    yellow_border_ratio: float
    edge_yellow_coverage: float
    unlocked: bool
    selected: bool
    confidence: float


@dataclass
class DifficultyAnalysis:
    rows: list[RowAnalysis] = field(default_factory=list)
    selected_index: Optional[int] = None


@dataclass
class FrameAnalysis:
    state: BotState
    select_anchor: VisionMatch
    challenge: VisionMatch
    start: VisionMatch
    close: VisionMatch
    other_device: VisionMatch
    difficulty: DifficultyAnalysis
    next_action: ActionType
    action_point: Optional[Point] = None
    action_bounds: Optional[Rect] = None
    reason: str = ""
    scale_x: float = 1.0
    scale_y: float = 1.0
    window_size: Tuple[int, int] = (550, 1020)


class BotError(Exception):
    """业务可恢复错误。"""


class ConfigError(BotError):
    """配置非法或缺失。"""


class WindowError(BotError):
    """窗口定位/激活相关错误。"""


class CaptureError(BotError):
    """截图失败。"""


class VisionError(BotError):
    """视觉识别相关错误。"""


class AspectRatioError(WindowError):
    """窗口宽高比异常。"""


def ensure_roi(value: Sequence[Any], name: str) -> Tuple[int, int, int, int]:
    if len(value) != 4:
        raise ConfigError(f"ROI '{name}' 必须是 [x1, y1, x2, y2]")
    x1, y1, x2, y2 = (int(v) for v in value)
    if x2 <= x1 or y2 <= y1:
        raise ConfigError(f"ROI '{name}' 坐标无效: {(x1, y1, x2, y2)}")
    return x1, y1, x2, y2


def project_root() -> Path:
    """可写根目录：源码仓库根，或打包后 exe 所在目录。"""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """只读资源根目录（config/assets）。打包后兼容 PyInstaller _internal 布局。"""
    import sys

    root = project_root()
    if getattr(sys, "frozen", False):
        candidates = [
            root,
            root / "_internal",
            Path(getattr(sys, "_MEIPASS", root)),
        ]
        for candidate in candidates:
            if (candidate / "config" / "default.yaml").exists() or (
                candidate / "assets" / "templates"
            ).exists():
                return candidate
    return root
