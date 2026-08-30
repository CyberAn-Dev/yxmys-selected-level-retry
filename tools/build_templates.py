from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tower_bot.config import load_config, reference_dir, templates_dir  # noqa: E402
from tower_bot.models import ensure_roi  # noqa: E402
from tower_bot.vision import TemplateMatcher, load_bgr  # noqa: E402


TEMPLATE_SOURCES = {
    "select_anchor": ("select_screen.png", "select_anchor"),
    "challenge_button": ("select_screen.png", "challenge_button"),
    "start_button": ("confirm_screen.png", "start_button"),
    "close_prompt": ("result_screen.png", "close_prompt"),
    "other_device_message": ("other_device_screen.png", "other_device_message"),
}


def crop_and_save(
    image,
    crop: tuple[int, int, int, int],
    out_path: Path,
) -> None:
    x1, y1, x2, y2 = crop
    patch = image[y1:y2, x1:x2]
    if patch.size == 0:
        raise SystemExit(f"裁剪区域为空: {crop}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), patch)


def main() -> None:
    parser = argparse.ArgumentParser(description="从参考截图裁剪模板")
    parser.add_argument("--force", action="store_true", help="覆盖已有模板")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    ref = reference_dir()
    out_dir = templates_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    for name, (ref_name, crop_key) in TEMPLATE_SOURCES.items():
        ref_path = ref / ref_name
        if not ref_path.exists():
            raise SystemExit(f"缺少参考图: {ref_path}")
        out_path = out_dir / f"{name}.png"
        if out_path.exists() and not args.force:
            print(f"跳过已存在模板: {out_path} （使用 --force 覆盖）")
            continue
        image = load_bgr(ref_path)
        crop = ensure_roi(cfg["template_crops"][crop_key], crop_key)
        crop_and_save(image, crop, out_path)
        h, w = cv2.imread(str(out_path)).shape[:2]
        print(f"已生成 {name}: {out_path} size={w}x{h} crop={crop}")
        created.append(name)

    # 验证匹配
    matcher = TemplateMatcher(cfg)
    print("\n=== 参考图匹配验证 ===")
    for label, filename in (
        ("SELECT", "select_screen.png"),
        ("CONFIRM", "confirm_screen.png"),
        ("RESULT", "result_screen.png"),
    ):
        frame = load_bgr(ref / filename)
        matches = matcher.match_all(frame)
        print(f"[{label}] {filename}")
        for key, match in matches.items():
            print(f"  {key}: hit={match.hit} score={match.score:.4f}")


if __name__ == "__main__":
    main()
