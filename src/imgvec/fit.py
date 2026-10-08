"""픽셀 경로 → 직선·호 분할 피팅, 파편 정리, 모서리 맞춤.

입력은 순서가 있는 점열(작업 이미지 픽셀 좌표)이고, 출력은 LineSeg/ArcSeg 목록이다.
허용 오차 tol은 점열에서 피팅 도형까지의 최대 거리(작업 픽셀)이다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .geom import (
    TWO_PI,
    ArcSeg,
    Circle,
    LineSeg,
    Seg,
    angle_between,
    intersect_lines,
    line_through,
)


@dataclass
class FitConfig:
    tol: float = 1.0                # 최대 허용 편차 (작업 px)
    frag_len: float = 4.0           # 이보다 짧은 조각은 파편으로 보고 정리
    allow_arcs: bool = True
    min_arc_deg: float = 12.0       # 이보다 작은 각의 호는 직선으로 둔다
    arc_gain: float = 1.3           # 호가 직선보다 이만큼 더 길어야 호를 택함
    max_radius_factor: float = 60.0 # 반지름 상한 = 이 값 x 호의 현 길이
    ortho_snap_deg: float = 1.0     # 수평·수직에 이만큼 가까우면 맞춤 (0이면 끔)
    min_circle_r: float = 3.0


@dataclass
class Piece:
    kind: str            # "line" | "arc"
    pts: np.ndarray      # 이 조각이 덮는 점열 (n, 2)
    geo: Seg


# --------------------------------------------------------------------------- 기본 피팅


def fit_line(P: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """전체 최소제곱(주성분) 직선. (중심, 단위방향, 부호 있는 수직 편차)를 돌려준다."""
    c = P.mean(axis=0)
    Q = P - c
    if len(P) < 2:
        return c, np.array([1.0, 0.0]), np.zeros(len(P))
    cov = Q.T @ Q
    w, v = np.linalg.eigh(cov)
    d = v[:, 1]
    n = np.array([-d[1], d[0]])
    return c, d, Q @ n


def fit_circle(P: np.ndarray, iters: int = 12) -> tuple[float, float, float, np.ndarray] | None:
    """원 피팅: Kåsa 초기값 + 기하 거리 Gauss-Newton. (cx, cy, R, 잔차) 또는 None."""
    if len(P) < 3:
        return None
    c0 = P.mean(axis=0)
    Q = P - c0
    x, y = Q[:, 0], Q[:, 1]
    A = np.column_stack((2.0 * x, 2.0 * y, np.ones_like(x)))
    b = x * x + y * y
    try:
        sol = np.linalg.lstsq(A, b, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    cx, cy = float(sol[0]), float(sol[1])
    r2 = float(sol[2]) + cx * cx + cy * cy
    if not np.isfinite(r2) or r2 <= 1e-9:
        return None
    R = math.sqrt(r2)
    for _ in range(iters):
        dx, dy = x - cx, y - cy
        d = np.maximum(np.hypot(dx, dy), 1e-9)
        res = d - R
        J = np.column_stack((-dx / d, -dy / d, -np.ones_like(d)))
        try:
            delta = np.linalg.lstsq(J, -res, rcond=None)[0]
        except np.linalg.LinAlgError:
            break
        cx += float(delta[0])
        cy += float(delta[1])
        R += float(delta[2])
        if float(np.abs(delta).max()) < 1e-7:
            break
    R = abs(R)
    res = np.hypot(x - cx, y - cy) - R
    if not (np.isfinite(R) and np.all(np.isfinite(res))):
        return None
    return cx + float(c0[0]), cy + float(c0[1]), R, res


def _arc_span(P: np.ndarray, cx: float, cy: float) -> tuple[float, float, float]:
    """(시작각, 총 휩쓸림각, 진행 방향 일관도)."""
    ang = np.unwrap(np.arctan2(P[:, 1] - cy, P[:, 0] - cx))
    total = float(ang[-1] - ang[0])
    steps = np.diff(ang)
    sgn = 1.0 if total >= 0 else -1.0
    consistent = float(np.mean(steps * sgn >= -1e-9)) if len(steps) else 0.0
    return float(ang[0]), total, consistent


def _line_ok(P: np.ndarray, tol: float) -> bool:
    _, _, dev = fit_line(P)
    return float(np.abs(dev).max()) <= tol


def _arc_candidate(P: np.ndarray, cfg: FitConfig, check_span: bool = True) -> tuple[ArcSeg, float] | None:
    """P 전체가 하나의 호로 허용 오차 안에 들어오면 (호, 잔차 RMS), 아니면 None.

    check_span=False이면 최소 중심각 검사를 건너뛴다 (호 구간을 키워 가는 중에는 짧은 구간의
    중심각이 작으므로, 최종 구간에서만 검사한다).
    """
    if len(P) < 6:
        return None
    r = fit_circle(P)
    if r is None:
        return None
    cx, cy, R, res = r
    chord = float(np.linalg.norm(P[-1] - P[0]))
    if R < 2.0 * cfg.tol or R > cfg.max_radius_factor * max(chord, 1.0):
        return None
    if float(np.abs(res).max()) > cfg.tol:
        return None
    a0, total, cons = _arc_span(P, cx, cy)
    if abs(total) > TWO_PI - 0.35 or cons < 0.92:
        return None
    if check_span and abs(total) < math.radians(cfg.min_arc_deg):
        return None
    return ArcSeg(cx, cy, R, a0, a0 + total), float(np.sqrt(np.mean(res**2)))


def _arc_fit(P: np.ndarray, cfg: FitConfig) -> ArcSeg | None:
    c = _arc_candidate(P, cfg)
    return c[0] if c else None


def _line_rms(P: np.ndarray) -> float:
    _, _, dev = fit_line(P)
    return float(np.sqrt(np.mean(dev**2)))


def _line_geo(P: np.ndarray) -> LineSeg:
    c, d, _ = fit_line(P)
    t0 = float((P[0] - c) @ d)
    t1 = float((P[-1] - c) @ d)
    p0 = c + d * t0
    p1 = c + d * t1
    return LineSeg((float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1])))


# --------------------------------------------------------------------------- 분할 (탐욕)


def _grow(i: int, ok, n: int, min_end: int) -> int | None:
    """i에서 시작해 ok(i, j)가 참인 가장 큰 j. 최소 min_end도 안 되면 None."""
    lo = i + min_end
    if lo > n - 1:
        lo = n - 1
        if lo <= i or not ok(i, lo):
            return None
    elif not ok(i, lo):
        return None
    hi = lo
    step = 4
    while hi < n - 1:
        cand = min(hi + step, n - 1)
        if ok(i, cand):
            hi = cand
            step *= 2
            continue
        a, b = hi, cand
        while b - a > 1:
            m = (a + b) // 2
            if ok(i, m):
                a = m
            else:
                b = m
        hi = a
        break
    return hi


def _grow_arc(i: int, ok, n: int, cap: int) -> int | None:
    """호로 맞는 가장 긴 구간의 끝. 곡률이 작은 짧은 구간은 원 피팅이 불안정해 탈락하므로,
    작은 구간부터 순서대로 캐지 않고 점점 큰 구간을 시험해 마지막으로 성공한 구간을 찾는다."""
    best: int | None = None
    sz = 12.0
    while True:
        j = min(i + int(sz), n - 1)
        if j - i < 7:
            return best
        if ok(i, j):
            best = j
        elif best is not None:
            a, b = best, j
            while b - a > 1:
                m = (a + b) // 2
                if ok(i, m):
                    a = m
                else:
                    b = m
            return a
        elif int(sz) > cap:
            return None
        if j >= n - 1:
            return best
        sz *= 1.5


def _segment(P: np.ndarray, cfg: FitConfig) -> list[Piece]:
    n = len(P)
    pieces: list[Piece] = []
    i = 0

    def line_ok(a: int, b: int) -> bool:
        return _line_ok(P[a : b + 1], cfg.tol)

    def arc_ok(a: int, b: int) -> bool:
        return _arc_candidate(P[a : b + 1], cfg, check_span=False) is not None

    while i < n - 1:
        jl = _grow(i, line_ok, n, 1)
        if jl is None:
            jl = min(i + 1, n - 1)
        kind, j = "line", jl
        if cfg.allow_arcs and jl < n - 1:
            ja = _grow_arc(i, arc_ok, n, cap=max(40, 4 * (jl - i) + 20))
            cand = _arc_candidate(P[i : ja + 1], cfg) if (ja is not None and ja - i >= 7) else None
            if cand is not None:
                longer = (ja - i) > cfg.arc_gain * (jl - i) + 4
                # 같은 구간에서 직선이 호보다 확연히 못 맞으면 허용 오차 안이어도 호로 본다
                rms_a, rms_l = cand[1], _line_rms(P[i : ja + 1])
                clearly_better = ja >= jl and rms_l >= 1.6 * rms_a and (rms_l - rms_a) >= 0.08 * cfg.tol
                if longer or clearly_better:
                    kind, j = "arc", ja
        sub = P[i : j + 1]
        if kind == "arc":
            geo = _arc_fit(sub, cfg)
            if geo is None:
                kind, geo = "line", _line_geo(sub)
        else:
            geo = _line_geo(sub)
        pieces.append(Piece(kind, sub.copy(), geo))
        i = j
    return pieces


# --------------------------------------------------------------------------- 호 경계 보정


def _max_extension(line_pts: np.ndarray, arc_pts: np.ndarray, line_first: bool, cfg: FitConfig) -> int:
    """호가 이웃 직선의 점을 m개(공유점 포함) 더 삼켜도 호로 남는 최대 m. 1이면 확장 없음."""
    M = len(line_pts) - 3  # 직선에 3점은 남긴다

    def ok(m: int) -> bool:
        comb = np.vstack([line_pts[-m:], arc_pts[1:]]) if line_first else np.vstack([arc_pts[:-1], line_pts[:m]])
        return _arc_fit(comb, cfg) is not None

    if M < 2 or not ok(2):
        return 1
    lo, step, hi = 2, 2, None
    while lo < M:
        cand = min(M, lo + step)
        if ok(cand):
            lo = cand
            step *= 2
        else:
            hi = cand
            break
    if hi is not None:
        a, b = lo, hi
        while b - a > 1:
            mid = (a + b) // 2
            if ok(mid):
                a = mid
            else:
                b = mid
        lo = a
    return lo


def _refine_arcs(pieces: list[Piece], cfg: FitConfig) -> list[Piece]:
    """탐욕 분할은 직선이 호 안쪽으로 파고들므로, 호의 경계를 접점 쪽으로 되돌려 넓힌다."""
    if not cfg.allow_arcs or len(pieces) < 2:
        return pieces
    c2 = FitConfig(**{**cfg.__dict__, "tol": 0.6 * cfg.tol})
    for k, pc in enumerate(pieces):
        if pc.kind != "arc":
            continue
        if k > 0 and pieces[k - 1].kind == "line":
            prev = pieces[k - 1]
            m = _max_extension(prev.pts, pc.pts, True, c2)
            if m > 1:
                pc.pts = np.vstack([prev.pts[-m:], pc.pts[1:]])
                prev.pts = prev.pts[: len(prev.pts) - m + 1]
                geo = _arc_fit(pc.pts, c2)
                if geo is not None:
                    pc.geo = geo
                prev.geo = _line_geo(prev.pts)
        if k < len(pieces) - 1 and pieces[k + 1].kind == "line":
            nxt = pieces[k + 1]
            m = _max_extension(nxt.pts, pc.pts, False, c2)
            if m > 1:
                pc.pts = np.vstack([pc.pts[:-1], nxt.pts[:m]])
                nxt.pts = nxt.pts[m - 1 :]
                geo = _arc_fit(pc.pts, c2)
                if geo is not None:
                    pc.geo = geo
                nxt.geo = _line_geo(nxt.pts)
    return pieces


def _fillet_one(prev: Piece, arc: Piece, nxt: Piece, cfg: FitConfig) -> bool:
    """직선-호-직선에서 호를 두 직선에 접하는 필렛으로 다시 맞춘다. 성공하면 세 조각을 갱신하고 True."""
    g1: LineSeg = prev.geo  # type: ignore[assignment]
    g2: LineSeg = nxt.geo  # type: ignore[assignment]
    d1, d2 = np.array(g1.direction()), np.array(g2.direction())
    cross = float(d1[0] * d2[1] - d1[1] * d2[0])
    alpha = math.atan2(abs(cross), float(d1 @ d2))          # 꺾임각 (0..π)
    if alpha < math.radians(12.0) or alpha > math.radians(168.0):
        return False
    pa, da = line_through(g1)
    pb, db = line_through(g2)
    X = intersect_lines(pa, da, pb, db)
    if X is None:
        return False
    s = 1.0 if cross > 0 else -1.0
    n1 = s * np.array([-d1[1], d1[0]])
    ta = math.tan(alpha / 2.0)
    P = arc.pts
    r0 = arc.geo.r  # type: ignore[union-attr]

    def build(r: float):
        t = r * ta
        T1 = X - d1 * t
        T2 = X + d2 * t
        C = T1 + n1 * r
        return T1, T2, C

    def loss(r: float) -> float:
        T1, T2, C = build(r)
        a_start = math.atan2(T1[1] - C[1], T1[0] - C[0])
        rel = ((np.arctan2(P[:, 1] - C[1], P[:, 0] - C[0]) - a_start) * s) % TWO_PI
        inside = rel <= alpha
        if int(inside.sum()) < 8:
            return float("inf")
        res = np.abs(np.hypot(P[inside, 0] - C[0], P[inside, 1] - C[1]) - r)
        h = max(0.5, 0.35 * cfg.tol)
        return float(np.mean(np.where(res < h, 0.5 * res**2, h * (res - 0.5 * h))))

    rs = r0 * np.exp(np.linspace(math.log(0.3), math.log(3.0), 70))
    ls = [loss(float(r)) for r in rs]
    k = int(np.argmin(ls))
    if not np.isfinite(ls[k]):
        return False
    lo, hi = float(rs[max(0, k - 1)]), float(rs[min(len(rs) - 1, k + 1)])
    for _ in range(30):  # 황금분할 탐색
        m1, m2 = lo + 0.382 * (hi - lo), lo + 0.618 * (hi - lo)
        if loss(m1) < loss(m2):
            hi = m2
        else:
            lo = m1
    r = 0.5 * (lo + hi)
    if r < 2.0 * cfg.tol:
        return False
    T1, T2, C = build(r)
    # 접점이 직선의 실제 범위 안에 있어야 한다
    if float((T1 - np.array(g1.p0)) @ d1) < 0 or float((np.array(g2.p1) - T2) @ d2) < 0:
        return False
    # 합성 곡선(직선 꼬리 + 호)이 점열에 허용 오차 안으로 맞는가
    a_start = math.atan2(T1[1] - C[1], T1[0] - C[0])
    rel = ((np.arctan2(P[:, 1] - C[1], P[:, 0] - C[0]) - a_start) * s) % TWO_PI
    inside = rel <= alpha
    d_arc = np.abs(np.hypot(P[:, 0] - C[0], P[:, 1] - C[1]) - r)
    n1v, n2v = np.array([-d1[1], d1[0]]), np.array([-d2[1], d2[0]])
    d_l1 = np.abs((P - X) @ n1v)
    d_l2 = np.abs((P - X) @ n2v)
    d_all = np.where(inside, d_arc, np.minimum(d_l1, d_l2))
    if float(d_all.max()) > cfg.tol * 1.2:
        return False
    arc.geo = ArcSeg(float(C[0]), float(C[1]), float(r), a_start, a_start + s * alpha)
    g1.p1 = (float(T1[0]), float(T1[1]))
    g2.p0 = (float(T2[0]), float(T2[1]))
    return True


def _fillet_arcs(pieces: list[Piece], cfg: FitConfig, closed: bool) -> None:
    """직선-호-직선 모서리를 필렛(두 직선에 접하는 호)으로 정리해 둥근 모서리의 반지름·접점을 정확히 한다."""
    if not cfg.allow_arcs:
        return
    n = len(pieces)
    for i in range(n):
        if pieces[i].kind != "arc":
            continue
        if not closed and (i == 0 or i == n - 1):
            continue
        prev, nxt = pieces[(i - 1) % n], pieces[(i + 1) % n]
        if prev.kind == "line" and nxt.kind == "line" and prev is not nxt:
            _fillet_one(prev, pieces[i], nxt, cfg)


# --------------------------------------------------------------------------- 정리


def _refit(pts: np.ndarray, cfg: FitConfig, slack: float = 1.3) -> Piece | None:
    """점열 전체를 하나의 직선 또는 호로 맞출 수 있으면 조각을, 아니면 None."""
    tol = cfg.tol * slack
    if _line_ok(pts, tol):
        return Piece("line", pts, _line_geo(pts))
    if cfg.allow_arcs:
        c2 = FitConfig(**{**cfg.__dict__, "tol": tol})
        geo = _arc_fit(pts, c2)
        if geo is not None:
            return Piece("arc", pts, geo)
    return None


def _join(*arrs: np.ndarray) -> np.ndarray:
    out = [arrs[0]]
    for a in arrs[1:]:
        out.append(a[1:] if len(a) > 1 else a)
    return np.vstack(out)


def _set_end(piece: Piece, which: str, pt: tuple[float, float]) -> None:
    if piece.kind != "line":
        return
    g = piece.geo
    if which == "start":
        g.p0 = (float(pt[0]), float(pt[1]))
    else:
        g.p1 = (float(pt[0]), float(pt[1]))


def _cleanup(pieces: list[Piece], cfg: FitConfig, closed: bool) -> list[Piece]:
    """짧은 파편을 이웃과 합치거나 두 직선의 교점으로 대체한다."""
    guard = 0
    while len(pieces) > 1 and guard < 400:
        guard += 1
        n = len(pieces)
        lens = [p.geo.length for p in pieces]
        k = int(np.argmin(lens))
        if lens[k] >= cfg.frag_len:
            break
        has_prev = closed or k > 0
        has_next = closed or k < n - 1
        if closed and n >= 3:
            a = (k - 1) % n
            pieces = pieces[a:] + pieces[:a]
            k = 1
        prev = pieces[k - 1] if (has_prev and k - 1 >= 0) else None
        nxt = pieces[k + 1] if (has_next and k + 1 < len(pieces)) else None
        if prev is nxt:
            nxt = None
        frag = pieces[k]

        if prev is not None and nxt is not None:
            merged = _refit(_join(prev.pts, frag.pts, nxt.pts), cfg)
            if merged is not None:
                pieces = pieces[: k - 1] + [merged] + pieces[k + 2 :]
                continue
            if prev.kind == "line" and nxt.kind == "line":
                pa, da = line_through(prev.geo)
                pb, db = line_through(nxt.geo)
                mid = (np.array(prev.geo.p1) + np.array(nxt.geo.p0)) * 0.5
                X = intersect_lines(pa, da, pb, db) if angle_between(da, db) >= 8.0 else None
                if X is not None and float(np.linalg.norm(X - mid)) <= 2.0 * cfg.frag_len + 2.0 * cfg.tol:
                    _set_end(prev, "end", (X[0], X[1]))
                    _set_end(nxt, "start", (X[0], X[1]))
                else:
                    _set_end(prev, "end", (mid[0], mid[1]))
                    _set_end(nxt, "start", (mid[0], mid[1]))
            elif prev.kind == "line":
                _set_end(prev, "end", nxt.geo.start)
            elif nxt.kind == "line":
                _set_end(nxt, "start", prev.geo.end)
            pieces = pieces[:k] + pieces[k + 1 :]
            continue

        nb = prev if prev is not None else nxt
        if nb is None:
            break
        merged = _refit(_join(nb.pts, frag.pts) if nb is prev else _join(frag.pts, nb.pts), cfg)
        if merged is not None:
            if nb is prev:
                pieces = pieces[: k - 1] + [merged] + pieces[k + 1 :]
            else:
                pieces = pieces[:k] + [merged] + pieces[k + 2 :]
            continue
        pieces = pieces[:k] + pieces[k + 1 :]

    # 방향이 거의 같은 이웃 직선은 하나로
    i = 0
    while i < len(pieces) - 1:
        a, b = pieces[i], pieces[i + 1]
        if a.kind == "line" and b.kind == "line":
            _, da = line_through(a.geo)
            _, db = line_through(b.geo)
            if angle_between(da, db) < 3.0:
                m = _refit(_join(a.pts, b.pts), cfg)
                if m is not None and m.kind == "line":
                    pieces = pieces[:i] + [m] + pieces[i + 2 :]
                    continue
        i += 1
    return pieces


def _snap_orthogonal(pieces: list[Piece], cfg: FitConfig) -> None:
    """수평·수직에 아주 가까운 직선을 중점 기준으로 회전해 맞춘다 (허용 오차를 넘지 않는 범위)."""
    if cfg.ortho_snap_deg <= 0:
        return
    for pc in pieces:
        if pc.kind != "line":
            continue
        g: LineSeg = pc.geo  # type: ignore[assignment]
        L = g.length
        if L < 1e-9:
            continue
        ang = math.degrees(math.atan2(g.p1[1] - g.p0[1], g.p1[0] - g.p0[0]))
        k = round(ang / 90.0)
        delta = ang - 90.0 * k
        allow = min(cfg.ortho_snap_deg, math.degrees(cfg.tol / L))
        if abs(delta) <= allow and abs(delta) > 1e-9:
            mx = (g.p0[0] + g.p1[0]) * 0.5
            my = (g.p0[1] + g.p1[1]) * 0.5
            th = math.radians(90.0 * k)
            h = L * 0.5
            dx, dy = math.cos(th), math.sin(th)
            g.p0 = (mx - dx * h, my - dy * h)
            g.p1 = (mx + dx * h, my + dy * h)


def _solve_corners(pieces: list[Piece], cfg: FitConfig, closed: bool) -> None:
    n = len(pieces)
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        a, b = pieces[i], pieces[(i + 1) % n]
        if a is b:
            continue
        ea, sb = a.geo.end, b.geo.start
        gap = math.hypot(ea[0] - sb[0], ea[1] - sb[1])
        if a.kind == "line" and b.kind == "line":
            pa, da = line_through(a.geo)
            pb, db = line_through(b.geo)
            mid = (np.array(ea) + np.array(sb)) * 0.5
            X = intersect_lines(pa, da, pb, db) if angle_between(da, db) >= 8.0 else None
            if X is not None and float(np.linalg.norm(X - mid)) <= 3.0 * cfg.tol + cfg.frag_len + gap:
                p = (float(X[0]), float(X[1]))
            else:
                p = (float(mid[0]), float(mid[1]))
            _set_end(a, "end", p)
            _set_end(b, "start", p)
        elif a.kind == "line":
            _set_end(a, "end", sb)
        elif b.kind == "line":
            _set_end(b, "start", ea)


# --------------------------------------------------------------------------- 공개 API


def _best_start(P: np.ndarray) -> int:
    """닫힌 점열에서 가장 날카로운 모서리 위치 (없으면 0)."""
    n = len(P)
    k = max(3, min(8, n // 12))
    idx = np.arange(n)
    a = P[(idx + k) % n] - P
    b = P - P[(idx - k) % n]
    na = np.maximum(np.linalg.norm(a, axis=1), 1e-9)
    nb = np.maximum(np.linalg.norm(b, axis=1), 1e-9)
    cosang = np.clip((a * b).sum(axis=1) / (na * nb), -1.0, 1.0)
    ang = np.arccos(cosang)
    i = int(np.argmax(ang))
    return i if ang[i] > math.radians(35.0) else 0


def fit_open(P: np.ndarray, cfg: FitConfig) -> list[Seg]:
    """열린 점열을 직선·호 목록으로. 이웃 조각의 끝점은 서로 맞춰져 있다."""
    P = np.asarray(P, float)
    if len(P) < 2:
        return []
    if len(P) == 2:
        return [LineSeg((float(P[0, 0]), float(P[0, 1])), (float(P[1, 0]), float(P[1, 1])))]
    pieces = _segment(P, cfg)
    pieces = _refine_arcs(pieces, cfg)
    pieces = _cleanup(pieces, cfg, closed=False)
    _snap_orthogonal(pieces, cfg)
    _fillet_arcs(pieces, cfg, closed=False)
    _solve_corners(pieces, cfg, closed=False)
    return [p.geo for p in pieces]


def _robust_circle(P: np.ndarray, cfg: FitConfig) -> Circle | None:
    """점열의 대부분(75% 이상)이 하나의 원 위에 있고 둘레를 270° 이상 덮으면 원으로 본다.
    글자 등이 붙어 일부 점이 어긋난 단자 원을 살리기 위한 반복 절사 피팅."""
    n = len(P)
    keep = np.ones(n, bool)
    fit = None
    for _ in range(5):
        if keep.sum() < max(8, 0.5 * n):
            return None
        fit = fit_circle(P[keep])
        if fit is None:
            return None
        cx, cy, R, _ = fit
        res = np.abs(np.hypot(P[:, 0] - cx, P[:, 1] - cy) - R)
        new_keep = res <= max(1.2 * cfg.tol, 1.0)
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
    if fit is None or keep.sum() < 0.75 * n:
        return None
    cx, cy, R, res_in = fit
    if R < cfg.min_circle_r or float(np.abs(res_in).max()) > cfg.tol * 1.2:
        return None
    ang = np.arctan2(P[keep, 1] - cy, P[keep, 0] - cx)
    bins = np.zeros(72, bool)
    bins[((ang + math.pi) / TWO_PI * 72).astype(int) % 72] = True
    if bins.mean() < 270.0 / 360.0:
        return None
    return Circle(cx, cy, R)


def fit_closed(P: np.ndarray, cfg: FitConfig) -> Circle | list[Seg] | None:
    """닫힌 점열 → 원이면 Circle, 아니면 닫힌 경로의 조각 목록."""
    P = np.asarray(P, float)
    if len(P) < 6:
        return None
    r = fit_circle(P)
    if r is not None:
        cx, cy, R, res = r
        if R >= cfg.min_circle_r and float(np.abs(res).max()) <= cfg.tol:
            _, total, cons = _arc_span(np.vstack([P, P[:1]]), cx, cy)
            if abs(total) > TWO_PI - 0.6 and cons >= 0.9:
                return Circle(cx, cy, R)
        rc = _robust_circle(P, cfg)
        if rc is not None:
            return rc
    s = _best_start(P)
    Pr = np.roll(P, -s, axis=0)
    seq = np.vstack([Pr, Pr[:1]])
    pieces = _segment(seq, cfg)
    if len(pieces) >= 2 and s == 0:
        # 이음매를 조각 경계로 옮겨 다시 분할한다 (이음매가 직선 한가운데 놓이는 것을 방지)
        start2 = int(np.argmin(np.linalg.norm(Pr - np.array(pieces[1].geo.start), axis=1)))
        Pr = np.roll(Pr, -start2, axis=0)
        seq = np.vstack([Pr, Pr[:1]])
        pieces = _segment(seq, cfg)
    pieces = _refine_arcs(pieces, cfg)
    pieces = _cleanup(pieces, cfg, closed=True)
    if not pieces:
        return None
    _snap_orthogonal(pieces, cfg)
    _fillet_arcs(pieces, cfg, closed=True)
    _solve_corners(pieces, cfg, closed=True)
    return [p.geo for p in pieces]
