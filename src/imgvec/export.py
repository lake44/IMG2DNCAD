"""변환 결과를 JSON으로 내보내기 (장치 모델이 읽을 수 있는 중간 형식)."""
from __future__ import annotations

import json
from pathlib import Path as FsPath

from .geom import scale_primitive
from .pipeline import VectorResult
from .primio import prims_to_list

SCHEMA = "imgvec-primitives/0.1"


def primitives_to_dict(res: VectorResult, source: str = "") -> dict:
    """도형 목록을 사전으로. 축척을 알면 mm, 모르면 원본 px 단위이며 y는 아래로 증가한다."""
    s = res.mm_per_px if res.mm_per_px else 1.0
    return {
        "schema": SCHEMA,
        "source": source,
        "size_px": [res.size[0], res.size[1]],
        "unit": "mm" if res.mm_per_px else "px",
        "mm_per_px": res.mm_per_px,
        "y_axis": "down",
        "angle_unit": "rad",
        "primitives": prims_to_list([scale_primitive(p, s) for p in res.prims]),
    }


def write_primitives_json(path: str | FsPath, res: VectorResult, source: str = "") -> None:
    p = FsPath(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(primitives_to_dict(res, source), ensure_ascii=False, indent=1), encoding="utf-8")


def write_report_json(path: str | FsPath, res: VectorResult, source: str = "") -> None:
    p = FsPath(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"source": source, **res.report}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
