"""그림에서 본체 사각형과 접속점 자리를 **자동으로** 찾는다 (4단계).

사람이 클릭해 주던 것(`imgvec device pick`)을 그림만 보고 짐작한다. 결과는 명세(device-spec)의 초안이고,
맞지 않으면 사람이 고친다 — 이 모듈은 사람의 일을 줄일 뿐 대신하지 않는다.

무엇을 단서로 삼나
  본체  : 조각난 변을 공선끼리 모아, 네 변이 충분히 '덮인' 사각형 중 가장 큰 것.
          (선 그림의 본체 변은 단자 줄기에 잘려 여러 토막이 된다 — 토막을 합쳐야 보인다)
  접속점: 변을 따라 **같은 거리에 등간격으로 늘어선 같은 꼴**을 찾는다. 꼴의 서열은
          원(circle) · 변에 붙은 줄기의 바깥 끝(stub) > 둥근 색 덩이(blob) > 글자 획(glyph).
          윗 단서가 둘 이상 있으면 아랫 단서는 버린다 — 글자가 접속점으로 둔갑하는 것을 막는다.

실측 성능은 docs/자동-인식-성능.md (샘플 20종, `imgvec device autotest`로 다시 잴 수 있다).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from .geom import Circle, LineSeg, Primitive

SIDES = ("top", "bottom", "left", "right")
# 단서의 서열. 큰 쪽이 믿을 만하다.
KIND_WEIGHT = {"circle": 1.6, "dot": 1.55, "stub": 1.5, "blob": 1.0, "glyph": 0.3}
# 점(●)이 둘 이상이면 그것만 쓴다 — 점으로 단자를 그린 그림에서 변선 모서리·교차가 섞이는 것을 막는다
TIERS = (("dot",), ("circle", "dot", "stub"), ("circle", "dot", "stub", "blob"))
DOT_BAND = 4         # 변선 둘레 이만큼(px)을 보고 두께를 잰다
DOT_RISE = 2.2       # 두께가 변선 보통 두께의 이만큼을 넘으면 점 단자로 본다
DOT_MIN_WIDTH = 4    # 봉우리가 이만큼은 이어져야 한다 (px)
SOFT_MERGE_RATIO = 0.06      # 색 덩이를 합칠 반경 (본체 짧은 변 대비). 접속점 간격보다 작아야 한다
ROUNDNESS_MIN = 0.35         # 이보다 둥글면 단자(blob), 아니면 글자 획(glyph)으로 본다
SECOND_ROW_FRAC = 0.5        # 둘째 줄로 인정할 점수 (첫 줄 대비)
SECOND_ROW_PITCH_TOL = 0.15  # 둘째 줄의 간격이 첫 줄과 이만큼 안에서 같아야 한다
LATTICE_MIN_RATIO = 0.015    # 격자로 인정할 최소 크기 (변 길이 대비). 잘면 아무 간격이나 배수로 맞는다
LATTICE_TOL = 0.15           # 간격이 격자의 정수배라고 볼 오차
OCCUPANCY_BASE = 0.4         # 격자 자리가 듬성듬성해도 이만큼은 인정한다
MAXD_RATIO = 0.45            # 변에서 이만큼(그 변에 수직인 본체 치수 대비) 깊이까지 접속점을 찾는다

Box = tuple[float, float, float, float]


# =============================================================================== 본체
def _axis_lines(prims: Iterable[Primitive], slope_tol: float = 0.03) -> tuple[dict, dict]:
    """수평·수직에 가까운 선분을 좌표별로 모은다 → ({y: [(x0, x1)…]}, {x: [(y0, y1)…]})"""
    hor: dict[float, list[tuple[float, float]]] = {}
    ver: dict[float, list[tuple[float, float]]] = {}
    for p in prims:
        if isinstance(p, Circle):
            continue
        for g in p.segs:
            if not isinstance(g, LineSeg):
                continue
            (ax, ay), (bx, by) = g.p0, g.p1
            dx, dy = abs(ax - bx), abs(ay - by)
            if dy <= slope_tol * dx:
                hor.setdefault(round(0.5 * (ay + by), 1), []).append((min(ax, bx), max(ax, bx)))
            elif dx <= slope_tol * dy:
                ver.setdefault(round(0.5 * (ax + bx), 1), []).append((min(ay, by), max(ay, by)))
    return hor, ver


def _merge_near(d: dict, tol: float) -> list[tuple[float, list[tuple[float, float]]]]:
    """좌표가 tol 안이면 같은 직선으로 본다."""
    out: list[list[Any]] = []
    for k in sorted(d):
        if out and k - out[-1][0] <= tol:
            out[-1][1].extend(d[k])
        else:
            out.append([k, list(d[k])])
    return [(k, v) for k, v in out]


def _covered(spans: list[tuple[float, float]], lo: float, hi: float) -> float:
    """[lo, hi] 중 spans에 덮인 길이 (겹친 곳은 한 번만 센다)."""
    total, cur = 0.0, lo
    for a, b in sorted(spans):
        a, b = max(a, lo), min(b, hi)
        if b <= cur:
            continue
        total += b - max(a, cur)
        cur = max(cur, b)
        if cur >= hi:
            break
    return total


def detect_body(prims: list[Primitive], size: tuple[int, int], *, tol: float = 3.0,
                min_cover: float = 0.5, top: int = 16) -> Box | None:
    """본체 사각형을 찾는다. 네 변이 min_cover 이상 덮인 것 중 가장 큰 사각형. 못 찾으면 None."""
    W, H = size
    if W <= 0 or H <= 0:
        return None
    hor, ver = _axis_lines(prims)
    hc = [(y, s) for y, s in _merge_near(hor, tol) if _covered(s, 0, W) > 0.12 * W]
    vc = [(x, s) for x, s in _merge_near(ver, tol) if _covered(s, 0, H) > 0.12 * H]
    hc = sorted(hc, key=lambda t: -_covered(t[1], 0, W))[:top]
    vc = sorted(vc, key=lambda t: -_covered(t[1], 0, H))[:top]
    found: list[tuple[float, Box]] = []
    for (y0, s0), (y1, s1) in itertools.combinations(sorted(hc), 2):
        for (x0, t0), (x1, t1) in itertools.combinations(sorted(vc), 2):
            w, h = x1 - x0, y1 - y0
            if w < 0.08 * W or h < 0.08 * H or w * h > 0.97 * W * H:
                continue
            cover = min(_covered(s0, x0, x1) / w, _covered(s1, x0, x1) / w,
                        _covered(t0, y0, y1) / h, _covered(t1, y0, y1) / h)
            if cover < min_cover:
                continue
            found.append((w * h, (float(x0), float(y0), float(x1), float(y1))))
    if not found:
        return None
    return max(found)[1]


def _iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def detect_body_candidates(prims: list[Primitive], size: tuple[int, int], *, limit: int = 6, tol: float = 3.0,
                           min_cover: float = 0.5, top: int = 16, overlap: float = 0.7) -> list[Box]:
    """본체가 될 만한 사각형들을 큰 것부터. 서로 많이 겹치는 것은 하나만 남긴다.

    그림에 **액자**(PDF 매뉴얼 캡처의 테두리)가 있으면 가장 큰 사각형이 본체가 아니다. 접속점을 가장 잘
    설명하는 사각형을 고르려면 후보가 여럿 있어야 한다 — 고르는 일은 `detect_device`가 한다.
    """
    W, H = size
    if W <= 0 or H <= 0:
        return []
    hor, ver = _axis_lines(prims)
    hc = [(y, s) for y, s in _merge_near(hor, tol) if _covered(s, 0, W) > 0.12 * W]
    vc = [(x, s) for x, s in _merge_near(ver, tol) if _covered(s, 0, H) > 0.12 * H]
    hc = sorted(hc, key=lambda t: -_covered(t[1], 0, W))[:top]
    vc = sorted(vc, key=lambda t: -_covered(t[1], 0, H))[:top]
    found: list[tuple[float, Box]] = []
    for (y0, s0), (y1, s1) in itertools.combinations(sorted(hc), 2):
        for (x0, t0), (x1, t1) in itertools.combinations(sorted(vc), 2):
            w, h = x1 - x0, y1 - y0
            if w < 0.08 * W or h < 0.08 * H or w * h > 0.97 * W * H:
                continue
            cover = min(_covered(s0, x0, x1) / w, _covered(s1, x0, x1) / w,
                        _covered(t0, y0, y1) / h, _covered(t1, y0, y1) / h)
            if cover < min_cover:
                continue
            # 덮임을 한 자리로 묶어 **먼저** 보고, 같으면 큰 것부터. 액자와 본체의 변을 섞은 사각형은
            # 덮임이 낮아 뒤로 밀린다 — 넓이만 보면 그것들이 앞을 막아 진짜 본체가 후보에서 빠졌다.
            found.append(((round(cover, 1), w * h), (float(x0), float(y0), float(x1), float(y1))))
    out: list[Box] = []
    for _, box in sorted(found, key=lambda t: t[0], reverse=True):
        if any(_iou(box, kept) > overlap for kept in out):
            continue
        out.append(box)
        if len(out) >= limit:
            break
    return out


# =============================================================================== 접속점
@dataclass
class PinHit:
    side: str
    x_px: float
    y_px: float
    kind: str          # circle | stub | blob | glyph
    pitch: float       # 그 줄에서 잰 접속점 간격 (px)
    score: float       # 그 줄의 점수 (클수록 확실)


def perp_size(side: str, body: Box) -> float:
    """그 변에 **수직인** 본체 치수. 변에서 얼마나 깊이까지 접속점을 찾을지, 어느 변에 속하는지를
    이 치수로 잰다 — 짧은 변 하나로 재면 길쭉한 장치에서 한쪽이 턱없이 얕아진다."""
    x0, y0, x1, y1 = body
    return (y1 - y0) if side in ("top", "bottom") else (x1 - x0)


def _floor(kind: str, min_v: float) -> float:
    """변에서 이만큼은 떨어져 있어야 접속점으로 본다 — **원과 점은 면제한다.**

    변선 자체가 접속점으로 둔갑하는 것을 막는 장치인데, 원은 변선이 아니다. 단자 원이 본체 변에 딱
    붙어 그려진 그림(실측: PDF 매뉴얼의 안전 릴레이, 원 중심이 변에서 2.5 px)에서 진짜 단자가 통째로
    걸러지고 안쪽 잡음이 대신 잡혔다 — 9/12 → 12/12.
    """
    return 0.0 if kind in ("circle", "dot") else min_v


def _side_axes(side: str, body: Box):
    """변 좌표계: u = 변을 따라, v = 변에서 떨어진 거리(바깥이 +, 안쪽이 -)."""
    x0, y0, x1, y1 = body
    if side == "top":
        return (lambda x, y: (x, y0 - y)), x0, x1
    if side == "bottom":
        return (lambda x, y: (x, y - y1)), x0, x1
    if side == "left":
        return (lambda x, y: (y, x0 - x)), y0, y1
    return (lambda x, y: (y, x - x1)), y0, y1


def gather_candidates(prims: list[Primitive], labels: np.ndarray | None, body: Box,
                      size: tuple[int, int]) -> list[tuple[float, float, str, float]]:
    """접속점 후보 (x, y, 꼴, 크기). 원 중심 · 변에 붙은 줄기의 바깥 끝 · 작은 색 덩이."""
    W, H = size
    x0, y0, x1, y1 = body
    short = min(x1 - x0, y1 - y0)
    out: list[tuple[float, float, str, float]] = []
    for p in prims:
        if isinstance(p, Circle):
            if p.r <= 0.12 * short:
                out.append((p.cx, p.cy, "circle", float(np.pi * p.r * p.r)))
            continue
        for g in p.segs:
            if not isinstance(g, LineSeg):
                continue
            (ax, ay), (bx, by) = g.p0, g.p1
            dx, dy = bx - ax, by - ay
            length = float(np.hypot(dx, dy))
            if not 2.0 <= length <= 0.6 * short:
                continue
            vert, hori = abs(dx) <= 0.2 * abs(dy), abs(dy) <= 0.2 * abs(dx)
            if not (vert or hori):
                continue
            tol = max(4.0, 0.012 * short)
            for (px, py), (qx, qy) in (((ax, ay), (bx, by)), ((bx, by), (ax, ay))):
                on_edge = ((vert and (abs(py - y0) <= tol or abs(py - y1) <= tol) and x0 - tol <= px <= x1 + tol)
                           or (hori and (abs(px - x0) <= tol or abs(px - x1) <= tol) and y0 - tol <= py <= y1 + tol))
                if on_edge:
                    out.append((float(qx), float(qy), "stub", length))
    if labels is not None:
        out += _merge_soft(_blob_candidates(labels, short, W * H), radius=SOFT_MERGE_RATIO * short)
        out += dot_candidates(labels, body)
    return out


def _background_label(labels: np.ndarray) -> int:
    vals, counts = np.unique(labels, return_counts=True)
    return int(vals[int(np.argmax(counts))])


def dot_candidates(labels: np.ndarray, body: Box, *, band: int = DOT_BAND, rise: float = DOT_RISE,
                   min_width: int = DOT_MIN_WIDTH) -> list[tuple[float, float, str, float]]:
    """본체 변을 따라 전경 두께를 재어 **굵어진 곳**을 찾는다 — 채워진 점(●)으로 그린 단자.

    점이 변선 위에 그려지면 이진화에서 선과 한 덩어리가 되어 연결 성분으로는 잡히지 않는다
    (실측: ABB 안전 릴레이 TSR20M, 16개 중 4개만 잡혔다). 선은 가늘고 점은 굵으므로 두께로 가른다.
    """
    x0, y0, x1, y1 = (int(round(v)) for v in body)
    H, W = labels.shape
    fg = (labels != _background_label(labels)).astype(np.int32)
    out: list[tuple[float, float, str, float]] = []
    for side in SIDES:
        horizontal = side in ("top", "bottom")
        if horizontal:
            c = y0 if side == "top" else y1
            lo, hi = max(0, c - band), min(H, c + band + 1)
            if hi - lo < 3 or x1 - x0 < 10:
                continue
            prof = fg[lo:hi, max(0, x0):min(W, x1)].sum(axis=0)
            base = x0
        else:
            c = x0 if side == "left" else x1
            lo, hi = max(0, c - band), min(W, c + band + 1)
            if hi - lo < 3 or y1 - y0 < 10:
                continue
            prof = fg[max(0, y0):min(H, y1), lo:hi].sum(axis=1)
            base = y0
        if prof.size < 10:
            continue
        med = float(np.median(prof))
        if med <= 0:
            continue
        margin = 2 * band + 2
        if prof.size <= 2 * margin + 2:
            continue
        mask = prof >= rise * med
        i = 0
        while i < len(mask):
            if not mask[i]:
                i += 1
                continue
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            if j - i >= min_width:
                u = base + (i + j - 1) / 2
                # 변이 만나는 모서리는 늘 두껍다 — 점이 아니다
                if margin <= u - base <= len(prof) - 1 - margin:
                    area = float(prof[i:j].sum())
                    out.append((u, float(c), "dot", area) if horizontal else (float(c), u, "dot", area))
            i = j
    return out


def _merge_soft(cands: list[tuple[float, float, str, float]], radius: float) -> list[tuple[float, float, str, float]]:
    """가까운 색 덩이를 하나로 합친다. 나사 머리는 색 양자화에서 머리·홈·그늘로 갈라져 여러 조각이 되는데,
    조각마다 '변에서 떨어진 거리'가 달라 한 줄로 모이지 않는 것이 사진류에서 가장 큰 손실이었다
    (샘플 실측: 사진 23/111 → 63/111). 접속점 간격보다 작은 반경만 쓴다."""
    if not cands or radius <= 0:
        return cands
    pts = np.array([[c[0], c[1]] for c in cands])
    used = np.zeros(len(cands), bool)
    out: list[tuple[float, float, str, float]] = []
    for i in np.argsort([-c[3] for c in cands]):          # 큰 덩이를 씨앗으로
        if used[i]:
            continue
        d = np.hypot(pts[:, 0] - pts[i][0], pts[:, 1] - pts[i][1])
        grp = np.where((d <= radius) & (~used))[0]
        used[grp] = True
        area = float(sum(cands[j][3] for j in grp))
        cx = sum(cands[j][0] * cands[j][3] for j in grp) / area
        cy = sum(cands[j][1] * cands[j][3] for j in grp) / area
        kind = "blob" if any(cands[j][2] == "blob" for j in grp) else "glyph"
        out.append((float(cx), float(cy), kind, area))
    return out


def owner_side(cand: tuple[float, float, str, float], body: Box, maxd: float, min_v: float) -> str | None:
    """후보가 속할 변 하나. 변까지 거리를 **그 변에 수직인 본체 치수로 나눠** 견준다.

    한 점이 네 변 모두의 후보가 되면, 멀리 떨어진 두 점이 '변을 따라 넓게 퍼진 줄'로 보여 이긴다
    (실측: 세로로 긴 장치에서 위·아래 단자가 왼쪽 변의 두 점으로 묶였다). 정규화하지 않으면 길쭉한
    장치에서 거꾸로 뒤집힌다.
    """
    best, chosen = None, None
    for side in SIDES:
        f, u0, u1 = _side_axes(side, body)
        u, v = f(cand[0], cand[1])
        span = u1 - u0
        if not (u0 - 0.03 * span <= u <= u1 + 0.03 * span):
            continue
        if not (_floor(cand[2], min_v) <= abs(v) <= _depth(side, body, maxd)):
            continue
        rel = abs(v) / max(1.0, perp_size(side, body))
        if best is None or rel < best:
            best, chosen = rel, side
    return chosen


def _depth(side: str, body: Box, maxd: float) -> float:
    """그 변에서 접속점을 찾을 깊이. maxd는 밑바닥일 뿐이고, 변마다 수직 치수로 다시 잰다."""
    return max(maxd, MAXD_RATIO * perp_size(side, body))


def _blob_candidates(labels: np.ndarray, short: float, img_area: float) -> list[tuple[float, float, str, float]]:
    """색 영역의 작은 연결 성분. 둥글면 단자(blob), 찌그러졌으면 글자 획(glyph)으로 본다."""
    import cv2

    out = []
    for v in range(int(labels.max()) + 1):
        mask = (labels == v).astype(np.uint8)
        n, _, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            area = float(stats[i, cv2.CC_STAT_AREA])
            w, h = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
            if not (20 <= area <= 0.02 * img_area) or max(w, h) > 0.25 * short:
                continue
            roundness = (area / max(1, w * h)) * (min(w, h) / max(w, h))
            out.append((float(cent[i][0]), float(cent[i][1]), "blob" if roundness >= ROUNDNESS_MIN else "glyph", area))
    return out


def _rows_on_side(cands, side: str, body: Box, maxd: float, bin_px: float, min_v: float):
    """변 근처 후보를 '변에서 떨어진 거리'로 묶는다 → 줄 후보."""
    f, u0, u1 = _side_axes(side, body)
    span = u1 - u0
    items = []
    for x, y, kind, size_ in cands:
        u, v = f(x, y)
        if not (_floor(kind, min_v) <= abs(v) <= maxd):
            continue
        if not (u0 - 0.03 * span <= u <= u1 + 0.03 * span):
            continue
        items.append((v, u, x, y, kind, size_))
    items.sort()
    rows, cur = [], []
    for it in items:
        if cur and it[0] - cur[-1][0] > bin_px:
            rows.append(cur)
            cur = []
        cur.append(it)
    if cur:
        rows.append(cur)
    return rows


def _dedupe(row, tol: float):
    """같은 자리에 겹친 후보는 하나로 (서열이 높은 꼴을 남긴다)."""
    out: list = []
    for it in sorted(row, key=lambda r: r[1]):
        if out and it[1] - out[-1][1] <= tol:
            if KIND_WEIGHT[it[4]] > KIND_WEIGHT[out[-1][4]]:
                out[-1] = it
            continue
        out.append(it)
    return out


def lattice_fit(u: np.ndarray, span: float) -> tuple[float, float]:
    """접속점 자리가 **공통 격자의 배수**인가 → (적합도 0~1, 격자 크기).

    등간격을 따지면 실제 도면 심볼을 놓친다. 전기 도면의 단자는 격자 위에 놓이되 **빈 자리가 있다**
    (DNCAD 규칙도 '16의 배수, 같은 변 최소 간격 32'이지 등간격이 아니다). 실측한 변압기 심볼 -T1은
    간격이 343·85·171 — 모두 85의 배수였고, 등간격으로 재면 2/8만 찾던 것이 격자로 재니 8/8이 됐다.

    격자가 잘면 아무 간격이나 배수로 맞으므로, 격자 자리 중 실제로 채워진 비율(채움률)을 함께 본다.
    """
    gaps = np.diff(u)
    if len(gaps) == 0 or float(np.min(gaps)) <= 0:
        return 0.0, 0.0
    floor = max(LATTICE_MIN_RATIO * span, 1.0)
    # 격자 후보: 각 간격과 그 1/2·1/3·1/4. 빈 자리가 많은 줄은 격자가 어떤 간격에도 그대로 나타나지
    # 않는다 — 안전 컨트롤러 -SC1의 아래 변은 간격 155·94·125·93·156·63이 모두 31.5의 배수였다.
    candidates = {float(g) / d for g in gaps for d in (1, 2, 3, 4)}
    best = (0.0, float(np.min(gaps)))
    for p in sorted(candidates):
        if p < floor:
            continue
        k = gaps / p
        fit = float((np.abs(k - np.round(k)) <= LATTICE_TOL).mean())
        slots = (u[-1] - u[0]) / p + 1
        occupancy = len(u) / slots if slots > 0 else 0.0
        sc = fit * (OCCUPANCY_BASE + (1 - OCCUPANCY_BASE) * min(1.0, occupancy))
        if sc > best[0]:
            best = (sc, p)
    return best


def _row_score(row, span: float) -> tuple[float, float]:
    """줄 점수와 격자: 개수가 많고, 자리가 한 격자 위에 있고, 변에 넓게 퍼지고, 크기가 비슷하고, 꼴이 믿을 만할수록."""
    if len(row) < 2:
        return -1.0, 0.0
    u = np.array([r[1] for r in row])
    evenness, pitch = lattice_fit(u, span)
    if pitch <= 1 or evenness <= 0:
        return -1.0, 0.0
    spread = (u[-1] - u[0]) / span if span else 0.0
    sizes = np.array([r[5] for r in row])
    alike = 1.0 - min(1.0, float(np.std(sizes) / max(1e-6, float(np.mean(sizes)))))
    kind_w = float(np.mean([KIND_WEIGHT[r[4]] for r in row]))
    return len(row) * evenness * (0.3 + 0.7 * spread) * (0.5 + 0.5 * alike) * kind_w, pitch


def _is_second_row(cand: tuple[float, list, float], head: tuple[float, list, float]) -> bool:
    """둘째 줄이 진짜 2열 단자인가. 글자 줄은 간격·개수·꼴 중 하나는 어긋난다."""
    s, keep, pitch = cand
    s0, keep0, pitch0 = head
    if s < SECOND_ROW_FRAC * s0:
        return False
    if abs(pitch - pitch0) > SECOND_ROW_PITCH_TOL * pitch0:
        return False
    if abs(len(keep) - len(keep0)) > 1:
        return False
    return {it[4] for it in keep} == {it[4] for it in keep0}


def detect_pins(prims: list[Primitive], labels: np.ndarray | None, body: Box, size: tuple[int, int],
                *, maxd_ratio: float = 0.45, min_v_ratio: float = 0.03, bin_ratio: float = 0.08,
                min_pins: int = 2, second_row: bool = True) -> list[PinHit]:
    """본체 네 변에서 접속점 줄을 찾는다. 접속점은 보통 한 축(위·아래 또는 왼·오른쪽)에 있으므로 그 축만 쓴다.
    second_row면 한 변의 2열 단자(안전 릴레이·I/O 카드에 흔하다)도 받는다 — 간격·개수·꼴이 같을 때만."""
    x0, y0, x1, y1 = body
    short = min(x1 - x0, y1 - y0)
    if short <= 0:
        return []
    maxd = max(50.0, maxd_ratio * short)
    min_v = max(6.0, min_v_ratio * short)
    bin_px = max(8.0, bin_ratio * short)
    cands = gather_candidates(prims, labels, body, size)
    mine: dict[str, list] = {s: [] for s in SIDES}
    for c in cands:                                  # 후보는 제 변 하나에만 속한다
        side = owner_side(c, body, maxd, min_v)
        if side:
            mine[side].append(c)

    per_side: dict[str, tuple[float, list[PinHit]]] = {}
    for side in SIDES:
        _, u0, u1 = _side_axes(side, body)
        span = u1 - u0
        rows: list[tuple[float, list, float]] = []
        for row in _rows_on_side(mine[side], side, body, _depth(side, body, maxd), bin_px, min_v):
            for tier in TIERS:                       # 믿을 만한 꼴이 둘 이상이면 아래 꼴은 버린다
                sel = [it for it in row if it[4] in tier]
                if len(sel) >= 2:
                    break
            else:
                sel = row
            r = _dedupe(sel, tol=0.02 * span + 4)
            s, pitch = _row_score(r, span)
            if s <= 0:
                continue
            keep = [r[0]]
            for it in r[1:]:
                if it[1] - keep[-1][1] >= 0.5 * pitch:
                    keep.append(it)
            if len(keep) >= min_pins:
                rows.append((s, keep, pitch))
        if not rows:
            continue
        rows.sort(key=lambda t: -t[0])
        picked = [rows[0]]
        if second_row:
            picked += [t for t in rows[1:2] if _is_second_row(t, rows[0])]
        top = rows[0][0]
        per_side[side] = (top, [PinHit(side, x, y, kind, pitch, s)
                                for s, keep, pitch in picked for _, _, x, y, kind, _ in keep])

    if not per_side:
        return []
    axes = {"tb": ("top", "bottom"), "lr": ("left", "right")}
    totals = {k: sum(per_side[s][0] for s in v if s in per_side) for k, v in axes.items()}
    sides = [s for s in axes[max(totals, key=lambda k: totals[k])] if s in per_side]
    if not sides:
        return []
    top_score = max(per_side[s][0] for s in sides)
    hits: list[PinHit] = []
    for s in sides:
        if per_side[s][0] >= 0.4 * top_score:        # 다른 변보다 한참 약한 줄은 버린다
            hits += per_side[s][1]
    return hits


def name_pins(hits: list[PinHit]) -> list[PinHit]:
    """이름을 붙이지 않은 접속점에 1부터 번호를 매긴다 (위 변 왼쪽→오른쪽, 그다음 아래 변)."""
    order = {"top": 0, "bottom": 1, "left": 2, "right": 3}
    return sorted(hits, key=lambda h: (order.get(h.side, 9), h.x_px if h.side in ("top", "bottom") else h.y_px))


def detect_device(prims: list[Primitive], labels: np.ndarray | None, size: tuple[int, int],
                  *, limit: int = 12, overlap: float = 0.92, **kw: Any) -> tuple[Box | None, list[PinHit]]:
    """본체와 접속점을 함께 정한다 — 후보 사각형마다 접속점을 찾아 **가장 잘 설명하는** 것을 고른다.

    가장 큰 사각형이 늘 본체는 아니다. PDF 매뉴얼에서 캡처한 그림은 바깥에 액자가 있고, 액자를 본체로
    삼으면 변이 접속점에서 멀어져 절반을 놓친다 (실측: 안전 릴레이 9/12 → 12/12).
    """
    boxes = detect_body_candidates(prims, size, limit=limit, overlap=overlap)
    if not boxes:
        return None, []
    # 후보는 '덮임이 높고 큰 것' 순으로 온다. 접속점을 더 많이 설명하는 것만 앞의 것을 밀어낸다 —
    # 수가 같으면 먼저 온 것을 지킨다(액자와 본체의 변을 섞은 사각형이 넓이로 이기는 것을 막는다).
    best: tuple[int, Box, list[PinHit]] | None = None
    for box in boxes:
        hits = detect_pins(prims, labels, box, size, **kw)
        if hits and (best is None or len(hits) > best[0]):
            best = (len(hits), box, hits)
    if best is None:
        return boxes[0], []
    return best[1], best[2]
