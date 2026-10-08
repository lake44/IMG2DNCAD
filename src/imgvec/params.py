"""변환 설정과 그림 종류별 프리셋.

사용자가 지정한 값(overrides)이 프리셋 기본값보다 우선한다.
이름이 `_px`로 끝나는 값은 별도 표기가 없으면 '작업 이미지' 픽셀 기준이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 프리셋: 선화(흑백 선 그림) / 일러스트(색 영역 그림, 제품 정면도 등) / 사진(실험적)
PRESETS: dict[str, dict[str, Any]] = {
    "lineart": {
        "upscale_target": 1600,      # 긴 변이 이보다 작으면 키운다
        "max_upscale": 3.0,
        "colors": 2,
        "palette_sep": 10.0,
        "min_color_frac": 0.0002,
        "denoise": 0.0,
        "thin_band_px": 0,
        "min_region_px": 10,
        "binarize": "otsu",          # otsu | sauvola
        "invert": "auto",            # auto | yes | no
        "ortho_snap_deg": 1.0,
    },
    "illustration": {
        "upscale_target": 1800,
        "max_upscale": 4.0,
        "colors": 10,
        "palette_sep": 10.0,
        "min_color_frac": 0.0002,
        "denoise": 0.0,
        "thin_band_px": 2,
        "min_region_px": 30,
        "binarize": "otsu",
        "invert": "no",
        "ortho_snap_deg": 1.0,
    },
    "photo": {
        "upscale_target": 0,
        "max_upscale": 1.0,
        "colors": 12,
        "palette_sep": 14.0,
        "min_color_frac": 0.0004,
        "denoise": 25.0,
        "thin_band_px": 3,
        "min_region_px": 150,
        "binarize": "otsu",
        "invert": "no",
        "ortho_snap_deg": 0.0,
    },
}

# 모든 프리셋에 공통인 기본값
COMMON: dict[str, Any] = {
    "max_side": 3200,                # 작업 이미지 긴 변 상한(px)
    "merge_px": None,                # 경계 쌍 병합 반경(작업 px). None=자동
    "tolerance_mm": 0.2,             # 피팅 허용 오차 (축척을 알 때)
    "tolerance_src_px": 0.75,        # 축척을 모를 때 (원본 px)
    "min_tol_work_px": 0.9,          # 작업 px 기준 허용 오차 하한 (픽셀 양자화 한계)
    "min_length_mm": 1.0,            # 이보다 짧은 선은 버림 (축척을 알 때)
    "min_length_src_px": 4.0,        # 축척을 모를 때 (원본 px)
    "allow_arcs": True,
    "extend_free_ends": True,
    "min_circle_r_work_px": 3.0,
}


@dataclass
class Params:
    preset: str = "auto"             # auto | lineart | illustration | photo
    mm_per_px: float | None = None   # 원본 픽셀 1개의 실제 길이(mm)
    overrides: dict[str, Any] = field(default_factory=dict)

    def resolve(self, kind: str) -> dict[str, Any]:
        """프리셋 + 공통값 + 사용자 지정값을 합친 설정 사전."""
        if kind not in PRESETS:
            raise ValueError(f"알 수 없는 프리셋: {kind} (lineart | illustration | photo)")
        cfg = {**COMMON, **PRESETS[kind]}
        cfg.update({k: v for k, v in self.overrides.items() if v is not None})
        cfg["kind"] = kind
        return cfg
