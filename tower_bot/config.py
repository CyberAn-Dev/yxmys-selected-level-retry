from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

import yaml

from .models import ConfigError, ensure_roi, project_root, resource_root


DEFAULT_CONFIG: dict[str, Any] = {
    "window": {
        "title_contains": "英雄没有闪",
        "reference_width": 550,
        "reference_height": 1020,
        "aspect_ratio_tolerance": 0.05,
        "minimum_width": 300,
        "minimum_height": 500,
    },
    "capture": {
        "interval": 0.12,
        "battle_interval": 0.08,
        "locate_retry_interval": 1.0,
        "max_frame_age": 1.0,
    },
    "matching": {
        "select_anchor_threshold": 0.78,
        "challenge_threshold": 0.75,
        "start_threshold": 0.75,
        "close_threshold": 0.75,
        "other_device_threshold": 0.85,
        "near_threshold_delta": 0.05,
    },
    "rois": {
        "select_anchor": [100, 55, 450, 150],
        "difficulty_list": [70, 320, 480, 805],
        "challenge_button": [150, 820, 400, 950],
        "start_button": [160, 620, 390, 770],
        "close_prompt": [140, 680, 410, 850],
        "other_device_message": [40, 400, 510, 620],
    },
    "template_crops": {
        "select_anchor": [140, 60, 410, 140],
        "challenge_button": [180, 875, 370, 925],
        "start_button": [205, 675, 350, 740],
        "close_prompt": [220, 748, 335, 785],
        "other_device_message": [70, 470, 480, 560],
    },
    "difficulty": {
        "last_level_only": True,
        "target_level": 50,
        "row_centers": [360, 450, 555, 655, 760],
        "click_x": 110,
        "click_half_width": 35,
        "row_half_height": 28,
        "row_inner_x1": 90,
        "row_inner_x2": 460,
        "border_thickness": 8,
        "saturation_threshold": 75.0,
        "high_saturation_threshold": 90.0,
        "high_saturation_ratio": 0.25,
        "gray_ratio_threshold": 0.40,
        "yellow_hsv_lower": [5, 40, 100],
        "yellow_hsv_upper": [50, 255, 255],
        "yellow_border_ratio_threshold": 0.06,
        "yellow_edge_coverage_threshold": 0.25,
        "scroll": {
            "enabled": True,
            "scroll_x": 275,
            "down_start_y": 680,
            "down_end_y": 540,
            "up_start_y": 420,
            "up_end_y": 560,
            "center_min_index": 1,
            "center_up_start_y": 480,
            "center_up_end_y": 580,
            # 下滑后列表画面不变才视为满级底部；勿用小次数全解锁误判。
            "unchanged_scrolls_for_max": 1,
            "fingerprint_mean_diff_threshold": 0.75,
            "fingerprint_equal_ratio_threshold": 0.50,
            "max_all_unlocked_scrolls": 99,
            "duration": 0.35,
            "post_wait": 0.55,
            "max_attempts": 25,
        },
    },
    "actions": {
        "cooldown": 0.45,
        "post_select_wait": 0.25,
        "post_click_wait": 0.20,
        "confirm_open_wait": 1.2,
        "confirm_start_point": [277, 707],
        "confirm_start_bounds": [205, 675, 350, 740],
        "start_retry_wait": 1.5,
        "max_select_retries": 2,
        "battle_timeout": 900.0,
        "unknown_timeout": 30.0,
        "debounce_frames": 2,
        "debounce_required": 1,
        "click_jitter": {
            "enabled": True,
            "inset_px": 4,
            "inset_ratio": 0.18,
        },
    },
    "hotkeys": {
        "toggle": "<f8>",
        "stop": "<f9>",
        "debug": "<f10>",
        "emergency_pause": "<esc>",
    },
    "debug": {
        "enabled": False,
        "save_unknown_frames": True,
        "save_action_frames": True,
        "max_files": 80,
    },
    "pyautogui": {
        "failsafe": True,
        "pause": 0.05,
    },
}


def deep_merge(base: MutableMapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = deepcopy(dict(base))
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, Mapping)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _require_number(section: Mapping[str, Any], key: str, minimum: Optional[float] = None) -> float:
    if key not in section:
        raise ConfigError(f"缺少配置项: {key}")
    try:
        value = float(section[key])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置项 {key} 必须是数字") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"配置项 {key} 必须 >= {minimum}")
    return value


