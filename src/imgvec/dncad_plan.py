"""배치 명세 → 생성기(api-build) 명세 — 방식 A 어댑터 (3단계).

`.dncadbak`(방식 B)는 파일을 만들어 **복원**하는 길이고, 이쪽은 사용자의 생성기가 먹는 명세를 만들어
**편집기 API로 바로 그리는** 길이다. 둘 다 같은 배치 명세(`dncad-device-layout/1.0`)를 읽는다.

생성기 명세의 op (실측, `documents/scripts/dncad/api-build.mjs`)

    {"op": "box",    "id": "-T1", "x1": …, "y1": …, "x2": …, "y2": …, "tag": "-T1"}
    {"op": "place",  "id": "c1", "tool": "tool-place-device-connection-point", "x": …, "y": …, "rot": 180}
    {"op": "cpname", "x": …, "y": …, "name": "A1"}

    상자 안(변 위)에 놓인 접속점은 그 상자의 장치에 합류한다. rot는 0이면 적지 않는다.
    파일은 {"name": …, "ops": [...]} 꼴이고, api-build가 관문·검증을 스스로 부른다.

좌표는 DNCAD 쪽 좌표(단위, 1 mm = 6.4)이고 **16의 배수**여야 한다 — 배치 명세가 이미 그 격자 위에 있다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .dncad_backup import KitError
from .layout import SCHEMA as LAYOUT_SCHEMA

GRID = 16
DCP_TOOL = "tool-place-device-connection-point"


def _snap(v: float) -> int:
    return int(round(v / GRID) * GRID)


def layout_to_ops(layout: dict, *, x: int, y: int, tag: str, start: int = 1) -> list[dict]:
    """배치 명세 하나를 (x, y)에 놓는 op 목록으로. x·y는 블랙박스 왼쪽 위, 16 격자에 맞춘다."""
    if layout.get("schema") != LAYOUT_SCHEMA:
        raise KitError(f"배치 명세 형식이 아닙니다 (schema={layout.get('schema')!r})")
    box = layout.get("box") or {}
    w, h = box.get("w"), box.get("h")
    if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
        raise KitError(f"블랙박스 크기가 올바르지 않습니다 ({w}x{h})")
    pins = layout.get("pins") or []
    if not pins:
        raise KitError("접속점이 하나도 없습니다 (접속점 0개 블랙박스는 DNCAD 검증에서 막힙니다)")
    if not tag.startswith("-"):
        tag = "-" + tag
    x0, y0 = _snap(x), _snap(y)
    ops: list[dict] = [{"op": "box", "id": tag, "x1": x0, "y1": y0, "x2": x0 + w, "y2": y0 + h, "tag": tag}]
    for i, p in enumerate(pins, start=start):
        px, py = x0 + int(p["x"]), y0 + int(p["y"])
        place: dict[str, Any] = {"op": "place", "id": f"c{i}", "tool": DCP_TOOL, "x": px, "y": py}
        if int(p.get("rotation") or 0):
            place["rot"] = int(p["rotation"])
        ops.append(place)
        ops.append({"op": "cpname", "x": px, "y": py, "name": str(p["designation"])})
    return ops


def plan_devices(items: Iterable[tuple[dict, str]], *, origin: tuple[int, int] = (320, 256),
                 gap: int = 128, page_w: int = 1900, row_gap: int = 256) -> list[dict]:
    """여러 장치를 한 쪽에 왼쪽부터 늘어놓는다 → op 목록. items는 (배치 명세, 태그) 짝."""
    ops: list[dict] = []
    cx, cy, row_h, n = origin[0], origin[1], 0, 1
    for layout, tag in items:
        w, h = layout["box"]["w"], layout["box"]["h"]
        if cx > origin[0] and cx + w > page_w - origin[0]:
            cx, cy, row_h = origin[0], cy + row_h + row_gap, 0
        ops += layout_to_ops(layout, x=cx, y=cy, tag=tag, start=n)
        n += len(layout["pins"])
        cx += w + gap
        row_h = max(row_h, h)
    return ops


def write_plan(path: str | Path, items: Iterable[tuple[dict, str]], *, name: str | None = None,
               **kw: Any) -> dict:
    """생성기 명세 파일을 쓴다 → {"name": …, "ops": [...]}"""
    p = Path(path)
    plan = {"name": name or p.stem, "ops": plan_devices(items, **kw)}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan
