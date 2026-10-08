"""그림 → 벡터 변환 파이프라인.

1. 작업 이미지 만들기(필요하면 확대)  2. 라벨 지도(흑백 또는 색 영역)  3. 경계 리본 → 골격
4. 골격 그래프 + 가지치기  5. 변마다 직선·호 피팅  6. 교차점·자유 끝점 정리  7. 평가
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .consolidate import consolidate_circles, dedupe
from .fit import FitConfig, fit_closed, fit_open
from .geom import Circle, LineSeg, Path, Primitive, Seg, scale_primitive
from .labels import (
    boundary_mask,
    classify_kind,
    estimate_merge_px,
    labels_illustration,
    labels_lineart,
    make_ribbon,
)
from .metrics import draw_overlay, evaluate
from .params import Params
from .skeleton import Edge, Node, build_graph, prune, skeleton_of


@dataclass
class VectorResult:
    prims: list[Primitive]                    # 원본 이미지 픽셀 좌표 (픽셀 (c, r)의 중심 = (c+0.5, r+0.5))
    size: tuple[int, int]                     # 원본 (너비, 높이) px
    mm_per_px: float | None
    kind: str
    cfg: dict
    report: dict
    work_rgb: np.ndarray | None = None
    work_prims: list[Primitive] = field(default_factory=list)
    work_scale: float = 1.0

    def overlay(self) -> np.ndarray:
        if self.work_rgb is None:
            raise RuntimeError("작업 이미지가 보관되지 않았습니다")
        return draw_overlay(self.work_rgb, self.work_prims)


@dataclass
class _EdgeFit:
    edge: Edge
    pts: np.ndarray                      # 실제로 피팅에 쓴 점열
    segs: list[Seg] | None = None
    circle: Circle | None = None
    closed: bool = False


# --------------------------------------------------------------------------- 보조


def _choose_scale(long0: int, cfg: dict) -> float:
    tgt = float(cfg.get("upscale_target") or 0)
    s = 1.0
    if tgt and long0 < tgt:
        s = max(1.0, float(min(cfg["max_upscale"], math.ceil(tgt / long0))))
    if long0 * s > cfg["max_side"]:
        s = cfg["max_side"] / long0
    return s


def _trim_junction_ends(e: Edge, nodes: dict[int, Node]) -> np.ndarray:
    """교차점 근처(왜곡 구간)의 점을 잘라낸 점열."""
    pts = e.pts
    lo, hi = 0, len(pts)
    if e.a in nodes and nodes[e.a].kind == "junction":
        nd = nodes[e.a]
        d = np.hypot(pts[:, 0] - nd.x, pts[:, 1] - nd.y)
        r = nd.radius * 1.2 + 1.0
        while lo < len(pts) and d[lo] < r:
            lo += 1
    if e.b in nodes and nodes[e.b].kind == "junction":
        nd = nodes[e.b]
        d = np.hypot(pts[:, 0] - nd.x, pts[:, 1] - nd.y)
        r = nd.radius * 1.2 + 1.0
        while hi > lo and d[hi - 1] < r:
            hi -= 1
    if hi - lo < 4:
        return pts
    return pts[lo:hi]


def _line_end(seg: LineSeg, which: str) -> tuple[np.ndarray, np.ndarray]:
    """(끝점, 그 끝에서 반대쪽 끝을 향하는 단위벡터)."""
    p0, p1 = np.array(seg.p0), np.array(seg.p1)
    q, other = (p0, p1) if which == "start" else (p1, p0)
    t = other - q
    L = float(np.linalg.norm(t))
    return q, (t / L if L > 1e-9 else np.array([1.0, 0.0]))


def _set_line_end(seg: LineSeg, which: str, pt: np.ndarray) -> None:
    v = (float(pt[0]), float(pt[1]))
    if which == "start":
        seg.p0 = v
    else:
        seg.p1 = v


def _solve_junctions(nodes: dict[int, Node], fits: list[_EdgeFit], tol: float) -> None:
    """교차점마다 접하는 직선들의 최소제곱 교점을 구해 끝점을 맞춘다."""
    inc: dict[int, list[tuple[_EdgeFit, str]]] = {}
    for ef in fits:
        if ef.segs is None or ef.closed or not ef.segs:
            continue
        e = ef.edge
        if e.a in nodes and nodes[e.a].kind == "junction":
            inc.setdefault(e.a, []).append((ef, "start"))
        if e.b in nodes and nodes[e.b].kind == "junction":
            inc.setdefault(e.b, []).append((ef, "end"))
    for nid, lst in inc.items():
        nd = nodes[nid]
        J0 = np.array([nd.x, nd.y])
        lines = []
        arcs = []
        for ef, which in lst:
            seg = ef.segs[0] if which == "start" else ef.segs[-1]  # type: ignore[index]
            if isinstance(seg, LineSeg):
                q, t = _line_end(seg, which)
                lines.append((q, t, seg, which))
            else:
                arcs.append((ef, which))
        J = J0
        if len(lines) >= 2:
            A = np.zeros((2, 2))
            b = np.zeros(2)
            for q, t, _, _ in lines:
                M = np.eye(2) - np.outer(t, t)
                A += M
                b += M @ q
            if np.linalg.det(A) > 0.05:
                Js = np.linalg.solve(A, b)
                if float(np.linalg.norm(Js - J0)) <= nd.radius * 1.5 + 2.0 * tol + 2.0:
                    J = Js
        for _, _, seg, which in lines:
            _set_line_end(seg, which, J)
        for ef, which in arcs:
            arc = ef.segs[0] if which == "start" else ef.segs[-1]  # type: ignore[index]
            pt = np.array(arc.start if which == "start" else arc.end)
            if float(np.linalg.norm(pt - J)) > 0.5 * tol:  # 호는 움직일 수 없으니 짧은 선으로 이어준다
                if which == "start":
                    ef.segs.insert(0, LineSeg((float(J[0]), float(J[1])), (float(pt[0]), float(pt[1]))))  # type: ignore[union-attr]
                else:
                    ef.segs.append(LineSeg((float(pt[0]), float(pt[1])), (float(J[0]), float(J[1]))))  # type: ignore[union-attr]


def _extend_free_ends(nodes: dict[int, Node], fits: list[_EdgeFit], dist: np.ndarray, cap: float) -> None:
    """골격은 획 끝에서 반 폭만큼 짧아지므로 자유 끝점을 그만큼 연장한다."""
    H, W = dist.shape
    for ef in fits:
        if ef.segs is None or ef.closed or not ef.segs:
            continue
        e = ef.edge
        for which in ("start", "end"):
            nid = e.a if which == "start" else e.b
            if nid in nodes and nodes[nid].kind == "junction":
                continue
            seg = ef.segs[0] if which == "start" else ef.segs[-1]
            if not isinstance(seg, LineSeg):
                continue
            q, t = _line_end(seg, which)
            px = min(W - 1, max(0, int(q[0])))
            py = min(H - 1, max(0, int(q[1])))
            ext = min(cap, max(0.0, float(dist[py, px]) - 0.5))
            if ext > 0.05:
                _set_line_end(seg, which, q - t * ext)


# --------------------------------------------------------------------------- 본체


def convert_image(rgb: np.ndarray, params: Params | None = None, keep_work: bool = True) -> VectorResult:
    params = params or Params()
    tm: dict[str, float] = {}
    t0 = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal t0
        t1 = time.perf_counter()
        tm[name] = round(t1 - t0, 3)
        t0 = t1

    rgb = np.ascontiguousarray(rgb)
    H0, W0 = rgb.shape[:2]
    kind = params.preset if params.preset != "auto" else classify_kind(rgb)
    cfg = params.resolve(kind)

    s = _choose_scale(max(H0, W0), cfg)
    if abs(s - 1.0) > 1e-9:
        Wn, Hn = int(round(W0 * s)), int(round(H0 * s))
        interp = cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA
        work = cv2.resize(rgb, (Wn, Hn), interpolation=interp)
        sw = Wn / W0
    else:
        work, sw = rgb, 1.0
    lap("resize")

    mm_per_px = params.mm_per_px
    mmw = (mm_per_px / sw) if mm_per_px else None
    if mmw:
        tol_w = max(cfg["tolerance_mm"] / mmw, cfg["min_tol_work_px"])
        min_len_w = max(cfg["min_length_mm"] / mmw, 3.0)
    else:
        tol_w = max(cfg["tolerance_src_px"] * sw, cfg["min_tol_work_px"])
        min_len_w = max(cfg["min_length_src_px"] * sw, 3.0)

    if kind == "lineart":
        labels, info = labels_lineart(work, cfg)
    else:
        labels, info = labels_illustration(work, cfg)
    lap("labels")

    merge = float(cfg["merge_px"]) if cfg.get("merge_px") else estimate_merge_px(kind, labels)
    boundary = boundary_mask(labels)
    ribbon = make_ribbon(boundary, merge)
    sk = skeleton_of(ribbon)
    dist = cv2.distanceTransform(ribbon.astype(np.uint8), cv2.DIST_L2, 3)
    lap("ribbon_skeleton")

    nodes, edges = build_graph(sk, dist)
    n_edges0 = len(edges)
    spur_len = max(4.0, 2.5 * merge)
    edges = prune(nodes, edges, spur_len=spur_len, min_len=max(min_len_w, spur_len))
    lap("graph")

    fcfg = FitConfig(
        tol=tol_w,
        frag_len=max(4.0, 2.0 * tol_w, merge),
        allow_arcs=bool(cfg["allow_arcs"]),
        ortho_snap_deg=float(cfg["ortho_snap_deg"]),
        min_circle_r=float(cfg["min_circle_r_work_px"]),
    )
    gap_close = max(2.5 * tol_w, 1.5 * merge)
    fits: list[_EdgeFit] = []
    for e in edges:
        pts = e.pts
        open_but_ring = (
            not e.closed
            and len(pts) >= 12
            and (e.a == 0 or nodes.get(e.a, Node(0, 0, 0, "end")).kind == "end")
            and (e.b == 0 or nodes.get(e.b, Node(0, 0, 0, "end")).kind == "end")
            and float(np.hypot(*(pts[0] - pts[-1]))) <= gap_close
        )
        if e.closed or open_but_ring:
            P = pts[:-1] if (len(pts) > 1 and float(np.hypot(*(pts[0] - pts[-1]))) < 1e-9) else pts
            res = fit_closed(P, fcfg)
            if res is None:
                continue
            if isinstance(res, Circle):
                fits.append(_EdgeFit(e, P, circle=res, closed=True))
            else:
                fits.append(_EdgeFit(e, P, segs=list(res), closed=True))
        else:
            P = _trim_junction_ends(e, nodes)
            segs = fit_open(P, fcfg)
            if segs:
                fits.append(_EdgeFit(e, P, segs=segs))
    lap("fit")

    _solve_junctions(nodes, fits, tol_w)
    if cfg.get("extend_free_ends", True):
        _extend_free_ends(nodes, fits, dist, cap=merge + 1.0)

    work_prims: list[Primitive] = []
    kept_pts: list[np.ndarray] = []
    for ef in fits:
        if ef.circle is not None:
            work_prims.append(ef.circle)
            kept_pts.append(ef.pts)
            continue
        path = Path(list(ef.segs or []), closed=ef.closed)
        if path.segs and path.length >= min_len_w:
            work_prims.append(path)
            kept_pts.append(ef.pts)
    work_prims = consolidate_circles(work_prims, tol_w, float(cfg["min_circle_r_work_px"]) * 2.0, min_len_w)
    work_prims = dedupe(work_prims, tol=max(1.0, 0.8 * tol_w))
    lap("assemble")

    fit_pts = np.vstack(kept_pts) if kept_pts else np.zeros((0, 2))
    metrics = evaluate(work_prims, fit_pts, boundary, merge, mmw)
    lap("evaluate")

    prims = [scale_primitive(p, 1.0 / sw) for p in work_prims]
    report = {
        "input": {"width_px": W0, "height_px": H0},
        "kind": kind,
        "work_scale": round(sw, 4),
        "mm_per_px": mm_per_px,
        "settings": {k: v for k, v in cfg.items()},
        "label_info": info,
        "merge_px_work": round(merge, 2),
        "tolerance_work_px": round(tol_w, 3),
        "graph": {"nodes": len(nodes), "edges_before_prune": n_edges0, "edges_after_prune": len(edges)},
        "metrics": metrics,
        "timing_s": tm,
    }
    return VectorResult(
        prims=prims,
        size=(W0, H0),
        mm_per_px=mm_per_px,
        kind=kind,
        cfg=cfg,
        report=report,
        work_rgb=work if keep_work else None,
        work_prims=work_prims if keep_work else [],
        work_scale=sw,
    )
