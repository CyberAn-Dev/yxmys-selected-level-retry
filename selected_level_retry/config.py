from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from tower_bot.config import deep_merge, load_config
from tower_bot.models import ConfigError, ensure_roi, project_root


PACKAGE_ROOT = Path(__file__).resolve().parent

FEATURE_DEFAULTS: dict[str, Any] = {
    "target": {
        "signature_x1": 90,
        "signature_x2": 460,
        "signature_half_height": 24,
        "min_good_matches": 8,
        "min_feature_ratio": 0.06,
        "template_threshold": 0.88,
    },
    "recovery": {
        "enabled": True,
        "max_scrolls": 36,
        "unchanged_scrolls_before_reverse": 1,
        "scroll_x": 275,
        "down_start_y": 680,
        "down_end_y": 540,
        "up_start_y": 420,
        "up_end_y": 560,
        "duration": 0.35,
        "post_wait": 0.55,
    },
    "result": {
        "success_template": "",
        "success_roi": [0, 0, 550, 1020],
        "success_threshold": 0.80,
        "failure_template": "",
        "failure_roi": [0, 0, 550, 1020],
        "failure_threshold": 0.75,
        "assume_non_success_is_failure": True,
        "unknown_policy": "pause",
        "max_attempts": 0,
    },
}


def _number(section: Mapping[str, Any], key: str, minimum: float = 0.0) -> float:
    if key not in section:
        raise ConfigError(f"选定关卡配置缺少配置项: {key}")
    try:
        value = float(section[key])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"选定关卡配置 {key} 必须是数字") from exc
    if value < minimum:
        raise ConfigError(f"选定关卡配置 {key} 必须 >= {minimum}")
    return value


def _threshold(section: Mapping[str, Any], key: str) -> float:
    value = _number(section, key, 0.0)
    if value > 1.0:
        raise ConfigError(f"选定关卡配置 {key} 必须在 0~1 之间")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"选定关卡配置 YAML 解析失败: {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取选定关卡配置: {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError("选定关卡配置根节点必须是字典")
    return loaded


def validate_feature_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    data = deep_merge(FEATURE_DEFAULTS, cfg)

    target = data["target"]
    x1 = int(_number(target, "signature_x1", 0))
    x2 = int(_number(target, "signature_x2", 1))
    half = int(_number(target, "signature_half_height", 1))
    if x2 <= x1:
        raise ConfigError("target.signature_x2 必须大于 signature_x1")
    target["signature_x1"] = x1
    target["signature_x2"] = x2
    target["signature_half_height"] = half
    target["min_good_matches"] = int(_number(target, "min_good_matches", 1))
    target["min_feature_ratio"] = _threshold(target, "min_feature_ratio")
    target["template_threshold"] = _threshold(target, "template_threshold")

    recovery = data["recovery"]
    recovery["max_scrolls"] = int(_number(recovery, "max_scrolls", 1))
    recovery["unchanged_scrolls_before_reverse"] = int(
        _number(recovery, "unchanged_scrolls_before_reverse", 1)
    )
    for key in (
        "scroll_x",
        "down_start_y",
        "down_end_y",
        "up_start_y",
        "up_end_y",
    ):
        recovery[key] = int(_number(recovery, key, 0))
    recovery["duration"] = _number(recovery, "duration", 0.01)
    recovery["post_wait"] = _number(recovery, "post_wait", 0.01)

    result = data["result"]
    result["success_roi"] = list(ensure_roi(result["success_roi"], "result.success_roi"))
    result["failure_roi"] = list(ensure_roi(result["failure_roi"], "result.failure_roi"))
    result["success_threshold"] = _threshold(result, "success_threshold")
    result["failure_threshold"] = _threshold(result, "failure_threshold")
    result["success_template"] = str(result.get("success_template", "")).strip()
    result["failure_template"] = str(result.get("failure_template", "")).strip()
    result["unknown_policy"] = str(result.get("unknown_policy", "pause")).lower()
    if result["unknown_policy"] not in {"pause", "retry"}:
        raise ConfigError("result.unknown_policy 只能是 pause 或 retry")
    result["max_attempts"] = int(_number(result, "max_attempts", 0))

    return data


def resolve_feature_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root() / path


def load_feature_config(
    path: Optional[Path] = None,
    *,
    root_config_path: Optional[Path] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root_cfg = load_config(root_config_path)
    feature_path = path or (PACKAGE_ROOT / "default.yaml")
    loaded = _load_yaml(feature_path)
    return root_cfg, validate_feature_config(loaded)
