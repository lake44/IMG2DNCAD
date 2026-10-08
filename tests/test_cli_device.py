import json

import pytest
from PIL import Image

from imgvec.cli import main
from imgvec.device import DeviceSpec
from imgvec.pins import PinSpec, write_pins_csv
from imgvec.synth import make_device


@pytest.fixture()
def files(tmp_path):
    s = make_device(seed=1, pin_sides="tb", n_pins=4)
    img = tmp_path / "dev.png"
    Image.fromarray(s.rgb).save(img)
    pins = tmp_path / "pins.csv"
    write_pins_csv(pins, [PinSpec(p["name"], p["side"], x_px=p["x_px"], y_px=p["y_px"]) for p in s.pins])
    return tmp_path, img, pins, s


def test_device_build_validate_layout_and_from_svg(files, capsys):
    tmp, img, pins, s = files
    x0, y0, x1, y1 = s.body_px
    base = tmp / "o" / "dev"
    rc = main(["device", "build", str(img), "--pins", str(pins), "--body", str(x0), str(y0), str(x1), str(y1),
               "--mm-per-px", "0.1", "--name", "DEV", "--preset", "lineart", "-o", str(base)])
    assert rc == 0
    model = tmp / "o" / "dev.device.json"
    assert model.exists() and (tmp / "o" / "dev.device.svg").exists() and (tmp / "o" / "dev.layout.json").exists()
    out = capsys.readouterr().out
    assert "접속점 8개" in out and "블랙박스" in out

    assert main(["device", "validate", str(model)]) == 0

    lay = tmp / "lay.json"
    assert main(["device", "layout", str(model), "-o", str(lay)]) == 0
    assert len(json.loads(lay.read_text(encoding="utf-8"))["pins"]) == 8

    back = tmp / "back.device.json"
    assert main(["device", "from-svg", str(tmp / "o" / "dev.device.svg"), "-o", str(back)]) == 0
    assert len(json.loads(back.read_text(encoding="utf-8"))["pins"]) == 8


def test_device_build_with_spec_and_relative_image(files):
    tmp, img, pins, s = files
    spec = DeviceSpec(name="SP", image=img.name, mm_per_px=0.1, body_px=s.body_px, preset="lineart",
                      pins=[PinSpec(p["name"], p["side"], x_px=p["x_px"], y_px=p["y_px"]) for p in s.pins])
    spec.dump(tmp / "dev.spec.json")
    assert main(["device", "build", "--spec", str(tmp / "dev.spec.json"), "-o", str(tmp / "o2" / "x")]) == 0
    assert (tmp / "o2" / "x.device.json").exists()


def test_device_build_reports_errors_with_exit_code(files, capsys):
    tmp, img, pins, s = files
    bad = tmp / "dup.csv"
    write_pins_csv(bad, [PinSpec("A", "top", offset_mm=5), PinSpec("A", "top", offset_mm=10)])
    rc = main(["device", "build", str(img), "--pins", str(bad), "--body", "0", "0", "300", "200", "--mm-per-px", "0.1", "--no-graphics", "-o", str(tmp / "e")])
    assert rc == 1
    assert "E-DUP" in capsys.readouterr().out
    # 축척이 없으면 안내와 함께 종료 코드 2
    rc = main(["device", "build", str(img), "--pins", str(bad), "--body", "0", "0", "300", "200", "--no-graphics", "-o", str(tmp / "e2")])
    assert rc == 2
    assert "축척" in capsys.readouterr().err
