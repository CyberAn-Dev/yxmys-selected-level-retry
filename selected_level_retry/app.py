from __future__ import annotations

import argparse
import traceback
from pathlib import Path
from typing import Optional

from tower_bot.app import HotkeyBridge
from tower_bot.logger import get_logger, setup_logging

from .config import load_feature_config
from .controller import SelectedLevelRetryController
from .ui import SelectedLevelRetryUI


logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用户选中关卡后的失败重试与列表复位找回工具"
    )
    parser.add_argument("--config", type=Path, default=None, help="根项目配置路径")
    parser.add_argument(
        "--feature-config",
        type=Path,
        default=None,
        help="选定关卡模式附加配置，默认 selected_level_retry/default.yaml",
    )
    parser.add_argument(
        "--success-template",
        type=Path,
        default=None,
        help="成功结算模板路径，覆盖附加配置中的 result.success_template",
    )
    parser.add_argument("--dry-run", action="store_true", help="只识别和记录动作，不移动鼠标")
    parser.add_argument("--no-gui", action="store_true", help="只使用热键和日志，不打开 GUI")
    parser.add_argument("--debug", action="store_true", help="启动调试标注")
    parser.add_argument("--no-hotkeys", action="store_true", help="禁用全局热键")
    return parser


def run_app(argv: Optional[list[str]] = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    root_cfg, feature_cfg = load_feature_config(
        args.feature_config,
        root_config_path=args.config,
    )
    if args.success_template is not None:
        feature_cfg["result"]["success_template"] = str(args.success_template)
    if args.debug:
        root_cfg["debug"]["enabled"] = True

    controller = SelectedLevelRetryController(
        root_cfg,
        feature_cfg,
        dry_run=args.dry_run,
    )
    hotkeys: Optional[HotkeyBridge] = None
    try:
        if not args.no_hotkeys:
            hotkeys = HotkeyBridge(controller)  # 复用主框架的 F8/F9/F10 控制桥
            hotkeys.start()
        if args.no_gui:
            controller.start()
            while not controller.stopping.is_set():
                controller.join(timeout=0.5)
        else:
            SelectedLevelRetryUI(controller).run()
    finally:
        if hotkeys is not None:
            hotkeys.stop()
        controller.shutdown()
    return 0


def main(argv: Optional[list[str]] = None) -> None:
    try:
        raise SystemExit(run_app(argv))
    except KeyboardInterrupt:
        logger.info("收到 KeyboardInterrupt，退出")
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        logger.exception("选定关卡重复挑战启动失败")
        print("\n启动失败，详细错误见日志", flush=True)

