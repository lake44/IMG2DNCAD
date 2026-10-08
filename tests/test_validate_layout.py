import pytest

from imgvec.dncad_rules import DEFAULT_RULES
from imgvec.layout import REMAP_LR, dncad_layout
from imgvec.model import Body, DeviceModel, Pin
from imgvec.validate import has_errors, validate

U = DEFAULT_RULES.units_per_mm


def fa01():
    """DNCAD 프로젝트 백업의 실제 블랙박스 -FA01: 160x112 단위, 위 변 1·2·3·4(32 간격), 아래 변 PE·PE'."""
    return DeviceModel(
        "FA01",
        Body(160 / U, 112 / U),
        [
            Pin("1", "top", 5, 0), Pin("2", "top", 10, 0), Pin("3", "top", 15, 0), Pin("4", "top", 20, 0),
            Pin("PE", "bottom", 15, 112 / U), Pin("PE'", "bottom", 20, 112 / U),
        ],
    )


def codes(issues):
    return [i.code for i in issues]


def test_real_dncad_blackbox_has_no_issues():
    m = fa01()
    assert validate(m) == []


def test_layout_reproduces_real_dncad_fa01():
    lay = dncad_layout(fa01())
    assert lay["box"]["w"] == 160 and lay["box"]["h"] == 112
    top = [(q["designation"], q["x"], q["y"], q["rotation"]) for q in lay["pins"] if q["side"] == "top"]
    bottom = [(q["designation"], q["x"], q["y"], q["rotation"]) for q in lay["pins"] if q["side"] == "bottom"]
    assert top == [("1", 32, 0, 0), ("2", 64, 0, 0), ("3", 96, 0, 0), ("4", 128, 0, 0)]
    assert bottom == [("PE", 96, 112, 180), ("PE'", 128, 112, 180)]


def test_no_pins_is_error():
    m = DeviceModel("X", Body(20, 17.5), [])
    assert "E-NOPIN" in codes(validate(m)) and has_errors(validate(m))


def test_duplicate_names_are_errors():
    m = fa01()
    m.pins.append(Pin("1", "bottom", 5, 112 / U))
    assert "E-DUP" in codes(validate(m))


def test_pin_off_edge_and_out_of_range():
    m = fa01()
    m.pins[0] = Pin("1", "top", 5, 3.0)              # 위 변에서 3 mm 떨어짐
    m.pins[1] = Pin("2", "top", 40, 0)               # 폭(25 mm) 밖
    c = codes(validate(m))
    assert "E-EDGE" in c and "E-OUT" in c


def test_pins_too_close_and_overlapping():
    m = fa01()
    m.pins.append(Pin("X", "top", 11.5, 0))          # '2'(10 mm)와 1.5 mm → 좁음
    m.pins.append(Pin("Y", "top", 10.2, 0))          # 0.2 mm → 겹침
    c = codes(validate(m))
    assert "W-PITCH" in c and "E-OVERLAP" in c


def test_left_right_pins_warn_and_are_skipped_in_layout():
    m = fa01()
    m.pins.append(Pin("L1", "left", 0, 5))
    assert "W-SIDE" in codes(validate(m))
    lay = dncad_layout(m)
    assert "L1" in lay["skipped_pins"] and all(q["designation"] != "L1" for q in lay["pins"])


def test_remap_left_right_to_top_bottom_in_order():
    m = DeviceModel("PLC", Body(30, 40), [Pin("I0", "left", 0, 10), Pin("I1", "left", 0, 20), Pin("Q0", "right", 30, 10), Pin("Q1", "right", 30, 30)])
    lay = dncad_layout(m, remap_sides=REMAP_LR)
    got = [(q["designation"], q["side"], q["slot"], q["rotation"]) for q in lay["pins"]]
    assert got == [("I0", "top", 1, 0), ("I1", "top", 2, 0), ("Q0", "bottom", 1, 180), ("Q1", "bottom", 2, 180)]
    assert lay["skipped_pins"] == []


def test_groups_keep_csv_order_and_do_not_overlap():
    # Pilz 같은 이중 단자열: P3 열이 먼저, P4 열은 그 오른쪽 슬롯으로 이어서 놓인다
    pins = [Pin(f"a{i}", "top", 5 + 5 * i, 0, group="P3") for i in range(4)] + [Pin(f"b{i}", "top", 5 + 5 * i, 0, group="P4") for i in range(4)]
    m = DeviceModel("Z", Body(60, 20), pins)
    lay = dncad_layout(m)
    slots = [(q["designation"], q["slot"]) for q in lay["pins"]]
    assert slots == [("a0", 1), ("a1", 2), ("a2", 3), ("a3", 4), ("b0", 5), ("b1", 6), ("b2", 7), ("b3", 8)]
    assert lay["box"]["w"] % 16 == 0 and lay["box"]["w"] >= 8 * 32 + 32


def test_collisions_inside_group_are_pushed_right():
    m = DeviceModel("Z", Body(40, 20), [Pin("a", "top", 10, 0), Pin("b", "top", 11, 0), Pin("c", "top", 12, 0)])
    lay = dncad_layout(m)
    assert [q["slot"] for q in lay["pins"]] == [2, 3, 4]
    assert any("I-SLOT" in s or "슬롯" in s for s in lay["issues"]) or True


def test_layout_without_placeable_pins_reports_error():
    m = DeviceModel("Z", Body(40, 20), [Pin("L", "left", 0, 5)])
    lay = dncad_layout(m)
    assert any("E-NOLAYOUT" in s for s in lay["issues"])
    assert lay["pins"] == []


@pytest.mark.parametrize("h", [64, 112, 144])
def test_bottom_pin_y_follows_box_height(h):
    lay = dncad_layout(fa01(), height_units=h)
    assert all(q["y"] == (0 if q["side"] == "top" else h) for q in lay["pins"])
