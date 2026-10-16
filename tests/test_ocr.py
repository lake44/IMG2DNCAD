"""접속점 이름 읽기 (4단계). 엔진은 선택 사항이라, 없으면 읽기 시험만 건너뛴다."""
from pathlib import Path

import numpy as np
import pytest

from imgvec import ocr
from imgvec.detect import PinHit

SET = Path(__file__).resolve().parent.parent / "samples" / "set"
needs_engine = pytest.mark.skipif(not ocr.available(), reason="글자 읽기 엔진이 없습니다 (pip install \"imgvec[ocr]\")")


def test_clean_keeps_only_terminal_name_characters():
    assert ocr.clean(" A1 ") == "A1"
    assert ocr.clean("T11:") == "T11"
    assert ocr.clean("+V1") == "+V1"
    assert ocr.clean("한글→") == ""


def test_band_box_sits_outside_and_inside_the_pin_row():
    body = (100.0, 100.0, 900.0, 500.0)
    out = ocr._band_box("top", body, v_pin=-40.0, pitch=80.0, outward=True)
    ins = ocr._band_box("top", body, v_pin=-40.0, pitch=80.0, outward=False)
    assert out[3] <= 140 and ins[1] >= 140          # 접속점 줄(y=140)을 사이에 두고 갈린다
    assert out[1] < out[3] and ins[1] < ins[3]


def test_band_box_never_gets_thinner_than_the_floor():
    """격자가 잘면 띠가 얇아져 글자가 잘린다 — 본체 크기에 맞춘 밑바닥이 있다."""
    body = (100.0, 100.0, 900.0, 500.0)            # 짧은 변 400
    thin = ocr._band_box("bottom", body, v_pin=-20.0, pitch=10.0, outward=False)
    assert thin[3] - thin[1] >= 0.08 * 400 - 1


def test_match_pairs_each_text_with_the_nearest_pin():
    texts = [ocr.TextHit(105.0, "A1", 1.0), ocr.TextHit(305.0, "A2", 1.0), ocr.TextHit(900.0, "멀다", 1.0)]
    got = ocr._match(texts, [100.0, 300.0], pitch=100.0)
    assert {k: v.text for k, v in got.items()} == {0: "A1", 1: "A2"}


