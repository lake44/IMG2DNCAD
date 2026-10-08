"""장치 모델 → DNCAD 배치 명세.

DNCAD에서 블랙박스는 크기 {w, h}를 가진 배치(왼쪽 위 기준)이고, 장치 접속점은 같은 장치의 별도 부품으로
위 변(회전 0) 또는 아래 변(회전 180)에 32 단위(5 mm) 간격으로 놓인다. 이 모듈은 그 규칙대로
슬롯 번호·단위 좌표·회전을 계산해 사전으로 돌려준다 (파일·명령 출력은 3단계 어댑터의 몫).
"""
from __future__ import annotations

import math

from .dncad_rules import DEFAULT_RULES, DncadRules
from .model import DeviceModel
from .validate import Issue

SCHEMA = "dncad-device-layout/1.0"
REMAP_LR = {"left": "top", "right": "bottom"}   # 입력은 위, 출력은 아래로 흐르는 회로도 관례


def _ceil_to(v: float, step: float) -> float:
    return math.ceil(v / step - 1e-9) * step


def dncad_layout(
    model: DeviceModel,
    rules: DncadRules = DEFAULT_RULES,
    height_units: int | None = None,
    fit: str = "pins",
    remap_sides: dict[str, str] | None = None,
) -> dict:
    """fit='pins'  본체 폭을 접속점이 들어갈 만큼으로 정한다 (DNCAD 블랙박스는 개략 기호라 실물 크기를 따르지 않는다)
    fit='body'  본체 폭을 실물 크기(mm x 6.4)로 하되 접속점이 넘치면 넓힌다.
    remap_sides  {"left": "top", "right": "bottom"} 처럼 DNCAD가 확인하지 못한 변의 접속점을 옮긴다.
                 옮긴 접속점은 원래 간격 대신 순서대로(빈틈 없이) 슬롯에 놓는다."""
    u = rules.units_per_mm
    pitch_u = rules.pin_pitch_mm * u
    remap = remap_sides or {}
    issues: list[Issue] = []
    placed: list[dict] = []

    group_rank: dict[str, int] = {}
    for p in model.pins:
        group_rank.setdefault(p.group, len(group_rank))

    # 변별 접속점 목록 (옮길 변은 목적지 변으로)
    per_side: dict[str, list[tuple[object, bool]]] = {s: [] for s in rules.supported_sides}
    skipped = []
    for p in model.pins:
        if p.side in rules.supported_sides:
            per_side[p.side].append((p, False))
        elif remap.get(p.side) in rules.supported_sides:
            per_side[remap[p.side]].append((p, True))
        else:
            skipped.append(p)

    for side in rules.supported_sides:
        items = per_side[side]
        # 옮겨 온 접속점은 원래 변을 따른 거리(rank)로, 원래 있던 접속점은 실제 간격으로
        native = sorted([p for p, mv in items if not mv], key=lambda p: (group_rank[p.group], p.offset(), p.name))  # type: ignore[attr-defined]
        moved = sorted([p for p, mv in items if mv], key=lambda p: (p.side, p.offset(), p.name))  # type: ignore[attr-defined]
        last_slot = 0
        i = 0
        while i < len(native):
            j = i
            while j < len(native) and native[j].group == native[i].group:
                j += 1
            grp = native[i:j]
            slots: list[int] = []
            prev = -1
            for p in grp:  # 그룹 안에서는 실제 간격을 살리고, 겹치면 오른쪽으로 민다
                s = max(1, round(p.offset() / rules.pin_pitch_mm), prev + 1)
                slots.append(s)
                prev = s
            shift = max(0, last_slot + 1 - slots[0])  # 앞 그룹과 겹치면 그룹 전체를 오른쪽으로
            slots = [s + shift for s in slots]
            last_slot = slots[-1]
            for p, s in zip(grp, slots):
                _place(placed, issues, p, side, s, p.offset(), rules, pitch_u)
            i = j
        for p in moved:  # 옮겨 온 접속점: 순서대로 이어서
            s = last_slot + 1
            last_slot = s
            _place(placed, issues, p, side, s, s * rules.pin_pitch_mm, rules, pitch_u, moved_from=p.side)

    for p in skipped:
        issues.append(Issue("warn", "W-SIDE", f"'{p.side}' 변 접속점은 DNCAD에서 확인되지 않아 배치 명세에서 뺐습니다. 위·아래 변으로 옮겨 다시 지정하거나 --remap-lr을 쓰세요", p.name))
    if not placed:
        issues.append(Issue("error", "E-NOLAYOUT", "배치할 수 있는 접속점이 없습니다"))

    max_slot = max((q["slot"] for q in placed), default=0)
    need_w = max_slot * pitch_u + rules.default_edge_margin_mm * u
    w_units = _ceil_to(need_w, 16)
    if fit == "body":
        w_units = max(w_units, _ceil_to(model.body.w * u, 16))
    h_units = int(height_units or rules.default_height_units)
    for q in placed:
        q["y"] = 0 if q["side"] == "top" else h_units

    return {
        "schema": SCHEMA,
        "name": model.name,
        "units_per_mm": u,
        "origin": "box top-left, x right, y down",
        "box": {"w": int(w_units), "h": h_units, "w_mm": round(w_units / u, 2), "h_mm": round(h_units / u, 2)},
        "pins": sorted(placed, key=lambda q: (0 if q["side"] == "top" else 1, q["slot"])),
        "skipped_pins": [p.name for p in skipped],
        "issues": [str(i) for i in issues],
    }


def _place(placed, issues, p, side, slot, ref_offset, rules, pitch_u, moved_from: str | None = None) -> None:
    dev = abs(slot * rules.pin_pitch_mm - ref_offset)
    placed.append(
        {
            "designation": p.name,
            "side": side,
            "slot": slot,
            "x": int(round(slot * pitch_u)),
            "y": 0,  # 아래 변은 박스 높이를 정한 뒤 채운다
            "rotation": 0 if side == "top" else 180,
            "mirrored": None,
            "deviation_mm": round(dev, 3),
            "group": p.group,
            **({"moved_from": moved_from} if moved_from else {}),
        }
    )
    if moved_from:
        issues.append(Issue("info", "I-MOVED", f"'{moved_from}' 변에서 '{side}' 변 슬롯 {slot}로 옮겼습니다", p.name))
    elif dev > 0.5 * rules.pin_pitch_mm:
        issues.append(Issue("info", "I-SLOT", f"원래 위치에서 {dev:.1f} mm 옮겨 슬롯 {slot}에 놓았습니다", p.name))
