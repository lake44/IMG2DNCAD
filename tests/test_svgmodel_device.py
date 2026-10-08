import json
import math

import pytest

from imgvec.device import DeviceBuildError, DeviceSpec, build_device, guess_body_px, write_build_outputs
from imgvec.geom import ArcSeg, Circle, LineSeg, Path
from imgvec.model import Body, DeviceModel, Pin
from imgvec.picker import PickerState, next_name, save_picked
from imgvec.pins import PinSpec
from imgvec.svgmodel import SvgModelError, model_to_svg, parse_transform, svg_to_model
from imgvec.synth import make_device


def sample_model():
    return DeviceModel(
        "RT",
        Body(40.0, 22.0),
        [Pin("A1", "top", 5.0, 0.0, group="P3", src=(5.5, 4.0)), Pin("A2", "bottom", 10.0, 22.0, label="공통")],
        [
            Circle(10, 8, 3),
            Path([LineSeg((2, 2), (12, 2)), ArcSeg(12, 5, 3, -math.pi / 2, 0.0), LineSeg((15, 5), (15, 12))], closed=False),
            Path([LineSeg((20, 10), (30, 10)), LineSeg((30, 10), (30, 18)), LineSeg((30, 18), (20, 10))], closed=True),
        ],
    )


# ------------------------------------------------------------------ 주석 SVG
def test_annotated_svg_roundtrip():
    m = sample_model()
    r = svg_to_model(model_to_svg(m))
    assert (round(r.body.w, 3), round(r.body.h, 3)) == (40.0, 22.0)
    assert [(p.name, p.side, round(p.x, 3), round(p.y, 3), p.group, p.label) for p in r.pins] == [
        ("A1", "top", 5.0, 0.0, "P3", ""), ("A2", "bottom", 10.0, 22.0, "", "공통")]
    assert r.pins[0].src == pytest.approx((5.5, 4.0))
    assert len(r.graphics) == 3
    kinds = [type(g).__name__ for g in r.graphics[1].segs]
    assert kinds == ["LineSeg", "ArcSeg", "LineSeg"]
    arc = r.graphics[1].segs[1]
    assert (arc.cx, arc.cy, arc.r) == pytest.approx((12, 5, 3), abs=1e-3) and arc.a1 - arc.a0 == pytest.approx(math.pi / 2, abs=1e-3)


def test_svg_survives_moving_everything_with_a_group_transform():
    svg = model_to_svg(sample_model())
    moved = svg.replace('<rect data-role="body"', '<g transform="translate(100 50) scale(2)"><rect data-role="body"', 1)
    # 본체 사각형 바로 뒤의 모든 내용을 같은 그룹 안에 넣는다
    moved = moved.replace("</svg>", "</g></svg>")
    r = svg_to_model(moved)
    assert (round(r.body.w, 3), round(r.body.h, 3)) == (80.0, 44.0)               # 2배 확대가 반영된다
    assert [round(p.x, 3) for p in r.pins] == [10.0, 20.0]
    assert [p.side for p in r.pins] == ["top", "bottom"]


def test_svg_pin_moved_by_user_is_resnapped_to_nearest_edge():
    svg = model_to_svg(sample_model())
    svg = svg.replace('cx="5" cy="0"', 'cx="5" cy="1.4"')       # 위 변에서 1.4 mm 안쪽으로 끌었다
    r = svg_to_model(svg)
    p = next(p for p in r.pins if p.name == "A1")
    assert p.side == "top" and p.y == 0.0


def test_svg_without_body_rect_is_rejected():
    with pytest.raises(SvgModelError, match="body"):
        svg_to_model('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="1" cy="1" r="1"/></svg>')


