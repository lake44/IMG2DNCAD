"""변환 결과 평가(원본 대비 편차)와 겹쳐보기 이미지."""
from __future__ import annotations

import cv2
import numpy as np
from scipy.spatial import cKDTree

from .geom import Circle, Primitive, count_points, sample_primitive


def _stats(d: np.ndarray, scale: float | None) -> dict:
    if len(d) == 0:
        return {"max": None, "p99": None, "mean": None}
    k = scale if scale else 1.0
    return {
        "max": round(float(d.max()) * k, 4),
        "p99": round(float(np.percentile(d, 99)) * k, 4),
        "mean": round(float(d.mean()) * k, 4),
    }


def evaluate(
    prims: list[Primitive],
    fit_pts: np.ndarray,
    boundary: np.ndarray,
    merge_px: float,
    mm_per_work_px: float | None,
) -> dict:
    """작업 이미지 좌표의 도형을 두 가지 기준으로 평가한다.

    * fit: 추적한 골격 픽셀(fit_pts)이 도형에서 얼마나 떨어져 있는가 (피팅 오차)
    * edge: 원본의 색 경계가 도형에서 merge_px+2 안에 얼마나 들어오는가(재현율),
            도형 위의 점이 경계 근처에 있는가(정밀도)
    mm 값은 축척을 알 때만 mm 단위, 아니면 작업 px 단위이다.
    """
    unit = "mm" if mm_per_work_px else "work_px"
    out: dict = {"unit": unit}
    samples = [sample_primitive(p, 0.5) for p in prims]
    samples = [s for s in samples if len(s)]
    if not samples or len(fit_pts) == 0:
        out.update({"fit": _stats(np.zeros(0), None), "edge_coverage": 0.0, "edge_precision": 0.0})
        return out
    S = np.vstack(samples)
    tree_v = cKDTree(S)
    d_fit = tree_v.query(fit_pts)[0]
    out["fit"] = _stats(d_fit, mm_per_work_px)

    d_rev = cKDTree(fit_pts).query(S)[0]
    out["vector_to_skeleton"] = _stats(d_rev, mm_per_work_px)

    ys, xs = np.nonzero(boundary)
    if len(xs):
        step = max(1, len(xs) // 200_000)
        bp = np.column_stack((xs[::step] + 0.5, ys[::step] + 0.5))
        lim = merge_px + 2.0
        out["edge_coverage"] = round(float(np.mean(tree_v.query(bp)[0] <= lim)), 4)
        out["edge_precision"] = round(float(np.mean(cKDTree(bp).query(S)[0] <= lim)), 4)
    else:
        out["edge_coverage"] = 0.0
        out["edge_precision"] = 0.0
    out["edge_match_limit_work_px"] = round(merge_px + 2.0, 2)

    n_lines = n_arcs = n_circles = n_paths = 0
    pts = 0
    for p in prims:
        pts += count_points(p)
        if isinstance(p, Circle):
            n_circles += 1
        else:
            n_paths += 1
            for g in p.segs:
                if g.__class__.__name__ == "LineSeg":
                    n_lines += 1
                else:
                    n_arcs += 1
    out["counts"] = {"paths": n_paths, "circles": n_circles, "line_segments": n_lines, "arc_segments": n_arcs}
    out["points_out"] = pts
    out["points_in"] = int(len(fit_pts))
    out["simplification_ratio"] = round(pts / max(1, len(fit_pts)), 4)
    return out


def draw_overlay(rgb: np.ndarray, prims: list[Primitive], fade: float = 0.55) -> np.ndarray:
    """원본을 흐리게 깔고 결과 도형을 빨강, 꼭짓점을 파랑으로 그린다 (rgb 입력, rgb 출력)."""
    base = (rgb.astype(np.float32) * (1.0 - fade) + 255.0 * fade).astype(np.uint8)
    base = np.ascontiguousarray(base)
    SH = 4
    K = 1 << SH
    for p in prims:
        pts = sample_primitive(p, 1.0)
        if len(pts) < 2:
            continue
        q = np.round(pts * K).astype(np.int32).reshape(-1, 1, 2)
        closed = isinstance(p, Circle) or p.closed
        cv2.polylines(base, [q], closed, (230, 0, 0), 1, cv2.LINE_AA, SH)
        if not isinstance(p, Circle):
            for g in p.segs:
                for e in (g.start, g.end):
                    c = (int(round(e[0] * K)), int(round(e[1] * K)))
                    cv2.circle(base, c, 2 * K, (0, 0, 230), -1, cv2.LINE_AA, SH)
    return base
