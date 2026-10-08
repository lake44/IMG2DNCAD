"""축척 보정 — 그림 px을 실제 mm로 바꾸는 값을 여러 근거로 구한다.

어느 방법이든 결과는 mm_per_px 하나다. 근거와 세부 값은 모델 meta에 남겨 나중에 되짚을 수 있게 한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence


class CalibrationError(ValueError):
    pass


@dataclass
class Calibration:
    mm_per_px: float
    method: str                                   # two-point | width | pin-pitch | dpi | manual
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (math.isfinite(self.mm_per_px) and self.mm_per_px > 0):
            raise CalibrationError(f"축척(mm/px)이 올바르지 않습니다: {self.mm_per_px}")

    def to_dict(self) -> dict:
        return {"mm_per_px": self.mm_per_px, "method": self.method, **self.detail}

    @staticmethod
    def from_dict(d: dict) -> "Calibration":
        det = {k: v for k, v in d.items() if k not in ("mm_per_px", "method")}
        return Calibration(float(d["mm_per_px"]), str(d.get("method", "manual")), det)


def manual(mm_per_px: float) -> Calibration:
    return Calibration(float(mm_per_px), "manual")


def from_dpi(dpi: float) -> Calibration:
    if dpi <= 0:
        raise CalibrationError("DPI는 0보다 커야 합니다")
    return Calibration(25.4 / dpi, "dpi", {"dpi": dpi})


def from_two_points(p1: tuple[float, float], p2: tuple[float, float], length_mm: float) -> Calibration:
    d = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if d <= 0 or length_mm <= 0:
        raise CalibrationError("두 점이 서로 달라야 하고 실제 거리는 0보다 커야 합니다")
    return Calibration(length_mm / d, "two-point", {"p1": list(p1), "p2": list(p2), "length_mm": length_mm})


def from_width(width_px: float, width_mm: float) -> Calibration:
    """본체 가로(또는 세로) 길이처럼 실제 치수를 아는 변으로 보정."""
    if width_px <= 0 or width_mm <= 0:
        raise CalibrationError("px 길이와 mm 길이는 모두 0보다 커야 합니다")
    return Calibration(width_mm / width_px, "width", {"length_px": width_px, "length_mm": width_mm})


def from_pin_pitch(positions_px: Sequence[float], pitch_mm: float) -> Calibration:
    """같은 줄에 늘어선 접속점(단자) 위치로 보정: 인접 간격의 최빈값이 pitch_mm에 해당한다.

    간격이 pitch의 정수배로 건너뛰어도(빈 단자) 가장 작은 간격 묶음을 기준으로 삼는다.
    """
    xs = sorted(float(v) for v in positions_px)
    if len(xs) < 3 or pitch_mm <= 0:
        raise CalibrationError("접속점 위치가 3개 이상 필요하고 pitch는 0보다 커야 합니다")
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 1e-6]
    base = min(gaps)
    unit = [g for g in gaps if g < 1.5 * base]
    pitch_px = sum(unit) / len(unit)
    spread = (max(unit) - min(unit)) / pitch_px if len(unit) > 1 else 0.0
    cal = Calibration(pitch_mm / pitch_px, "pin-pitch", {"pitch_mm": pitch_mm, "pitch_px": round(pitch_px, 3), "gaps_used": len(unit), "spread": round(spread, 4)})
    return cal