def validate_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    data = deep_merge(DEFAULT_CONFIG, cfg)

    window = data["window"]
    if not str(window.get("title_contains", "")).strip():
        raise ConfigError("window.title_contains 不能为空")
    _require_number(window, "reference_width", 1)
    _require_number(window, "reference_height", 1)
    _require_number(window, "aspect_ratio_tolerance", 0)
    _require_number(window, "minimum_width", 1)
    _require_number(window, "minimum_height", 1)

    capture = data["capture"]
    for key in ("interval", "battle_interval", "locate_retry_interval", "max_frame_age"):
        _require_number(capture, key, 0.01)

    matching = data["matching"]
    for key in (
        "select_anchor_threshold",
        "challenge_threshold",
        "start_threshold",
        "close_threshold",
        "other_device_threshold",
        "near_threshold_delta",
    ):
        value = _require_number(matching, key, 0)
        if value > 1.0 and key != "near_threshold_delta":
            raise ConfigError(f"matching.{key} 必须在 0~1 之间")

    rois = data["rois"]
    for name in (
        "select_anchor",
        "difficulty_list",
        "challenge_button",
        "start_button",
        "close_prompt",
        "other_device_message",
    ):
        rois[name] = list(ensure_roi(rois[name], name))

    crops = data["template_crops"]
    for name in (
        "select_anchor",
        "challenge_button",
        "start_button",
        "close_prompt",
        "other_device_message",
    ):
        crops[name] = list(ensure_roi(crops[name], f"template_crops.{name}"))

    difficulty = data["difficulty"]
    _require_number(difficulty, "target_level", 1)
    centers = difficulty.get("row_centers")
    if not isinstance(centers, list) or len(centers) < 1:
        raise ConfigError("difficulty.row_centers 至少需要一行")
    difficulty["row_centers"] = [int(v) for v in centers]
    for key in (
        "click_x",
        "click_half_width",
        "row_half_height",
        "row_inner_x1",
        "row_inner_x2",
        "border_thickness",
        "saturation_threshold",
        "high_saturation_threshold",
        "high_saturation_ratio",
        "gray_ratio_threshold",
        "yellow_border_ratio_threshold",
        "yellow_edge_coverage_threshold",
    ):
        _require_number(difficulty, key, 0)
    for key in ("yellow_hsv_lower", "yellow_hsv_upper"):
        values = difficulty.get(key)
        if not isinstance(values, list) or len(values) != 3:
            raise ConfigError(f"difficulty.{key} 必须是长度为 3 的列表")

    actions = data["actions"]
    for key in (
        "cooldown",
        "post_select_wait",
        "post_click_wait",
        "confirm_open_wait",
        "start_retry_wait",
        "battle_timeout",
        "unknown_timeout",
    ):
        _require_number(actions, key, 0)
    start_point = actions.get("confirm_start_point")
    if not isinstance(start_point, list) or len(start_point) != 2:
        raise ConfigError("actions.confirm_start_point 必须是 [x, y]")
    actions["confirm_start_point"] = [int(value) for value in start_point]
    actions["confirm_start_bounds"] = list(
        ensure_roi(actions.get("confirm_start_bounds", []), "actions.confirm_start_bounds")
    )
    for key in ("max_select_retries", "debounce_frames", "debounce_required"):
        _require_number(actions, key, 1)

    debug = data["debug"]
    _require_number(debug, "max_files", 1)

    return data


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    if path is not None:
        config_path = path
    else:
        # 优先 exe 旁可编辑配置，其次打包内置配置。
        external = project_root() / "config" / "default.yaml"
        bundled = resource_root() / "config" / "default.yaml"
        config_path = external if external.exists() else bundled
    if not config_path.exists():
        return validate_config({})

    try:
        text = config_path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败: {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件: {config_path}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ConfigError("配置文件根节点必须是字典")
    return validate_config(loaded)


def assets_dir() -> Path:
    external = project_root() / "assets"
    if (external / "templates").exists():
        return external
    return resource_root() / "assets"


def templates_dir() -> Path:
    return assets_dir() / "templates"


def reference_dir() -> Path:
    return assets_dir() / "reference"


def debug_dir() -> Path:
    return project_root() / "debug"


def logs_dir() -> Path:
    return project_root() / "logs"
