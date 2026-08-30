
from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import mss
import numpy as np
import pyautogui
import win32con
import win32gui
from pynput import keyboard

# 用户提供的参考截图尺寸。程序会把实时窗口缩放到这个尺寸再识别。
BASE_W = 550
BASE_H = 1020

# 微信小程序独立窗口标题中包含的文字。
WINDOW_TITLE = "英雄没有闪"

# 模板搜索区域，坐标基于 550×1020 参考图。
ROI_CHALLENGE = (140, 800, 410, 950)
ROI_START = (150, 620, 400, 780)
ROI_CLOSE = (140, 680, 410, 850)

# 难度行中心。程序选择“最靠下且不是灰色”的一行。
ROW_CENTERS = (360, 450, 555, 655, 760)
ROW_X1, ROW_X2 = 80, 470
ROW_HALF_HEIGHT = 34

# 未解锁行几乎是灰色，HSV 饱和度很低；可选行是红/粉色，饱和度很高。
UNLOCKED_SATURATION_THRESHOLD = 80.0

MATCH_THRESHOLD = 0.82
LOOP_INTERVAL = 0.25
ACTION_COOLDOWN = 0.85

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# 保留急停：鼠标快速移到主屏幕任意角落时，PyAutoGUI 会抛出异常并暂停。
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

# 避免 Windows DPI 缩放导致截图坐标和点击坐标不一致。
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class Match:
    score: float
    x: int
    y: int


