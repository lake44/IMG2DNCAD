"""접속점 수동 지정 — CSV 읽기·쓰기와, 그림 위 위치를 본체 경계 위 부착점으로 바꾸는 계산.

CSV 열 (머리글 이름은 한글 별칭도 가능, 필요한 열만 있으면 된다):
  name      접속점 이름 (필수)            side      top|bottom|left|right (상·하·좌·우)
  offset_mm 변을 따른 거리(mm)            x_mm,y_mm 본체 왼쪽 위 기준 위치(mm)
  x_px,y_px 원본 그림 위 위치(px)          kind      종류 (기본 electrical)
  label     설명                          group     단자열 이름 (예: P3). 배치 순서에 쓴다
위치는 offset_mm > (x_mm,y_mm) > (x_px,y_px) 순으로 우선한다. side가 없으면 가장 가까운 변으로 정한다.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path as FsPath

from .dncad_rules import normalize_kind, normalize_side
from .model import Pin, side_edge_point

CSV_COLUMNS = ["name", "side", "offset_mm", "x_mm", "y_mm", "x_px", "y_px", "kind", "label", "group"]

_ALIASES = {
    "name": {"name", "이름", "접속점", "접속점이름", "designation", "pin", "핀", "단자", "핀이름"},
    "side": {"side", "변", "위치", "방향", "edge"},
    "offset_mm": {"offset_mm", "offset", "오프셋", "변위치", "거리", "offsetmm"},
    "x_mm": {"x_mm", "xmm", "x(mm)"},
    "y_mm": {"y_mm", "ymm", "y(mm)"},
    "x_px": {"x_px", "xpx", "x(px)", "px_x", "x"},
    "y_px": {"y_px", "ypx", "y(px)", "px_y", "y"},
    "kind": {"kind", "type", "종류", "유형", "cp_type"},
    "label": {"label", "설명", "비고", "description", "desc"},
    "group": {"group", "그룹", "단자열", "열", "row"},
}


class PinsFileError(ValueError):
    """접속점 CSV·명세를 해석할 수 없을 때. 메시지에 줄 번호를 담는다."""


@dataclass
class PinSpec:
    name: str
    side: str | None = None
    offset_mm: float | None = None
    x_mm: float | None = None
    y_mm: float | None = None
    x_px: float | None = None
    y_px: float | None = None
    kind: str = "electrical"      # electrical · dual · connector(=female) · connector_male · connector_mf
    label: str = ""
    group: str = ""
    line: int = 0

    def to_row(self) -> list[str]:
        def f(v: float | None) -> str:
            return "" if v is None else f"{v:.4f}".rstrip("0").rstrip(".")

        return [self.name, self.side or "", f(self.offset_mm), f(self.x_mm), f(self.y_mm), f(self.x_px), f(self.y_px), self.kind if self.kind != "electrical" else "", self.label, self.group]


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise PinsFileError("파일 인코딩을 알 수 없습니다 (UTF-8 또는 CP949 필요)")


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]", "", h.strip().lower())


def _canon(header: str) -> str | None:
    h = _norm_header(header)
    for key, names in _ALIASES.items():
        if h in {_norm_header(n) for n in names}:
            return key
    return None


def _kind(s: str, line: int) -> str:
    try:
        return normalize_kind(s)
    except ValueError as e:
        raise PinsFileError(f"{line}번째 줄: {e}") from e


def _num(s: str, line: int, col: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError as e:
        raise PinsFileError(f"{line}번째 줄: '{col}' 열 값 {s!r}을(를) 숫자로 읽을 수 없습니다") from e


def parse_pins_csv(text: str) -> list[PinSpec]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise PinsFileError("접속점 CSV가 비어 있습니다")
    head = lines[0]
    delim = max((",", ";", "\t"), key=lambda d: head.count(d))
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
    rows = list(reader)
    cols = [_canon(h) for h in rows[0]]
    if "name" not in cols:
        raise PinsFileError("머리글 줄에 'name'(또는 '이름'·'접속점') 열이 없습니다")
    specs: list[PinSpec] = []
    for i, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        rec = {c: row[k] for k, c in enumerate(cols) if c and k < len(row)}
        name = rec.get("name", "").strip()
        if not name:
            raise PinsFileError(f"{i}번째 줄: 접속점 이름이 비어 있습니다")
        try:
            side = normalize_side(rec.get("side"))
        except ValueError as e:
            raise PinsFileError(f"{i}번째 줄: {e}") from e
        specs.append(
            PinSpec(
                name=name,
                side=side,
                offset_mm=_num(rec.get("offset_mm", ""), i, "offset_mm"),
                x_mm=_num(rec.get("x_mm", ""), i, "x_mm"),
                y_mm=_num(rec.get("y_mm", ""), i, "y_mm"),
                x_px=_num(rec.get("x_px", ""), i, "x_px"),
                y_px=_num(rec.get("y_px", ""), i, "y_px"),
                kind=_kind(rec.get("kind", ""), i),
                label=rec.get("label", "").strip(),
                group=rec.get("group", "").strip(),
                line=i,
            )
        )
    if not specs:
        raise PinsFileError("접속점이 한 줄도 없습니다")
    return specs


def read_pins_csv(path: str | FsPath) -> list[PinSpec]:
    return parse_pins_csv(_decode(FsPath(path).read_bytes()))


def write_pins_csv(path: str | FsPath, specs: list[PinSpec]) -> None:
    p = FsPath(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM: 엑셀에서 한글이 깨지지 않게
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for s in specs:
            w.writerow(s.to_row())


def _nearest_side(x: float, y: float, w: float, h: float) -> str:
    d = {"top": abs(y), "bottom": abs(h - y), "left": abs(x), "right": abs(w - x)}
    # 거리가 같으면 위·아래를 우선 (DNCAD가 확인한 변)
    return min(d, key=lambda k: (round(d[k], 6), 0 if k in ("top", "bottom") else 1))


def resolve_pins(
    specs: list[PinSpec],
    body_w: float,
    body_h: float,
    body_px: tuple[float, float, float, float] | None = None,
    mm_per_px: float | None = None,
) -> list[Pin]:
    """PinSpec → Pin (본체 경계 위 부착점). 해석할 수 없는 줄이 있으면 모아서 PinsFileError로 알린다."""
    pins: list[Pin] = []
    errors: list[str] = []
    for s in specs:
        where = f"{s.line}번째 줄 '{s.name}'" if s.line else f"'{s.name}'"
        src: tuple[float, float] | None = None
        if s.x_mm is not None and s.y_mm is not None:
            src = (s.x_mm, s.y_mm)
        elif s.x_px is not None and s.y_px is not None:
            if body_px is None or not mm_per_px:
                errors.append(f"{where}: px 좌표를 쓰려면 본체 위치(body)와 축척이 필요합니다")
                continue
            src = ((s.x_px - body_px[0]) * mm_per_px, (s.y_px - body_px[1]) * mm_per_px)
        side = s.side
        if side is None:
            if src is None:
                errors.append(f"{where}: side도 위치(x,y)도 없어 변을 정할 수 없습니다")
                continue
            side = _nearest_side(src[0], src[1], body_w, body_h)
        if s.offset_mm is not None:
            off = s.offset_mm
        elif src is not None:
            off = src[0] if side in ("top", "bottom") else src[1]
        else:
            errors.append(f"{where}: offset_mm 또는 위치(x,y)가 필요합니다")
            continue
        extent = body_w if side in ("top", "bottom") else body_h
        off = min(max(off, 0.0), extent)
        x, y = side_edge_point(side, off, body_w, body_h)
        pins.append(Pin(s.name, side, x, y, s.kind, s.label, s.group, src))
    if errors:
        raise PinsFileError("\n".join(errors))
    return pins


def pins_to_specs(pins: list[Pin]) -> list[PinSpec]:
    return [PinSpec(p.name, p.side, round(p.offset(), 4), None, None, None, None, p.kind, p.label, p.group) for p in pins]
