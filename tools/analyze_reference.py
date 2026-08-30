from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tower_bot.config import load_config, reference_dir  # noqa: E402
from tower_bot.difficulty_detector import DifficultyDetector  # noqa: E402
from tower_bot.vision import TemplateMatcher, load_bgr, to_reference_size  # noqa: E402


def analyze_image(path: Path, cfg: dict) -> None:
    frame = to_reference_size(
        load_bgr(path),
        int(cfg["window"]["reference_width"]),
        int(cfg["window"]["reference_height"]),
    )
    matcher = TemplateMatcher(cfg)
    difficulty = DifficultyDetector(cfg)
    matches = matcher.match_all(frame)
    rows = difficulty.analyze(frame)

    print(f"\n===== {path.name} =====")
    print("模板匹配:")
    for name, match in matches.items():
        print(
            f"  {name}: hit={match.hit} score={match.score:.4f} "
            f"center={None if match.center is None else (match.center.x, match.center.y)}"
        )
    print("难度行分析:")
    for row in rows.rows:
        print(
            f"  #{row.row_index} y={row.center_y} "
            f"meanS={row.mean_saturation:.1f} highRatio={row.high_saturation_ratio:.3f} "
            f"gray={row.gray_ratio:.3f} yellowBorder={row.yellow_border_ratio:.3f} "
            f"edgeCov={row.edge_yellow_coverage:.2f} "
            f"unlocked={row.unlocked} selected={row.selected} conf={row.confidence:.2f}"
        )
    print(
        f"最高 unlocked 行={rows.highest_unlocked_index}, "
        f"selected 行={rows.selected_index}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="分析参考截图视觉指标")
    parser.add_argument("images", nargs="*", type=Path, help="图片路径，默认三张参考图")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    images = args.images
    if not images:
        ref = reference_dir()
        images = [
            ref / "select_screen.png",
            ref / "confirm_screen.png",
            ref / "result_screen.png",
        ]
    for path in images:
        if not path.exists():
            print(f"缺少文件: {path}")
            continue
        analyze_image(path, cfg)


if __name__ == "__main__":
    main()
