from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tower_bot.config import debug_dir, load_config  # noqa: E402
from tower_bot.state_machine import StateMachine  # noqa: E402
from tower_bot.vision import draw_debug_overlay, load_bgr, to_reference_size  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="离线回放视觉检测")
    parser.add_argument("image", type=Path, help="待检测图片")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="标注输出路径，默认 debug/replay_<name>.png",
    )
    args = parser.parse_args()

    if not args.image.exists():
        raise SystemExit(f"图片不存在: {args.image}")

    cfg = load_config(args.config)
    frame = to_reference_size(
        load_bgr(args.image),
        int(cfg["window"]["reference_width"]),
        int(cfg["window"]["reference_height"]),
    )
    sm = StateMachine(cfg)
    analysis = sm.analyze_frame(frame, debounce=False)

    print(f"图片: {args.image}")
    print(f"状态: {analysis.state.name}")
    print(f"下一动作: {analysis.next_action.name}")
    print(f"原因: {analysis.reason}")
    print(
        f"分数: challenge={analysis.challenge.score:.4f} "
        f"start={analysis.start.score:.4f} close={analysis.close.score:.4f} "
        f"anchor={analysis.select_anchor.score:.4f}"
    )
    print(
        f"最高 unlocked={analysis.difficulty.highest_unlocked_index} "
        f"selected={analysis.difficulty.selected_index}"
    )
    for row in analysis.difficulty.rows:
        print(
            f"  row#{row.row_index}: unlocked={row.unlocked} selected={row.selected} "
            f"S={row.mean_saturation:.1f} yellow={row.yellow_border_ratio:.3f}"
        )

    matches = {
        "select_anchor": analysis.select_anchor,
        "challenge_button": analysis.challenge,
        "start_button": analysis.start,
        "close_prompt": analysis.close,
    }
    overlay = draw_debug_overlay(
        frame,
        matches,
        rows=analysis.difficulty.rows,
        state_name=analysis.state.name,
        action_name=analysis.next_action.name,
        highest_index=analysis.difficulty.highest_unlocked_index,
    )
    out = args.out
    if out is None:
        debug_dir().mkdir(parents=True, exist_ok=True)
        out = debug_dir() / f"replay_{args.image.stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), overlay)
    print(f"标注图: {out}")


if __name__ == "__main__":
    main()
