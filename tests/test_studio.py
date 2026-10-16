"""작업대 (5단계): 그림 한 장이 DNCAD 백업까지 가는 길. 화면 없이 논리만 시험한다."""
import json
from pathlib import Path

import pytest

from imgvec.cli import main
from imgvec.device import DeviceSpec
from imgvec.dncad_backup import read_backup, read_black_boxes
from imgvec.studio import StudioError, Workbench, run_pipeline

SET = Path(__file__).resolve().parent.parent / "samples" / "set"
IMG = SET / "block_plc.png"


@pytest.fixture()
def wb():
    w = Workbench()
    w.open_image(IMG, name="PLC", preset="lineart")
    return w


def test_open_image_sets_the_spec(wb):
    assert wb.spec.name == "PLC" and wb.spec.image == "block_plc.png"
    assert wb.rgb is not None and wb.spec.pins == []


def test_auto_detect_fills_body_and_pins(wb):
    note = wb.auto_detect()
    assert note["pins"] == 16 and len(wb.spec.pins) == 16
    assert wb.spec.body_px is not None
    assert [p.name for p in wb.spec.pins][:3] == ["1", "2", "3"]


def test_auto_detect_keeps_names_already_given(wb):
    wb.auto_detect()
    wb.rename_pins([f"I{i}" for i in range(16)])
    wb.auto_detect()                                   # 다시 돌려도 이름은 자리 순서대로 남는다
    assert [p.name for p in wb.spec.pins][:3] == ["I0", "I1", "I2"]


def test_auto_detect_needs_an_image():
    with pytest.raises(StudioError, match="그림"):
        Workbench().auto_detect()


def test_build_refuses_without_pins_or_scale(wb):
    with pytest.raises(StudioError, match="접속점이 하나도"):
        wb.build()
    wb.auto_detect()
    with pytest.raises(StudioError, match="축척"):
        wb.build()
    wb.set_scale(pin_pitch_mm=5.08)
    res = wb.build()
    assert res.layout["box"]["w"] > 0 and wb.ok


def test_set_scale_replaces_the_previous_one(wb):
    wb.set_scale(mm_per_px=0.1)
    assert wb.spec.mm_per_px == 0.1
    wb.set_scale(pin_pitch_mm=5.08)
    assert wb.spec.mm_per_px is None and wb.spec.pin_pitch_mm == 5.08


def test_pin_edits_invalidate_the_built_device(wb):
    wb.auto_detect()
    wb.set_scale(pin_pitch_mm=5.08)
    wb.build()
    assert wb.result is not None
    wb.add_pin(500, 500, "X1")
    assert wb.result is None                           # 고쳤으니 다시 만들어야 한다
    assert wb.remove_pin_near(500, 500) is not None
    assert wb.remove_pin_near(10, 10) is None          # 멀면 지우지 않는다


def test_rename_pins_checks_the_count(wb):
    wb.auto_detect()
    with pytest.raises(StudioError, match="수가 다릅니다"):
        wb.rename_pins(["A", "B"])


def test_status_tells_where_we_are(wb):
    assert "축척 없음" in wb.status()
    wb.auto_detect()
    wb.set_scale(pin_pitch_mm=5.08)
    wb.build()
    s = wb.status()
    assert "접속점 16개" in s and "블랙박스" in s and "검증 오류 0" in s


def test_save_spec_and_reopen(tmp_path, wb):
    wb.auto_detect()
    wb.set_scale(pin_pitch_mm=5.08)
    spec_path, csv_path = wb.save_spec(tmp_path / "plc.spec.json")
    assert csv_path.name == "plc.pins.csv" and csv_path.exists()
    # 명세 옆에 그림이 있어야 다시 열 때 그림까지 따라온다
    (tmp_path / "block_plc.png").write_bytes(IMG.read_bytes())
    w2 = Workbench()
    w2.open_spec(spec_path)
    assert len(w2.spec.pins) == 16 and w2.rgb is not None
    assert w2.spec.pin_pitch_mm == 5.08


def test_export_backup_new_project(tmp_path, wb):
    wb.auto_detect()
    wb.set_scale(pin_pitch_mm=5.08)
    out = tmp_path / "plc.dncadbak"
    res = wb.export_backup(out, tag="-ZZ07")
    assert out.exists() and res.counts["devices"] == 1
    assert [r["tag"] for r in read_backup(out).rows["devices"]] == ["-ZZ07"]


