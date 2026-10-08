"""작업대 — 그림 한 장을 DNCAD 장치까지 끌고 가는 한 묶음의 일 (5단계).

화면(`imgvec device studio`)과 명령줄이 **같은 논리**를 쓴다. 화면 없이도 전부 시험할 수 있도록
여기에는 Tkinter가 들어오지 않는다. 화면은 picker.py가 이 작업대를 감싼다.

    wb = Workbench()
    wb.open_image("그림.png")      # 열기
    wb.auto_detect()               # 자동 인식 (4단계)
    wb.set_scale(pin_pitch_mm=5.08)
    wb.build()                     # 장치 모델 + 검증 + DNCAD 배치 명세
    wb.save_outputs("out/MY")      # .device.json · .device.svg · .layout.json · .issues.txt
    wb.export_backup("out/MY.dncadbak")   # DNCAD 백업 (새 프로젝트) 또는 base=기존 백업에 합치기
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .device import BuildResult, DeviceBuildError, DeviceSpec, auto_spec, build_device, write_build_outputs
from .dncad_backup import KitResult, write_backup, write_merged_backup
from .dncad_rules import DEFAULT_RULES, DncadRules
from .imageio import load_image
from .layout import REMAP_LR
from .pins import PinSpec, write_pins_csv
from .validate import has_errors


class StudioError(ValueError):
    pass


@dataclass
class Workbench:
    """한 장치를 만드는 동안의 모든 상태. 어느 단계든 건너뛰거나 되돌아올 수 있다."""

    spec: DeviceSpec = field(default_factory=lambda: DeviceSpec(name="device"))
    image_path: Path | None = None
    rgb: np.ndarray | None = None
    note: dict = field(default_factory=dict)
    result: BuildResult | None = None
    remap_lr: bool = True
    rules: DncadRules = DEFAULT_RULES

    # ------------------------------------------------------------------ 열기
    def open_image(self, path: str | Path, *, name: str = "", preset: str = "auto") -> None:
        p = Path(path)
        self.rgb = load_image(p).rgb
        self.image_path = p
        self.spec = DeviceSpec(name=name or p.stem, image=p.name, preset=preset)
        self.note, self.result = {}, None

    def open_spec(self, path: str | Path) -> None:
        """이미 만든 명세를 불러와 이어서 손본다 (그림은 명세 옆에서 찾는다)."""
        p = Path(path)
        self.spec = DeviceSpec.load(p)
        self.note, self.result = {}, None
        img = p.parent / self.spec.image if self.spec.image else None
        if img and img.exists():
            self.image_path, self.rgb = img, load_image(img).rgb
        else:
            self.image_path, self.rgb = None, None

    # ------------------------------------------------------------------ 자동 인식
    def auto_detect(self, *, pin_prefix: str = "", keep_names: bool = True, read_names: bool = True,
                    try_bodies: bool = False, from_labels: bool = False) -> dict:
        """그림만 보고 본체·접속점을 채운다.
        read_names면 접속점 옆 글자를 읽어 이름을 붙이고, keep_names면 **이미 사람이 고친 이름**을 지키지 않고
        자리 순서대로 물려준다 (읽은 이름이 있으면 그것이 이긴다)."""
        if self.rgb is None:
            raise StudioError("먼저 그림을 여세요")
        old = [p.name for p in self.spec.pins]
        found, note = auto_spec(self.rgb, name=self.spec.name, image=self.spec.image,
                                preset=self.spec.preset, pin_prefix=pin_prefix, read_names=read_names,
                                try_bodies=try_bodies, from_labels=from_labels)
        self.spec.body_px = found.body_px
        self.spec.pins = found.pins
        if keep_names and old and not note.get("named"):
            for i, p in enumerate(self.spec.pins):
                if i < len(old):
                    p.name = old[i]
        self.note = note
        self.result = None
        return note

    # ------------------------------------------------------------------ 손질
    def add_pin(self, x_px: float, y_px: float, name: str, *, side: str | None = None, group: str = "") -> PinSpec:
        p = PinSpec(name=name, side=side, x_px=round(float(x_px), 2), y_px=round(float(y_px), 2),
                    group=group, line=len(self.spec.pins) + 1)
        self.spec.pins.append(p)
        self.result = None
        return p

    def remove_pin_near(self, x_px: float, y_px: float, max_dist: float = 15.0) -> PinSpec | None:
        best, bi = None, -1
        for i, p in enumerate(self.spec.pins):
            if p.x_px is None or p.y_px is None:
                continue
            d = float(np.hypot(p.x_px - x_px, p.y_px - y_px))
            if best is None or d < best:
                best, bi = d, i
        if best is None or best > max_dist:
            return None
        self.result = None
        return self.spec.pins.pop(bi)

    def rename_pins(self, names: list[str]) -> None:
        if len(names) != len(self.spec.pins):
            raise StudioError(f"이름 {len(names)}개와 접속점 {len(self.spec.pins)}개의 수가 다릅니다")
        for p, n in zip(self.spec.pins, names):
            p.name = n
        self.result = None

    def set_body(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.spec.body_px = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.result = None

    def set_scale(self, *, mm_per_px: float | None = None, body_width_mm: float | None = None,
                  pin_pitch_mm: float | None = None, ref: tuple[float, float, float, float, float] | None = None) -> None:
        """축척은 하나만 쓴다 — 나중에 준 것이 앞의 것을 지운다."""
        self.spec.mm_per_px = mm_per_px
        self.spec.body_width_mm = body_width_mm
        self.spec.pin_pitch_mm = pin_pitch_mm
        self.spec.ref = ref
        self.result = None

    @property
    def has_scale(self) -> bool:
        return bool(self.spec.mm_per_px or self.spec.body_width_mm or self.spec.pin_pitch_mm or self.spec.ref)

    # ------------------------------------------------------------------ 만들기
    def build(self) -> BuildResult:
        if not self.spec.pins:
            raise StudioError("접속점이 하나도 없습니다 (접속점 0개 블랙박스는 DNCAD 검증에서 막힙니다)")
        if not self.has_scale:
            raise StudioError("축척이 없습니다. mm/px · 본체 폭 · 접속점 간격 · 두 점 중 하나를 정하세요")
        self.result = build_device(self.rgb, self.spec, rules=self.rules,
                                   remap_sides=REMAP_LR if self.remap_lr else None)
        return self.result

    @property
    def ok(self) -> bool:
        return self.result is not None and not has_errors(self.result.issues)

    def status(self) -> str:
        """한 줄 상태 — 화면 아래에 그대로 쓴다."""
        bits = [f"접속점 {len(self.spec.pins)}개",
                f"본체 {'있음' if self.spec.body_px else '없음'}",
                f"축척 {'있음' if self.has_scale else '없음'}"]
        if self.result is not None:
            n_err = sum(i.severity == "error" for i in self.result.issues)
            n_warn = sum(i.severity == "warn" for i in self.result.issues)
            box = self.result.layout["box"]
            bits.append(f"블랙박스 {box['w']}x{box['h']} 단위")
            bits.append(f"검증 오류 {n_err}·경고 {n_warn}")
        return " · ".join(bits)

    # ------------------------------------------------------------------ 내보내기
    def save_spec(self, path: str | Path) -> tuple[Path, Path]:
        p = Path(path)
        self.spec.dump(p)
        base = p.name[: -len(".spec.json")] if p.name.endswith(".spec.json") else p.stem
        csv_path = p.with_name(base + ".pins.csv")
        write_pins_csv(csv_path, self.spec.pins)
        return p, csv_path

    def save_outputs(self, base: str | Path) -> dict[str, Path]:
        if self.result is None:
            self.build()
        assert self.result is not None
        return write_build_outputs(self.result, base)

    def export_backup(self, path: str | Path, *, base: str | Path | None = None, tag: str | None = None,
                      **kw: Any) -> KitResult:
        """배치 명세를 DNCAD 백업으로. base를 주면 그 백업에 **더한다**(원본은 읽기만)."""
        if self.result is None:
            self.build()
        assert self.result is not None
        if has_errors(self.result.issues):
            raise StudioError("검증 오류가 있는 장치는 내보내지 않습니다: "
                              + "; ".join(str(i) for i in self.result.issues if i.severity == "error"))
        layouts = [self.result.layout]
        tags = [tag] if tag else None
        if base is None:
            return write_backup(path, layouts, tags=tags, rules=self.rules, **kw)
        return write_merged_backup(path, base, layouts, tags=tags, rules=self.rules, **kw)


def run_pipeline(image: str | Path, *, name: str = "", preset: str = "auto", out_base: str | Path | None = None,
                 mm_per_px: float | None = None, body_width_mm: float | None = None, pin_pitch_mm: float | None = None,
                 backup: str | Path | None = None, merge_into: str | Path | None = None,
                 tag: str | None = None, read_names: bool = True,
                 try_bodies: bool = False, from_labels: bool = False) -> tuple[Workbench, dict[str, Path]]:
    """그림 → 자동 인식 → 장치 → (원하면) DNCAD 백업까지 한 번에. 화면 없이 도는 '한 번에' 길."""
    wb = Workbench()
    wb.open_image(image, name=name, preset=preset)
    wb.auto_detect(read_names=read_names, try_bodies=try_bodies, from_labels=from_labels)
    wb.set_scale(mm_per_px=mm_per_px, body_width_mm=body_width_mm, pin_pitch_mm=pin_pitch_mm)
    wb.build()
    files: dict[str, Path] = {}
    if out_base:
        files.update(wb.save_outputs(out_base))
    if backup or merge_into:
        target = Path(backup) if backup else Path(str(out_base or "out/장치") + ".dncadbak")
        wb.export_backup(target, base=merge_into, tag=tag)
        files["backup"] = target
    return wb, files


__all__ = ["Workbench", "StudioError", "DeviceBuildError", "run_pipeline"]
