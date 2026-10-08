import cv2
import numpy as np

from imgvec.skeleton import build_graph, prune, skeleton_of


def _graph(mask, spur=6.0, min_len=6.0):
    sk = skeleton_of(mask)
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    nodes, edges = build_graph(sk, dist)
    return nodes, prune(nodes, edges, spur_len=spur, min_len=min_len)


def test_t_shape_has_one_junction_and_three_branches():
    m = np.zeros((120, 160), bool)
    m[20:26, 20:140] = True      # 가로 막대
    m[26:100, 77:83] = True      # 세로 줄기
    nodes, edges = _graph(m)
    assert sum(1 for n in nodes.values() if n.kind == "junction") == 1
    assert len(edges) == 3
    assert not any(e.closed for e in edges)


def test_ring_is_single_closed_edge():
    m = np.zeros((140, 140), np.uint8)
    cv2.circle(m, (70, 70), 40, 1, 5)
    nodes, edges = _graph(m.astype(bool))
    assert len(edges) == 1 and edges[0].closed


def test_short_spur_is_pruned():
    m = np.zeros((100, 200), bool)
    m[48:52, 10:190] = True      # 긴 선
    m[40:48, 98:102] = True      # 짧은 돌기
    nodes, edges = _graph(m, spur=12.0)
    assert len(edges) == 1
    assert edges[0].length > 150
