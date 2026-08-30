"""根据现有模板合成 550×1020 参考截图，便于离线测试与模板裁剪验证。

若仓库中已有真实参考图，本脚本默认不会覆盖；加 --force 可重建。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REF_DIR = ROOT / "assets" / "reference"
TPL_DIR = ROOT / "assets" / "templates"

W, H = 550, 1020
ROW_CENTERS = [360, 450, 555, 655, 760]
ROW_X1, ROW_X2 = 90, 460
ROW_HALF = 34


def _paste(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    h, w = src.shape[:2]
    x2 = min(dst.shape[1], x + w)
    y2 = min(dst.shape[0], y + h)
    dst[y:y2, x:x2] = src[: y2 - y, : x2 - x]


def _draw_row(
    canvas: np.ndarray,
    center_y: int,
    *,
    unlocked: bool,
    selected: bool,
) -> None:
    y1 = center_y - ROW_HALF
    y2 = center_y + ROW_HALF
    if unlocked:
        # 红粉紫红色背景，模拟已解锁行。
        color = (90, 70, 190)
    else:
        color = (110, 110, 110)
    cv2.rectangle(canvas, (ROW_X1, y1), (ROW_X2, y2), color, thickness=-1)
    # 轻微纹理，避免模板误匹配。
    noise = np.random.randint(-12, 13, size=(y2 - y1, ROW_X2 - ROW_X1, 3), dtype=np.int16)
    region = canvas[y1:y2, ROW_X1:ROW_X2].astype(np.int16)
    canvas[y1:y2, ROW_X1:ROW_X2] = np.clip(region + noise, 0, 255).astype(np.uint8)

    if selected:
        yellow = (40, 220, 255)
        for thickness, inset in ((10, 2), (6, 5)):
            cv2.rectangle(
                canvas,
                (ROW_X1 - inset, y1 - inset),
                (ROW_X2 + inset, y2 + inset),
                yellow,
                thickness=thickness,
            )


def _base_background() -> np.ndarray:
    canvas = np.zeros((H, W, 3), dtype=np.uint8)
    # 深色游戏背景。
    canvas[:] = (35, 28, 42)
    # 顶部装饰条，作为 select_anchor 区域。
    cv2.rectangle(canvas, (80, 50), (470, 145), (55, 70, 120), thickness=-1)
    cv2.putText(
        canvas,
        "JUEJING SHENYUAN",
        (120, 105),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.85,
        (220, 220, 240),
        2,
        cv2.LINE_AA,
    )
    return canvas


def make_select_screen(challenge_tpl: np.ndarray) -> np.ndarray:
    canvas = _base_background()
    # 前三行已解锁，第 3 行（index=2, y=555）为最高可选并已选中。
    # 后两行锁定。对应参考说明里的 44 可选、45/46 锁定。
    unlocked_flags = [True, True, True, False, False]
    selected_index = 2
    for idx, cy in enumerate(ROW_CENTERS):
        _draw_row(
            canvas,
            cy,
            unlocked=unlocked_flags[idx],
            selected=(idx == selected_index),
        )
    # 挑战深渊按钮放在裁剪坐标处。
    _paste(canvas, challenge_tpl, 180, 875)
    return canvas


def make_confirm_screen(challenge_tpl: np.ndarray, start_tpl: np.ndarray) -> np.ndarray:
    # 底层仍保留选择界面痕迹，验证优先级。
    canvas = make_select_screen(challenge_tpl)
    # 羊皮纸弹窗遮罩。
    overlay = canvas.copy()
    cv2.rectangle(overlay, (60, 220), (490, 800), (180, 200, 220), thickness=-1)
    canvas = cv2.addWeighted(overlay, 0.85, canvas, 0.15, 0)
    _paste(canvas, start_tpl, 205, 675)
    return canvas


def make_result_screen(close_tpl: np.ndarray) -> np.ndarray:
    canvas = np.zeros((H, W, 3), dtype=np.uint8)
    canvas[:] = (25, 20, 30)
    # 战斗失败标题区域。
    cv2.rectangle(canvas, (90, 280), (460, 420), (40, 40, 70), thickness=-1)
    cv2.putText(
        canvas,
        "BATTLE FAIL",
        (160, 360),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.1,
        (200, 200, 220),
        2,
        cv2.LINE_AA,
    )
    _paste(canvas, close_tpl, 220, 748)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description="合成参考截图")
    parser.add_argument("--force", action="store_true", help="覆盖已有参考图")
    args = parser.parse_args()

    REF_DIR.mkdir(parents=True, exist_ok=True)
    challenge = cv2.imread(str(TPL_DIR / "challenge_button.png"), cv2.IMREAD_COLOR)
    start = cv2.imread(str(TPL_DIR / "start_button.png"), cv2.IMREAD_COLOR)
    close = cv2.imread(str(TPL_DIR / "close_prompt.png"), cv2.IMREAD_COLOR)
    if challenge is None or start is None or close is None:
        raise SystemExit("缺少 assets/templates 下的按钮模板，无法合成参考图")

    targets = {
        "select_screen.png": make_select_screen(challenge),
        "confirm_screen.png": make_confirm_screen(challenge, start),
        "result_screen.png": make_result_screen(close),
    }
    for name, image in targets.items():
        path = REF_DIR / name
        if path.exists() and not args.force:
            print(f"跳过已存在: {path}")
            continue
        cv2.imwrite(str(path), image)
        print(f"已写入: {path} ({image.shape[1]}x{image.shape[0]})")


if __name__ == "__main__":
    main()
