"""주석 SVG — 장치 모델을 SVG로 쓰고(Inkscape 등에서 고칠 수 있게) 다시 읽는다.

SVG 좌표 = 모델 좌표(mm, 본체 왼쪽 위가 원점, y는 아래)이고 viewBox 원점만 여백만큼 음수로 둔다.
요소의 역할은 data-role로 표시한다:
  body   본체 사각형 (읽을 때 이 사각형의 왼쪽 위가 원점이 된다 → 통째로 옮겨도 안전)
  graphics  외형 도형 그룹            pins / pin  접속점 (data-name·data-side·data-group·data-kind·data-label)
  texts / text  글자
읽을 때는 조상의 transform을 모두 합성해 적용한다 (이동·확대·회전·뒤집기 지원).
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path as FsPath
from xml.sax.saxutils import escape, quoteattr

import numpy as np

from .dncad_rules import SIDES
from .geom import ArcSeg, Circle, LineSeg, Path, Primitive
from .model import Body, DeviceModel, Pin, TextItem, side_edge_point
from .svg import path_d

NS = "http://www.w3.org/2000/svg"
Matrix = tuple[float, float, float, float, float, float]  # (a, b, c, d, e, f)
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class SvgModelError(ValueError):
    pass


def _f(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


# =============================================================================== 쓰기


def _extent(model: DeviceModel, pad: float) -> tuple[float, float, float, float]:
    """본체·외형·접속점(부착점과 원본 위치)·글자를 모두 담는 (minx, miny, maxx, maxy)."""
    xs = [0.0, model.body.w]
    ys = [0.0, model.body.h]
    bb = model.graphics_bbox()
    if bb:
        xs += [bb[0], bb[2]]
        ys += [bb[1], bb[3]]
    for p in model.pins:
        xs.append(p.x)
        ys.append(p.y)
        if p.src is not None:
            xs.append(p.src[0])
            ys.append(p.src[1])
    for t in model.texts:
        xs.append(t.x)
        ys.append(t.y)
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def model_to_svg(model: DeviceModel, margin: float | None = None, show_src: bool = True) -> str:
    """margin=None이면 모든 요소가 들어가도록 여백(최소 6 mm)을 자동으로 잡는다."""
    b = model.body
    if margin is None:
        x0, y0, x1, y1 = _extent(model, 4.0)
        x0, y0, x1, y1 = min(x0, -6.0), min(y0, -6.0), max(x1, b.w + 6.0), max(y1, b.h + 6.0)
    else:
        x0, y0, x1, y1 = -margin, -margin, b.w + margin, b.h + margin
    W, H = x1 - x0, y1 - y0
    pin_r = 0.9
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="{NS}" width="{_f(W)}mm" height="{_f(H)}mm" viewBox="{_f(x0)} {_f(y0)} {_f(W)} {_f(H)}" data-imgvec="device-model/1.0">',
        f"  <title>{escape(model.name)}</title>",
        f'  <rect data-role="body" x="0" y="0" width="{_f(b.w)}" height="{_f(b.h)}" fill="#fffbe6" stroke="#222" stroke-width="0.35"/>',
        '  <g data-role="graphics" fill="none" stroke="#7a7a7a" stroke-width="0.2" stroke-linecap="round" stroke-linejoin="round">',
    ]
    for p in model.graphics:
        if isinstance(p, Circle):
            out.append(f'    <circle cx="{_f(p.cx)}" cy="{_f(p.cy)}" r="{_f(p.r)}"/>')
        else:
            d = path_d(p)
            if d:
                out.append(f'    <path d="{d}"/>')
    out.append("  </g>")
    out.append('  <g data-role="pins">')
    for p in model.pins:
        attrs = f'data-role="pin" data-name={quoteattr(p.name)} data-side="{p.side}" data-kind={quoteattr(p.kind)}'
        if p.group:
            attrs += f" data-group={quoteattr(p.group)}"
        if p.label:
            attrs += f" data-label={quoteattr(p.label)}"
        if p.src is not None:
            attrs += f' data-src-x="{_f(p.src[0])}" data-src-y="{_f(p.src[1])}"'
        out.append(f"    <g {attrs}>")
        if show_src and p.src is not None:
            out.append(f'      <path data-role="pin-src" d="M{_f(p.src[0])} {_f(p.src[1])}L{_f(p.x)} {_f(p.y)}" fill="none" stroke="#d33" stroke-width="0.12" stroke-dasharray="0.5 0.5"/>')
        out.append(f'      <circle cx="{_f(p.x)}" cy="{_f(p.y)}" r="{pin_r}" fill="#d33" fill-opacity="0.85" stroke="#900" stroke-width="0.15"/>')
        tx, ty, anchor = {
            "top": (p.x, p.y - 1.6, "middle"),
            "bottom": (p.x, p.y + 3.2, "middle"),
            "left": (p.x - 1.6, p.y + 0.7, "end"),
            "right": (p.x + 1.6, p.y + 0.7, "start"),
        }[p.side]
        out.append(f'      <text x="{_f(tx)}" y="{_f(ty)}" font-size="2.2" text-anchor="{anchor}" fill="#900" font-family="sans-serif">{escape(p.name)}</text>')
        out.append("    </g>")
    out.append("  </g>")
    if model.texts:
        out.append('  <g data-role="texts" fill="#333" font-family="sans-serif">')
        for t in model.texts:
            rot = f' transform="rotate({_f(t.rotation)} {_f(t.x)} {_f(t.y)})"' if t.rotation else ""
            out.append(f'    <text data-role="text" x="{_f(t.x)}" y="{_f(t.y)}" font-size="{_f(t.height)}"{rot}>{escape(t.value)}</text>')
        out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_model_svg(path: str | FsPath, model: DeviceModel, **kw) -> None:
    p = FsPath(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(model_to_svg(model, **kw), encoding="utf-8")


# =============================================================================== 행렬


def _mul(m: Matrix, n: Matrix) -> Matrix:
    """m x n (n을 먼저 적용하고 m을 나중에)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def _apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def parse_transform(s: str | None) -> Matrix:
    if not s:
        return IDENTITY
    m = IDENTITY
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", s):
        v = [float(t) for t in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", args)]
        if name == "matrix" and len(v) == 6:
            t: Matrix = (v[0], v[1], v[2], v[3], v[4], v[5])
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            a = math.radians(v[0])
            ca, sa = math.cos(a), math.sin(a)
            t = (ca, sa, -sa, ca, 0, 0)
            if len(v) >= 3:
                t = _mul(_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            raise SvgModelError(f"해석할 수 없는 transform: {name}({args})")
        m = _mul(m, t)
    return m


def _similarity(m: Matrix) -> tuple[float, float, int] | None:
    """등방 변환(이동·회전·균등 확대·뒤집기)이면 (배율, 회전각 rad, det 부호), 아니면 None."""
    a, b, c, d, _, _ = m
    s1, s2 = math.hypot(a, b), math.hypot(c, d)
    if s1 < 1e-12 or abs(s1 - s2) > 1e-6 * max(s1, s2) or abs(a * c + b * d) > 1e-6 * s1 * s2:
        return None
    det = a * d - b * c
    return s1, math.atan2(b, a), 1 if det > 0 else -1


def _unit_scale(root: ET.Element) -> float:
    """사용자 단위 1이 몇 mm인지."""
    vb = root.get("viewBox")
    w = root.get("width")
    if not vb:
        return 25.4 / 96.0 if w and not w.strip().endswith("mm") else 1.0
    vbw = float(re.split(r"[ ,]+", vb.strip())[2])
    if not w:
        return 1.0
    mm = re.fullmatch(r"\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*(mm|cm|in|px|pt|pc)?\s*", w)
    if not mm:
        return 1.0
    val, unit = float(mm.group(1)), mm.group(2) or "px"
    per = {"mm": 1.0, "cm": 10.0, "in": 25.4, "px": 25.4 / 96.0, "pt": 25.4 / 72.0, "pc": 25.4 / 6.0}[unit]
    return (val * per) / vbw


# =============================================================================== 경로 해석


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _tokens(d: str):
    for m in re.finditer(rf"([MmLlHhVvCcSsQqTtAaZz])|({_NUM})", d):
        yield (m.group(1), None) if m.group(1) else (None, float(m.group(2)))


def _arc_center(p0, p1, rx, ry, phi_deg, large, sweep):
    """SVG 호 끝점 표기 → (cx, cy, rx, ry, a0, sweep rad). 구현 노트 F.6.5."""
    (x1, y1), (x2, y2) = p0, p1
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return None
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p**2 / rx**2 + y1p**2 / ry**2
    if lam > 1:
        k = math.sqrt(lam)
        rx, ry = rx * k, ry * k
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    coef = math.sqrt(max(0.0, num / den)) * (1 if large != sweep else -1)
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    return cx, cy, rx, ry, th1, dth


def _flatten_curve(pts: list[tuple[float, float]], n: int = 16) -> list[tuple[float, float]]:
    """2차·3차 베지어를 직선 n개로."""
    t = np.linspace(0, 1, n + 1)[1:]
    out = []
    if len(pts) == 3:
        (x0, y0), (x1, y1), (x2, y2) = pts
        for u in t:
            out.append(((1 - u) ** 2 * x0 + 2 * (1 - u) * u * x1 + u**2 * x2, (1 - u) ** 2 * y0 + 2 * (1 - u) * u * y1 + u**2 * y2))
    else:
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
        for u in t:
            w0, w1, w2, w3 = (1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u**2, u**3
            out.append((w0 * x0 + w1 * x1 + w2 * x2 + w3 * x3, w0 * y0 + w1 * y1 + w2 * y2 + w3 * y3))
    return out


def _parse_path(d: str) -> list[list[tuple]]:
    """경로 → 하위경로 목록. 각 하위경로는 [("L", p0, p1) | ("A", p0, p1, rx, ry, phi, large, sweep)] 와 닫힘 표시."""
    toks = list(_tokens(d))
    i = 0
    subpaths: list[list[tuple]] = []
    cur: list[tuple] = []
    pos = (0.0, 0.0)
    start = (0.0, 0.0)
    cmd = ""
    last_ctrl = None

    def nums(k: int) -> list[float]:
        nonlocal i
        vals = []
        for _ in range(k):
            if i >= len(toks) or toks[i][1] is None:
                raise SvgModelError(f"경로 데이터 오류 ({cmd}): 숫자가 모자랍니다")
            vals.append(toks[i][1])
            i += 1
        return vals

    while i < len(toks):
        c, v = toks[i]
        if c:
            cmd = c
            i += 1
            if cmd in "Zz":
                if cur:
                    if pos != start:
                        cur.append(("L", pos, start))
                    cur.append(("Z",))
                    subpaths.append(cur)
                    cur = []
                pos = start
                continue
        elif not cmd:
            raise SvgModelError("경로 데이터가 명령 없이 시작합니다")
        rel = cmd.islower()
        C = cmd.upper()
        if C == "M":
            x, y = nums(2)
            if rel:
                x, y = pos[0] + x, pos[1] + y
            if cur:
                subpaths.append(cur)
                cur = []
            pos = start = (x, y)
            cmd = "l" if rel else "L"  # 이어지는 좌표쌍은 직선
        elif C == "L":
            x, y = nums(2)
            if rel:
                x, y = pos[0] + x, pos[1] + y
            cur.append(("L", pos, (x, y)))
            pos = (x, y)
        elif C == "H":
            (x,) = nums(1)
            x = pos[0] + x if rel else x
            cur.append(("L", pos, (x, pos[1])))
            pos = (x, pos[1])
        elif C == "V":
            (y,) = nums(1)
            y = pos[1] + y if rel else y
            cur.append(("L", pos, (pos[0], y)))
            pos = (pos[0], y)
        elif C == "A":
            rx, ry, phi, la, sw, x, y = nums(7)
            if rel:
                x, y = pos[0] + x, pos[1] + y
            cur.append(("A", pos, (x, y), rx, ry, phi, int(la) != 0, int(sw) != 0))
            pos = (x, y)
        elif C in ("C", "S", "Q", "T"):
            if C == "C":
                x1, y1, x2, y2, x, y = nums(6)
                if rel:
                    x1, y1, x2, y2, x, y = (pos[0] + x1, pos[1] + y1, pos[0] + x2, pos[1] + y2, pos[0] + x, pos[1] + y)
                ctrl = [pos, (x1, y1), (x2, y2), (x, y)]
                last_ctrl = (x2, y2)
            elif C == "S":
                x2, y2, x, y = nums(4)
                if rel:
                    x2, y2, x, y = pos[0] + x2, pos[1] + y2, pos[0] + x, pos[1] + y
                x1, y1 = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_ctrl else pos
                ctrl = [pos, (x1, y1), (x2, y2), (x, y)]
                last_ctrl = (x2, y2)
            elif C == "Q":
                x1, y1, x, y = nums(4)
                if rel:
                    x1, y1, x, y = pos[0] + x1, pos[1] + y1, pos[0] + x, pos[1] + y
                ctrl = [pos, (x1, y1), (x, y)]
                last_ctrl = (x1, y1)
            else:
                x, y = nums(2)
                if rel:
                    x, y = pos[0] + x, pos[1] + y
                x1, y1 = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_ctrl else pos
                ctrl = [pos, (x1, y1), (x, y)]
                last_ctrl = (x1, y1)
            p = pos
            for q in _flatten_curve(ctrl):
                cur.append(("L", p, q))
                p = q
            pos = (x, y)
            continue
        last_ctrl = None
    if cur:
        subpaths.append(cur)
    return subpaths


# =============================================================================== 도형 변환


def _transform_arc(cx, cy, r, a0, sweep, m: Matrix):
    sim = _similarity(m)
    if sim is None:
        return None
    s, th, sign = sim
    ncx, ncy = _apply(m, cx, cy)
    return ArcSeg(ncx, ncy, r * s, th + sign * a0, th + sign * (a0 + sweep))


def _segments_to_prims(subpaths: list[list[tuple]], m: Matrix) -> list[Primitive]:
    out: list[Primitive] = []
    for sp in subpaths:
        segs = []
        closed = bool(sp and sp[-1] == ("Z",))
        for it in sp:
            if it[0] == "L":
                p0, p1 = _apply(m, *it[1]), _apply(m, *it[2])
                if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 1e-9:
                    segs.append(LineSeg(p0, p1))
            elif it[0] == "A":
                _, p0, p1, rx, ry, phi, la, sw = it
                geo = _arc_center(p0, p1, rx, ry, phi, la, sw)
                if geo is None:
                    segs.append(LineSeg(_apply(m, *p0), _apply(m, *p1)))
                    continue
                cx, cy, rx2, ry2, th1, dth = geo
                arc = _transform_arc(cx, cy, rx2, th1, dth, m) if abs(rx2 - ry2) < 1e-6 * max(rx2, 1) and abs(phi) < 1e-9 else None
                if arc is not None:
                    segs.append(arc)
                else:  # 타원·비등방 변환: 직선으로 근사
                    n = max(8, int(abs(dth) / 0.2))
                    prev = _apply(m, *p0)
                    for k in range(1, n + 1):
                        a = th1 + dth * k / n
                        q = _apply(m, cx + rx2 * math.cos(a), cy + ry2 * math.sin(a))
                        segs.append(LineSeg(prev, q))
                        prev = q
        if segs:
            out.append(Path(segs, closed))
    return out


def _rect_prims(x, y, w, h, rx, ry, m: Matrix) -> list[Primitive]:
    if rx <= 0 and ry <= 0:
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        q = [_apply(m, *p) for p in pts]
        return [Path([LineSeg(q[i], q[(i + 1) % 4]) for i in range(4)], True)]
    r = min(max(rx, ry), w / 2, h / 2)
    d = (
        f"M{x + r} {y}H{x + w - r}A{r} {r} 0 0 1 {x + w} {y + r}V{y + h - r}A{r} {r} 0 0 1 {x + w - r} {y + h}"
        f"H{x + r}A{r} {r} 0 0 1 {x} {y + h - r}V{y + r}A{r} {r} 0 0 1 {x + r} {y}Z"
    )
    return _segments_to_prims(_parse_path(d), m)


def _fl(el: ET.Element, key: str, default: float = 0.0) -> float:
    v = el.get(key)
    if v is None:
        return default
    m = re.match(_NUM, v.strip())
    return float(m.group(0)) if m else default


def _element_prims(el: ET.Element, m: Matrix) -> list[Primitive]:
    tag = el.tag.split("}")[-1]
    if tag == "circle":
        cx, cy = _apply(m, _fl(el, "cx"), _fl(el, "cy"))
        sim = _similarity(m)
        if sim is None:
            return []
        return [Circle(cx, cy, _fl(el, "r") * sim[0])]
    if tag == "ellipse":
        rx, ry = _fl(el, "rx"), _fl(el, "ry")
        sim = _similarity(m)
        if sim is None or abs(rx - ry) > 1e-6 * max(rx, 1):
            return []
        cx, cy = _apply(m, _fl(el, "cx"), _fl(el, "cy"))
        return [Circle(cx, cy, rx * sim[0])]
    if tag == "rect":
        return _rect_prims(_fl(el, "x"), _fl(el, "y"), _fl(el, "width"), _fl(el, "height"), _fl(el, "rx"), _fl(el, "ry"), m)
    if tag == "line":
        p0, p1 = _apply(m, _fl(el, "x1"), _fl(el, "y1")), _apply(m, _fl(el, "x2"), _fl(el, "y2"))
        return [Path([LineSeg(p0, p1)])]
    if tag in ("polyline", "polygon"):
        nums = [float(t) for t in re.findall(_NUM, el.get("points", ""))]
        pts = [_apply(m, nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        if len(pts) < 2:
            return []
        closed = tag == "polygon"
        n = len(pts)
        segs = [LineSeg(pts[i], pts[(i + 1) % n]) for i in range(n if closed else n - 1)]
        return [Path(segs, closed)]
    if tag == "path":
        return _segments_to_prims(_parse_path(el.get("d", "")), m)
    return []


# =============================================================================== 읽기


def _walk(el: ET.Element, m: Matrix, role_stack: tuple[str, ...]):
    mm = _mul(m, parse_transform(el.get("transform")))
    role = el.get("data-role")
    stack = role_stack + (role,) if role else role_stack
    yield el, mm, stack
    for ch in el:
        yield from _walk(ch, mm, stack)


def svg_to_model(source: str | FsPath, name: str | None = None) -> DeviceModel:
    p = FsPath(source) if not (isinstance(source, str) and source.lstrip().startswith("<")) else None
    try:
        root = ET.parse(p).getroot() if p else ET.fromstring(source)  # type: ignore[arg-type]
    except ET.ParseError as e:
        raise SvgModelError(f"SVG를 읽을 수 없습니다: {e}") from e
    unit = _unit_scale(root)
    items = list(_walk(root, IDENTITY, ()))

    body_items = [(el, m) for el, m, st in items if el.get("data-role") == "body"]
    if not body_items:
        raise SvgModelError('data-role="body" 사각형을 찾을 수 없습니다 (본체 사각형에 이 속성이 있어야 원점을 정할 수 있습니다)')
    bel, bm = body_items[0]
    bx, by, bw, bh = _fl(bel, "x"), _fl(bel, "y"), _fl(bel, "width"), _fl(bel, "height")
    corners = [_apply(bm, bx, by), _apply(bm, bx + bw, by), _apply(bm, bx + bw, by + bh), _apply(bm, bx, by + bh)]
    ox, oy = min(c[0] for c in corners), min(c[1] for c in corners)
    W = (max(c[0] for c in corners) - ox) * unit
    H = (max(c[1] for c in corners) - oy) * unit
    if W <= 0 or H <= 0:
        raise SvgModelError("본체 사각형의 크기가 0입니다")

    def to_model(x: float, y: float) -> tuple[float, float]:
        return (x - ox) * unit, (y - oy) * unit

    def prim_to_model(pr: Primitive) -> Primitive:
        from .geom import scale_primitive

        return scale_primitive(pr, unit, -ox * unit, -oy * unit)

    has_gfx = any(st and "graphics" in st for _, _, st in items)
    graphics: list[Primitive] = []
    for el, m, st in items:
        if el.get("data-role") in ("body", "pin-src") or any(r in ("pins", "pin", "texts", "text", "body", "pin-src") for r in st):
            continue
        if has_gfx and "graphics" not in st:
            continue
        for pr in _element_prims(el, m):
            graphics.append(prim_to_model(pr))

    pins: list[Pin] = []
    for el, m, _st in items:
        if el.get("data-role") != "pin":
            continue
        circle = el if el.tag.endswith("circle") else None
        cm = m
        if circle is None:
            for ch_el, ch_m, _ in _walk(el, m, ()):
                if ch_el is not el and ch_el.tag.endswith("circle"):
                    circle, cm = ch_el, ch_m
                    break
        if circle is None:
            raise SvgModelError(f"접속점 '{el.get('data-name', '?')}'에 원(circle)이 없습니다")
        px, py = to_model(*_apply(cm, _fl(circle, "cx"), _fl(circle, "cy")))
        pname = el.get("data-name") or "".join(t.text or "" for t in el.iter() if t.tag.endswith("text")).strip()
        if not pname:
            raise SvgModelError("이름(data-name)이 없는 접속점이 있습니다")
        side = el.get("data-side", "")
        dists = {"top": abs(py), "bottom": abs(py - H), "left": abs(px), "right": abs(px - W)}
        nearest = min(dists, key=lambda k: (round(dists[k], 4), 0 if k in ("top", "bottom") else 1))
        if side not in SIDES or dists[side] > 0.5:
            side = nearest  # 사용자가 옮겨 놓았으면 가장 가까운 변으로 다시 정한다
        off = px if side in ("top", "bottom") else py
        x, y = side_edge_point(side, min(max(off, 0.0), W if side in ("top", "bottom") else H), W, H)
        src = None
        if el.get("data-src-x") is not None and el.get("data-src-y") is not None:
            src = (float(el.get("data-src-x")), float(el.get("data-src-y")))  # type: ignore[arg-type]
        pins.append(Pin(pname, side, x, y, el.get("data-kind", "electrical"), el.get("data-label", ""), el.get("data-group", ""), src))

    texts: list[TextItem] = []
    for el, m, _st in items:
        if el.get("data-role") == "text":
            tx, ty = to_model(*_apply(m, _fl(el, "x"), _fl(el, "y")))
            texts.append(TextItem("".join(el.itertext()).strip(), tx, ty, _fl(el, "font-size", 2.5) * unit))

    title = next((t.text.strip() for t in root.iter() if t.tag.endswith("title") and t.text), "")
    model = DeviceModel(name or title or "device", Body(W, H), pins, graphics, texts)
    model.meta["source_svg"] = str(p) if p else "(문자열)"
    return model
