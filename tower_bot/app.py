from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Optional

from .config import load_config
from .controller import TowerController
from .logger import get_logger, setup_logging
from .ui import TowerUI

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="英雄没有闪——绝境深渊纯视觉自动爬塔工具",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="配置文件路径，默认 config/default.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="识别并输出本应点击的位置，但不移动鼠标、不点击",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="不启动 GUI，仅热键+日志控制",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启动时开启调试标注",
    )
    parser.add_argument(
        "--no-hotkeys",
        action="store_true",
        help="禁用全局热键（仅用 GUI 按钮）",
    )
    return parser


class HotkeyBridge:
    def __init__(self, controller: TowerController) -> None:
        self.controller = controller
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception as exc:  # noqa: BLE001
            logger.warning("无法导入 pynput，热键不可用: %s", exc)
            return False

        cfg = self.controller.cfg["hotkeys"]
        # Esc 作为全局热键在部分 Windows 环境会注册失败，放到可选映射里。
        mapping = {
            str(cfg["toggle"]): self._on_toggle,
            str(cfg["stop"]): self._on_stop,
            str(cfg["debug"]): self._on_debug,
        }
        emergency = str(cfg.get("emergency_pause", "")).strip()
        if emergency and emergency.lower() not in {"<esc>", "esc"}:
            mapping[emergency] = self._on_emergency

        try:
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.start()
            logger.info(
                "热键已注册：F8 开始/暂停，F9 停止，F10 调试",
            )
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

    def _on_debug(self) -> None:
        self.controller.toggle_debug()

    def _on_emergency(self) -> None:
        self.controller.emergency_pause("热键紧急暂停")


def run_app(argv: Optional[list[str]] = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.debug:
        cfg["debug"]["enabled"] = True

    controller = TowerController(cfg, dry_run=args.dry_run)
    hotkeys: Optional[HotkeyBridge] = None

    try:
        if not args.no_hotkeys:
            hotkeys = HotkeyBridge(controller)
            hotkeys.start()

        if args.no_gui:
            logger.info(
                "无 GUI 模式已启动。dry_run=%s。按 F8 开始，F9 停止。",
                args.dry_run,
            )
            controller.start()
            while not controller.stopping.is_set():
                controller.join(timeout=0.5)
        else:
            logger.info("正在打开控制面板……")
            ui = TowerUI(cfg, controller)
            ui.run()
    finally:
        if hotkeys is not None:
            hotkeys.stop()
        controller.shutdown()
        logger.info("程序已退出")
    return 0


def main(argv: Optional[list[str]] = None) -> None:
    try:
        code = run_app(argv)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 0
    except KeyboardInterrupt:
        logger.info("收到 KeyboardInterrupt，退出")
        code = 0
    except Exception:
        traceback.print_exc()
        logger.exception("启动失败")
        print("\n启动失败，详细错误见上方或 logs/tower_bot.log", flush=True)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
