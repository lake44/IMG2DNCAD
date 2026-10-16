"""자동 인식 (4단계): 본체 사각형과 접속점 자리를 그림에서 찾는다."""
import json
from pathlib import Path

import numpy as np
import pytest

from imgvec.cli import main
from imgvec import detect
from imgvec.detect import KIND_WEIGHT, detect_body, detect_pins, gather_candidates, name_pins
from imgvec.device import DeviceSpec, auto_spec, label_map
from imgvec.geom import Circle, LineSeg, Path as GPath
from imgvec.imageio import load_image

SET = Path(__file__).resolve().parent.parent / "samples" / "set"


def rect(x0, y0, x1, y1, *, cut=()):
    """사각형을 선분 네 개로. cut에 준 변은 두 토막으로 쪼갠다 (단자 줄기에 잘린 변 흉내)."""
    segs = []
    edges = {"top": ((x0, y0), (x1, y0)), "bottom": ((x0, y1), (x1, y1)),
             "left": ((x0, y0), (x0, y1)), "right": ((x1, y0), (x1, y1))}
    for side, (p0, p1) in edges.items():
        if side in cut:
            mid = (0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1]))
            gap = 8.0
            if p0[0] == p1[0]:
                segs += [LineSeg(p0, (mid[0], mid[1] - gap)), LineSeg((mid[0], mid[1] + gap), p1)]
            else:
                segs += [LineSeg(p0, (mid[0] - gap, mid[1])), LineSeg((mid[0] + gap, mid[1]), p1)]
        else:
            segs.append(LineSeg(p0, p1))
    return [GPath([s]) for s in segs]


def test_detect_body_finds_plain_rectangle():
    box = detect_body(rect(100, 80, 500, 400), (600, 500))
    assert box is not None
    assert max(abs(a - b) for a, b in zip(box, (100, 80, 500, 400))) < 1.0


def test_detect_body_survives_edges_broken_into_pieces():
    """선 그림의 본체 변은 단자 줄기에 잘려 토막난다 — 토막을 모아도 찾아야 한다."""
    box = detect_body(rect(100, 80, 500, 400, cut=("top", "bottom", "left", "right")), (600, 500))
    assert box is not None
    assert max(abs(a - b) for a, b in zip(box, (100, 80, 500, 400))) < 1.0


def test_detect_body_prefers_the_outer_rectangle():
    prims = rect(100, 80, 500, 400) + rect(150, 120, 300, 200)
    box = detect_body(prims, (600, 500))
    assert box == pytest.approx((100, 80, 500, 400), abs=1.0)


def test_detect_body_returns_none_without_a_rectangle():
    assert detect_body([GPath([LineSeg((0, 0), (10, 300))])], (600, 500)) is None


def test_detect_pins_reads_stubs_on_the_top_and_bottom_edges():
    body = (100.0, 100.0, 500.0, 400.0)
    prims = rect(*body)
    for i in range(4):                       # 위·아래 변에 줄기 4개씩, 간격 80
        x = 140 + 80 * i
        prims.append(GPath([LineSeg((x, 100), (x, 40))]))
        prims.append(GPath([LineSeg((x, 400), (x, 460))]))
    hits = detect_pins(prims, None, body, (600, 500))
    assert len(hits) == 8
    assert {h.side for h in hits} == {"top", "bottom"}
    assert sorted(round(h.x_px) for h in hits if h.side == "top") == [140, 220, 300, 380]
    assert all(h.kind == "stub" for h in hits)
    assert hits[0].pitch == pytest.approx(80, abs=1)


def test_detect_pins_uses_circles_at_the_stub_ends():
    body = (100.0, 100.0, 500.0, 400.0)
    prims = rect(*body)
    for i in range(4):
        x = 140 + 80 * i
        prims.append(GPath([LineSeg((x, 100), (x, 55))]))
        prims.append(Circle(x, 40, 14))
    hits = detect_pins(prims, None, body, (600, 500))
    top = [h for h in hits if h.side == "top"]
    assert len(top) == 4
    assert all(h.kind == "circle" for h in top)       # 원이 줄기 끝보다 믿을 만하다
    assert all(abs(h.y_px - 40) < 1 for h in top)


