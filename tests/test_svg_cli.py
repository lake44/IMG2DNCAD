import json
import math
import xml.etree.ElementTree as ET

from PIL import Image

from imgvec.cli import main
from imgvec.geom import ArcSeg, Circle, LineSeg, Path
from imgvec.svg import build_svg, path_d


def test_path_d_flags_for_arc():
    # 시계 방향(화면에서) 90° 호: a1 > a0 이면 sweep-flag=1, 큰 호 아님
    p = Path([ArcSeg(0, 0, 10, 0.0, math.pi / 2)])
    d = path_d(p)
    assert d.startswith("M10 0A10 10 0 0 1 0 10") or "A10 10 0 0 1" in d
    big = path_d(Path([ArcSeg(0, 0, 10, 0.0, -1.5 * math.pi)]))
    assert "0 1 0" in big      # large-arc=1, sweep=0


def test_build_svg_is_valid_xml_with_mm_units():
    prims = [Circle(5, 5, 2), Path([LineSeg((0, 0), (10, 0)), LineSeg((10, 0), (10, 8))], closed=False)]
    svg = build_svg(prims, 100, 80, 0.1)
    root = ET.fromstring(svg.split("\n", 1)[1])
    assert root.attrib["width"] == "10mm" and root.attrib["height"] == "8mm"
    assert root.attrib["viewBox"] == "0 0 10 8"
    tags = [e.tag.split("}")[1] for e in root.iter()]
    assert tags.count("circle") == 1 and tags.count("path") == 1


def test_build_svg_without_scale_uses_px():
    svg = build_svg([Circle(5, 5, 2)], 100, 80, None)
    assert 'width="100"' in svg and "mm" not in svg.split("<g")[0]


def test_cli_convert_writes_svg_report_overlay_json(tmp_path, png_path):
    out = tmp_path / "o" / "dev.svg"
    rc = main(["convert", str(png_path), "-o", str(out), "--mm-per-px", "0.1", "--json"])
    assert rc == 0
    assert out.exists()
    assert (tmp_path / "o" / "dev.report.json").exists()
    assert (tmp_path / "o" / "dev.overlay.png").exists()
    data = json.loads((tmp_path / "o" / "dev.prims.json").read_text(encoding="utf-8"))
    assert data["unit"] == "mm" and data["schema"].startswith("imgvec-primitives")
    assert any(p["type"] == "circle" for p in data["primitives"])
    ET.parse(out)  # 유효한 XML


def test_cli_ref_two_point_scale(tmp_path, png_path, device_clean):
    # 본체 가로 변(x 320→1080 px, 정답 길이 76 mm)을 기준 거리로 사용
    out = tmp_path / "ref.svg"
    rc = main(["convert", str(png_path), "-o", str(out), "--ref", "320", "500", "1080", "500", "76", "--no-overlay"])
    assert rc == 0
    root = ET.parse(out).getroot()
    assert abs(float(root.attrib["width"].replace("mm", "")) - 140.0) < 0.2


def test_cli_batch_with_scales_csv(tmp_path, device_clean, device_coarse):
    src = tmp_path / "in"
    src.mkdir()
    Image.fromarray(device_clean.rgb).save(src / "a.png")
    Image.fromarray(device_coarse.rgb).save(src / "b.png")
    (src / "scales.csv").write_text("file,mm_per_px\na.png,0.1\nb.png,0.25\n", encoding="utf-8")
    out = tmp_path / "out"
    rc = main(["batch", str(src), "-o", str(out), "--scales", str(src / "scales.csv"), "--no-overlay"])
    assert rc == 0
    assert (out / "a.svg").exists() and (out / "b.svg").exists() and (out / "summary.csv").exists()
    wa = float(ET.parse(out / "a.svg").getroot().attrib["width"].replace("mm", ""))
    wb = float(ET.parse(out / "b.svg").getroot().attrib["width"].replace("mm", ""))
    assert abs(wa - 140.0) < 0.2 and abs(wb - 140.0) < 0.2


def test_cli_does_not_overwrite_foreign_svg(tmp_path, png_path):
    mine = tmp_path / "mine.svg"
    mine.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    assert main(["convert", str(png_path), "-o", str(mine), "--mm-per-px", "0.1"]) == 2
    assert mine.read_text(encoding="utf-8").startswith("<svg")        # 그대로
    assert main(["convert", str(png_path), "-o", str(mine), "--mm-per-px", "0.1", "--force", "--no-overlay"]) == 0
    assert "imgvec" in mine.read_text(encoding="utf-8")
    # imgvec이 만든 파일은 다시 변환할 때 덮어쓴다
    assert main(["convert", str(png_path), "-o", str(mine), "--mm-per-px", "0.1", "--no-overlay"]) == 0


def test_cli_missing_file_returns_error(tmp_path):
    assert main(["convert", str(tmp_path / "nope.png")]) == 2


def test_cli_info(png_path, capsys):
    assert main(["info", str(png_path)]) == 0
    assert "lineart" in capsys.readouterr().out