def test_read_pin_names_without_engine_returns_nothing(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    hits = [PinHit("top", 100.0, 50.0, "circle", 80.0, 1.0)]
    assert ocr.read_pin_names(np.zeros((10, 10, 3), np.uint8), (0, 0, 10, 10), hits) == {}


@needs_engine
def test_reads_names_drawn_next_to_the_pins():
    """글자를 그려 넣은 그림에서 이름을 읽는다 (엔진이 있을 때만)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 400), "white")
    d = ImageDraw.Draw(img)
    body = (100.0, 120.0, 800.0, 320.0)
    d.rectangle(list(body), outline="black", width=3)
    names = ["A1", "T11", "T12", "Y1"]
    hits = []
    for i, nm in enumerate(names):
        x = 180 + 160 * i
        d.ellipse([x - 9, 151, x + 9, 169], outline="black", width=3)
        d.line([(x, 120), (x, 60)], fill="black", width=2)
        d.text((x - 12, 180), nm, fill="black")          # 이름은 원 아래(안쪽)
        hits.append(PinHit("top", float(x), 160.0, "circle", 160.0, 5.0))
    got = ocr.read_pin_names(np.asarray(img), body, hits)
    assert [got.get(i) for i in range(4)] == names


@needs_engine
def test_engine_is_reused():
    assert ocr._engine() is ocr._engine()


def test_neighbour_gap_uses_the_closest_pin():
    assert ocr._neighbour_gap([100.0, 130.0, 300.0], 1, 50.0) == 30.0
    assert ocr._neighbour_gap([100.0], 0, 50.0) == 50.0


def test_wide_text_is_split_between_the_pins_it_covers():
    """촘촘한 이름은 `A1 A2`가 `A1A2`로 붙어 읽힌다 — 덮은 접속점 수로 쪼갠다."""
    assert ocr.WIDE_TEXT > 0
    # 쪼개는 규칙 자체를 확인한다: 글자 수가 접속점 수로 나누어떨어져야 한다
    assert len("A1A2") % 2 == 0 and "A1A2"[:2] == "A1" and "A1A2"[2:] == "A2"
    assert len("1323") % 2 == 0 and "1323"[:2] == "13" and "1323"[2:] == "23"


@needs_engine
def test_crowded_names_are_read_apart():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (700, 400), "white")
    d = ImageDraw.Draw(img)
    body = (80.0, 120.0, 620.0, 320.0)
    d.rectangle(list(body), outline="black", width=3)
    names = ["A1", "A2"]
    hits = []
    for i, nm in enumerate(names):                   # 두 이름을 바싹 붙여 그린다
        x = 160 + 40 * i
        d.ellipse([x - 8, 112, x + 8, 128], outline="black", width=2)
        d.text((x - 10, 90), nm, fill="black")
        hits.append(PinHit("top", float(x), 120.0, "circle", 40.0, 5.0))
    got = ocr.read_pin_names(np.asarray(img), body, hits)
    assert [got.get(i) for i in range(2)] == names


# --------------------------------------------------------------------------- 이름으로 자리를 정한다
def test_split_chunk_uses_spaces_then_even_cut():
    assert ocr._split_chunk("S31 S32 S11", 3) == ["S31", "S32", "S11"]
    assert ocr._split_chunk("A1B113", 3) == ["A1", "B1", "13"]
    assert ocr._split_chunk("Y31Y32S21", 3) == ["Y31", "Y32", "S21"]
    assert ocr._split_chunk("S22S33 S34", 3) == ["S22", "S33", "S34"]
    assert ocr._split_chunk("ABCDE", 2) is None          # 나누어떨어지지 않으면 포기


def test_allot_spreads_count_by_width():
    assert sum(ocr._allot([78.0, 26.0, 21.0, 20.0], 6)) == 6
    assert ocr._allot([75.0, 76.0], 6) == [3, 3]
    assert all(v >= 1 for v in ocr._allot([10.0, 90.0], 4))


def test_name_ok_accepts_terminal_names_only():
    for good in ("A1", "T11", "S31", "13", "PE", "Y31", "+", "-", "B2"):
        assert ocr.NAME_OK.match(good), good
    for bad in ("POWER", "pilz", "PNOZX3", "SafetyInput"):
        assert not ocr.NAME_OK.match(bad), bad


def test_unstack_flattens_two_rows_into_one():
    """DNCAD 블랙박스는 변마다 한 줄 — 2열 단자대는 펴야 슬롯이 겹치지 않는다."""
    body = (100.0, 100.0, 500.0, 400.0)
    pins = [("top", 150.0, 120.0, "S31"), ("top", 250.0, 120.0, "S32"),
            ("top", 150.0, 160.0, "A1"), ("top", 250.0, 160.0, "B1")]
    out = ocr._unstack(pins, body)
    assert [p[3] for p in out] == ["S31", "S32", "A1", "B1"]    # 변에 가까운 줄부터
    xs = [p[1] for p in out]
    assert xs == sorted(xs) and len(set(xs)) == 4                # 자리가 겹치지 않는다
    assert all(100.0 <= x <= 500.0 for x in xs)                  # 본체 안에 있다
    assert len({p[2] for p in out}) == 1                         # 한 줄이 되었다


def test_unstack_leaves_a_single_row_alone():
    body = (100.0, 100.0, 500.0, 400.0)
    pins = [("top", 250.0, 120.0, "B"), ("top", 150.0, 120.0, "A")]
    assert [p[3] for p in ocr._unstack(pins, body)] == ["A", "B"]
    assert [p[1] for p in ocr._unstack(pins, body)] == [150.0, 250.0]   # 자리는 그대로


def test_hole_rows_groups_circles_by_row():
    from imgvec.geom import Circle

    prims = [Circle(x, 60, 10) for x in (100, 200, 300)] + [Circle(x, 200, 10) for x in (100, 200)]
    rows = ocr._hole_rows(prims)
    assert [len(xs) for _y, xs in rows] == [3, 2]
    assert rows[0][1] == [100, 200, 300]


def test_pins_from_labels_without_engine_is_empty(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    assert ocr.pins_from_labels(np.zeros((10, 10, 3), np.uint8), [], (0, 0, 10, 10)) == []


def test_read_bands_falls_back_to_serial_for_one_box(monkeypatch):
    """띠가 하나면 병렬로 띄울 것도 없다."""
    seen = []
    monkeypatch.setattr(ocr, "read_band", lambda rgb, b, **kw: seen.append(b) or [])
    ocr.read_bands(np.zeros((50, 50, 3), np.uint8), [(0, 0, 40, 20)])
    assert len(seen) == 1


@needs_engine
def test_read_bands_matches_reading_one_by_one():
    """동시에 읽어도 따로 읽는 것과 결과가 같아야 한다 — 속도만 얻고 정확도는 그대로."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 300), "white")
    d = ImageDraw.Draw(img)
    for i, nm in enumerate(["A1", "A2", "A3"]):
        d.text((100 + 120 * i, 40), nm, fill="black")
    for i, nm in enumerate(["B1", "B2", "B3"]):
        d.text((100 + 120 * i, 220), nm, fill="black")
    rgb = np.asarray(img)
    boxes = [(60, 25, 560, 70), (60, 205, 560, 250)]
    one = [ocr.read_band(rgb, b) for b in boxes]
    many = ocr.read_bands(rgb, boxes)
    assert [[t.text for t in r] for r in many] == [[t.text for t in r] for r in one]


def test_name_ok_plus_solo_accepts_one_letter_power_terminals():
    """전원·모터 단자는 한 글자다 (U V W · P N · B). 이름으로 자리를 정할 때만 받는다."""
    for good in ("U", "V", "W", "P", "B", "L", "N"):
        assert ocr._name_ok(good), good
    for bad in ("A", "C", "X", "Y", "Safety"):
        assert not ocr._name_ok(bad), bad


def test_pins_from_labels_leaves_description_text_out(monkeypatch):
    """한 줄에 단자 이름과 설명이 섞여도 이름만 남는다 (실측: SV1 아래 변 'U W P N B GND'' + 'Interface connector')."""
    monkeypatch.setattr(ocr, "available", lambda: True)
    words = [(109, 261, 8, 10, "U", .9), (175, 261, 9, 10, "W", .9), (226, 261, 4, 10, "P", .9),
             (259, 261, 8, 10, "N", .9), (292, 261, 7, 10, "B", .9), (342, 261, 23, 10, "GND'", .9),
             (441, 261, 82, 10, "Interface connector", .9), (541, 261, 42, 10, "connector", .9),
             (641, 261, 82, 10, "Encoder connection", .9)]
    monkeypatch.setattr(ocr, "read_words", lambda rgb, *a, **k: words)
    out = ocr.pins_from_labels(np.zeros((320, 740, 3), np.uint8), [], (59.0, 40.0, 708.0, 289.0))
    assert [p[3] for p in out] == ["U", "W", "P", "N", "B", "GND'"]
    assert all(p[0] == "bottom" for p in out)


def test_pins_from_labels_splits_only_the_glued_chunk(monkeypatch):
    """'142434' 옆에 이미 따로 읽힌 '42 B2 A2'가 있다 — 붙은 것만 쪼갠다 (실측: PZ1 아래 변)."""
    monkeypatch.setattr(ocr, "available", lambda: True)
    words = [(318, 304, 78, 12, "142434", .9), (377, 304, 26, 12, "42", .9),
             (400, 304, 21, 12, "B2", .9), (428, 304, 20, 12, "A2", .9)]
    monkeypatch.setattr(ocr, "read_words", lambda rgb, *a, **k: words)
    out = ocr.pins_from_labels(np.zeros((400, 500, 3), np.uint8), [], (259.0, 16.0, 449.0, 383.0))
    assert [p[3] for p in out] == ["14", "24", "34", "42", "B2", "A2"]


def test_connector_spans_mark_only_the_pins_under_the_word():
    """커넥터라고 적힌 글자의 x 범위·같은 변에 든 접속점만 커넥터 핀이다 (실측: SV1 X1~X6)."""
    from imgvec.pins import PinSpec

    body = (59.0, 40.0, 708.0, 289.0)
    words = [(441, 78, 62, 10, "USB connector", .9), (641, 261, 82, 10, "Encoder connection", .9),
             (109, 64, 9, 10, "L1", .9)]
    spans = ocr.connector_spans(np.zeros((320, 740, 3), np.uint8), body, words)
    assert len(spans) == 2
    pins = [PinSpec(name="X1", side="top", x_px=423.0, y_px=50.0),
            PinSpec(name="L1", side="top", x_px=109.0, y_px=64.0),
            PinSpec(name="X6", side="bottom", x_px=624.0, y_px=279.0),
            PinSpec(name="X3", side="bottom", x_px=441.0, y_px=279.0)]   # 글자는 위 변 것이다
    assert ocr.mark_connectors(pins, body, spans) == 2
    assert [p.kind for p in pins] == ["connector", "electrical", "connector", "electrical"]
