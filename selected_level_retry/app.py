from __future__ import annotations

import argparse
import traceback
from pathlib import Path
from typing import Optional

from tower_bot.logger import get_logger, setup_logging

from .config import load_feature_config
from .controller import SelectedLevelRetryController
from .ui import SelectedLevelRetryUI


logger = get_logger(__name__)


class HotkeyBridge:
    """当前难度重复挑战模式使用的全局热键。"""

    def __init__(self, controller) -> None:
        self.controller = controller
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception as exc:  # noqa: BLE001
            logger.warning("无法导入 pynput，热键不可用: %s", exc)
            return False

        cfg = self.controller.cfg["hotkeys"]
        mapping = {
            str(cfg["toggle"]): self._on_toggle,
            str(cfg["stop"]): self._on_stop,
        }
        emergency = str(cfg.get("emergency_pause", "")).strip()
        if emergency and emergency.lower() not in {"<esc>", "esc"}:
            mapping[emergency] = self._on_emergency

        try:
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.start()
            logger.info("热键已注册：F8 开始/暂停，F9 停止")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("全局热键注册失败，将仅使用 GUI 控制: %s", exc)
            self._listener = None
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("停止热键监听失败: %s", exc)
            self._listener = None

    def _on_toggle(self) -> None:
        self.controller.toggle()

    def _on_stop(self) -> None:
        self.controller.stop()

    def _on_emergency(self) -> None:
        self.controller.emergency_pause("热键紧急暂停")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
            description="当前难度重复挑战工具"
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
    controller = SelectedLevelRetryController(
        root_cfg,
        feature_cfg,
        dry_run=args.dry_run,
    )
    hotkeys: Optional[HotkeyBridge] = None
    try:
        if not args.no_hotkeys:
            hotkeys = HotkeyBridge(controller)
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
