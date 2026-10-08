"""골격(1픽셀 선) → 그래프(끝점·교차점·변) 추출과 가지치기."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from skimage.morphology import skeletonize

_DY = (-1, -1, 0, 1, 1, 1, 0, -1)
_DX = (0, 1, 1, 1, 0, -1, -1, -1)


@dataclass
class Node:
    id: int
    x: float
    y: float
    kind: str            # "end" | "junction"
    radius: float = 0.0  # 교차점 주변에서 골격이 왜곡되는 반경 추정 (작업 px)


@dataclass
class Edge:
    pts: np.ndarray      # (n, 2) 픽셀 중심 좌표 (x, y). 양 끝에 노드 좌표를 포함
    a: int               # 시작 노드 id (0이면 노드 없음)
    b: int
    closed: bool = False

    @property
    def length(self) -> float:
        return path_length(self.pts)


def path_length(pts: np.ndarray) -> float:
    if len(pts) < 2:
        return 0.0
    d = np.diff(pts, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def skeleton_of(mask: np.ndarray) -> np.ndarray:
    """이진 마스크의 1픽셀 골격 (Lee 세선화)."""
    sk = skeletonize(np.asarray(mask, dtype=bool), method="lee")
    return np.asarray(sk, dtype=bool)


def _neighbor_stats(sk: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """각 픽셀의 이웃 수와 교차수(0→1 전이 횟수)."""
    H, W = sk.shape
    p = np.zeros((H + 2, W + 2), np.uint8)
    p[1:-1, 1:-1] = sk
    n = [p[1 + dy : 1 + dy + H, 1 + dx : 1 + dx + W] for dy, dx in zip(_DY, _DX)]
    cnt = np.zeros((H, W), np.uint8)
    for a in n:
        cnt += a
    trans = np.zeros((H, W), np.uint8)
    for k in range(8):
        trans += ((n[k] == 0) & (n[(k + 1) % 8] == 1)).astype(np.uint8)
    return cnt, trans


def build_graph(sk: np.ndarray, dist: np.ndarray | None = None) -> tuple[dict[int, Node], list[Edge]]:
    """골격 → (노드 사전, 변 목록). dist(거리변환)가 있으면 교차점 반경을 추정한다."""
    H, W = sk.shape
    cnt, trans = _neighbor_stats(sk)
    junc = sk & (trans >= 3)
    ends = sk & (cnt == 1)
    jd = cv2.dilate(junc.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & sk
    nj, jl = cv2.connectedComponents(jd.astype(np.uint8), connectivity=8)
    region = np.zeros(sk.shape, np.int32)
    region[jd] = jl[jd]
    nid = int(nj)
    ey, ex = np.nonzero(ends & ~jd)
    region[ey, ex] = np.arange(nid, nid + len(ey), dtype=np.int32)
    nid += len(ey)

    ys, xs = np.nonzero(region)
    ids = region[ys, xs]
    cnts = np.bincount(ids, minlength=nid)
    sx = np.bincount(ids, weights=xs + 0.5, minlength=nid)
    sy = np.bincount(ids, weights=ys + 0.5, minlength=nid)
    nodes: dict[int, Node] = {}
    for k in range(1, nid):
        if cnts[k] == 0:
            continue
        cx, cy = sx[k] / cnts[k], sy[k] / cnts[k]
        nodes[k] = Node(k, float(cx), float(cy), "junction" if k < nj else "end")
    # 교차점 군집의 퍼짐 + 그 위치의 선 반쪽 폭 = 왜곡 반경
    far = np.hypot(xs + 0.5 - sx[ids] / cnts[ids], ys + 0.5 - sy[ids] / cnts[ids])
    spread = np.zeros(nid)
    np.maximum.at(spread, ids, far)
    for k, nd in nodes.items():
        if nd.kind == "junction":
            w = 0.0
            if dist is not None:
                w = float(dist[min(H - 1, int(nd.y)), min(W - 1, int(nd.x))])
            nd.radius = float(spread[k]) + w

    Hp, Wp = H + 2, W + 2
    flat = np.zeros((Hp, Wp), bool)
    flat[1:-1, 1:-1] = sk
    skset = set(np.flatnonzero(flat.ravel()).tolist())
    flat_reg = np.zeros((Hp, Wp), np.int32)
    flat_reg[1:-1, 1:-1] = region
    nz = np.flatnonzero(flat_reg.ravel())
    regd: dict[int, int] = dict(zip(nz.tolist(), flat_reg.ravel()[nz].tolist()))
    members: dict[int, list[int]] = {}
    for p, r in regd.items():
        members.setdefault(r, []).append(p)

    OFF = (-Wp, -Wp + 1, 1, Wp + 1, Wp, Wp - 1, -1, -Wp - 1)
    OFF4 = {-Wp, 1, Wp, -1}
    OFFS = set(OFF)
    visited: set[int] = set()

    def choose(cands: list[int], cur: int) -> int:
        if len(cands) == 1:
            return cands[0]
        nxt = min(cands, key=lambda n: (0 if (n - cur) in OFF4 else 1))
        for n in cands:
            if n != nxt and (n - nxt) in OFFS:
                visited.add(n)  # 중복 이웃(2x2 덩어리) 흡수
        return nxt

    def to_xy(idx: int) -> tuple[float, float]:
        r, c = divmod(idx, Wp)
        return c - 1 + 0.5, r - 1 + 0.5

    edges: list[Edge] = []

    def trace(q: int, prev: int, start_id: int) -> tuple[list[int], int]:
        path = [q]
        visited.add(q)
        cur, prv = q, prev
        end_id = 0
        while True:
            hit = 0
            cands: list[int] = []
            for o in OFF:
                n = cur + o
                if n == prv or n not in skset:
                    continue
                r = regd.get(n, 0)
                if r:
                    if r != start_id or len(path) >= 4:
                        hit = r
                        break
                    continue
                if n not in visited:
                    cands.append(n)
            if hit:
                end_id = hit
                break
            if not cands:
                break
            nxt = choose(cands, cur)
            path.append(nxt)
            visited.add(nxt)
            prv, cur = cur, nxt
        return path, end_id

    for rid, mem in members.items():
        for p in mem:
            for o in OFF:
                q = p + o
                if q in skset and q not in regd and q not in visited:
                    path, end_id = trace(q, p, rid)
                    pts = [(nodes[rid].x, nodes[rid].y)] if rid in nodes else []
                    pts += [to_xy(i) for i in path]
                    if end_id in nodes:
                        pts.append((nodes[end_id].x, nodes[end_id].y))
                    arr = np.asarray(pts, float)
                    closed = end_id == rid and rid != 0 and len(arr) >= 6
                    edges.append(Edge(arr, rid, end_id, closed))

    # 노드에 닿지 않는 닫힌 고리 (원 등)
    for s in sorted(skset):
        if s in visited or s in regd:
            continue
        path = [s]
        visited.add(s)
        cur = s
        while True:
            cands = [cur + o for o in OFF if (cur + o) in skset and (cur + o) not in visited and (cur + o) not in regd]
            if not cands:
                break
            nxt = choose(cands, cur)
            path.append(nxt)
            visited.add(nxt)
            cur = nxt
        arr = np.asarray([to_xy(i) for i in path], float)
        closed = len(path) >= 6 and (path[0] - path[-1]) in OFFS
        edges.append(Edge(arr, 0, 0, closed))
    return nodes, edges


# --------------------------------------------------------------------------- 가지치기·병합


def _degrees(edges: list[Edge]) -> dict[int, list[tuple[int, str]]]:
    inc: dict[int, list[tuple[int, str]]] = {}
    for k, e in enumerate(edges):
        if e.a:
            inc.setdefault(e.a, []).append((k, "a"))
        if e.b:
            inc.setdefault(e.b, []).append((k, "b"))
    return inc


def _merge_degree2(nodes: dict[int, Node], edges: list[Edge]) -> tuple[list[Edge], bool]:
    changed = False
    while True:
        inc = _degrees(edges)
        target = None
        for nid, lst in inc.items():
            if nodes[nid].kind == "junction" and len(lst) == 2:
                target = (nid, lst)
                break
        if target is None:
            break
        nid, lst = target
        (k1, w1), (k2, w2) = lst
        if k1 == k2:  # 노드를 지나는 고리 → 닫힌 경로
            e = edges[k1]
            e.a = e.b = 0
            e.closed = True
            changed = True
            continue
        e1, e2 = edges[k1], edges[k2]
        if e1.b == nid:
            p1, o1 = e1.pts, e1.a
        else:
            p1, o1 = e1.pts[::-1], e1.b
        if e2.a == nid:
            p2, o2 = e2.pts, e2.b
        else:
            p2, o2 = e2.pts[::-1], e2.a
        pts = np.vstack([p1, p2[1:]])
        closed = o1 == o2 and o1 != 0
        edges = [e for k, e in enumerate(edges) if k not in (k1, k2)] + [Edge(pts, o1, o2, closed)]
        changed = True
    return edges, changed


def prune(nodes: dict[int, Node], edges: list[Edge], spur_len: float, min_len: float) -> list[Edge]:
    """짧은 가지(스퍼)와 짧은 고립 선분을 지우고, 차수 2가 된 교차점을 이어 붙인다."""
    for _ in range(5):
        changed = False
        kept: list[Edge] = []
        for e in edges:
            if e.closed:
                if e.length < min_len:
                    changed = True
                    continue
                kept.append(e)
                continue
            ka = nodes[e.a].kind if e.a in nodes else "end"
            kb = nodes[e.b].kind if e.b in nodes else "end"
            L = e.length
            if ka == "end" and kb == "end":
                if L < min_len:
                    changed = True
                    continue
            elif (ka == "end" or kb == "end") and L < spur_len:
                changed = True
                continue
            kept.append(e)
        edges, merged = _merge_degree2(nodes, kept)
        if not (changed or merged):
            break
    return edges