def test_detect_pins_keeps_one_axis_only():
    """접속점은 보통 한 축에만 있다 — 반대 축의 약한 줄은 버린다."""
    body = (100.0, 100.0, 500.0, 400.0)
    prims = rect(*body)
    for i in range(4):
        x = 140 + 80 * i
        prims.append(GPath([LineSeg((x, 100), (x, 40))]))
        prims.append(GPath([LineSeg((x, 400), (x, 460))]))
    prims.append(GPath([LineSeg((100, 200), (40, 200))]))      # 왼쪽에 외톨이 줄기 하나
    hits = detect_pins(prims, None, body, (600, 500))
    assert {h.side for h in hits} == {"top", "bottom"}


def test_kind_weight_orders_the_clues():
    assert KIND_WEIGHT["circle"] > KIND_WEIGHT["blob"] > KIND_WEIGHT["glyph"]


def test_gather_candidates_ignores_lines_that_do_not_touch_the_body():
    body = (100.0, 100.0, 500.0, 400.0)
    far = [GPath([LineSeg((300, 20), (300, 50))])]            # 본체에서 떨어진 짧은 선 (글자 획 흉내)
    assert not [c for c in gather_candidates(far, None, body, (600, 500)) if c[2] == "stub"]


def test_name_pins_orders_top_then_bottom_left_to_right():
    body = (100.0, 100.0, 500.0, 400.0)
    prims = rect(*body)
    for i in range(3):
        x = 160 + 90 * i
        prims.append(GPath([LineSeg((x, 100), (x, 40))]))
        prims.append(GPath([LineSeg((x, 400), (x, 460))]))
    hits = name_pins(detect_pins(prims, None, body, (600, 500)))
    assert [h.side for h in hits] == ["top"] * 3 + ["bottom"] * 3
    assert [round(h.x_px) for h in hits[:3]] == [160, 250, 340]


# --------------------------------------------------------------------------- 실제 샘플
@pytest.mark.parametrize("stem,pins", [("block_plc", 16), ("line_dev_tb", 8)])
def test_auto_spec_on_real_samples(stem, pins):
    """정답이 있는 샘플: 본체는 5px 안, 접속점은 전부 15px 안에 들어와야 한다."""
    truth = DeviceSpec.load(SET / f"{stem}.spec.json")
    rgb = load_image(SET / truth.image).rgb
    spec, note = auto_spec(rgb, name=stem, image=truth.image, preset=truth.preset)
    assert max(abs(a - b) for a, b in zip(spec.body_px, truth.body_px)) <= 5.0
    assert len(spec.pins) == pins
    for q in truth.pins:
        assert min(float(np.hypot(p.x_px - q.x_px, p.y_px - q.y_px)) for p in spec.pins) <= 15.0
    assert note["body_source"] == "auto-rect"


def test_label_map_matches_the_image_size():
    rgb = load_image(SET / "block_plc.png").rgb
    labels, kind = label_map(rgb, "lineart")
    assert labels.shape == rgb.shape[:2]
    assert kind == "lineart"


def test_auto_spec_without_pins_still_writes_a_body():
    rgb = np.full((300, 400, 3), 255, np.uint8)
    rgb[60:240, 80:320] = 0
    rgb[70:230, 90:310] = 255
    spec, note = auto_spec(rgb, name="plain", preset="lineart")
    assert spec.body_px is not None
    assert note["pins"] == len(spec.pins)


