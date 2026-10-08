import math

import numpy as np

from imgvec.fit import FitConfig, fit_circle, fit_closed, fit_line, fit_open
from imgvec.geom import ArcSeg, Circle, LineSeg

from conftest import pixel_path


def _along(p0, p1, step=1.0):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    n = max(2, int(np.hypot(*(p1 - p0)) / step) + 1)
    t = np.linspace(0, 1, n)[:, None]
    return p0 + (p1 - p0) * t


def test_fit_line_recovers_slope_and_deviation():
    t = np.linspace(0, 100, 200)
    P = pixel_path(np.column_stack((t, 0.5 * t + 20)), noise=0.3)
    _, d, dev = fit_line(P)
    assert abs(abs(d[1] / d[0]) - 0.5) < 0.01
    assert np.abs(dev).max() < 1.2


def test_fit_circle_recovers_parameters():
    th = np.linspace(0.3, 2.5, 120)
    P = pixel_path(np.column_stack((50 + 30 * np.cos(th), 40 + 30 * np.sin(th))), noise=0.2)
    cx, cy, R, res = fit_circle(P)
    assert abs(cx - 50) < 0.6 and abs(cy - 40) < 0.6 and abs(R - 30) < 0.6
    assert np.abs(res).max() < 1.0


def test_straight_noisy_path_is_one_line():
    P = pixel_path(_along((10, 10), (210, 90)), noise=0.3)
    segs = fit_open(P, FitConfig(tol=1.0))
    assert len(segs) == 1 and isinstance(segs[0], LineSeg)
    assert abs(segs[0].length - math.hypot(200, 80)) < 2.0


def test_right_angle_corner_shares_exact_vertex():
    P = pixel_path(np.vstack([_along((0, 0), (100, 0)), _along((100, 0), (100, 80))[1:]]), noise=0.25)
    segs = fit_open(P, FitConfig(tol=1.0))
    assert [type(s) for s in segs] == [LineSeg, LineSeg]
    assert segs[0].p1 == segs[1].p0                      # 꼭짓점이 정확히 일치
    assert abs(segs[0].p1[0] - 100) < 1.0 and abs(segs[0].p1[1]) < 1.0


def test_quarter_arc_is_detected_as_arc():
    th = np.linspace(0, math.pi / 2, 90)
    P = pixel_path(np.column_stack((100 + 40 * np.cos(th), 100 + 40 * np.sin(th))), noise=0.2)
    segs = fit_open(P, FitConfig(tol=1.0))
    assert len(segs) == 1 and isinstance(segs[0], ArcSeg)
    assert abs(segs[0].r - 40) < 1.0
    assert abs(math.degrees(abs(segs[0].sweep)) - 90) < 4


def test_closed_circle_returns_circle():
    th = np.linspace(0, 2 * math.pi, 240, endpoint=False)
    P = pixel_path(np.column_stack((60 + 25 * np.cos(th), 70 + 25 * np.sin(th))), noise=0.25)
    res = fit_closed(P, FitConfig(tol=1.0))
    assert isinstance(res, Circle)
    assert abs(res.r - 25) < 0.6 and abs(res.cx - 60) < 0.6 and abs(res.cy - 70) < 0.6


def test_closed_rectangle_gives_four_orthogonal_lines():
    pts = np.vstack(
        [_along((0, 0), (120, 0))[:-1], _along((120, 0), (120, 70))[:-1], _along((120, 70), (0, 70))[:-1], _along((0, 70), (0, 0))[:-1]]
    )
    segs = fit_closed(pixel_path(pts, noise=0.3), FitConfig(tol=1.0, ortho_snap_deg=1.0))
    assert isinstance(segs, list) and len(segs) == 4 and all(isinstance(s, LineSeg) for s in segs)
    for s in segs:  # 수평/수직으로 맞춰졌는가
        dx, dy = abs(s.p1[0] - s.p0[0]), abs(s.p1[1] - s.p0[1])
        assert min(dx, dy) < 1e-6


def test_rounded_rectangle_keeps_corner_arcs():
    W, H, r = 200.0, 120.0, 25.0
    t = np.linspace(0, math.pi / 2, 50)
    parts = [
        _along((r, 0), (W - r, 0)),
        np.column_stack((W - r + r * np.sin(t), r - r * np.cos(t))),
        _along((W, r), (W, H - r)),
        np.column_stack((W - r + r * np.cos(t), H - r + r * np.sin(t))),
        _along((W - r, H), (r, H)),
        np.column_stack((r - r * np.sin(t), H - r + r * np.cos(t))),
        _along((0, H - r), (0, r)),
        np.column_stack((r - r * np.cos(t), r - r * np.sin(t))),
    ]
    P = pixel_path(np.vstack([p[:-1] for p in parts]), noise=0.25)
    segs = fit_closed(P, FitConfig(tol=1.5, ortho_snap_deg=1.0))
    assert isinstance(segs, list)
    arcs = [s for s in segs if isinstance(s, ArcSeg)]
    lines = [s for s in segs if isinstance(s, LineSeg)]
    assert len(arcs) == 4 and len(lines) == 4
    assert all(r * 0.7 < a.r < r * 1.4 for a in arcs)
