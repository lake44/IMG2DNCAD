"""정답(기하)을 아는 합성 장치 그림 생성기 — 변환 정확도 측정과 시험용.

`python -m imgvec.synth OUT_DIR` 로 샘플 이미지와 정답 JSON을 만든다.
좌표는 모두 '연속 좌표'(픽셀 (c, r)의 중심 = (c+0.5, r+0.5))이며 단위는 이미지 px이다.
"""
from __future__ import annotations

import io
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path as FsPath

import cv2
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree

from .geom import ArcSeg, Circle, LineSeg, Path, Primitive, sample_primitive

SS = 4          # 슈퍼샘플링 배율 (안티앨리어싱)
FX = 4          # cv2 고정소수점 비트


@dataclass
class SynthImage:
    rgb: np.ndarray
    mm_per_px: float
    gt: list[Primitive]
    text_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    name: str = ""
    body_px: tuple[float, float, float, float] | None = None
    pins: list[dict] = field(default_factory=list)   # {name, side, x_px, y_px, group} — 접속점 정답


class _Canvas:
    def __init__(self, w_px: int, h_px: int, stroke_px: float):
        self.w, self.h = w_px, h_px
        self.img = np.full((h_px * SS, w_px * SS), 255, np.uint8)
        self.t = max(1, int(round(stroke_px * SS)))
        self.gt: list[Primitive] = []
        self.boxes: list[tuple[float, float, float, float]] = []

    @staticmethod
    def _p(u: float, v: float) -> tuple[int, int]:
        k = 1 << FX
        return int(round((u * SS - 0.5) * k)), int(round((v * SS - 0.5) * k))

    def line(self, p0, p1, record=True):
        cv2.line(self.img, self._p(*p0), self._p(*p1), 0, self.t, cv2.LINE_8, FX)
        if record:
            self.gt.append(Path([LineSeg(tuple(p0), tuple(p1))]))

    def poly(self, pts, closed=True):
        n = len(pts)
        segs = [LineSeg(tuple(pts[i]), tuple(pts[(i + 1) % n])) for i in range(n if closed else n - 1)]
        for s in segs:
            self.line(s.p0, s.p1, record=False)
        self.gt.append(Path(segs, closed=closed))

    def circle(self, c, r, record=True):
        k = 1 << FX
        cv2.circle(self.img, self._p(*c), int(round(r * SS * k)), 0, self.t, cv2.LINE_8, FX)
        if record:
            self.gt.append(Circle(c[0], c[1], r))

    def arc(self, c, r, a0_deg, a1_deg, record=True):
        k = 1 << FX
        R = int(round(r * SS * k))
        cv2.ellipse(self.img, self._p(*c), (R, R), 0, a0_deg, a1_deg, 0, self.t, cv2.LINE_8, FX)
        if record:
            self.gt.append(Path([ArcSeg(c[0], c[1], r, math.radians(a0_deg), math.radians(a1_deg))]))

    def rrect(self, x0, y0, x1, y1, r):
        """둥근 사각형: 위 직선 → 오른쪽 위 호 → 오른쪽 직선 → 오른쪽 아래 호 → ... 순서."""
        lines = [
            ((x0 + r, y0), (x1 - r, y0)),
            ((x1, y0 + r), (x1, y1 - r)),
            ((x1 - r, y1), (x0 + r, y1)),
            ((x0, y1 - r), (x0, y0 + r)),
        ]
        arcs = [
            ((x1 - r, y0 + r), -90, 0),
            ((x1 - r, y1 - r), 0, 90),
            ((x0 + r, y1 - r), 90, 180),
            ((x0 + r, y0 + r), 180, 270),
        ]
        segs: list = []
        for (a, b), (c, s0, s1) in zip(lines, arcs):
            self.line(a, b, record=False)
            segs.append(LineSeg(a, b))
            self.arc(c, r, s0, s1, record=False)
            segs.append(ArcSeg(c[0], c[1], r, math.radians(s0), math.radians(s1)))
        self.gt.append(Path(segs, closed=True))

    def text(self, s, org, height_px):
        scale = height_px * SS / 22.0
        th = max(1, int(round(self.t * 0.6)))
        (tw, tH), base = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
        x, y = int(org[0] * SS), int(org[1] * SS)
        cv2.putText(self.img, s, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, 0, th, cv2.LINE_8)
        pad = 8 * SS  # 글자 획 주변 여유 (정답에서 문자는 제외하므로 넉넉히)
        self.boxes.append(((x - pad) / SS, (y - tH - pad) / SS, (x + tw + pad) / SS, (y + base + pad) / SS))

    def finish(self) -> np.ndarray:
        small = cv2.resize(self.img, (self.w, self.h), interpolation=cv2.INTER_AREA)
        return np.stack([small] * 3, axis=-1)


