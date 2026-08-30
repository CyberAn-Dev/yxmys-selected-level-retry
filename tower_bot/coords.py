from __future__ import annotations

from typing import Mapping, Tuple

from .models import AspectRatioError, Point, Rect


def reference_to_screen(
    x: float,
    y: float,
    window_rect: Rect,
    *,
    reference_width: int = 550,
    reference_height: int = 1020,
) -> Point:
    sx = window_rect.left + round(x * window_rect.width / reference_width)
    sy = window_rect.top + round(y * window_rect.height / reference_height)
    return Point(int(sx), int(sy))


def screen_to_reference(
    x: float,
    y: float,
    window_rect: Rect,
    *,
    reference_width: int = 550,
    reference_height: int = 1020,
) -> Point:
    rx = (x - window_rect.left) * reference_width / max(window_rect.width, 1)
    ry = (y - window_rect.top) * reference_height / max(window_rect.height, 1)
    return Point(int(round(rx)), int(round(ry)))


def scale_factors(
    window_rect: Rect,
    *,
    reference_width: int = 550,
    reference_height: int = 1020,
) -> Tuple[float, float]:
    return (
        window_rect.width / reference_width,
        window_rect.height / reference_height,
    )


def assess_window_geometry(
    window_rect: Rect,
    cfg: Mapping,
) -> Tuple[bool, str, str]:
    """
    评估窗口尺寸是否合适。
    返回: (ok, size_text, advice)
    """
    ref_w = int(cfg["window"]["reference_width"])
    ref_h = int(cfg["window"]["reference_height"])
    tolerance = float(cfg["window"]["aspect_ratio_tolerance"])
    min_w = int(cfg["window"]["minimum_width"])
    min_h = int(cfg["window"]["minimum_height"])

    w, h = window_rect.width, window_rect.height
    size_text = f"{w}×{h} px"
    if h <= 0 or w <= 0:
        return False, size_text, "窗口尺寸无效，请恢复小程序窗口"

    expected = ref_w / ref_h
    actual = w / h
    ratio_err = abs(actual - expected) / expected
    scale = (w / ref_w + h / ref_h) / 2.0

    if w < min_w or h < min_h:
        return (
            False,
            size_text,
            f"窗口过小（最小约 {min_w}×{min_h}），请放大后再运行",
        )
    if ratio_err > tolerance:
        # 给出接近参考比例的建议高度/宽度
        suggest_h = int(round(w / expected))
        suggest_w = int(round(h * expected))
        return (
            False,
            f"{size_text}（比例 {actual:.3f}，期望≈{expected:.3f}）",
            (
                f"宽高比偏差 {ratio_err:.1%}，超过容差 {tolerance:.0%}。"
                f"建议调到接近 {ref_w}:{ref_h}，例如保持当前宽度则高度≈{suggest_h}，"
                f"或保持当前高度则宽度≈{suggest_w}"
            ),
        )
    if 0.85 <= scale <= 1.25:
        advice = f"尺寸合适（缩放约 {scale:.2f}x），可直接使用"
    elif scale < 0.85:
        advice = f"比例正常但偏小（约 {scale:.2f}x），可用；若识别不稳可略放大"
    else:
        advice = f"比例正常但偏大（约 {scale:.2f}x），可用；过大时可能更吃性能"
    return True, f"{size_text}（比例正常，缩放≈{scale:.2f}x）", advice


def validate_aspect_ratio(
    window_rect: Rect,
    cfg: Mapping,
) -> None:
    ok, size_text, advice = assess_window_geometry(window_rect, cfg)
    if not ok:
        raise AspectRatioError(f"{size_text}；{advice}")
