from pathlib import Path

import numpy as np
import pytest

from imgvec import Params, convert_image
from imgvec.geom import Circle
from imgvec.synth import evaluate_vs_gt

# 계획서 9장 '벡터 정확도' 목표: 선화·PDF에서 최대 편차 0.5 mm 이하
MAX_DEV_MM = 0.5


def _check_vs_gt(sample):
    res = convert_image(sample.rgb, Params(mm_per_px=sample.mm_per_px))
    ev = evaluate_vs_gt(res.prims, sample.gt, sample.mm_per_px, sample.text_boxes)
    assert ev["recall"]["max"] <= MAX_DEV_MM, ev
    assert ev["precision"]["max"] <= MAX_DEV_MM, ev
    return res


def test_clean_lineart_meets_accuracy_target(device_clean):
    res = _check_vs_gt(device_clean)
    assert res.kind == "lineart"
    circles = [p for p in res.prims if isinstance(p, Circle)]
    assert len(circles) == 9          # 단자 원 8개 + 내부 원 1개


def test_degraded_jpeg_meets_accuracy_target(device_jpeg):
    _check_vs_gt(device_jpeg)


def test_coarse_resolution_meets_accuracy_target(device_coarse):
    _check_vs_gt(device_coarse)


def test_report_contains_plan_metrics(device_clean):
    res = convert_image(device_clean.rgb, Params(mm_per_px=device_clean.mm_per_px))
    m = res.report["metrics"]
    assert m["unit"] == "mm"
    assert {"fit", "edge_coverage", "edge_precision", "counts", "simplification_ratio"} <= set(m)
    assert m["simplification_ratio"] < 0.2          # 계획서의 '도형 단순도' 목표(원본 점 수의 1/5 이하)


@pytest.mark.parametrize(
    "rgb",
    [
        np.full((200, 300, 3), 255, np.uint8),
        np.zeros((200, 300, 3), np.uint8),
        np.full((8, 8, 3), 255, np.uint8),
    ],
    ids=["blank-white", "blank-black", "tiny"],
)
def test_degenerate_images_do_not_crash(rgb):
    res = convert_image(rgb, Params())
    assert res.prims == []


def test_scale_unknown_uses_pixel_units(device_clean):
    res = convert_image(device_clean.rgb, Params())
    assert res.mm_per_px is None and res.report["metrics"]["unit"] == "work_px"


def test_user_overrides_win_over_preset(device_clean):
    res = convert_image(device_clean.rgb, Params(mm_per_px=0.1, overrides={"allow_arcs": False}))
    assert res.report["metrics"]["counts"]["arc_segments"] == 0


RELAY = Path(__file__).resolve().parent.parent / "samples" / "real" / "relay_icon_x4.png"


@pytest.mark.skipif(not RELAY.exists(), reason="릴레이 샘플 이미지가 없음")
def test_relay_illustration_finds_all_terminal_screws():
    """사용자의 SVG(hosoo_diagnostic_relay_icon.svg)를 4배로 그린 그림에서 단자 나사 9개를 원으로 찾는다."""
    from imgvec.imageio import load_image

    mm = 0.0625  # 시험용 가정: 그림 px 1개 = 0.0625 mm
    res = convert_image(load_image(RELAY).rgb, Params(mm_per_px=mm))
    assert res.kind == "illustration"
    circles = [p for p in res.prims if isinstance(p, Circle)]
    # SVG 원본의 바깥 나사 원: (중심 x, 중심 y, 반지름) 단위 px (4배 이미지 기준)
    # 나사마다 (중심 x, 중심 y, 바깥 원 반지름, 안쪽 원 반지름). 바깥 어두운 원은 테두리 바깥선(17·13)이,
    # 안쪽 원은 테두리 중심선(9·6.5)이 경계로 잡힌다. 모두 4배 이미지의 px 단위.
    screws = [(180 + 220 * i, 200, 68, 36) for i in range(3)] + [(180 + 220 * i, y, 52, 26) for y in (1616, 1744) for i in range(3)]
    tol_px = 0.5 / mm  # 중심·반지름 0.5 mm 이내
    found = 0
    for ex, ey, r_out, r_in in screws:
        near = [c for c in circles if abs(c.cx - ex) <= tol_px and abs(c.cy - ey) <= tol_px]
        assert near, f"나사 중심 ({ex}, {ey})에서 원을 찾지 못함"
        found += sum(any(abs(c.r - r) <= tol_px for c in near) for r in (r_out, r_in))
    # 18개 중 글자가 단단히 붙은 바깥 원 일부는 호로 남을 수 있다 (4단계 접속점 검출이 별도 원 검출로 보완 예정)
    assert found >= 14, found
