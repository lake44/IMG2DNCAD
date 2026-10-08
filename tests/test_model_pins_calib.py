import math

import pytest

from imgvec import calib
from imgvec.geom import Circle, LineSeg, Path
from imgvec.model import Body, DeviceModel, ModelError, Pin
from imgvec.pins import PinsFileError, PinSpec, parse_pins_csv, read_pins_csv, resolve_pins, write_pins_csv


# ------------------------------------------------------------------ 모델
def _model():
    return DeviceModel(
        "T1",
        Body(25.0, 17.5),
        [Pin("1", "top", 5.0, 0.0, group="P3", src=(5.4, 3.0)), Pin("PE", "bottom", 15.0, 17.5, label="접지")],
        [Circle(3, 4, 1.5), Path([LineSeg((0, 0), (5, 0)), LineSeg((5, 0), (5, 5))], closed=False)],
        meta={"k": 1},
    )


def test_model_json_roundtrip(tmp_path):
    m = _model()
    m.dump(tmp_path / "m.json")
    r = DeviceModel.load(tmp_path / "m.json")
    assert r.name == "T1" and r.body.w == 25.0 and r.body.h == 17.5
    assert [(p.name, p.side, p.x, p.y, p.group, p.label) for p in r.pins] == [("1", "top", 5.0, 0.0, "P3", ""), ("PE", "bottom", 15.0, 17.5, "", "접지")]
    assert r.pins[0].src == (5.4, 3.0)
    assert len(r.graphics) == 2 and r.meta == {"k": 1}


def test_model_rejects_wrong_schema():
    d = _model().to_dict()
    d["schema"] = "device-model/9.0"
    with pytest.raises(ModelError):
        DeviceModel.from_dict(d)
    d = _model().to_dict()
    del d["body"]
    d["schema"] = "device-model/1.0"
    with pytest.raises(ModelError):
        DeviceModel.from_dict(d)


# ------------------------------------------------------------------ 축척
def test_calibration_two_point_and_width():
    c = calib.from_two_points((100, 50), (400, 50), 30.0)
    assert math.isclose(c.mm_per_px, 0.1) and c.method == "two-point"
    assert math.isclose(calib.from_width(580, 45).mm_per_px, 45 / 580)
    assert math.isclose(calib.from_dpi(254).mm_per_px, 0.1)


def test_calibration_pin_pitch_tolerates_skipped_slot():
    xs = [100, 162, 224, 348, 410]            # 간격 62 px, 하나는 두 칸을 건너뜀
    c = calib.from_pin_pitch(xs, 5.08)
    assert math.isclose(c.mm_per_px, 5.08 / 62, rel_tol=1e-3)


@pytest.mark.parametrize("bad", [lambda: calib.from_two_points((1, 1), (1, 1), 5), lambda: calib.from_width(0, 5), lambda: calib.manual(-1), lambda: calib.from_pin_pitch([1, 2], 5)])
def test_calibration_rejects_invalid(bad):
    with pytest.raises(calib.CalibrationError):
        bad()


# ------------------------------------------------------------------ 접속점 CSV
def test_csv_korean_headers_and_delimiter():
    text = "이름;변;오프셋;그룹\nA1;상;5;P3\nS31;상;15;P3\nA2;하;5;\n"
    specs = parse_pins_csv(text)
    assert [(s.name, s.side, s.offset_mm, s.group) for s in specs] == [("A1", "top", 5.0, "P3"), ("S31", "top", 15.0, "P3"), ("A2", "bottom", 5.0, "")]


def test_csv_cp949_file_and_roundtrip(tmp_path):
    p = tmp_path / "pins.csv"
    p.write_bytes("name,side,offset_mm,label\nL1,top,5,전원\n".encode("cp949"))
    specs = read_pins_csv(p)
    assert specs[0].label == "전원"
    out = tmp_path / "out.csv"
    write_pins_csv(out, specs)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")        # 엑셀용 BOM
    assert read_pins_csv(out)[0].name == "L1"


def test_csv_errors_name_the_line():
    with pytest.raises(PinsFileError, match="3번째 줄"):
        parse_pins_csv("name,side\nA1,top\n,top\n")
    with pytest.raises(PinsFileError, match="알 수 없는 변"):
        parse_pins_csv("name,side\nA1,옆\n")
    with pytest.raises(PinsFileError, match="숫자"):
        parse_pins_csv("name,x_px\nA1,abc\n")
    with pytest.raises(PinsFileError, match="name"):
        parse_pins_csv("foo,bar\n1,2\n")


def test_resolve_pins_from_px_infers_side_and_projects_to_edge():
    specs = [PinSpec("A", x_px=140, y_px=105), PinSpec("B", x_px=160, y_px=340), PinSpec("C", side="top", offset_mm=7.0)]
    pins = resolve_pins(specs, body_w=20.0, body_h=30.0, body_px=(100, 100, 300, 400), mm_per_px=0.1)
    a, b, c = pins
    assert (a.side, round(a.x, 2), a.y) == ("top", 4.0, 0.0)                 # 위 변에 가장 가깝다
    assert (b.side, round(b.x, 2), b.y) == ("bottom", 6.0, 30.0)
    assert (c.side, c.x, c.y) == ("top", 7.0, 0.0)
    assert a.src == pytest.approx((4.0, 0.5))


def test_resolve_pins_collects_all_errors():
    with pytest.raises(PinsFileError) as e:
        resolve_pins([PinSpec("A", line=2), PinSpec("B", x_px=5, y_px=5, line=3)], 20, 30, None, None)
    assert "2번째 줄" in str(e.value) and "3번째 줄" in str(e.value)
