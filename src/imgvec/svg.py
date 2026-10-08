"""SVG 출력. 좌표 단위는 mm(축척을 알 때) 또는 원본 px이며 y는 아래로 증가한다."""
from __future__ import annotations

import math
from pathlib import Path as FsPath
from xml.sax.saxutils import escape

from .geom import Circle, LineSeg, Primitive, scale_primitive
from .geom import Path as GPath


def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def path_d(path: GPath) -> str:
    if not path.segs:
        return ""
    cur = path.segs[0].start
    out = [f"M{_f(cur[0])} {_f(cur[1])}"]
    for g in path.segs:
        st = g.start
        if math.hypot(cur[0] - st[0], cur[1] - st[1]) > 1e-6:
            out.append(f"L{_f(st[0])} {_f(st[1])}")
        en = g.end
        if isinstance(g, LineSeg):
            out.append(f"L{_f(en[0])} {_f(en[1])}")
        else:
            large = 1 if abs(g.sweep) > math.pi else 0
            sweep = 1 if g.sweep > 0 else 0
            out.append(f"A{_f(g.r)} {_f(g.r)} 0 {large} {sweep} {_f(en[0])} {_f(en[1])}")
        cur = en
    if path.closed:
        out.append("Z")
    return "".join(out)


def build_svg(
    prims: list[Primitive],
    width_px: int,
    height_px: int,
    mm_per_px: float | None,
    stroke_mm: float = 0.25,
    desc: str = "",
) -> str:
    s = mm_per_px if mm_per_px else 1.0
    W, H = width_px * s, height_px * s
    unit = "mm" if mm_per_px else ""
    sw = stroke_mm if mm_per_px else max(1.0, stroke_mm)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(W)}{unit}" height="{_f(H)}{unit}" viewBox="0 0 {_f(W)} {_f(H)}">',
    ]
    if desc:
        lines.append(f"  <desc>{escape(desc)}</desc>")
    lines.append(f'  <g fill="none" stroke="#000000" stroke-width="{_f(sw)}" stroke-linecap="round" stroke-linejoin="round">')
    for p in prims:
        q = scale_primitive(p, s)
        if isinstance(q, Circle):
            lines.append(f'    <circle cx="{_f(q.cx)}" cy="{_f(q.cy)}" r="{_f(q.r)}"/>')
        else:
            d = path_d(q)
            if d:
                lines.append(f'    <path d="{d}"/>')
    lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def write_svg(path: str | FsPath, svg_text: str) -> None:
    p = FsPath(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(svg_text, encoding="utf-8")
