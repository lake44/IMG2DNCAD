"""장치 모델 — 본체(블랙박스)·접속점·외형 도형을 한곳에 모은 중간 데이터.

좌표계: 원점 = 본체의 왼쪽 위 모서리, x는 오른쪽, y는 아래(DNCAD·SVG·이미지와 같다), 단위 mm.
접속점의 (x, y)는 본체 경계 위의 부착점이고, 원본 그림에서 보인 위치는 `src`에 따로 둔다.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path as FsPath
from typing import Any

from .dncad_rules import SIDES
from .geom import Primitive
from .primio import prims_from_list, prims_to_list

SCHEMA = "device-model/1.0"


class ModelError(ValueError):
    """장치 모델 파일이 형식에 맞지 않을 때."""


@dataclass
class Body:
    w: float
    h: float
    shape: str = "rect"


@dataclass
class Pin:
    name: str                              # 접속점 이름(DNCAD 'designation'). 장치 안에서 유일
    side: str                              # top | bottom | left | right
    x: float                               # 본체 경계 위 부착점 (mm)
    y: float
    kind: str = "electrical"               # DNCAD cp_type
    label: str = ""                        # 보조 설명
    group: str = ""                        # 단자열 이름 등 (예: P3, P4) — 배치 순서에 쓴다
    src: tuple[float, float] | None = None  # 원본 그림에서 보인 위치 (mm, 본체 기준)

    def offset(self) -> float:
        """변을 따른 거리: 위·아래 변은 x, 왼쪽·오른쪽 변은 y."""
        return self.x if self.side in ("top", "bottom") else self.y


@dataclass
class TextItem:
    value: str
    x: float
    y: float
    height: float = 2.5
    rotation: float = 0.0


@dataclass
class DeviceModel:
    name: str
    body: Body
    pins: list[Pin] = field(default_factory=list)
    graphics: list[Primitive] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    unit: str = "mm"

    def pins_on(self, side: str) -> list[Pin]:
        return sorted((p for p in self.pins if p.side == side), key=lambda p: (p.offset(), p.name))

    # ------------------------------------------------------------------ 직렬화
    def to_dict(self) -> dict:
        def pin_d(p: Pin) -> dict:
            d: dict[str, Any] = {"name": p.name, "side": p.side, "x": round(p.x, 4), "y": round(p.y, 4)}
            if p.kind != "electrical":
                d["kind"] = p.kind
            if p.label:
                d["label"] = p.label
            if p.group:
                d["group"] = p.group
            if p.src is not None:
                d["src"] = [round(p.src[0], 4), round(p.src[1], 4)]
            return d

        return {
            "schema": SCHEMA,
            "name": self.name,
            "unit": self.unit,
            "coords": "origin=body top-left, x right, y down",
            "body": {"shape": self.body.shape, "w": round(self.body.w, 4), "h": round(self.body.h, 4)},
            "pins": [pin_d(p) for p in self.pins],
            "graphics": prims_to_list(self.graphics),
            "texts": [{"value": t.value, "x": round(t.x, 4), "y": round(t.y, 4), "height": round(t.height, 4), "rotation": t.rotation} for t in self.texts],
            "meta": self.meta,
        }

    @staticmethod
    def from_dict(d: dict) -> "DeviceModel":
        schema = str(d.get("schema", ""))
        if not schema.startswith("device-model/1."):
            raise ModelError(f"지원하지 않는 스키마입니다: {schema!r} (device-model/1.x 필요)")
        try:
            b = d["body"]
            body = Body(float(b["w"]), float(b["h"]), str(b.get("shape", "rect")))
            pins = []
            for p in d.get("pins", []):
                src = p.get("src")
                pins.append(
                    Pin(
                        name=str(p["name"]),
                        side=str(p["side"]),
                        x=float(p["x"]),
                        y=float(p["y"]),
                        kind=str(p.get("kind", "electrical")),
                        label=str(p.get("label", "")),
                        group=str(p.get("group", "")),
                        src=(float(src[0]), float(src[1])) if src else None,
                    )
                )
            texts = [TextItem(str(t["value"]), float(t["x"]), float(t["y"]), float(t.get("height", 2.5)), float(t.get("rotation", 0.0))) for t in d.get("texts", [])]
            graphics = prims_from_list(d.get("graphics", []))
        except (KeyError, TypeError, ValueError, IndexError) as e:
            raise ModelError(f"장치 모델 형식 오류: {e!r}") from e
        return DeviceModel(str(d.get("name", "")), body, pins, graphics, texts, dict(d.get("meta", {})), str(d.get("unit", "mm")))

    def dump(self, path: str | FsPath) -> None:
        p = FsPath(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def load(path: str | FsPath) -> "DeviceModel":
        try:
            data = json.loads(FsPath(path).read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            raise ModelError(f"JSON을 읽을 수 없습니다: {e}") from e
        return DeviceModel.from_dict(data)

    # ------------------------------------------------------------------ 편의
    def graphics_bbox(self) -> tuple[float, float, float, float] | None:
        from .geom import sample_primitive

        pts = [sample_primitive(p, 1.0) for p in self.graphics]
        pts = [a for a in pts if len(a)]
        if not pts:
            return None
        import numpy as np

        a = np.vstack(pts)
        return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())

    def is_finite(self) -> bool:
        vals = [self.body.w, self.body.h] + [v for p in self.pins for v in (p.x, p.y)]
        return all(math.isfinite(v) for v in vals)


def side_edge_point(side: str, offset: float, w: float, h: float) -> tuple[float, float]:
    """변과 변을 따른 거리에서 부착점 좌표."""
    if side == "top":
        return offset, 0.0
    if side == "bottom":
        return offset, h
    if side == "left":
        return 0.0, offset
    if side == "right":
        return w, offset
    raise ValueError(f"알 수 없는 변: {side!r} {SIDES}")
