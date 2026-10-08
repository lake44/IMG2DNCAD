"""작업대 화면(Tkinter) 스모크 시험 — 창을 투명하게 띄워 단추를 누르고 결과를 본다.

화면이 없는 곳(서버 등)에서는 통째로 건너뛴다. 논리는 test_studio.py가 화면 없이 시험한다.
"""
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from tkinter import filedialog, messagebox, simpledialog  # noqa: E402

from imgvec.device import DeviceSpec  # noqa: E402
from imgvec.dncad_backup import read_backup  # noqa: E402
from imgvec.studio_ui import run_studio  # noqa: E402

SET = Path(__file__).resolve().parent.parent / "samples" / "set"
IMG = SET / "block_plc.png"


def _display_available() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


pytestmark = pytest.mark.skipif(not _display_available(), reason="화면(디스플레이)이 없습니다")


def run(*args, **kw):
    """Tk가 자기 .tcl을 못 찾는 환경 문제는 실패가 아니라 건너뜀으로 센다 (test_picker_ui와 같은 처리)."""
    try:
        run_studio(*args, **kw)
    except tk.TclError as e:
        if ".tcl" in str(e):
            pytest.skip(f"Tk 초기화 실패(환경): {str(e)[:60]}")
        raise


@pytest.fixture()
def quiet(monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)


def test_studio_opens_with_auto_detect_done(tmp_path, quiet):
    img = tmp_path / "plc.png"
    img.write_bytes(IMG.read_bytes())
    seen = {}

    def on_ready(h):
        seen["pins"] = len(h["wb"].spec.pins)
        seen["list"] = h["listbox"].size()
        seen["alive"] = bool(h["root"].winfo_exists())

    run(img, name="PLC", preset="lineart", auto_close_ms=1500, on_ready=on_ready)
    assert seen["pins"] == 16 and seen["list"] == 16


def test_studio_builds_and_saves(tmp_path, quiet, monkeypatch):
    img = tmp_path / "plc.png"
    img.write_bytes(IMG.read_bytes())
    monkeypatch.setattr(simpledialog, "askfloat", lambda *a, **k: 5.08)

    def on_ready(h):
        h["wb"].set_scale(pin_pitch_mm=5.08)
        h["build"]()
        h["save"]()

    run(img, name="PLC", out_dir=tmp_path, preset="lineart", auto_close_ms=3000, on_ready=on_ready)
    assert (tmp_path / "PLC.device.json").exists()
    assert (tmp_path / "PLC.layout.json").exists()
    spec = DeviceSpec.load(tmp_path / "PLC.spec.json")
    assert len(spec.pins) == 16


def test_studio_clicks_add_and_remove_pins(tmp_path, quiet, monkeypatch):
    img = tmp_path / "plc.png"
    img.write_bytes(IMG.read_bytes())
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: "X9")
    seen = {}

    def on_ready(h):
        wb = h["wb"]
        before = len(wb.spec.pins)
        h["canvas"].event_generate("<Button-1>", x=120, y=90)
        seen["added"] = len(wb.spec.pins) - before
        h["canvas"].event_generate("<Button-3>", x=120, y=90)
        seen["after_remove"] = len(wb.spec.pins) - before

    run(img, name="PLC", preset="lineart", auto_close_ms=1500, on_ready=on_ready)
    assert seen["added"] == 1 and seen["after_remove"] == 0


def test_studio_exports_a_backup(tmp_path, quiet, monkeypatch):
    img = tmp_path / "plc.png"
    img.write_bytes(IMG.read_bytes())
    out = tmp_path / "PLC.dncadbak"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda *a, **k: str(out))

    def on_ready(h):
        h["wb"].set_scale(pin_pitch_mm=5.08)
        h["backup"]()

    run(img, name="PLC", preset="lineart", auto_close_ms=3000, on_ready=on_ready)
    assert out.exists()
    assert len(read_backup(out).rows["devices"]) == 1


def test_studio_opens_an_existing_spec(tmp_path, quiet):
    (tmp_path / "block_plc.png").write_bytes(IMG.read_bytes())
    spec = DeviceSpec.load(SET / "block_plc.spec.json")
    spec.dump(tmp_path / "block_plc.spec.json")
    seen = {}

    def on_ready(h):
        seen["pins"] = [p.name for p in h["wb"].spec.pins]

    run(tmp_path / "block_plc.spec.json", auto_close_ms=1500, on_ready=on_ready)
    assert seen["pins"][:2] == ["I0", "I1"]      # 명세에 적힌 이름이 그대로 (자동 인식이 덮지 않는다)
