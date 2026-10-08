"""후처리: 같은 원 위에 흩어진 호를 하나의 원으로 합친다.

단자 원에 글자나 선이 붙으면 원이 여러 호로 쪼개져 Circle로 인식되지 않는다.
중심·반지름이 같은 호들의 중심각 합이 300° 이상이면 원으로 본다.
"""
from __future__ import annotations

import math

import numpy as np

from scipy.spatial import cKDTree

from .fit import fit_circle
from .geom import ArcSeg, Circle, Path, Primitive, sample_primitive, sample_segment


def _max_gap(grp: list[tuple[int, int, ArcSeg]]) -> float:
    """원 둘레를 호들이 덮은 뒤 남는 가장 큰 빈 각도(rad). 교차점 근처에서 잘려 나간 틈은 작다."""
    nb = 360
    cov = np.zeros(nb, bool)
    for _, _, g in grp:
        k = max(2, int(abs(g.a1 - g.a0) / (2 * math.pi) * nb * 2) + 2)
        ang = np.linspace(g.a0, g.a1, k) % (2 * math.pi)
        cov[(ang / (2 * math.pi) * nb).astype(int) % nb] = True
    idx = np.flatnonzero(cov)
    if len(idx) == 0:
        return 2 * math.pi
    nxt = np.roll(idx, -1)
    nxt[-1] += nb
    return float((nxt - idx - 1).max()) / nb * 2 * math.pi


def dedupe(prims: list[Primitive], tol: float, step: float = 2.0, frac: float = 0.9) -> list[Primitive]:
    """다른(더 긴) 도형 위에 거의 포개진 도형을 지운다 (골격 고리에서 생기는 중복선 등)."""
    n = len(prims)
    if n < 2:
        return prims
    samples = [sample_primitive(p, step) for p in prims]
    lens = [p.length for p in prims]
    key = [(lens[i], -i) for i in range(n)]
    ids = np.concatenate([np.full(len(s), i, dtype=np.int64) for i, s in enumerate(samples)])
    allp = np.vstack(samples)
    tree = cKDTree(allp)
    drop: set[int] = set()
    for i in sorted(range(n), key=lambda k: key[k]):
        s = samples[i]
        if len(s) == 0:
            continue
        d, idx = tree.query(s, k=min(8, len(allp)), distance_upper_bound=tol)
        d = np.atleast_2d(d.T).T if d.ndim == 1 else d
        idx = np.atleast_2d(idx.T).T if idx.ndim == 1 else idx
        covered = 0
        for row_d, row_i in zip(d, idx):
            for dd, ii in zip(row_d, row_i):
                if not np.isfinite(dd):
                    break
                j = int(ids[ii])
                if j != i and j not in drop and key[j] > key[i]:
                    covered += 1
                    break
        if covered >= frac * len(s):
            drop.add(i)
    return [p for i, p in enumerate(prims) if i not in drop]


def consolidate_circles(prims: list[Primitive], tol: float, min_r: float, min_len: float) -> list[Primitive]:
    arcs: list[tuple[int, int, ArcSeg]] = []
    for pi, p in enumerate(prims):
        if isinstance(p, Path):
            for si, g in enumerate(p.segs):
                if isinstance(g, ArcSeg) and g.r >= min_r:
                    arcs.append((pi, si, g))
    if len(arcs) < 2:
        return prims

    groups: list[list[tuple[int, int, ArcSeg]]] = []
    for item in sorted(arcs, key=lambda t: t[2].r):
        g = item[2]
        for grp in groups:
            c = grp[0][2]
            gt = max(2.0 * tol, 0.04 * c.r)
            if abs(c.cx - g.cx) <= gt and abs(c.cy - g.cy) <= gt and abs(c.r - g.r) <= gt:
                grp.append(item)
                break
        else:
            groups.append([item])

    remove: set[tuple[int, int]] = set()
    circles: list[Circle] = []
    for grp in groups:
        total = sum(abs(g.a1 - g.a0) for _, _, g in grp)
        if len(grp) < 2 or total < math.radians(270.0) or _max_gap(grp) > math.radians(60.0):
            continue
        pts = np.vstack([sample_segment(g, 0.5) for _, _, g in grp])
        fit = fit_circle(pts)
        if fit is None:
            continue
        cx, cy, R, res = fit
        if R < min_r or float(np.abs(res).max()) > 1.5 * tol:
            continue
        circles.append(Circle(cx, cy, R))
        remove.update((pi, si) for pi, si, _ in grp)
    if not circles:
        return prims

    out: list[Primitive] = []
    for pi, p in enumerate(prims):
        if isinstance(p, Path) and any((pi, si) in remove for si in range(len(p.segs))):
            run = []
            for si, g in enumerate(p.segs):
                if (pi, si) in remove:
                    if run:
                        out.append(Path(run, False, p.layer))
                        run = []
                else:
                    run.append(g)
            if run:
                out.append(Path(run, False, p.layer))
        else:
            out.append(p)
    out = [x for x in out if isinstance(x, Circle) or x.length >= min_len]
    return out + circles
