"""도형 자료구조와 기하 계산.

좌표는 모두 (x, y)이며 이미지 좌표계(y가 아래로 증가)를 따른다.
호의 각도는 atan2(y - cy, x - cx)로 재고, a1 - a0이 부호 있는 휩쓸림각이다
(SVG의 sweep-flag=1이 a1 > a0에 해당한다).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]
TWO_PI = 2.0 * math.pi


@dataclass
class LineSeg:
    p0: Point
    p1: Point

    @property
    def start(self) -> Point:
        return self.p0

    @property
    def end(self) -> Point:
        return self.p1

    @property
    def length(self) -> float:
        return math.hypot(self.p1[0] - self.p0[0], self.p1[1] - self.p0[1])

    def direction(self) -> tuple[float, float]:
        L = self.length
        if L < 1e-12:
            return (1.0, 0.0)
        return ((self.p1[0] - self.p0[0]) / L, (self.p1[1] - self.p0[1]) / L)


@dataclass
class ArcSeg:
    cx: float
    cy: float
    r: float
    a0: float
    a1: float

    @property
    def start(self) -> Point:
        return (self.cx + self.r * math.cos(self.a0), self.cy + self.r * math.sin(self.a0))

    @property
    def end(self) -> Point:
        return (self.cx + self.r * math.cos(self.a1), self.cy + self.r * math.sin(self.a1))

    @property
    def sweep(self) -> float:
        return self.a1 - self.a0

    @property
    def length(self) -> float:
        return self.r * abs(self.a1 - self.a0)


Seg = LineSeg | ArcSeg


@dataclass
class Path:
    segs: list[Seg]
    closed: bool = False
    layer: str = ""

    @property
    def length(self) -> float:
        return sum(s.length for s in self.segs)


@dataclass
class Circle:
    cx: float
    cy: float
    r: float
    layer: str = ""

    @property
    def length(self) -> float:
        return TWO_PI * self.r


Primitive = Path | Circle


def line_through(seg: LineSeg) -> tuple[np.ndarray, np.ndarray]:
    """직선 선분이 지나는 무한 직선 (점, 단위방향)."""
    p = np.array(seg.p0, float)
    d = np.array(seg.direction(), float)
    return p, d


def intersect_lines(p: np.ndarray, d: np.ndarray, q: np.ndarray, e: np.ndarray) -> np.ndarray | None:
    """두 무한 직선의 교점. 거의 평행이면 None."""
    cross = d[0] * e[1] - d[1] * e[0]
    if abs(cross) < 1e-9:
        return None
    w = q - p
    t = (w[0] * e[1] - w[1] * e[0]) / cross
    return p + d * t


def angle_between(d: np.ndarray, e: np.ndarray) -> float:
    """두 방향 사이의 각(0..90°)을 도 단위로 (직선은 방향 부호 무시)."""
    c = abs(float(d @ e)) / max(float(np.linalg.norm(d) * np.linalg.norm(e)), 1e-12)
    return math.degrees(math.acos(min(1.0, c)))


def sample_segment(seg: Seg, step: float) -> np.ndarray:
    """선분/호를 step 간격으로 샘플링한 점들 (끝점 포함)."""
    L = seg.length
    n = max(2, int(math.ceil(L / max(step, 1e-6))) + 1)
    if isinstance(seg, LineSeg):
        t = np.linspace(0.0, 1.0, n)
        x = seg.p0[0] + (seg.p1[0] - seg.p0[0]) * t
        y = seg.p0[1] + (seg.p1[1] - seg.p0[1]) * t
        return np.column_stack((x, y))
    a = np.linspace(seg.a0, seg.a1, n)
    return np.column_stack((seg.cx + seg.r * np.cos(a), seg.cy + seg.r * np.sin(a)))


def sample_primitive(prim: Primitive, step: float = 0.5) -> np.ndarray:
    if isinstance(prim, Circle):
        n = max(8, int(math.ceil(prim.length / max(step, 1e-6))))
        a = np.linspace(0.0, TWO_PI, n, endpoint=False)
        return np.column_stack((prim.cx + prim.r * np.cos(a), prim.cy + prim.r * np.sin(a)))
    if not prim.segs:
        return np.zeros((0, 2))
    return np.vstack([sample_segment(s, step) for s in prim.segs])


def scale_primitive(prim: Primitive, s: float, ox: float = 0.0, oy: float = 0.0) -> Primitive:
    """좌표를 s배 하고 (ox, oy)만큼 이동한 새 도형."""

    def pt(p: Point) -> Point:
        return (p[0] * s + ox, p[1] * s + oy)

    if isinstance(prim, Circle):
        return Circle(prim.cx * s + ox, prim.cy * s + oy, prim.r * s, prim.layer)
    segs: list[Seg] = []
    for g in prim.segs:
        if isinstance(g, LineSeg):
            segs.append(LineSeg(pt(g.p0), pt(g.p1)))
        else:
            segs.append(ArcSeg(g.cx * s + ox, g.cy * s + oy, g.r * s, g.a0, g.a1))
    return Path(segs, prim.closed, prim.layer)


def count_points(prim: Primitive) -> int:
    """도형을 이루는 점(꼭짓점) 수. 원은 3, 호는 3(양끝+중심)으로 센다."""
    if isinstance(prim, Circle):
        return 3
    n = 0
    for g in prim.segs:
        n += 2 if isinstance(g, LineSeg) else 3
    return n
