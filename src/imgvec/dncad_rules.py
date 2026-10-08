"""DNCAD 장치(블랙박스 + 장치 접속점)의 배치 규칙.

모든 값은 이 PC의 DNCAD 자료에서 실측한 것이다 (docs/DNCAD-연동-조사서.md 참고).
  - 프로젝트 백업(.dncadbak)의 black_box·device_connection_point 배치: 좌표·크기·회전
  - 생성기 규칙 파일(L0-불변.mjs)을 인용한 문서: 격자 16, 핀 간격 32, 금지 규칙
  - 문서 DNCAD-도면-받아서-그리기까지.md: 쪽 폭 297 mm = 1900 단위 → 6.4 단위/mm
"""
from __future__ import annotations

from dataclasses import dataclass

SIDES = ("top", "bottom", "left", "right")
SIDE_ALIASES = {
    "top": "top", "t": "top", "up": "top", "상": "top", "위": "top", "상단": "top",
    "bottom": "bottom", "b": "bottom", "down": "bottom", "하": "bottom", "아래": "bottom", "하단": "bottom",
    "left": "left", "l": "left", "좌": "left", "왼쪽": "left", "좌측": "left",
    "right": "right", "r": "right", "우": "right", "오른쪽": "right", "우측": "right",
}


@dataclass(frozen=True)
class DncadRules:
    units_per_mm: float = 6.4          # 극 간격 5 mm = 32, 격자 2.5 mm = 16, 쪽 폭 297 mm = 1900
    pin_pitch_mm: float = 5.0          # 장치 접속점 간격 (32 단위)
    grid_mm: float = 2.5               # 격자 (16 단위)
    edge_tol_mm: float = 0.5           # 접속점이 본체 경계에서 벗어나도 되는 거리
    min_edge_margin_mm: float = 2.5    # 접속점과 모서리 사이 최소 간격 (실측: 대부분 첫 접속점이 32 단위=5 mm, 가장 가까운 예 -KF03은 64)
    default_edge_margin_mm: float = 5.0  # 배치 명세를 만들 때 마지막 접속점 오른쪽 여백
    supported_sides: tuple[str, ...] = ("top", "bottom")   # 실측: 회전 0(위 변)·180(아래 변)만 확인됨
    default_height_units: int = 112    # 실측 샘플의 본체 높이 (112 또는 144)
    min_height_units: int = 64
    max_pins_warn: int = 64
    max_designation_len: int = 12
    # 한 장치 안의 같은 접속점 이름: 생성기 API 규칙은 거부하지만 실제 백업에는 있다 (-TA01 '+'·'-' 2번, -FC07 '0V' 3번).
    # 백업 가져오기 경로에서 받아들여지는지는 미확인이라 기본은 엄격하게 막는다.
    allow_duplicate_designations: bool = False


DEFAULT_RULES = DncadRules()


def mm_to_units(mm: float, rules: DncadRules = DEFAULT_RULES) -> float:
    return mm * rules.units_per_mm


def units_to_mm(units: float, rules: DncadRules = DEFAULT_RULES) -> float:
    return units / rules.units_per_mm


def normalize_side(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    if v not in SIDE_ALIASES:
        raise ValueError(f"알 수 없는 변 이름: {value!r} (top·bottom·left·right 또는 상·하·좌·우)")
    return SIDE_ALIASES[v]
