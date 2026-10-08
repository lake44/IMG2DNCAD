"""제품 정면도 풍 일러스트 합성기 — 하우징, 상·하단 단자 플러그(나사 원), 하우징에 인쇄된 핀 이름.

SVG 문자열과 정답(접속점 위치·이름)을 함께 만든다. 단위는 SVG 사용자 단위이고 1 단위 = 0.254 mm
(단자 간격 20 단위 = 5.08 mm)이다. 렌더링은 tools/make_sample_set.py가 Edge로 4배 크기로 한다.
"""
from __future__ import annotations

from dataclasses import dataclass

UNIT_MM = 0.254
PITCH = 20.0           # 단자 간격 (5.08 mm)
PLUG_H = 46.0
PLUG_STEP = 38.0       # 겹쳐 쌓인 플러그의 세로 간격
SCREW_R = 15.0


@dataclass
class Row:
    side: str                       # top | bottom
    names: list[str | None]         # None = 이름 없는(안 쓰는) 나사
    group: str = ""
    labels: list[str] | None = None  # 하우징에 인쇄할 글자(없으면 names)
    tag: str = ""                   # 플러그 표시 (예: P3)


@dataclass
class IllSpec:
    id: str
    rows: list[Row]
    width: float = 180.0
    height: float = 300.0
    housing: str = "#d9a400"
    housing_stroke: str = "#8a6a00"
    plug: str = "#26292c"
    strip: str = "#f2c01f"
    text: str = "#1b1b1b"
    decor: str = "window"
    brand: str = ""
    corner: float = 6.0


def _screw(cx: float, cy: float, k: int, plug: str) -> str:
    ang = 20 + 17 * (k % 5)
    return (
        f'<g><circle cx="{cx:.2f}" cy="{cy:.2f}" r="{SCREW_R}" fill="{plug}" stroke="#0b0c0d" stroke-width="1.6"/>'
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{SCREW_R * 0.62:.2f}" fill="#b9bcc0" stroke="#7d8084" stroke-width="0.8"/>'
        f'<rect x="{cx - SCREW_R * 0.55:.2f}" y="{cy - SCREW_R * 0.09:.2f}" width="{SCREW_R * 1.1:.2f}" height="{SCREW_R * 0.18:.2f}" fill="#4d5156" transform="rotate({ang} {cx:.2f} {cy:.2f})"/></g>'
    )


