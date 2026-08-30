from __future__ import annotations

from typing import Mapping, Optional

import cv2
import numpy as np

from .models import DifficultyAnalysis, RowAnalysis


class DifficultyDetector:
    def __init__(self, cfg: Mapping) -> None:
        self.cfg = cfg

    def analyze(self, frame: np.ndarray) -> DifficultyAnalysis:
        d = self.cfg["difficulty"]
        centers = [int(v) for v in d["row_centers"]]
        half = int(d["row_half_height"])
        x1 = int(d["row_inner_x1"])
        x2 = int(d["row_inner_x2"])
        border = int(d["border_thickness"])
        sat_th = float(d["saturation_threshold"])
        high_sat_th = float(d["high_saturation_threshold"])
        high_ratio_th = float(d["high_saturation_ratio"])
        gray_ratio_th = float(d["gray_ratio_threshold"])
        yellow_lower = np.array(d["yellow_hsv_lower"], dtype=np.uint8)
        yellow_upper = np.array(d["yellow_hsv_upper"], dtype=np.uint8)
        yellow_ratio_th = float(d["yellow_border_ratio_threshold"])
        edge_cov_th = float(d["yellow_edge_coverage_threshold"])

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        rows: list[RowAnalysis] = []

        for idx, cy in enumerate(centers):
            y1 = max(0, cy - half)
            y2 = min(frame.shape[0], cy + half)
            inner = hsv[y1:y2, x1:x2]
            if inner.size == 0:
                rows.append(
                    RowAnalysis(
                        row_index=idx,
                        center_y=cy,
                        mean_saturation=0.0,
                        high_saturation_ratio=0.0,
                        gray_ratio=1.0,
                        yellow_border_ratio=0.0,
                        edge_yellow_coverage=0.0,
                        unlocked=False,
                        selected=False,
                        confidence=0.0,
                    )
                )
                continue

            sat = inner[:, :, 1].astype(np.float32)
            val = inner[:, :, 2].astype(np.float32)
            mean_sat = float(np.mean(sat))
            high_ratio = float(np.mean(sat >= high_sat_th))
            # 灰色：低饱和且亮度中等。
            gray_mask = (sat < 45) & (val > 40) & (val < 200)
            gray_ratio = float(np.mean(gray_mask))

            unlocked = (mean_sat > sat_th or high_ratio > high_ratio_th) and (
                gray_ratio < gray_ratio_th
            )

            yellow_border_ratio, edge_cov = self._yellow_border_stats(
                hsv,
                x1=x1,
                x2=x2,
                y1=y1,
                y2=y2,
                border=border,
                lower=yellow_lower,
                upper=yellow_upper,
            )
            # 金边在实机上偏橙黄、左右边可能较弱：比例达标，或上下边同时有金边即可。
            selected = unlocked and (
                yellow_border_ratio >= yellow_ratio_th
                or edge_cov >= edge_cov_th
            )

            confidence = min(
                1.0,
                max(0.0, (mean_sat / 150.0) * 0.6 + high_ratio * 0.4),
            )
            rows.append(
                RowAnalysis(
                    row_index=idx,
                    center_y=cy,
                    mean_saturation=mean_sat,
                    high_saturation_ratio=high_ratio,
                    gray_ratio=gray_ratio,
                    yellow_border_ratio=yellow_border_ratio,
                    edge_yellow_coverage=edge_cov,
                    unlocked=unlocked,
                    selected=selected,
                    confidence=confidence,
                )
            )

        unlocked_indices = [r.row_index for r in rows if r.unlocked]
        highest = max(unlocked_indices) if unlocked_indices else None
        selected_indices = [r.row_index for r in rows if r.selected]
        selected = selected_indices[0] if selected_indices else None
        if highest is not None and rows[highest].selected:
            selected = highest

        at_frontier = False
        needs_scroll_down = False
        needs_scroll_up = False
        needs_center_up = False
        all_unlocked_visible = bool(rows) and all(r.unlocked for r in rows)

        center_min = int(self.cfg["difficulty"].get("scroll", {}).get("center_min_index", 1))

        if highest is None:
            needs_scroll_up = len(rows) > 0
        else:
            locked_below = any(
                (not row.unlocked) and row.row_index > highest for row in rows
            )
            locked_above = any(
                (not row.unlocked) and row.row_index < highest for row in rows
            )
            # 灰色锁定刚好在末行采样点下方（未落入 row_centers）时，也视为交界。
            gray_peek_below = False
            if not locked_below and rows[highest].unlocked:
                gray_peek_below = self._gray_locked_below_row(
                    hsv,
                    rows=rows,
                    below_index=highest,
                    x1=x1,
                    x2=x2,
                    half=half,
                    sat_th=sat_th,
                    high_sat_th=high_sat_th,
                    high_ratio_th=high_ratio_th,
                    gray_ratio_th=gray_ratio_th,
                )

            if locked_below or gray_peek_below:
                at_frontier = True
                # 最高层贴顶只露一部分时，先上滑居中再点选/挑战。
                if highest < center_min:
                    needs_center_up = True
            elif locked_above and highest == max(r.row_index for r in rows):
                at_frontier = True
            else:
                # 可见行没有灰色交界：可能是列表偏上，也可能是满级 50（无灰色）。
                needs_scroll_down = True

        # 硬规则：整页全是已解锁/已通过 → 绝不是可挑战的「最高交界」。
        # 战后列表弹回顶部时常见；若此时挑战会打到低难度（如4）。满级底部由
        # controller 用「下滑后画面不变」判定后再挑战。
        if all_unlocked_visible:
            at_frontier = False
            needs_scroll_down = True
            needs_center_up = False

        return DifficultyAnalysis(
            rows=rows,
            highest_unlocked_index=highest,
            selected_index=selected,
            needs_scroll_down=needs_scroll_down,
            needs_scroll_up=needs_scroll_up,
            needs_center_up=needs_center_up,
            all_unlocked_visible=all_unlocked_visible,
            at_unlock_frontier=at_frontier,
        )

    def _gray_locked_below_row(
        self,
        hsv: np.ndarray,
        *,
        rows: list[RowAnalysis],
        below_index: int,
        x1: int,
        x2: int,
        half: int,
        sat_th: float,
        high_sat_th: float,
        high_ratio_th: float,
        gray_ratio_th: float,
    ) -> bool:
        """在 highest 行下方再探测一格，捕捉未对齐到 row_centers 的灰色锁定。"""
        if below_index < 0 or below_index >= len(rows):
            return False
        centers = [int(v) for v in self.cfg["difficulty"]["row_centers"]]
        if len(centers) >= 2:
            spacing = max(40, centers[-1] - centers[-2])
        else:
            spacing = max(40, half * 2)
        cy = rows[below_index].center_y + spacing
        h = hsv.shape[0]
        y1 = max(0, cy - half)
        y2 = min(h, cy + half)
        if y2 - y1 < max(8, half):
            return False
        inner = hsv[y1:y2, x1:x2]
        if inner.size == 0:
            return False
        sat = inner[:, :, 1].astype(np.float32)
        val = inner[:, :, 2].astype(np.float32)
        mean_sat = float(np.mean(sat))
        high_ratio = float(np.mean(sat >= high_sat_th))
        gray_mask = (sat < 45) & (val > 40) & (val < 200)
        gray_ratio = float(np.mean(gray_mask))
        unlocked = (mean_sat > sat_th or high_ratio > high_ratio_th) and (
            gray_ratio < gray_ratio_th
        )
        return (not unlocked) and gray_ratio >= gray_ratio_th

    @staticmethod
    def _yellow_border_stats(
        hsv: np.ndarray,
        *,
        x1: int,
        x2: int,
        y1: int,
        y2: int,
        border: int,
        lower: np.ndarray,
        upper: np.ndarray,
    ) -> tuple[float, float]:
        h, w = hsv.shape[:2]
        # 仅统计行矩形外围边框带，避免内部金色图标干扰。
        outer_x1 = max(0, x1 - 6)
        outer_x2 = min(w, x2 + 6)
        outer_y1 = max(0, y1 - 6)
        outer_y2 = min(h, y2 + 6)

        mask = np.zeros((h, w), dtype=np.uint8)
        mask[outer_y1:outer_y2, outer_x1:outer_x2] = 1
        mask[y1 + border : max(y1 + border, y2 - border), x1 + border : max(x1 + border, x2 - border)] = 0

        yellow = cv2.inRange(hsv, lower, upper)
        border_pixels = mask > 0
        if not np.any(border_pixels):
            return 0.0, 0.0

        yellow_border = (yellow > 0) & border_pixels
        yellow_ratio = float(np.mean(yellow_border[border_pixels]))

        # 四边覆盖：上下边更关键（实机左右金边常偏弱）。
        edges = [
            yellow[outer_y1 : outer_y1 + border, outer_x1:outer_x2],  # top
            yellow[outer_y2 - border : outer_y2, outer_x1:outer_x2],  # bottom
            yellow[outer_y1:outer_y2, outer_x1 : outer_x1 + border],  # left
            yellow[outer_y1:outer_y2, outer_x2 - border : outer_x2],  # right
        ]
        covered = 0
        top_bottom = 0
        for i, edge in enumerate(edges):
            if edge.size == 0:
                continue
            if float(np.mean(edge > 0)) >= 0.08:
                covered += 1
                if i < 2:
                    top_bottom += 1
        # 上下都有金边时视为强选中信号。
        if top_bottom >= 2:
            return yellow_ratio, 1.0
        edge_coverage = covered / 4.0
        return yellow_ratio, edge_coverage

    def highest_unlocked_center(self, analysis: DifficultyAnalysis) -> Optional[int]:
        row = analysis.highest_unlocked_row
        return None if row is None else row.center_y
