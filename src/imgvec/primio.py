"""도형(선·호·원·경로)을 JSON 호환 사전 목록으로 바꾸고 되돌린다."""
from __future__ import annotations

from .geom import ArcSeg, Circle, LineSeg, Path, Primitive


def _r(v: float, nd: int) -> float:
    return round(float(v), nd)


def prims_to_list(prims: list[Primitive], nd: int = 4) -> list[dict]:
    items: list[dict] = []
    for p in prims:
        if isinstance(p, Circle):
            items.append({"type": "circle", "c": [_r(p.cx, nd), _r(p.cy, nd)], "r": _r(p.r, nd)})
            continue
        segs = []
        for g in p.segs:
            if isinstance(g, LineSeg):
                segs.append({"type": "line", "p0": [_r(g.p0[0], nd), _r(g.p0[1], nd)], "p1": [_r(g.p1[0], nd), _r(g.p1[1], nd)]})
            else:
                segs.append({"type": "arc", "c": [_r(g.cx, nd), _r(g.cy, nd)], "r": _r(g.r, nd), "a0": _r(g.a0, 6), "a1": _r(g.a1, 6)})
        items.append({"type": "path", "closed": bool(p.closed), "segs": segs})
    return items


def prims_from_list(items: list[dict]) -> list[Primitive]:
    out: list[Primitive] = []
    for it in items:
        t = it.get("type")
        if t == "circle":
            out.append(Circle(float(it["c"][0]), float(it["c"][1]), float(it["r"])))
        elif t == "path":
            segs = []
            for g in it["segs"]:
                if g["type"] == "line":
                    segs.append(LineSeg((float(g["p0"][0]), float(g["p0"][1])), (float(g["p1"][0]), float(g["p1"][1]))))
                elif g["type"] == "arc":
                    segs.append(ArcSeg(float(g["c"][0]), float(g["c"][1]), float(g["r"]), float(g["a0"]), float(g["a1"])))
                else:
                    raise ValueError(f"알 수 없는 선분 종류: {g['type']}")
            out.append(Path(segs, bool(it.get("closed", False))))
        else:
            raise ValueError(f"알 수 없는 도형 종류: {t}")
    return out