def build(spec: IllSpec) -> tuple[str, dict]:
    """(svg 문자열, 정답 사전). 정답: body(svg 단위 x0,y0,x1,y1), pins[{name,side,x,y,group}], size(w,h)."""
    top_rows = [r for r in spec.rows if r.side == "top"]
    bot_rows = [r for r in spec.rows if r.side == "bottom"]
    mx = my = 14.0
    top_ext = PLUG_STEP * max(0, len(top_rows) - 1) + PLUG_H if top_rows else 0.0
    bot_ext = PLUG_STEP * max(0, len(bot_rows) - 1) + PLUG_H if bot_rows else 0.0
    hx, hy = mx, my + (top_ext - 10 if top_rows else 0.0)
    W, H = spec.width, spec.height
    total_h = (hy - my) + H + (bot_ext - 10 if bot_rows else 0.0)
    cw, ch = W + 2 * mx, total_h + 2 * my
    out: list[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{cw:.0f}" height="{ch:.0f}" viewBox="0 0 {cw:.2f} {ch:.2f}">']
    pins: list[dict] = []

    def plug(row: Row, top: float, idx: int) -> None:
        n = len(row.names)
        x0 = hx + (W - (n - 1) * PITCH) / 2
        px0, px1 = x0 - 14, x0 + (n - 1) * PITCH + 14
        out.append(f'<rect x="{px0:.2f}" y="{top:.2f}" width="{px1 - px0:.2f}" height="{PLUG_H}" rx="4" fill="{spec.plug}" stroke="#0b0c0d" stroke-width="1.2"/>')
        for q in range(n):  # 플러그 위의 작은 사각 구멍
            out.append(f'<rect x="{x0 + q * PITCH - 3:.2f}" y="{top + 5 if row.side == "top" else top + PLUG_H - 11:.2f}" width="6" height="6" fill="#0c0d0e"/>')
        cy = top + PLUG_H / 2
        for q, nm in enumerate(row.names):
            cx = x0 + q * PITCH
            out.append(_screw(cx, cy, q + idx, spec.plug))
            if nm:
                pins.append({"name": nm, "side": row.side, "x": cx, "y": cy, "group": row.group or f"{row.side}{idx}"})
        if row.tag:
            ty = top + 13 if row.side == "top" else top + PLUG_H - 4
            out.append(f'<text x="{(px0 + px1) / 2:.2f}" y="{ty:.2f}" font-size="8" text-anchor="middle" fill="#9aa0a6" font-family="Arial, sans-serif">{row.tag}</text>')

    # 하우징을 먼저 그리고 그 위에 플러그를 얹는다(플러그가 하우징 가장자리를 10 단위 덮는다)
    out.append(f'<rect x="{hx:.2f}" y="{hy:.2f}" width="{W}" height="{H}" rx="{spec.corner}" fill="{spec.housing}" stroke="{spec.housing_stroke}" stroke-width="1.5"/>')
    for i, r in enumerate(top_rows):
        plug(r, my + i * PLUG_STEP, i)
    for j, r in enumerate(bot_rows):
        plug(r, hy + H - 10 + j * PLUG_STEP, j)

    # 인쇄된 핀 이름 띠
    for i, r in enumerate(top_rows):
        sy = hy + 12 + i * 28
        _strip(out, spec, r, hx, W, sy)
    for j, r in enumerate(bot_rows):
        sy = hy + H - 30 - j * 28
        _strip(out, spec, r, hx, W, sy)

    # 장식
    mid = hy + H / 2
    if spec.decor in ("window", "window+led"):
        out.append(f'<rect x="{hx + W * 0.22:.2f}" y="{mid - 8:.2f}" width="{W * 0.56:.2f}" height="34" rx="2" fill="#f4f4f4" stroke="#c9c9c9" stroke-width="1"/>')
    if spec.decor in ("led", "window+led"):
        for k, c in enumerate(("#3ddc4a", "#e7a700", "#d83a3a")):
            out.append(f'<rect x="{hx + 16:.2f}" y="{mid - 40 + k * 16:.2f}" width="12" height="9" rx="1.5" fill="{c}" stroke="#2c2c2c" stroke-width="0.8"/>')
            out.append(f'<text x="{hx + 34:.2f}" y="{mid - 32 + k * 16:.2f}" font-size="8" fill="{spec.text}" font-family="Arial, sans-serif">{("POWER", "CH.1", "FAULT")[k]}</text>')
    if spec.brand:
        out.append(f'<text x="{hx + W / 2:.2f}" y="{mid - 24:.2f}" font-size="15" font-weight="bold" text-anchor="middle" fill="{spec.text}" font-family="Arial, sans-serif">{spec.brand}</text>')
    out.append("</svg>")

    body = (hx, my, hx + W, total_h + my)
    gt = {"body": body, "pins": pins, "size": (cw, ch), "unit_mm": UNIT_MM}
    return "\n".join(out), gt


def _strip(out: list[str], spec: IllSpec, r: Row, hx: float, W: float, sy: float) -> None:
    n = len(r.names)
    x0 = hx + (W - (n - 1) * PITCH) / 2
    out.append(f'<rect x="{hx + 6:.2f}" y="{sy:.2f}" width="{W - 12:.2f}" height="18" rx="2" fill="{spec.strip}" stroke="{spec.text}" stroke-width="0.9"/>')
    labels = r.labels if r.labels is not None else [nm or "" for nm in r.names]
    for q, lb in enumerate(labels):
        if lb:
            out.append(f'<text x="{x0 + q * PITCH:.2f}" y="{sy + 13:.2f}" font-size="{10 if len(lb) <= 3 else 8}" text-anchor="middle" fill="{spec.text}" font-family="Arial, sans-serif">{lb}</text>')


# ============================================================================== 장치 6종


def device_specs() -> list[IllSpec]:
    return [
        IllSpec("ill_relay_din", [
            Row("top", ["A1", "11", "21", "31"], "T"),
            Row("bottom", ["A2", "12", "22", "32"], "B"),
        ], width=130, height=270, housing="#5b6770", housing_stroke="#2f383e", strip="#e8edf0", decor="window+led", brand="RELAY 4C"),
        IllSpec("ill_safety_relay", [
            Row("top", ["A1", None, "S31", "S32", "13", "23", "33", "41"], "P3", tag="P3"),
            Row("top", ["S11", "S12", "S13", "S14", "S21", "S22", "S33", "S34"], "P4", tag="P4"),
            Row("bottom", ["A2", "Y30", "Y31", "Y32", "14", "24", "34", "42"], "P4b", tag="P4"),
        ], width=190, height=330, housing="#f2c01f", housing_stroke="#8c6e00", strip="#f7d44a", decor="window+led", brand="SAFETY X3"),
        IllSpec("ill_plc_di_card", [
            Row("top", ["DI0", "DI1", "DI2", "DI3", "DI4", "DI5", "DI6", "DI7"], "DI-A"),
            Row("bottom", ["DI8", "DI9", "DI10", "DI11", "DI12", "DI13", "DI14", "COM"], "DI-B"),
        ], width=190, height=300, housing="#8d949a", housing_stroke="#4c5256", plug="#1f2225", strip="#d9dde0", decor="led", brand="DI 16"),
        IllSpec("ill_psu", [
            Row("top", ["L", "N", "PE"], "AC"),
            Row("bottom", ["+V1", "+V2", "-V1", "-V2"], "DC"),
        ], width=120, height=290, housing="#2f6fb4", housing_stroke="#16406d", strip="#dbe8f6", decor="led", brand="24V 5A"),
        IllSpec("ill_contactor", [
            Row("top", ["A1", "1/L1", "3/L2", "5/L3"], "T"),
            Row("bottom", ["A2", "2/T1", "4/T2", "6/T3"], "B"),
        ], width=130, height=250, housing="#3a3d40", housing_stroke="#15171a", plug="#16181a", strip="#c9ccd0", text="#101010", decor="window", brand="MC-18"),
        IllSpec("ill_io_module", [
            Row("top", ["X1", "X2", "X3", "X4", "X5", "X6"], "R1"),
            Row("top", ["X7", "X8", "X9", "X10", "X11", "X12"], "R2"),
            Row("bottom", ["Y1", "Y2", "Y3", "Y4", "Y5", "Y6"], "R3"),
        ], width=150, height=300, housing="#eceff1", housing_stroke="#9aa4aa", plug="#34383c", strip="#ffffff", decor="window+led", brand="I/O 18"),
    ]