def test_svg_path_commands_relative_and_curves():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="50mm" viewBox="0 0 50 50">'
        '<rect data-role="body" x="10" y="10" width="30" height="20"/>'
        '<g data-role="graphics"><path d="M 12 12 l 10 0 v 5 h -10 z"/><path d="M12 25 C 16 20, 20 20, 24 25"/>'
        '<rect x="30" y="12" width="6" height="6" rx="1"/></g></svg>'
    )
    m = svg_to_model(svg)
    assert (m.body.w, m.body.h) == (30.0, 20.0)
    assert len(m.graphics) == 3
    assert m.graphics[0].closed and len(m.graphics[0].segs) == 4
    assert any(isinstance(g, ArcSeg) for g in m.graphics[2].segs)           # 둥근 사각형의 모서리 호
    assert m.graphics[0].segs[0].p0 == pytest.approx((2.0, 2.0))             # 본체 왼쪽 위가 원점


def test_svg_units_are_scaled_to_mm():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="2in" height="1in" viewBox="0 0 200 100"><rect data-role="body" x="0" y="0" width="100" height="50"/></svg>'
    m = svg_to_model(svg)
    assert m.body.w == pytest.approx(25.4) and m.body.h == pytest.approx(12.7)


def test_parse_transform_rotate_about_point():
    a, b, c, d, e, f = parse_transform("rotate(90 10 10)")
    x, y = a * 20 + c * 10 + e, b * 20 + d * 10 + f                         # (20,10)을 (10,10) 둘레로 90°
    assert (round(x, 6), round(y, 6)) == (10.0, 20.0)


# ------------------------------------------------------------------ 만들기
@pytest.fixture(scope="module")
def tb_sample():
    return make_device(seed=1, pin_sides="tb", n_pins=4, name="tbsample")


def _spec_from(s, **kw):
    pins = [PinSpec(p["name"], p["side"], x_px=p["x_px"], y_px=p["y_px"], group=p["group"]) for p in s.pins]
    return DeviceSpec(name="TB", mm_per_px=s.mm_per_px, body_px=s.body_px, preset="lineart", pins=pins, **kw)


def test_build_device_from_synthetic_ground_truth(tb_sample):
    res = build_device(tb_sample.rgb, _spec_from(tb_sample), stamp=False)
    assert res.ok and res.body_source == "spec"
    assert res.model.body.w == pytest.approx(76.0, abs=0.01) and res.model.body.h == pytest.approx(52.0, abs=0.01)
    assert len(res.model.pins) == 8 and len(res.layout["pins"]) == 8
    top = res.model.pins_on("top")
    assert [p.name for p in top] == ["T1", "T2", "T3", "T4"]
    assert top[1].x - top[0].x == pytest.approx(7.5, abs=0.05)
    assert all(p.src is not None and p.src[1] < 0 for p in top)               # 단자 원은 본체 위쪽 바깥에 있다
    assert len(res.model.graphics) >= 10


@pytest.mark.parametrize("how", ["ref", "body_width", "pin_pitch"])
def test_calibration_alternatives_agree(tb_sample, how):
    s = tb_sample
    spec = _spec_from(s)
    spec.mm_per_px = None
    x0, y0, x1, y1 = s.body_px
    if how == "ref":
        spec.ref = (x0, y0, x1, y0, 76.0)
    elif how == "body_width":
        spec.body_width_mm = 76.0
    else:
        spec.pin_pitch_mm = 7.5
        for p in spec.pins:
            p.group = p.name[0]                                             # T / B 줄
    res = build_device(s.rgb, spec, stamp=False)
    assert res.calibration.mm_per_px == pytest.approx(0.1, rel=0.01)


def test_missing_scale_is_an_error(tb_sample):
    spec = _spec_from(tb_sample)
    spec.mm_per_px = None
    with pytest.raises(DeviceBuildError, match="축척"):
        build_device(tb_sample.rgb, spec)


def test_build_without_image_needs_body_and_no_graphics():
    spec = DeviceSpec(name="N", mm_per_px=0.1, body_px=(0, 0, 250, 175), graphics=False, pins=[PinSpec("1", "top", offset_mm=5.0), PinSpec("2", "top", offset_mm=10.0)])
    res = build_device(None, spec, stamp=False)
    assert res.ok and res.model.graphics == [] and res.model.body.w == pytest.approx(25.0)
    with pytest.raises(DeviceBuildError):
        build_device(None, DeviceSpec(name="N", mm_per_px=0.1, graphics=True, pins=spec.pins), stamp=False)


