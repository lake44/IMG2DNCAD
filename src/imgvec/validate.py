"""장치 모델 검증기 — DNCAD로 보내기 전에 걸러야 할 문제를 찾는다.

규칙의 근거(모두 DNCAD 실제 자료):
  E-NOPIN   '접속점 0개 블랙박스'는 2026-09-21 500쪽 도면 사고로 검증 ① 성립성에 들어갔다
  E-DUP     생성기 규칙(L0-불변): '한 장치에 같은 접속점 이름을 두 번' 금지, 서버가 거부한다
            (기존 백업에는 중복이 있으므로 rules.allow_duplicate_designations로 경고로 낮출 수 있다)
  E-OVERLAP·W-PITCH·W-GRID  접속점 간격 32 단위(5 mm), 격자 16 단위(2.5 mm)
  W-SIDE    실측 배치는 위·아래 변(회전 0·180)뿐
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .dncad_rules import DEFAULT_RULES, SIDES, DncadRules
from .model import DeviceModel

SEVERITIES = ("error", "warn", "info")


@dataclass(frozen=True)
class Issue:
    severity: str          # error | warn | info
    code: str
    message: str
    pin: str | None = None

    def __str__(self) -> str:
        tag = {"error": "오류", "warn": "경고", "info": "참고"}[self.severity]
        where = f" [{self.pin}]" if self.pin else ""
        return f"{tag} {self.code}{where}: {self.message}"


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def _is_multiple(value: float, step: float, tol: float) -> bool:
    r = value % step
    return min(r, step - r) <= tol


def validate(model: DeviceModel, rules: DncadRules = DEFAULT_RULES) -> list[Issue]:
    out: list[Issue] = []
    add = lambda sev, code, msg, pin=None: out.append(Issue(sev, code, msg, pin))  # noqa: E731

    if not model.name.strip():
        add("warn", "W-NAME", "장치 이름이 비어 있습니다")
    b = model.body
    if not (math.isfinite(b.w) and math.isfinite(b.h)) or b.w <= 0 or b.h <= 0:
        add("error", "E-BODY", f"본체 크기가 올바르지 않습니다 ({b.w} x {b.h} mm)")
        return out
    if b.shape != "rect":
        add("warn", "W-SHAPE", f"본체 모양 '{b.shape}'은 직사각형이 아닙니다. DNCAD 블랙박스는 직사각형만 확인되었습니다")
    h_units = b.h * rules.units_per_mm
    if h_units < rules.min_height_units:
        add("warn", "W-HEIGHT", f"본체 높이 {b.h:.1f} mm({h_units:.0f} 단위)가 매우 작습니다")

    if not model.pins:
        add("error", "E-NOPIN", "접속점이 하나도 없습니다 (접속점 0개 블랙박스는 DNCAD 검증에서 막힙니다)")
        return out
    if len(model.pins) > rules.max_pins_warn:
        add("warn", "W-MANY", f"접속점이 {len(model.pins)}개로 많습니다 (기준 {rules.max_pins_warn}개)")

    seen: dict[str, int] = {}
    for p in model.pins:
        name = p.name
        if not name.strip():
            add("error", "E-NAME", "이름이 비어 있는 접속점이 있습니다")
            continue
        if name != name.strip() or "\n" in name or "\t" in name:
            add("warn", "W-NAME-SPACE", "이름에 앞뒤 공백이나 줄바꿈이 있습니다", name)
        if len(name) > rules.max_designation_len:
            add("warn", "W-NAME-LEN", f"이름이 {len(name)}자로 깁니다 (기준 {rules.max_designation_len}자)", name)
        seen[name] = seen.get(name, 0) + 1
    for name, n in seen.items():
        if n > 1 and rules.allow_duplicate_designations:
            add("warn", "W-DUP", f"접속점 이름이 {n}번 중복됩니다. 생성기 API는 거부하지만 기존 DNCAD 백업에는 이런 장치가 있습니다", name)
        elif n > 1:
            add("error", "E-DUP", f"접속점 이름이 {n}번 중복됩니다. 한 장치 안에서 유일해야 합니다 (원본 핀표 이름 대신 단자 번호를 쓰기도 합니다. 중복을 허용하려면 --allow-dup-names)", name)

    tol = rules.edge_tol_mm
    off_grid: list[str] = []
    for p in model.pins:
        if p.side not in SIDES:
            add("error", "E-SIDE", f"변 이름 '{p.side}'이 올바르지 않습니다", p.name)
            continue
        if p.side not in rules.supported_sides:
            add("warn", "W-SIDE", f"'{p.side}' 변 접속점은 DNCAD에서 확인되지 않았습니다 (실측은 위·아래 변만). 배치 명세에서 제외됩니다", p.name)
        extent = b.w if p.side in ("top", "bottom") else b.h
        off = p.offset()
        if off < -tol or off > extent + tol:
            add("error", "E-OUT", f"접속점이 본체 {p.side} 변의 범위(0~{extent:.1f} mm) 밖입니다 (거리 {off:.2f} mm)", p.name)
            continue
        dist = {"top": abs(p.y), "bottom": abs(p.y - b.h), "left": abs(p.x), "right": abs(p.x - b.w)}[p.side]
        if dist > tol:
            add("error", "E-EDGE", f"접속점이 {p.side} 변에서 {dist:.2f} mm 떨어져 있습니다 (허용 {tol} mm). 접속점은 본체 경계 위에 있어야 합니다", p.name)
        margin_lo, margin_hi = off, extent - off
        if min(margin_lo, margin_hi) < rules.min_edge_margin_mm - tol:
            add("warn", "W-MARGIN", f"모서리에서 {min(margin_lo, margin_hi):.1f} mm로 가깝습니다 (권장 {rules.min_edge_margin_mm} mm 이상)", p.name)
        if not _is_multiple(off, rules.grid_mm, 0.25):
            off_grid.append(p.name)
    if off_grid:
        shown = ", ".join(off_grid[:6]) + (" 외" if len(off_grid) > 6 else "")
        add("info", "I-GRID", f"접속점 {len(off_grid)}개의 위치가 격자({rules.grid_mm} mm)에 맞지 않습니다 ({shown}). 배치 명세에서 {rules.pin_pitch_mm} mm 슬롯으로 맞춥니다")

    for side in SIDES:
        ps = model.pins_on(side)
        groups = {p.group for p in ps}
        if len(groups) > 1:
            add("info", "I-GROUPS", f"{side} 변에 단자열이 {len(groups)}개({', '.join(sorted(g or '(없음)' for g in groups))}) 있습니다. 배치 명세는 그룹 순서대로 이어서 놓습니다")
        # 다른 단자열(group)끼리는 겹쳐 보여도 배치할 때 슬롯을 따로 받으므로 같은 그룹 안에서만 간격을 본다
        for g in groups:
            pg = [p for p in ps if p.group == g]
            _check_gaps(pg, side, rules, add)
    bb = model.graphics_bbox()
    if bb is not None:
        pad = max(b.w, b.h) * 0.25
        if bb[0] < -pad or bb[1] < -pad or bb[2] > b.w + pad or bb[3] > b.h + pad:
            add("info", "I-GFX", "외형 도형이 본체 밖으로 많이 벗어나 있습니다 (본체 위치 지정을 확인하세요)")
    return out


def _check_gaps(ps, side, rules, add) -> None:
    for a, c in zip(ps, ps[1:]):
        gap = c.offset() - a.offset()
        if gap < rules.pin_pitch_mm - 0.5 * rules.grid_mm:
            add("error" if gap < 1.0 else "warn", "E-OVERLAP" if gap < 1.0 else "W-PITCH",
                f"같은 {side} 변의 '{a.name}'와 '{c.name}' 간격이 {gap:.2f} mm로 좁습니다 (최소 {rules.pin_pitch_mm} mm). 배치 명세에서 슬롯을 나눠 펼칩니다", c.name)