def make_device(
    seed: int = 0,
    mm_per_px: float = 0.1,
    stroke_mm: float = 0.4,
    n_pins: int = 4,
    blur: float = 0.0,
    noise: float = 0.0,
    jpeg_quality: int | None = None,
    shading: float = 0.0,
    with_text: bool = True,
    name: str = "",
    pin_sides: str = "lr",
    pin_pitch_mm: float = 7.5,
) -> SynthImage:
    """블랙박스형 장치 그림: 본체 사각형, 핀·단자 원, 내부 도형, 라벨.

    pin_sides='lr'  왼쪽·오른쪽 변에 핀(기본)   'tb'  위·아래 변에 핀 (DNCAD가 확인한 배치)
    """
    rng = np.random.default_rng(seed)
    W_mm, H_mm = 140.0, 100.0
    W, H = int(round(W_mm / mm_per_px)), int(round(H_mm / mm_per_px))
    cv = _Canvas(W, H, stroke_mm / mm_per_px)
    m = lambda x: x / mm_per_px  # noqa: E731  mm → px
    tb = pin_sides == "tb"
    pins: list[dict] = []

    x0, y0, x1, y1 = (m(32), m(24), m(108), m(76)) if tb else (m(32), m(14), m(108), m(86))
    cv.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)

    r_t = m(2.5)
    pin_len = m(10)
    if tb:
        n = max(1, min(n_pins, int((76 - 12) // pin_pitch_mm) + 1))
        for i in range(n):
            x = x0 + m(6 + pin_pitch_mm * i)
            cv.line((x, y0), (x, y0 - pin_len))
            cv.circle((x, y0 - pin_len - r_t), r_t)
            cv.line((x, y1), (x, y1 + pin_len))
            cv.circle((x, y1 + pin_len + r_t), r_t)
            pins.append({"name": f"T{i + 1}", "side": "top", "x_px": x, "y_px": y0 - pin_len - r_t, "group": ""})
            pins.append({"name": f"B{i + 1}", "side": "bottom", "x_px": x, "y_px": y1 + pin_len + r_t, "group": ""})
            if with_text:
                cv.text(f"T{i + 1}", (x - m(1.6), y0 - pin_len - 2 * r_t - m(1.2)), m(2.6))
                cv.text(f"B{i + 1}", (x - m(1.6), y1 + pin_len + 2 * r_t + m(4.2)), m(2.6))
        cv.circle((m(52), m(50)), m(9))
        cv.arc((m(86), m(38)), m(8), 30, 300)
        cv.line((m(70), y0), (m(70), m(34)))
        cv.line((m(70), m(34)), (m(100), m(34)))
        cv.line((m(38), m(58)), (m(58), m(70)))
        cv.rrect(m(78), m(54), m(100), m(70), m(3))
    else:
        ys = np.linspace(m(26), m(74), n_pins)
        for i, y in enumerate(ys):
            cv.line((x0 - pin_len, y), (x0, y))
            cv.circle((x0 - pin_len - r_t, y), r_t)
            cv.line((x1, y), (x1 + pin_len, y))
            cv.circle((x1 + pin_len + r_t, y), r_t)
            pins.append({"name": f"L{i + 1}", "side": "left", "x_px": x0 - pin_len - r_t, "y_px": float(y), "group": ""})
            pins.append({"name": f"{(i + 1) * 2}", "side": "right", "x_px": x1 + pin_len + r_t, "y_px": float(y), "group": ""})
            if with_text:
                cv.text(f"L{i + 1}", (x0 - pin_len - 2 * r_t - m(0.5), y - r_t - m(1.0)), m(3.0))
                cv.text(f"{(i + 1) * 2}", (x1 + pin_len, y - r_t - m(1.0)), m(3.0))

        # 내부 도형
        cv.circle((m(52), m(48)), m(9))
        cv.arc((m(86), m(34)), m(8), 30, 300)
        cv.line((m(70), y0), (m(70), m(30)))                 # 윗변에 닿는 T 교차
        cv.line((m(70), m(30)), (m(100), m(30)))             # L 교차
        cv.line((m(40), m(62)), (m(62), m(78)))              # 사선
        cv.rrect(m(78), m(52), m(100), m(72), m(3))          # 둥근 사각형

    img = cv.finish().astype(np.float32)
    if shading > 0:  # 조명 불균일: 배경이 한쪽으로 갈수록 어두워짐
        yy, xx = np.mgrid[0:H, 0:W]
        img = img * (1.0 - shading * (0.5 * xx / W + 0.5 * yy / H))[..., None].astype(np.float32)
    if blur > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    if noise > 0:
        img = img + rng.normal(0, noise, img.shape).astype(np.float32)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if jpeg_quality:
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, format="JPEG", quality=int(jpeg_quality))
        buf.seek(0)
        img = np.asarray(Image.open(buf).convert("RGB"))
    return SynthImage(img, mm_per_px, cv.gt, cv.boxes, name or f"device_s{seed}", (x0, y0, x1, y1), pins)


def make_block(
    seed: int = 0,
    mm_per_px: float = 0.1,
    stroke_mm: float = 0.4,
    top: tuple[str, ...] = ("A1", "A2", "A3", "A4"),
    bottom: tuple[str, ...] = ("Q1", "Q2", "Q3", "Q4"),
    title: str = "FUNC",
    pitch_mm: float = 10.0,
    stub_mm: float = 9.0,
    name: str = "",
) -> SynthImage:
    """블록도: 사각형 안에 기능 이름, 위·아래로 짧은 선(스텁)이 나가고 선 끝이 접속점, 이름은 사각형 안쪽에 쓴다."""
    W_mm, H_mm = 140.0, 90.0
    W, H = int(round(W_mm / mm_per_px)), int(round(H_mm / mm_per_px))
    cv = _Canvas(W, H, stroke_mm / mm_per_px)
    m = lambda v: v / mm_per_px  # noqa: E731
    n = max(len(top), len(bottom))
    body_w = pitch_mm * (n + 1)
    x0 = m((W_mm - body_w) / 2)
    x1 = m((W_mm + body_w) / 2)
    y0, y1 = m(26.0), m(64.0)
    cv.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)
    pins: list[dict] = []
    for i, nm in enumerate(top):
        x = x0 + m(pitch_mm * (i + 1))
        cv.line((x, y0), (x, y0 - m(stub_mm)))
        cv.text(nm, (x - m(1.1 * len(nm)), y0 + m(6.0)), m(3.2))
        pins.append({"name": nm, "side": "top", "x_px": x, "y_px": y0 - m(stub_mm), "group": ""})
    for i, nm in enumerate(bottom):
        x = x0 + m(pitch_mm * (i + 1))
        cv.line((x, y1), (x, y1 + m(stub_mm)))
        cv.text(nm, (x - m(1.1 * len(nm)), y1 - m(3.0)), m(3.2))
        pins.append({"name": nm, "side": "bottom", "x_px": x, "y_px": y1 + m(stub_mm), "group": ""})
    cv.text(title, ((x0 + x1) / 2 - m(1.6 * len(title)), (y0 + y1) / 2 + m(2.0)), m(5.0))
    img = cv.finish()
    return SynthImage(img, mm_per_px, cv.gt, cv.boxes, name or f"block_s{seed}", (x0, y0, x1, y1), pins)


def degrade(rgb: np.ndarray, *, blur: float = 0.0, noise: float = 0.0, shading: float = 0.0, jpeg_quality: int | None = None, scale: float = 1.0, seed: int = 0) -> np.ndarray:
    """스캔·사진 모사: 축소 → 조명 불균일 → 블러 → 잡음 → JPEG. 위치 정답은 축소 비율만큼만 바뀐다."""
    rng = np.random.default_rng(seed)
    img = rgb.astype(np.float32)
    if abs(scale - 1.0) > 1e-9:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    H, W = img.shape[:2]
    if shading > 0:
        yy, xx = np.mgrid[0:H, 0:W]
        img = img * (1.0 - shading * (0.5 * xx / W + 0.5 * yy / H))[..., None].astype(np.float32)
    if blur > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    if noise > 0:
        img = img + rng.normal(0, noise, img.shape).astype(np.float32)
    out = np.clip(img, 0, 255).astype(np.uint8)
    if jpeg_quality:
        buf = io.BytesIO()
        Image.fromarray(out).save(buf, format="JPEG", quality=int(jpeg_quality))
        buf.seek(0)
        out = np.asarray(Image.open(buf).convert("RGB"))
    return out


# --------------------------------------------------------------------------- 정답 비교


def _in_boxes(pts: np.ndarray, boxes) -> np.ndarray:
    m = np.zeros(len(pts), bool)
    for (a, b, c, d) in boxes:
        m |= (pts[:, 0] >= a) & (pts[:, 0] <= c) & (pts[:, 1] >= b) & (pts[:, 1] <= d)
    return m


def evaluate_vs_gt(
    out: list[Primitive],
    gt: list[Primitive],
    mm_per_px: float,
    text_boxes=(),
    step: float = 0.5,
) -> dict:
    """결과 도형과 정답 도형의 거리(mm). recall: 정답→결과, precision: 결과→정답(문자 상자 제외)."""
    S_out = np.vstack([sample_primitive(p, step) for p in out]) if out else np.zeros((0, 2))
    S_gt = np.vstack([sample_primitive(p, step) for p in gt])
    res: dict = {}
    if len(S_out) == 0:
        return {"recall_max_mm": None, "precision_max_mm": None}
    d_gt = cKDTree(S_out).query(S_gt)[0] * mm_per_px
    keep = ~_in_boxes(S_out, text_boxes) if len(text_boxes) else np.ones(len(S_out), bool)
    d_out = cKDTree(S_gt).query(S_out[keep])[0] * mm_per_px
    res["recall"] = {"max": round(float(d_gt.max()), 4), "p99": round(float(np.percentile(d_gt, 99)), 4), "mean": round(float(d_gt.mean()), 4)}
    res["precision"] = {
        "max": round(float(d_out.max()), 4) if len(d_out) else None,
        "p99": round(float(np.percentile(d_out, 99)), 4) if len(d_out) else None,
        "mean": round(float(d_out.mean()), 4) if len(d_out) else None,
    }
    return res


def gt_to_json(s: SynthImage) -> dict:
    def seg(g):
        if isinstance(g, LineSeg):
            return {"type": "line", "p0": list(g.p0), "p1": list(g.p1)}
        return {"type": "arc", "c": [g.cx, g.cy], "r": g.r, "a0": g.a0, "a1": g.a1}

    items = []
    for p in s.gt:
        if isinstance(p, Circle):
            items.append({"type": "circle", "c": [p.cx, p.cy], "r": p.r})
        else:
            items.append({"type": "path", "closed": p.closed, "segs": [seg(g) for g in p.segs]})
    return {
        "name": s.name,
        "mm_per_px": s.mm_per_px,
        "body_px": list(s.body_px) if s.body_px else None,
        "pins": s.pins,
        "primitives": items,
        "text_boxes": s.text_boxes,
    }


SAMPLE_SPECS = [
    dict(name="device_clean", seed=1),
    dict(name="device_blur", seed=2, blur=1.2),
    dict(name="device_noisy", seed=3, blur=0.8, noise=10.0),
    dict(name="device_jpeg", seed=4, blur=0.8, jpeg_quality=55),
    dict(name="device_coarse", seed=5, mm_per_px=0.25),
    dict(name="device_pins6", seed=6, n_pins=6, stroke_mm=0.3),
]


def write_samples(out_dir: str | FsPath) -> list[SynthImage]:
    d = FsPath(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    made = []
    for spec in SAMPLE_SPECS:
        s = make_device(**spec)
        Image.fromarray(s.rgb).save(str(d / f"{s.name}.png"))
        (d / f"{s.name}.gt.json").write_text(json.dumps(gt_to_json(s), ensure_ascii=False, indent=1), encoding="utf-8")
        made.append(s)
    lines = ["file,mm_per_px"] + [f"{s.name}.png,{s.mm_per_px}" for s in made]
    (d / "scales.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return made


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "samples/synthetic"
    for s in write_samples(target):
        print("생성:", s.name, s.rgb.shape[1], "x", s.rgb.shape[0], "px, mm/px =", s.mm_per_px)