def test_export_backup_merges_into_an_existing_one(tmp_path, wb):
    wb.auto_detect()
    wb.set_scale(pin_pitch_mm=5.08)
    first = tmp_path / "a.dncadbak"
    wb.export_backup(first)
    merged = tmp_path / "b.dncadbak"
    wb.export_backup(merged, base=first)
    after = read_backup(merged)
    assert len(after.rows["devices"]) == 2
    assert len(read_black_boxes(after)) == 2


def test_export_backup_refuses_a_device_with_errors(tmp_path, wb):
    wb.auto_detect()
    wb.set_scale(pin_pitch_mm=5.08)
    wb.rename_pins(["같은이름"] * 16)                   # 한 장치 안의 이름 중복은 오류
    with pytest.raises(StudioError, match="검증 오류"):
        wb.export_backup(tmp_path / "bad.dncadbak")


def test_run_pipeline_end_to_end(tmp_path):
    wb, files = run_pipeline(IMG, name="PLC", pin_pitch_mm=5.08, out_base=tmp_path / "PLC",
                             backup=tmp_path / "PLC.dncadbak", tag="-ZZ03")
    assert wb.ok
    assert set(files) == {"model", "svg", "layout", "issues", "backup"}
    layout = json.loads((tmp_path / "PLC.layout.json").read_text(encoding="utf-8"))
    assert len(layout["pins"]) == 16
    assert read_backup(tmp_path / "PLC.dncadbak").rows["devices"][0]["tag"] == "-ZZ03"


def test_cli_run_writes_everything(tmp_path, capsys):
    img = tmp_path / "plc.png"
    img.write_bytes(IMG.read_bytes())
    assert main(["device", "run", str(img), "--name", "PLC", "--pin-pitch-mm", "5.08",
                 "-o", str(tmp_path / "PLC"), "--backup", str(tmp_path / "PLC.dncadbak")]) == 0
    text = capsys.readouterr().out
    assert "검증 오류 0" in text and "자동 번호" in text
    assert (tmp_path / "PLC.device.json").exists() and (tmp_path / "PLC.dncadbak").exists()


def test_cli_run_reports_failure_as_exit_code(tmp_path, capsys):
    img = tmp_path / "plc.png"
    img.write_bytes(IMG.read_bytes())
    code = main(["device", "run", str(img), "--name", "PLC", "-o", str(tmp_path / "PLC")])
    assert code == 2                                   # 축척이 없으면 만들 수 없다
    assert "축척" in capsys.readouterr().err


def test_spec_from_studio_is_the_same_shape_as_a_handmade_one(tmp_path, wb):
    wb.auto_detect()
    wb.set_scale(pin_pitch_mm=5.08)
    path, _ = wb.save_spec(tmp_path / "s.spec.json")
    again = DeviceSpec.load(path)
    assert again.to_dict()["schema"] == "device-spec/1.0"
    assert all(p.side in ("top", "bottom", "left", "right") for p in again.pins)


def test_vector_is_made_once_and_reused(wb, monkeypatch):
    """같은 그림을 다시 인식하거나 장치를 만들 때 벡터화를 되풀이하지 않는다 (실측 20.3초 → 6.0초)."""
    from imgvec import device as device_mod

    calls = []
    real = device_mod.convert_image

    def counted(rgb, params=None, **kw):
        calls.append(1)
        return real(rgb, params, **kw)

    monkeypatch.setattr(device_mod, "convert_image", counted)
    wb.auto_detect(read_names=False)
    assert len(calls) == 1 and wb.vector is not None
    wb.auto_detect(read_names=False)          # 다시 인식 — 벡터는 그대로
    assert len(calls) == 1
    wb.set_scale(pin_pitch_mm=5.08)
    wb.build()                                 # 장치 만들기 — 역시 그대로
    assert len(calls) == 1


def test_opening_another_image_drops_the_cached_vector(wb):
    wb.auto_detect(read_names=False)
    assert wb.vector is not None
    wb.open_image(IMG, name="다시", preset="lineart")
    assert wb.vector is None                   # 그림이 바뀌면 버린다