def test_cli_auto_writes_spec_and_csv(tmp_path, capsys):
    img = tmp_path / "plc.png"
    img.write_bytes((SET / "block_plc.png").read_bytes())
    out = tmp_path / "plc.spec.json"
    assert main(["device", "auto", str(img), "--name", "PLC", "--pin-pitch-mm", "5.08", "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert "접속점 16개" in text and "초안" in text
    spec = DeviceSpec.load(out)
    assert spec.name == "PLC" and spec.pin_pitch_mm == 5.08 and len(spec.pins) == 16
    assert (tmp_path / "plc.pins.csv").exists()
    # 자동 명세가 그대로 장치가 되는가 (클릭 없이 끝까지)
    assert main(["device", "build", "--spec", str(out), "-o", str(tmp_path / "PLC")]) == 0
    layout = json.loads((tmp_path / "PLC.layout.json").read_text(encoding="utf-8"))
    assert len(layout["pins"]) == 16


def test_cli_autotest_reports_numbers(tmp_path, capsys):
    folder = tmp_path / "set"
    folder.mkdir()
    for n in ("block_plc.png", "block_plc.spec.json"):
        (folder / n).write_bytes((SET / n).read_bytes())
    assert main(["device", "autotest", str(folder)]) == 0
    text = capsys.readouterr().out
    assert "맞춘 접속점 16/16" in text


# --------------------------------------------------------------------------- 사진류를 위한 세 가지
def test_merge_soft_joins_pieces_of_one_screw():
    """나사 머리는 색 양자화에서 머리·홈·그늘로 갈라진다 — 가까운 조각은 한 개로 센다."""
    pieces = [(100.0, 50.0, "blob", 60.0), (104.0, 56.0, "glyph", 20.0), (99.0, 44.0, "glyph", 20.0),
              (200.0, 50.0, "blob", 60.0)]
    out = detect._merge_soft(pieces, radius=20.0)
    assert len(out) == 2
    assert {round(c[0]) for c in out} == {101, 200}
    assert all(c[2] == "blob" for c in out)          # 조각 하나라도 둥글면 단자로 본다
    assert detect._merge_soft(pieces, radius=0) == pieces


def test_owner_side_picks_one_side_using_relative_distance():
    """한 점이 네 변의 후보가 되면 멀리 떨어진 두 점이 '넓게 퍼진 줄'로 이긴다 — 제 변 하나에만 속하게 한다."""
    body = (56.0, 56.0, 536.0, 1504.0)               # 세로로 긴 장치 (폭 480, 높이 1448)
    screw = (216.0, 148.0, "blob", 60.0)             # 위 단자: 위 변에서 92, 왼쪽 변에서 160
    assert detect.owner_side(screw, body, maxd=216.0, min_v=14.0) == "top"
    # 거리를 그냥 견주면 왼쪽 변(160)이 아니라 위 변(92)이 이기지만, 가로로 긴 장치에서는 뒤집힌다.
    wide = (56.0, 56.0, 1504.0, 536.0)
    side_pin = (148.0, 216.0, "blob", 60.0)
    assert detect.owner_side(side_pin, wide, maxd=216.0, min_v=14.0) == "left"


def test_owner_side_ignores_points_beyond_the_edge_span():
    body = (320.0, 240.0, 1080.0, 760.0)
    outside = (195.0, 260.0, "circle", 50.0)         # 본체 왼쪽 바깥 — 위 변의 범위 밖이다
    assert detect.owner_side(outside, body, maxd=240.0, min_v=10.0) == "left"


def test_second_row_is_accepted_only_when_it_matches():
    head = (10.0, [(0, 100, 100, 50, "blob", 60)] * 4, 80.0)
    same = (6.0, [(0, 100, 100, 50, "blob", 60)] * 4, 80.0)
    assert detect._is_second_row(same, head)
    assert not detect._is_second_row((1.0, same[1], 80.0), head)            # 점수가 너무 낮다
    assert not detect._is_second_row((6.0, same[1], 120.0), head)           # 간격이 다르다
    assert not detect._is_second_row((6.0, same[1][:2], 80.0), head)        # 개수가 다르다
    other = (6.0, [(0, 100, 100, 50, "glyph", 60)] * 4, 80.0)
    assert not detect._is_second_row(other, head)                           # 꼴이 다르다


def test_two_rows_on_one_side_are_both_kept():
    """안전 릴레이·I/O 카드는 한 변에 단자가 2열이다."""
    body = (100.0, 200.0, 600.0, 500.0)
    prims = rect(*body)
    for i in range(4):                               # 위 변 바깥에 2열, 같은 간격
        x = 160 + 100 * i
        prims.append(Circle(x, 150, 12))
        prims.append(Circle(x, 100, 12))
    hits = detect.detect_pins(prims, None, body, (700, 600))
    top = [h for h in hits if h.side == "top"]
    assert len(top) == 8
    assert {round(h.y_px) for h in top} == {100, 150}
    assert len(detect.detect_pins(prims, None, body, (700, 600), second_row=False)) == 4


def test_autotest_numbers_do_not_regress():
    """샘플 세트 성능이 뒷걸음질 치면 알아차린다 (실측 2026-10-08: 186/220, 헛 9)."""
    total = hit = extra = 0
    for stem in ("block_plc", "line_dev_tb", "ill_relay_din", "real_relay_icon"):
        truth = DeviceSpec.load(SET / f"{stem}.spec.json")
        rgb = load_image(SET / truth.image).rgb
        spec, _ = auto_spec(rgb, name=stem, image=truth.image, preset=truth.preset)
        used = set()
        for q in truth.pins:
            best, bi = None, -1
            for i, p in enumerate(spec.pins):
                if i in used or p.side != q.side:
                    continue
                d = abs((p.x_px - q.x_px) if q.side in ("top", "bottom") else (p.y_px - q.y_px))
                if best is None or d < best:
                    best, bi = d, i
            if best is not None and best <= 15.0:
                used.add(bi)
        total += len(truth.pins)
        hit += len(used)
        extra += len(spec.pins) - len(used)
    assert hit == total, f"이 넷은 빠짐없이 찾아야 한다: {hit}/{total}"
    assert extra == 0, f"헛검출이 생겼다: {extra}"


# --------------------------------------------------------------------------- 실제 도면 심볼
def test_lattice_fit_accepts_gaps_that_are_multiples_of_one_grid():
    """실제 도면 단자는 등간격이 아니라 '격자 위에 빈 자리가 있는' 배치다 (실측 -T1: 343·85·171 = 85의 4·1·2배)."""
    u = np.array([375.0, 718.0, 803.0, 974.0])
    fit, pitch = detect.lattice_fit(u, span=770.0)
    assert pitch == pytest.approx(85, abs=2)
    assert fit > 0.5
    even = detect.lattice_fit(np.array([100.0, 180.0, 260.0, 340.0]), span=500.0)
    assert even[1] == pytest.approx(80, abs=1) and even[0] > 0.9      # 등간격은 그대로 만점에 가깝다


def test_lattice_fit_rejects_gaps_off_the_grid():
    off = detect.lattice_fit(np.array([0.0, 100.0, 237.0, 401.0]), span=500.0)
    even = detect.lattice_fit(np.array([0.0, 100.0, 200.0, 300.0]), span=500.0)
    assert off[0] < even[0]


def test_detect_pins_finds_unevenly_spaced_terminals():
    """등간격만 보던 때 2/8만 찾던 배치 — 격자로 보면 다 찾는다."""
    body = (290.0, 72.0, 1060.0, 414.0)
    prims = rect(*body)
    for x in (375, 718, 803, 974):                 # 위: 간격 343·85·171
        prims.append(Circle(x, 114, 14))
        prims.append(GPath([LineSeg((x, 72), (x, 0))]))
    for x in (375, 632, 718, 803):                 # 아래: 간격 257·86·85
        prims.append(Circle(x, 371, 14))
        prims.append(GPath([LineSeg((x, 414), (x, 466))]))
    hits = detect.detect_pins(prims, None, body, (1116, 466))
    top = sorted(round(h.x_px) for h in hits if h.side == "top")
    bottom = sorted(round(h.x_px) for h in hits if h.side == "bottom")
    assert top == [375, 718, 803, 974]
    assert bottom == [375, 632, 718, 803]


# --------------------------------------------------------------------------- 액자가 있는 그림 (PDF 캡처)
def test_body_candidates_are_ordered_and_deduped():
    outer = rect(10, 10, 590, 440)                    # 액자
    inner = rect(50, 100, 540, 390)                   # 본체
    boxes = detect.detect_body_candidates(outer + inner, (600, 450))
    assert len(boxes) >= 2
    assert boxes[0] == pytest.approx((10, 10, 590, 440), abs=1)
    assert any(b == pytest.approx((50, 100, 540, 390), abs=1) for b in boxes)
    # 서로 많이 겹치는 후보는 하나만 남는다
    assert all(detect._iou(a, b) <= 0.95 for i, a in enumerate(boxes) for b in boxes[i + 1:])


def test_detect_device_prefers_the_box_that_explains_the_pins():
    """PDF 매뉴얼 캡처처럼 액자가 있으면 가장 큰 사각형이 본체가 아니다."""
    body = (400.0, 450.0, 800.0, 650.0)
    prims = rect(10, 10, 1190, 890) + rect(*body)     # 액자는 단자에서 한참 떨어져 있다
    for x in (450, 550, 650, 750):                    # 본체 변 위에 걸친 단자 원
        prims.append(Circle(x, 450, 8))
        prims.append(Circle(x, 650, 8))
    box, hits = detect.detect_device(prims, None, (1200, 900))
    assert box == pytest.approx(body, abs=2)
    assert len(hits) == 8
    # 후보를 하나만 보면 액자를 고른다
    assert detect.detect_body(prims, (1200, 900)) == pytest.approx((10, 10, 1190, 890), abs=1)


def test_circles_are_exempt_from_the_edge_floor():
    """변선이 접속점으로 둔갑하는 것을 막는 문턱은 원에는 걸지 않는다 — 단자 원이 변에 붙어 그려진다."""
    assert detect._floor("circle", 10.0) == 0.0
    assert detect._floor("stub", 10.0) == 10.0
    assert detect._floor("blob", 10.0) == 10.0


def test_pins_drawn_right_on_the_edge_are_found():
    body = (50.0, 100.0, 540.0, 390.0)
    prims = rect(*body)
    for x in (100, 180, 300, 420):
        prims.append(Circle(x, 102, 8))               # 변에서 2px — 옛 문턱이면 통째로 걸러졌다
    hits = detect.detect_pins(prims, None, body, (600, 450))
    assert sorted(round(h.x_px) for h in hits if h.side == "top") == [100, 180, 300, 420]


# --------------------------------------------------------------------------- 점(●)으로 그린 단자
def dotted_image(xs, *, w=480, h=300, body=(60, 70, 440, 240), r=4):
    """본체 변 위에 채워진 점을 찍은 이진 라벨 지도 (0=배경, 1=잉크)."""
    lab = np.zeros((h, w), np.int32)
    x0, y0, x1, y1 = body
    lab[y0:y0 + 2, x0:x1] = 1                       # 가는 변선
    lab[y1:y1 + 2, x0:x1] = 1
    lab[y0:y1, x0:x0 + 2] = 1
    lab[y0:y1, x1:x1 + 2] = 1
    yy, xx = np.ogrid[:h, :w]
    for x in xs:                                     # 변 위의 굵은 점
        for cy in (y0, y1):
            lab[(xx - x) ** 2 + (yy - cy) ** 2 <= r * r] = 1
    return lab


def test_dot_candidates_find_filled_dots_on_the_edge():
    xs = [100, 180, 260, 340]
    lab = dotted_image(xs)
    dots = detect.dot_candidates(lab, (60.0, 70.0, 440.0, 240.0))
    assert dots and all(d[2] == "dot" for d in dots)
    top = sorted(round(d[0]) for d in dots if abs(d[1] - 70) < 2)
    assert top == pytest.approx(xs, abs=2)


def test_dot_candidates_ignore_a_plain_edge():
    lab = dotted_image([])                           # 점 없이 변선만
    assert detect.dot_candidates(lab, (60.0, 70.0, 440.0, 240.0)) == []


def test_pins_drawn_as_dots_are_found():
    """점이 변선 위에 그려지면 연결 성분으로는 안 잡힌다 — 두께로 찾는다 (실측 ABB TSR20M)."""
    xs = [100, 180, 260, 340]
    body = (60.0, 70.0, 440.0, 240.0)
    lab = dotted_image(xs)
    hits = detect.detect_pins(rect(*body), lab, body, (480, 300))
    assert sorted(round(h.x_px) for h in hits if h.side == "top") == pytest.approx(xs, abs=2)
    assert {h.kind for h in hits} == {"dot"}


def test_dot_tier_wins_over_other_clues():
    assert detect.TIERS[0] == ("dot",)
    assert detect.KIND_WEIGHT["dot"] > detect.KIND_WEIGHT["stub"]
    assert detect._floor("dot", 10.0) == 0.0         # 점은 변 위에 있다


def test_thin_crossings_are_not_dots():
    """줄기가 변에 닿는 자리도 두껍다 — 진짜 점(지름 5~6px)보다 가늘어서 갈린다.
    문턱이 낮았을 때 T1의 줄기 교차가 단자로 잡혀 이름 읽기가 8/8 → 7/8로 떨어졌다."""
    assert detect.DOT_MIN_WIDTH >= 4
    body = (60.0, 70.0, 440.0, 240.0)
    lab = np.zeros((300, 480), np.int32)
    x0, y0, x1, y1 = (int(v) for v in body)
    lab[y0:y0 + 3, x0:x1] = 1                        # 변선 (두께 3)
    for x in (120, 200, 280):                        # 변에 닿은 줄기 (폭 2)
        lab[y0 - 40:y0 + 3, x:x + 2] = 1
    assert detect.dot_candidates(lab, body) == []


# --------------------------------------------------------------------------- 배선 없는 단자
def test_fill_gaps_takes_an_unwired_terminal_on_the_lattice():
    """스페어·미결선 단자는 본체 밖으로 선이 안 나간다. 그래도 접속점으로 받는다 (2026-10-09 지시).

    실측: Pilz PNOZ X2.8P는 맨 오른쪽 접점 41·42만 선이 없어 16개 중 14개였다.
    """
    body = (100.0, 100.0, 500.0, 300.0)
    keep = [(10.0, 150.0, 150.0, 90.0, "stub", 50.0),      # v는 바깥이 +
            (10.0, 250.0, 250.0, 90.0, "stub", 50.0),
            (10.0, 350.0, 350.0, 90.0, "stub", 50.0)]
    cands = [(450.0, 115.0, "blob", 80.0),                 # 선 없는 단자 — 격자 다음 자리·같은 깊이 띠
             (450.0, 260.0, "blob", 80.0),                 # 본체 깊숙한 설명 글자
             (60.0, 115.0, "blob", 80.0),                  # 본체 밖 장치 이름 글자
             (470.0, 112.0, "glyph", 80.0),                # 글자 획
             (250.0, 112.0, "blob", 80.0)]                 # 이미 접속점이 있는 자리
    out = detect._fill_gaps(keep, cands, "top", body, pitch=100.0, maxd=90.0, bin_px=16.0)
    assert [round(it[1]) for it in out] == [150, 250, 350, 450]
    assert out[-1][4] == "blob"


def test_fill_gaps_does_nothing_without_a_lattice():
    body = (100.0, 100.0, 500.0, 300.0)
    keep = [(10.0, 150.0, 150.0, 90.0, "stub", 50.0)]
    assert detect._fill_gaps(keep, [(450.0, 115.0, "blob", 80.0)], "top", body,
                             pitch=0.0, maxd=90.0, bin_px=16.0) == keep