def test_left_right_pins_remapped_when_asked(tb_sample):
    s = make_device(seed=1, pin_sides="lr", n_pins=3)
    spec = _spec_from(s)
    from imgvec.layout import REMAP_LR

    res = build_device(s.rgb, spec, stamp=False, remap_sides=REMAP_LR)
    assert res.layout["skipped_pins"] == [] and len(res.layout["pins"]) == 6


def test_outputs_roundtrip_through_files(tmp_path, tb_sample):
    res = build_device(tb_sample.rgb, _spec_from(tb_sample), stamp=False)
    paths = write_build_outputs(res, tmp_path / "tb")
    back = DeviceModel.load(paths["model"])
    assert [p.name for p in back.pins] == [p.name for p in res.model.pins]
    from_svg = svg_to_model(paths["svg"])
    assert [(p.name, p.side) for p in from_svg.pins] == [(p.name, p.side) for p in res.model.pins]
    assert (from_svg.body.w, from_svg.body.h) == pytest.approx((res.model.body.w, res.model.body.h), abs=1e-3)
    assert json.loads(paths["layout"].read_text(encoding="utf-8"))["schema"].startswith("dncad-device-layout")


def test_spec_json_roundtrip_and_relative_image(tmp_path):
    spec = DeviceSpec(name="S", image="a.png", ref=(1, 2, 3, 4, 50.0), body_px=(1, 2, 30, 40), pins=[PinSpec("A", "top", x_px=5.5, y_px=6.5, group="P3")])
    spec.dump(tmp_path / "s.json")
    back = DeviceSpec.load(tmp_path / "s.json")
    assert back.ref == (1, 2, 3, 4, 50.0) and back.body_px == (1, 2, 30, 40)
    assert (back.pins[0].name, back.pins[0].side, back.pins[0].group) == ("A", "top", "P3")
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "device-spec/1.0", "pins": [{"side": "top"}]}', encoding="utf-8")
    with pytest.raises(DeviceBuildError, match="이름"):
        DeviceSpec.load(bad)


def test_guess_body_finds_the_largest_rectangle():
    big = Path([LineSeg((100, 100), (900, 100)), LineSeg((900, 100), (900, 700)), LineSeg((900, 700), (100, 700)), LineSeg((100, 700), (100, 100))], True)
    small = Path([LineSeg((200, 200), (300, 200)), LineSeg((300, 200), (300, 260)), LineSeg((300, 260), (200, 260)), LineSeg((200, 260), (200, 200))], True)
    body, how = guess_body_px([small, big], (1000, 800))
    assert how == "auto-rect" and body == pytest.approx((100, 100, 900, 700), abs=2)   # 4단계 검출기(detect.detect_body)
    assert guess_body_px([small], (1000, 800)) is None


# ------------------------------------------------------------------ 화면 없이 시험하는 지정 도구 논리
def test_next_name_increments_trailing_number():
    assert [next_name(x) for x in ("A1", "S11", "13", "L9", "PE", None, "X09")] == ["A2", "S12", "14", "L10", "PE1", "1", "X10"]


def test_picker_state_add_remove_undo_and_save(tmp_path):
    st = PickerState()
    st.group = "P3"
    st.add_pin(10.2, 20.4, "A1")
    st.add_pin(40, 21, "A2")
    st.set_body(100, 90, 5, 6)
    st.set_ref(0, 0, 100, 0, 25.0)
    assert st.body == (5, 6, 100, 90)
    assert st.remove_nearest(41, 22, 5) and [p["name"] for p in st.pins] == ["A1"]
    assert not st.remove_nearest(500, 500, 5)
    st.undo()                                                                 # 삭제 취소
    assert [p["name"] for p in st.pins] == ["A1", "A2"]
    spec_path, csv_path = save_picked(st, tmp_path / "x.spec.json", "DEV", "dev.png")
    spec = DeviceSpec.load(spec_path)
    assert spec.name == "DEV" and spec.image == "dev.png" and len(spec.pins) == 2 and spec.pins[0].group == "P3"
    assert csv_path.exists()
