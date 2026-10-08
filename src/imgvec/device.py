"""장치 만들기 — 그림 + 접속점 명세 → 장치 모델(검증·DNCAD 배치 명세 포함).

명세(DeviceSpec)는 사람이 쓰거나 `imgvec device pick`이 만드는 입력이고, 결과(BuildResult)는
모델·검증 결과·배치 명세를 묶은 것이다.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path as FsPath

import numpy as np

from . import __version__
from .calib import Calibration, CalibrationError, from_pin_pitch, from_two_points, from_width, manual
from .dncad_rules import DEFAULT_RULES, DncadRules, normalize_side
from .geom import Circle, Primitive, sample_primitive, scale_primitive
from .layout import dncad_layout
from .model import Body, DeviceModel
from .params import Params
from .pins import PinSpec, resolve_pins
from .pipeline import VectorResult, convert_image
from .validate import Issue, validate

SPEC_SCHEMA = "device-spec/1.0"


class DeviceBuildError(ValueError):
    pass


@dataclass
class DeviceSpec:
    name: str
    image: str | None = None
    mm_per_px: float | None = None
    ref: tuple[float, float, float, float, float] | None = None   # x1 y1 x2 y2 길이(mm)
    body_width_mm: float | None = None                            # body_px 가로 폭의 실제 길이
    pin_pitch_mm: float | None = None                             # 같은 줄 접속점 간격
    body_px: tuple[float, float, float, float] | None = None
    preset: str = "auto"
    graphics: bool = True
    pins: list[PinSpec] = field(default_factory=list)

    # ------------------------------------------------------------------ JSON
    def to_dict(self) -> dict:
        scale: dict = {}
        if self.mm_per_px:
            scale["mm_per_px"] = self.mm_per_px
        if self.ref:
            scale["ref"] = list(self.ref)
        if self.body_width_mm:
            scale["body_width_mm"] = self.body_width_mm
        if self.pin_pitch_mm:
            scale["pin_pitch_mm"] = self.pin_pitch_mm
        pins = []
        for s in self.pins:
            d = {"name": s.name}
            for k in ("side", "offset_mm", "x_mm", "y_mm", "x_px", "y_px", "label", "group"):
                v = getattr(s, k)
                if v not in (None, ""):
                    d[k] = round(v, 4) if isinstance(v, float) else v
            if s.kind != "electrical":
                d["kind"] = s.kind
            pins.append(d)
        return {
            "schema": SPEC_SCHEMA,
            "name": self.name,
            "image": self.image,
            "scale": scale,
            "body_px": list(self.body_px) if self.body_px else None,
            "preset": self.preset,
            "graphics": self.graphics,
            "pins": pins,
        }

    @staticmethod
    def from_dict(d: dict) -> "DeviceSpec":
        if not str(d.get("schema", "")).startswith("device-spec/1."):
            raise DeviceBuildError(f"지원하지 않는 명세 스키마입니다: {d.get('schema')!r}")
        sc = d.get("scale", {}) or {}
        pins = []
        for i, p in enumerate(d.get("pins", []), start=1):
            if not str(p.get("name", "")).strip():
                raise DeviceBuildError(f"명세의 {i}번째 접속점에 이름이 없습니다")
            try:
                side = normalize_side(p.get("side"))
            except ValueError as e:
                raise DeviceBuildError(f"명세의 {i}번째 접속점: {e}") from e
            pins.append(
                PinSpec(
                    name=str(p["name"]), side=side, offset_mm=p.get("offset_mm"), x_mm=p.get("x_mm"), y_mm=p.get("y_mm"),
                    x_px=p.get("x_px"), y_px=p.get("y_px"), kind=str(p.get("kind", "electrical")),
                    label=str(p.get("label", "")), group=str(p.get("group", "")), line=i,
                )
            )
        bp = d.get("body_px")
        return DeviceSpec(
            name=str(d.get("name", "")),
            image=d.get("image"),
            mm_per_px=sc.get("mm_per_px"),
            ref=tuple(sc["ref"]) if sc.get("ref") else None,  # type: ignore[arg-type]
            body_width_mm=sc.get("body_width_mm"),
            pin_pitch_mm=sc.get("pin_pitch_mm"),
            body_px=tuple(bp) if bp else None,  # type: ignore[arg-type]
            preset=str(d.get("preset", "auto")),
            graphics=bool(d.get("graphics", True)),
            pins=pins,
        )

    def dump(self, path: str | FsPath) -> None:
        p = FsPath(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def load(path: str | FsPath) -> "DeviceSpec":
        try:
            return DeviceSpec.from_dict(json.loads(FsPath(path).read_text(encoding="utf-8-sig")))
        except json.JSONDecodeError as e:
            raise DeviceBuildError(f"명세 JSON을 읽을 수 없습니다: {e}") from e


@dataclass
class BuildResult:
    model: DeviceModel
    issues: list[Issue]
    layout: dict
    calibration: Calibration
    body_px: tuple[float, float, float, float]
    body_source: str
    vector: VectorResult | None = None

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


# =============================================================================== 본체 추정


def _poly_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def guess_body_px(prims: list[Primitive], size: tuple[int, int]) -> tuple[tuple[float, float, float, float], str] | None:
    """결과 도형에서 본체 사각형을 짐작한다.
    1) 직사각형에 가까운 가장 큰 닫힌 경로  2) 길고 반듯한 선들이 둘러싼 영역.  못 찾으면 None."""
    W, H = size
    img_area = float(W * H)
    best = None
    for p in prims:
        if isinstance(p, Circle) or not p.closed:
            continue
        pts = sample_primitive(p, 2.0)
        if len(pts) < 8:
            continue
        x0, y0, x1, y1 = pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()
        area_bb = (x1 - x0) * (y1 - y0)
        if area_bb < 0.08 * img_area or area_bb > 0.97 * img_area:
            continue
        if _poly_area(pts) / area_bb < 0.9:  # 직사각형·둥근 사각형만
            continue
        if best is None or area_bb > best[0]:
            best = (area_bb, (float(x0), float(y0), float(x1), float(y1)))
    if best:
        return best[1], "auto-closed-path"
    xs: list[float] = []
    ys: list[float] = []
    for p in prims:
        if isinstance(p, Circle):
            continue
        for g in p.segs:
            if g.__class__.__name__ != "LineSeg":
                continue
            (ax, ay), (bx, by) = g.p0, g.p1
            if abs(ax - bx) < 0.02 * abs(ay - by) and abs(ay - by) > 0.3 * H:
                xs.append(0.5 * (ax + bx))
            elif abs(ay - by) < 0.02 * abs(ax - bx) and abs(ax - bx) > 0.3 * W:
                ys.append(0.5 * (ay + by))
    if len(set(round(v) for v in xs)) >= 2 and len(set(round(v) for v in ys)) >= 2:
        return (min(xs), min(ys), max(xs), max(ys)), "auto-long-lines"
    return None


# =============================================================================== 보정


def resolve_calibration(spec: DeviceSpec, body_px: tuple[float, float, float, float] | None) -> Calibration:
    if spec.mm_per_px:
        return manual(spec.mm_per_px)
    if spec.ref:
        x1, y1, x2, y2, mm = spec.ref
        return from_two_points((x1, y1), (x2, y2), mm)
    if spec.body_width_mm:
        if body_px is None:
            raise DeviceBuildError("body_width_mm으로 축척을 정하려면 body_px(본체 위치)가 필요합니다")
        return from_width(body_px[2] - body_px[0], spec.body_width_mm)
    if spec.pin_pitch_mm:
        by_group: dict[str, list[float]] = {}
        for s in spec.pins:
            key = s.group or (s.side or "")
            if s.x_px is None or not key:
                continue
            if (s.side or "top") in ("left", "right"):
                continue
            by_group.setdefault(key, []).append(s.x_px)
        if not by_group:
            raise DeviceBuildError("pin_pitch_mm으로 축척을 정하려면 접속점에 x_px와 group(또는 side)이 필요합니다")
        xs = max(by_group.values(), key=len)
        try:
            return from_pin_pitch(xs, spec.pin_pitch_mm)
        except CalibrationError as e:
            raise DeviceBuildError(str(e)) from e
    raise DeviceBuildError("축척이 없습니다. scale에 mm_per_px · ref · body_width_mm · pin_pitch_mm 중 하나를 지정하세요 (축척 보정은 필수 단계입니다)")


# =============================================================================== 만들기


def _keep_graphics(prims: list[Primitive], w: float, h: float, pad: float) -> list[Primitive]:
    keep = []
    for p in prims:
        pts = sample_primitive(p, 1.0)
        if len(pts) and np.any((pts[:, 0] >= -pad) & (pts[:, 0] <= w + pad) & (pts[:, 1] >= -pad) & (pts[:, 1] <= h + pad)):
            keep.append(p)
    return keep


def build_device(
    rgb: np.ndarray | None,
    spec: DeviceSpec,
    *,
    vector: VectorResult | None = None,
    rules: DncadRules = DEFAULT_RULES,
    stamp: bool = True,
    remap_sides: dict[str, str] | None = None,
    fit: str = "pins",
) -> BuildResult:
    """rgb: 원본 이미지(그래픽을 쓰지 않고 body_px가 있으면 None도 가능). vector: 이미 변환한 결과를 재사용할 때."""
    H0 = W0 = 0
    if rgb is not None:
        H0, W0 = rgb.shape[:2]

    body_px = tuple(spec.body_px) if spec.body_px else None
    body_source = "spec" if body_px else ""

    vec = vector
    need_vec = spec.graphics or body_px is None
    if need_vec and vec is None:
        if rgb is None:
            raise DeviceBuildError("외형 도형이나 본체 추정에는 이미지가 필요합니다")
        mm_hint = spec.mm_per_px
        if mm_hint is None and spec.ref:
            x1, y1, x2, y2, mm = spec.ref
            mm_hint = mm / math.hypot(x2 - x1, y2 - y1)
        vec = convert_image(rgb, Params(preset=spec.preset, mm_per_px=mm_hint))

    if body_px is None:
        guess = guess_body_px(vec.prims, (W0, H0)) if vec is not None else None  # type: ignore[union-attr]
        if guess is not None:
            body_px, body_source = guess
        elif W0 and H0:
            body_px, body_source = (0.0, 0.0, float(W0), float(H0)), "image"
        else:
            raise DeviceBuildError("본체 위치(body_px)를 정할 수 없습니다. 명세에 body_px를 지정하세요")

    cal = resolve_calibration(spec, body_px)
    k = cal.mm_per_px
    bw, bh = (body_px[2] - body_px[0]) * k, (body_px[3] - body_px[1]) * k
    if bw <= 0 or bh <= 0:
        raise DeviceBuildError(f"본체 크기가 올바르지 않습니다: body_px={body_px}")

    pins = resolve_pins(spec.pins, bw, bh, body_px, k)

    graphics: list[Primitive] = []
    if spec.graphics and vec is not None:
        moved = [scale_primitive(p, k, -body_px[0] * k, -body_px[1] * k) for p in vec.prims]
        graphics = _keep_graphics(moved, bw, bh, pad=20.0)  # 본체 밖 단자·핀 선도 살리되 배경은 어느 정도 거른다

    model = DeviceModel(spec.name or "device", Body(bw, bh), pins, graphics)
    model.meta = {
        "imgvec": __version__,
        "source_image": spec.image,
        "size_px": [W0, H0] if W0 else None,
        "calibration": cal.to_dict(),
        "body_px": [round(v, 2) for v in body_px],
        "body_source": body_source,
        "preset": vec.kind if vec is not None else None,
    }
    if stamp:
        model.meta["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if vec is not None:
        m = vec.report["metrics"]
        model.meta["vector"] = {"primitives": len(vec.prims), "fit_p99_mm": m["fit"]["p99"], "edge_coverage": m["edge_coverage"]}

    issues = validate(model, rules)
    layout = dncad_layout(model, rules, fit=fit, remap_sides=remap_sides)
    return BuildResult(model, issues, layout, cal, body_px, body_source, vec)


def write_build_outputs(res: BuildResult, base: str | FsPath) -> dict[str, FsPath]:
    """<base>.device.json / .device.svg / .layout.json / .issues.txt 을 쓴다."""
    from .svgmodel import write_model_svg

    b = FsPath(base)
    b.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        "model": b.with_name(b.name + ".device.json"),
        "svg": b.with_name(b.name + ".device.svg"),
        "layout": b.with_name(b.name + ".layout.json"),
        "issues": b.with_name(b.name + ".issues.txt"),
    }
    res.model.dump(paths["model"])
    write_model_svg(paths["svg"], res.model)
    paths["layout"].write_text(json.dumps(res.layout, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [str(i) for i in res.issues] or ["문제 없음"]
    paths["issues"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths
