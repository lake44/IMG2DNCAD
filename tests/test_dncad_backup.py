"""시험용 DNCAD 백업(.dncadbak) 읽기·쓰기 — 형식은 이 PC의 실제 백업(스키마 0070)에서 읽어 낸 것."""
import dataclasses
import hashlib
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone

import pytest

from imgvec.cli import main
from imgvec.dncad_backup import (
    KitError,
    _dump,
    build_backup,
    load_template,
    merge_backup,
    plan_pages,
    read_backup,
    read_black_boxes,
    verify_backup,
)
from imgvec.dncad_rules import DEFAULT_RULES
from imgvec.layout import SCHEMA as LAYOUT_SCHEMA
from imgvec.model import Body, DeviceModel, Pin
from imgvec.validate import has_errors, validate

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)

# 실제 백업(-FA01 등)의 행 열 이름 — 이 집합에서 벗어나면 템플릿·행 만들기가 실제와 달라진 것이다
REAL_COLUMNS = {
    "devices": "id tag created_at naming_scheme cable_length_m function_node_id location_node_id parent_device_id main_component_id",
    "components": "id device_id created_at properties description definition_id rated_current rated_voltage function_class definition_overrides source_part_assignment_id",
    "connection_points": "id cp_type ip_name ip_group created_at designation component_id potential_id",
    "placements": "x y id role size view page_id mirrored rotation pin_label created_at visibility component_id plc_channels main_function text_position mirror_position potential_label_position",
    "graphic_anchors": "x y id placement_id connection_point_id",
    "annotations": "x y id kind text style page_id z_order geometry rotation created_at",
    "projects": "id name settings created_at",
    "structure_nodes": "id name aspect ordinal created_at description parent_node_id",
    "pages": "id name scale ordinal sl_kind sheet_no page_type revisions sheet_sub created_at page_setup frame_form_id report_config continuation_of document_node_id function_node_id location_node_id continuation_index",
}


def pin(name, side, slot, h=112):
    return {"designation": name, "side": side, "slot": slot, "x": 32 * slot, "y": 0 if side == "top" else h,
            "rotation": 0 if side == "top" else 180, "mirrored": None, "deviation_mm": 0.0, "group": ""}


def layout(name="FA01", w=160, h=112, pins=None):
    pins = pins if pins is not None else [pin("1", "top", 1), pin("2", "top", 2), pin("3", "top", 3), pin("4", "top", 4),
                                          pin("PE", "bottom", 3), pin("PE'", "bottom", 4)]
    return {"schema": LAYOUT_SCHEMA, "name": name, "units_per_mm": 6.4, "origin": "box top-left, x right, y down",
            "box": {"w": w, "h": h, "w_mm": round(w / 6.4, 2), "h_mm": round(h / 6.4, 2)}, "pins": pins, "skipped_pins": [], "issues": []}


def kit(*lays, **kw):
    return build_backup(lays or (layout(),), seed=3, now=NOW, **kw)


def edit(data, fn, fix_hash=True, recount=True):
    """백업의 행을 고쳐 다시 묶는다 (해시·행 수는 기본으로 맞춰 주어, 고친 부분만 문제가 되게)."""
    b = read_backup(data)
    fn(b.rows)
    project = _dump({"rows": b.rows})
    manifest = json.loads(json.dumps(b.manifest))
    if fix_hash:
        manifest["integrity"]["project.json"] = hashlib.sha256(project).hexdigest()
    if recount:
        manifest["counts"] = {t: len(r) for t, r in b.rows.items()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", _dump(manifest))
        z.writestr("project.json", project)
        z.writestr("masters.json", b.raw["masters.json"])
    return buf.getvalue()


def codes(issues):
    return {i.code for i in issues}


def test_template_has_required_definitions():
    t = load_template()
    keys = {g["key"] for g in t["globalDefinitions"]}
    assert {"black_box", "device_connection_point"} <= keys
    assert len(t["tables"]) == 25 and t["manifest"]["schemaVersion"] == "0070"
    assert not any(t["tables"].count(x) > 1 for x in t["tables"])


def test_rows_have_same_columns_as_real_backup():
    res = kit(layout(), layout("B", pins=[pin("A", "top", 1)]))
    rows = read_backup(res.data).rows
    for table, cols in REAL_COLUMNS.items():
        assert rows[table], table
        for r in rows[table]:
            assert set(r) == set(cols.split()), (table, set(r) ^ set(cols.split()))
    assert set(rows) == set(load_template()["tables"])


def test_fa01_geometry_matches_real_backup():
    res = kit()
    b = read_backup(res.data)
    dev = b.rows["devices"][0]
    assert dev["tag"] == "-ZZ01"
    comps = {c["id"]: c for c in b.rows["components"]}
    defs = {g["oldDefinitionId"]: g["key"] for g in b.masters["globalDefinitions"]}
    pls = b.rows["placements"]
    box_pl = next(p for p in pls if defs[comps[p["component_id"]]["definition_id"]] == "black_box")
    assert box_pl["size"] == {"h": 112, "w": 160} and box_pl["rotation"] == 0 and box_pl["main_function"] is True
    assert box_pl["role"] == "PRIMARY" and box_pl["visibility"] is True
    assert box_pl["x"] % 16 == 0 and box_pl["y"] % 16 == 0
    pins = [p for p in pls if p is not box_pl]
    assert len(pins) == 6
    top = sorted(p["x"] - box_pl["x"] for p in pins if p["rotation"] == 0)
    bottom = sorted(p["x"] - box_pl["x"] for p in pins if p["rotation"] == 180)
    assert top == [32, 64, 96, 128] and bottom == [96, 128]
    assert all(p["y"] == box_pl["y"] for p in pins if p["rotation"] == 0)
    assert all(p["y"] == box_pl["y"] + 112 for p in pins if p["rotation"] == 180)
    assert all(p["size"] is None and p["main_function"] is None for p in pins)
    cps = b.rows["connection_points"]
    assert sorted(c["designation"] for c in cps) == ["1", "2", "3", "4", "PE", "PE'"]
    assert all(c["cp_type"] == "electrical" and c["potential_id"] is None for c in cps)
    assert all((a["x"], a["y"]) == (0, 0) for a in b.rows["graphic_anchors"])
    assert len(b.rows["graphic_anchors"]) == len(cps) == 6


def test_verify_is_clean_and_output_is_deterministic():
    a, b = kit(), kit()
    assert a.data == b.data
    assert not [i for i in a.issues if i.severity != "info"]
    assert codes(verify_backup(a.data)) == {"I-SCHEMA"}
    m = read_backup(a.data).manifest
    assert m["counts"]["devices"] == 1 and m["counts"]["components"] == 7 and m["counts"]["graphic_anchors"] == 6
    assert m["masters"]["globalDefinitionKeys"] == sorted(m["masters"]["globalDefinitionKeys"])
    assert m["exportedAt"].endswith("Z") and m["warnings"] == []
    # 다른 시드는 다른 ID
    assert build_backup([layout()], seed=4, now=NOW).data != a.data


def test_ids_are_time_ordered_uuid7():
    rows = read_backup(kit(layout(), layout("B")).data).rows
    ids = [r["id"] for t in ("devices", "components", "placements") for r in rows[t]]
    assert len(set(ids)) == len(ids)
    for i in ids:
        u = uuid.UUID(i)
        assert u.version == 7 and u.variant == uuid.RFC_4122
    stamps = [r["created_at"] for r in rows["components"]]
    assert stamps == sorted(stamps) and stamps[0].endswith("+00:00") and "." in stamps[0]
    comp_ids = [r["id"] for r in rows["components"]]
    assert comp_ids == sorted(comp_ids)


def test_labels_and_description_options():
    r = read_backup(kit(layout("PILZ")).data).rows
    assert [a["text"] for a in r["annotations"]] == ["PILZ"] and r["annotations"][0]["kind"] == "TEXT"
    assert all(c["description"] is None for c in r["components"])
    r2 = read_backup(kit(layout("PILZ"), labels=False, describe=True).data).rows
    assert r2["annotations"] == []
    assert [c["description"] for c in r2["components"] if c["description"]] == ["PILZ"]


def test_verify_detects_broken_backups():
    data = kit().data
    # 해시: project.json을 고치고 manifest는 그대로
    assert "E-HASH" in codes(verify_backup(edit(data, lambda r: r["devices"][0].update(tag="-X"), fix_hash=False)))
    # 행 수: 앵커 하나를 지우되 manifest.counts는 그대로
    assert "E-COUNT" in codes(verify_backup(edit(data, lambda r: r["graphic_anchors"].pop(), recount=False)))
    # 참조: 접속점이 없는 부품을 가리키게
    assert "E-FK" in codes(verify_backup(edit(data, lambda r: r["connection_points"][0].update(component_id=str(uuid.uuid4())))))
    # 같은 id
    assert "E-ID-DUP" in codes(verify_backup(edit(data, lambda r: r["placements"].append(dict(r["placements"][0])))))
    # ZIP이 아님
    assert codes(verify_backup(b"not a zip")) == {"E-ZIP"}


def test_verify_black_box_rules():
    data = kit().data

    def strip_pins(rows):
        defs = {g["key"]: g["oldDefinitionId"] for g in read_backup(data).masters["globalDefinitions"]}
        dcp = [c["id"] for c in rows["components"] if c["definition_id"] == defs["device_connection_point"]]
        rows["components"] = [c for c in rows["components"] if c["id"] not in dcp]
        rows["connection_points"] = [c for c in rows["connection_points"] if c["component_id"] not in dcp]
        pl = {p["id"] for p in rows["placements"] if p["component_id"] in dcp}
        rows["placements"] = [p for p in rows["placements"] if p["id"] not in pl]
        rows["graphic_anchors"] = [a for a in rows["graphic_anchors"] if a["placement_id"] not in pl]

    assert "E-NOPIN" in codes(verify_backup(edit(data, strip_pins)))

    def off_edge(rows):
        p = next(p for p in rows["placements"] if p["size"] is None)
        p["rotation"] = 180            # 위 변 위치인데 아래 변 회전

    assert "E-EDGE" in codes(verify_backup(edit(data, off_edge)))

    def rot90(rows):
        p = next(p for p in rows["placements"] if p["size"] is None)
        p["rotation"] = 90

    assert "W-SIDE" in codes(verify_backup(edit(data, rot90)))

    def dup(rows):
        rows["connection_points"][1]["designation"] = rows["connection_points"][0]["designation"]

    assert "E-DUP" in codes(verify_backup(edit(data, dup)))
    lenient = dataclasses.replace(DEFAULT_RULES, allow_duplicate_designations=True)
    c = codes(verify_backup(edit(data, dup), lenient))
    assert "W-DUP" in c and "E-DUP" not in c


def test_layout_checks_raise():
    with pytest.raises(KitError, match="접속점이 하나도 없습니다"):
        kit(layout(pins=[]))
    with pytest.raises(KitError, match="중복"):
        kit(layout(pins=[pin("A", "top", 1), pin("A", "top", 2)]))
    ok = build_backup([layout(pins=[pin("A", "top", 1), pin("A", "top", 2)])], seed=1, now=NOW,
                      rules=dataclasses.replace(DEFAULT_RULES, allow_duplicate_designations=True))
    assert any(i.code == "W-DUP" for i in ok.issues)
    with pytest.raises(KitError, match="배치 명세 형식"):
        kit({**layout(), "schema": "x"})
    bad = layout()
    bad["pins"][0]["y"] = 40
    with pytest.raises(KitError, match="변 위에 있지 않습니다"):
        kit(bad)
    left = layout(pins=[{**pin("L", "top", 1), "side": "left"}])
    with pytest.raises(KitError, match="변·회전"):
        kit(left)
    with pytest.raises(KitError, match="태그가 중복"):
        build_backup([layout(), layout("B")], tags=["-A01", "-A01"])
    with pytest.raises(KitError, match="수가 다릅니다"):
        build_backup([layout()], tags=["-A01", "-A02"])
    with pytest.raises(KitError, match="장치가 하나도 없습니다"):
        build_backup([])


def test_plan_pages_wraps_rows_and_pages_on_the_grid():
    pos, warns = plan_pages([(352, 112)] * 20)
    assert not warns
    pages = {p for p, _, _ in pos}
    assert pages == {0, 1, 2}   # 한 줄 3개, 한 쪽 3줄 → 9개씩
    assert all(x % 16 == 0 and y % 16 == 0 for _, x, y in pos)
    # 같은 쪽·같은 줄은 겹치지 않는다
    for i, (pa, xa, ya) in enumerate(pos):
        for pb, xb, yb in pos[i + 1:]:
            if pa == pb and ya == yb:
                assert xb >= xa + 352
    # 쪽보다 넓은 블랙박스는 경고
    _, warns = plan_pages([(2000, 112)])
    assert warns and "쪽 폭" in warns[0]


def test_read_two_black_boxes_and_unplaced_pins():
    data = kit(layout("A"), layout("B", w=224, pins=[pin("X", "top", 1), pin("Y", "bottom", 2)])).data

    def merge(rows):
        a, b = rows["devices"]
        for c in rows["components"]:
            if c["device_id"] == b["id"]:
                c["device_id"] = a["id"]
        rows["devices"] = [a]

    boxes = read_black_boxes(read_backup(edit(data, merge)))
    assert [(b.count, b.index, len(b.pins)) for b in boxes] == [(2, 0, 6), (2, 1, 2)]
    assert boxes[0].tag == "-ZZ01" and not boxes[0].problems

    def unplace(rows):
        cp = next(c for c in rows["connection_points"] if c["designation"] == "PE'")
        pl = next(p for p in rows["placements"] if p["component_id"] == cp["component_id"])
        rows["placements"] = [p for p in rows["placements"] if p is not pl]
        rows["graphic_anchors"] = [a for a in rows["graphic_anchors"] if a["placement_id"] != pl["id"]]

    one = read_black_boxes(read_backup(edit(data, unplace)))[0]
    assert one.unplaced == ["PE'"] and len(one.pins) == 5
    issues = verify_backup(edit(data, unplace))
    assert "I-UNPLACED" in codes(issues) and not [i for i in issues if i.severity == "error"]


def test_unrelated_zip_and_missing_members():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", "{}")
    assert codes(verify_backup(buf.getvalue())) == {"E-ZIP"}


def test_validate_allow_duplicate_designations_rule():
    m = DeviceModel(name="D", body=Body(w=30, h=17.5),
                    pins=[Pin(name="A", side="top", x=5, y=0), Pin(name="A", side="top", x=10, y=0)])
    assert "E-DUP" in {i.code for i in validate(m)}
    lenient = dataclasses.replace(DEFAULT_RULES, allow_duplicate_designations=True)
    got = {i.code for i in validate(m, lenient)}
    assert "W-DUP" in got and "E-DUP" not in got


def test_cli_kit_check_and_read(tmp_path, capsys):
    src = tmp_path / "lay"
    src.mkdir()
    for n in ("a", "b"):
        (src / f"{n}.layout.json").write_text(json.dumps(layout(n.upper()), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "o" / "kit.dncadbak"
    assert main(["device", "kit", str(src), "-o", str(out), "--seed", "5"]) == 0
    text = capsys.readouterr().out
    assert "장치 2개" in text and "새 프로젝트로 복원" in text
    assert out.exists() and out.with_suffix(".map.csv").exists()
    rows = out.with_suffix(".map.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("태그") and rows[1].startswith("-ZZ01,A,a.layout.json")
    assert main(["device", "check-bak", str(out)]) == 0
    assert main(["device", "read-bak", str(out), "-o", str(tmp_path / "back")]) == 0
    assert len(list((tmp_path / "back").glob("*.layout.json"))) == 2
    bad = tmp_path / "bad.dncadbak"
    bad.write_bytes(edit(out.read_bytes(), lambda r: r["devices"][0].update(tag="-Z"), fix_hash=False))
    assert main(["device", "check-bak", str(bad)]) == 1
    assert main(["device", "kit", str(tmp_path / "nothing")]) == 2   # 없는 경로는 오류 종료


# --------------------------------------------------------------------------- 합치기 (merge)
def test_merge_adds_devices_and_keeps_everything_before():
    """이미 있는 백업에 장치를 더한다: 기존 행·심볼·스키마 표기는 그대로, 장치와 쪽만 늘어난다."""
    base = kit(layout("A"), layout("B")).data
    before = read_backup(base)
    res = merge_backup(base, [layout("C", w=192), layout("D")], seed=9, now=NOW)
    after = read_backup(res.data)

    assert len(after.rows["devices"]) == len(before.rows["devices"]) + 2
    assert {r["id"] for r in before.rows["devices"]} <= {r["id"] for r in after.rows["devices"]}
    assert len(read_black_boxes(after)) == len(read_black_boxes(before)) + 2
    # 스키마 표기·전역 정의(masters.json)는 바탕 백업 그대로여야 복원이 같은 조건에서 된다
    for key in ("schemaVersion", "apiBuild", "formatVersion", "format"):
        assert after.manifest[key] == before.manifest[key]
    assert after.raw["masters.json"] == before.raw["masters.json"]
    assert after.manifest["integrity"]["masters.json"] == before.manifest["integrity"]["masters.json"]
    assert after.manifest["counts"] == {t: len(r) for t, r in after.rows.items()}
    assert not has_errors(verify_backup(res.data))


def test_merge_tags_do_not_clash_with_existing_devices():
    base = kit(layout("A"), layout("B")).data          # -ZZ01, -ZZ02
    res = merge_backup(base, [layout("C")], seed=9, now=NOW)
    assert [d.tag for d in res.devices] == ["-ZZ03"]
    with pytest.raises(KitError, match="이미 있는 태그"):
        merge_backup(base, [layout("C")], tags=["-ZZ01"], seed=9, now=NOW)


def test_merge_appends_pages_after_the_existing_ones():
    base = kit(layout("A")).data
    before = read_backup(base)
    res = merge_backup(base, [layout("C")], seed=9, now=NOW)
    after = read_backup(res.data)
    old_pages = {r["id"] for r in before.rows["pages"]}
    new_pages = [r for r in after.rows["pages"] if r["id"] not in old_pages]
    assert len(new_pages) == 1
    assert min(r["ordinal"] for r in new_pages) > max(r["ordinal"] for r in before.rows["pages"])
    assert min(r["sheet_no"] for r in new_pages) > max(r["sheet_no"] for r in before.rows["pages"])
    # 쪽 틀(양식·구조 참조)은 본보기 쪽에서 물려받는다
    model = before.rows["pages"][0]
    for key in ("page_type", "frame_form_id", "function_node_id", "location_node_id"):
        assert new_pages[0][key] == model[key]
    assert new_pages[0]["name"].endswith("(imgvec)")


def test_merge_rejects_bad_base_and_empty_input():
    with pytest.raises(KitError, match="장치가 하나도 없습니다"):
        merge_backup(kit().data, [])
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        for n, payload in (("manifest.json", {"format": "something.else"}), ("project.json", {"rows": {}}), ("masters.json", {})):
            z.writestr(n, json.dumps(payload))
    with pytest.raises(KitError, match="백업 형식이 아닙니다"):
        merge_backup(bad.getvalue(), [layout()])


def test_merge_needs_black_box_definition_in_base():
    def drop_defs(b):
        b.masters["globalDefinitions"] = [g for g in b.masters["globalDefinitions"] if g["key"] != "black_box"]
    b = read_backup(kit().data)
    drop_defs(b)
    masters = _dump(b.masters)
    project = b.raw["project.json"]
    manifest = {**b.manifest, "integrity": {"project.json": hashlib.sha256(project).hexdigest(),
                                            "masters.json": hashlib.sha256(masters).hexdigest()}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", _dump(manifest))
        z.writestr("project.json", project)
        z.writestr("masters.json", masters)
    with pytest.raises(KitError, match="black_box"):
        merge_backup(buf.getvalue(), [layout()])


def test_merge_cli_writes_new_file_and_refuses_overwriting_the_base(tmp_path, capsys):
    base = tmp_path / "바탕.dncadbak"
    base.write_bytes(kit(layout("A")).data)
    lay = tmp_path / "C.layout.json"
    lay.write_text(json.dumps(layout("C"), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "합침.dncadbak"

    assert main(["device", "merge", str(base), str(lay), "-o", str(out)]) == 0
    assert out.exists() and out.with_suffix(".map.csv").exists()
    assert len(read_backup(out).rows["devices"]) == 2
    assert read_backup(base).rows["devices"][0]["tag"] == "-ZZ01"      # 바탕 파일은 그대로
    assert main(["device", "merge", str(base), str(lay), "-o", str(base)]) == 2