class TowerBot:
    def __init__(self) -> None:
        self.enabled = threading.Event()
        self.stopping = threading.Event()
        self.last_action_at = 0.0

        self.templates = {
            "challenge": self._load_template("challenge.png"),
            "start": self._load_template("start.png"),
            "close": self._load_template("close.png"),
        }
        self.sct = mss.mss()

    @staticmethod
    def _load_template(name: str) -> np.ndarray:
        path = TEMPLATE_DIR / name
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise FileNotFoundError(f"找不到模板：{path}")
        return image

    @staticmethod
    def find_window() -> tuple[Optional[int], Optional[WindowRect]]:
        """通过 Windows 窗口标题定位小程序窗口。游戏状态判断仍完全依赖画面。"""
        found: list[tuple[int, WindowRect]] = []

        def callback(hwnd: int, _: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return

            title = win32gui.GetWindowText(hwnd)
            if WINDOW_TITLE not in title:
                return

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            rect = WindowRect(left, top, right, bottom)

            if rect.width >= 300 and rect.height >= 500:
                found.append((hwnd, rect))

        win32gui.EnumWindows(callback, None)

        if not found:
            return None, None

        # 多个同名窗口时，选择面积最大的。
        return max(found, key=lambda item: item[1].width * item[1].height)

    def capture(self, rect: WindowRect) -> np.ndarray:
        region = {
            "left": rect.left,
            "top": rect.top,
            "width": rect.width,
            "height": rect.height,
        }
        shot = np.asarray(self.sct.grab(region), dtype=np.uint8)
        frame = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

        return cv2.resize(
            frame,
            (BASE_W, BASE_H),
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def match_template(
        frame: np.ndarray,
        template: np.ndarray,
        roi: tuple[int, int, int, int],
    ) -> Match:
        x1, y1, x2, y2 = roi
        gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)

        if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
            return Match(0.0, 0, 0)

        result = cv2.matchTemplate(
            gray,
            template,
            cv2.TM_CCOEFF_NORMED,
        )
        _, max_score, _, max_loc = cv2.minMaxLoc(result)

        tx, ty = max_loc
        cx = x1 + tx + template.shape[1] // 2
        cy = y1 + ty + template.shape[0] // 2

        return Match(float(max_score), cx, cy)

    @staticmethod
    def highest_unlocked_row(
        frame: np.ndarray,
    ) -> tuple[Optional[int], list[float]]:
        """
        不识别“44/45/46”文字，只比较每行颜色：
        可选行是红/粉色；未解锁行是低饱和度灰色。
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        scores: list[float] = []
        unlocked: list[int] = []

        for y in ROW_CENTERS:
            y1 = max(0, y - ROW_HALF_HEIGHT)
            y2 = min(BASE_H, y + ROW_HALF_HEIGHT)
            saturation = hsv[y1:y2, ROW_X1:ROW_X2, 1]

            score = float(np.mean(saturation))
            scores.append(score)

            if score >= UNLOCKED_SATURATION_THRESHOLD:
                unlocked.append(y)

        return (max(unlocked) if unlocked else None), scores

    @staticmethod
    def map_point(
        rect: WindowRect,
        x: int,
        y: int,
    ) -> tuple[int, int]:
        sx = rect.left + round(x * rect.width / BASE_W)
        sy = rect.top + round(y * rect.height / BASE_H)
        return sx, sy

    @staticmethod
    def focus_window(hwnd: int) -> bool:
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.25)

            if win32gui.GetForegroundWindow() != hwnd:
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.15)

            return win32gui.GetForegroundWindow() == hwnd

        except Exception as exc:
            logging.warning("无法把窗口切到前台：%s", exc)
            return False

    def click_base(
        self,
        hwnd: int,
        rect: WindowRect,
        x: int,
        y: int,
    ) -> None:
        # 防止窗口切换失败后误点其他程序。
        if not self.focus_window(hwnd):
            raise RuntimeError("目标窗口不是前台窗口，取消本次点击。")

        sx, sy = self.map_point(rect, x, y)
        pyautogui.click(sx, sy)

    def can_act(self) -> bool:
        return time.monotonic() - self.last_action_at >= ACTION_COOLDOWN

    def mark_action(self) -> None:
        self.last_action_at = time.monotonic()

    def tick(self) -> None:
        hwnd, rect = self.find_window()

        if hwnd is None or rect is None:
            logging.warning("没有找到标题包含“%s”的窗口。", WINDOW_TITLE)
            time.sleep(1.0)
            return

        frame = self.capture(rect)

        close_match = self.match_template(
            frame,
            self.templates["close"],
            ROI_CLOSE,
        )
        start_match = self.match_template(
            frame,
            self.templates["start"],
            ROI_START,
        )
        challenge_match = self.match_template(
            frame,
            self.templates["challenge"],
            ROI_CHALLENGE,
        )

        if not self.can_act():
            return

        # 优先级必须是：结算 > 开始挑战 > 难度选择。
        # 因为确认弹窗出现时，底层“挑战深渊”按钮仍然能被看到。
        if close_match.score >= MATCH_THRESHOLD:
            logging.info(
                "检测到结算关闭提示，score=%.3f",
                close_match.score,
            )
            self.click_base(
                hwnd,
                rect,
                close_match.x,
                close_match.y,
            )
            self.mark_action()
            return

        if start_match.score >= MATCH_THRESHOLD:
            logging.info(
                "检测到“开始挑战”，score=%.3f",
                start_match.score,
            )
            self.click_base(
                hwnd,
                rect,
                start_match.x,
                start_match.y,
            )
            self.mark_action()
            return

        if challenge_match.score >= MATCH_THRESHOLD:
            row_y, saturation_scores = self.highest_unlocked_row(frame)

            if row_y is None:
                logging.warning(
                    "已到选择界面，但没有识别到可选难度：%s",
                    [round(v, 1) for v in saturation_scores],
                )
                return

            logging.info(
                "选择最高可用难度 y=%d，饱和度=%s",
                row_y,
                [round(v, 1) for v in saturation_scores],
            )

            # 即使该行已经有黄色选中框，再点一次通常也没有副作用。
            # 这样可以省去对黄色动画边框的额外识别。
            self.click_base(hwnd, rect, 270, row_y)
            time.sleep(0.35)

            self.click_base(
                hwnd,
                rect,
                challenge_match.x,
                challenge_match.y,
            )
            self.mark_action()
            return

        # 未识别到操作状态时不点击。
        # 通常表示正在战斗、加载中、窗口被遮挡或 UI 已改版。
        logging.debug(
            "等待：close=%.3f start=%.3f challenge=%.3f",
            close_match.score,
            start_match.score,
            challenge_match.score,
        )

    def toggle(self) -> None:
        if self.enabled.is_set():
            self.enabled.clear()
            logging.info("已暂停。按 F8 继续，F9 退出。")
        else:
            self.enabled.set()
            logging.info("已启动。按 F8 暂停，F9 退出。")

    def stop(self) -> None:
        self.stopping.set()
        self.enabled.clear()
        logging.info("正在退出……")

    def run(self) -> None:
        logging.info(
            "程序已加载：F8 开始/暂停，F9 退出；"
            "鼠标移到屏幕角落也会触发急停。"
        )

        hotkeys = keyboard.GlobalHotKeys({
            "<f8>": self.toggle,
            "<f9>": self.stop,
        })
        hotkeys.start()

        try:
            while not self.stopping.is_set():
                if self.enabled.is_set():
                    try:
                        self.tick()

                    except pyautogui.FailSafeException:
                        logging.warning(
                            "触发 PyAutoGUI 急停，程序已暂停。"
                        )
                        self.enabled.clear()

                    except Exception:
                        logging.exception(
                            "本轮识别或点击失败，已跳过。"
                        )
                        time.sleep(1.0)

                time.sleep(LOOP_INTERVAL)

        finally:
            hotkeys.stop()
            self.sct.close()


if __name__ == "__main__":
    TowerBot().run()
