"""docs/schema/*.json이 실제 출력과 맞는지 — 스키마 문서가 낡지 않게 지키는 시험."""
import copy
import json
from pathlib import Path

import pytest
from PIL import Image

jsonschema = pytest.importorskip("jsonschema")

from imgvec.device import DeviceSpec, build_device  # noqa: E402
from imgvec.imageio import load_image  # noqa: E402
from imgvec.layout import REMAP_LR  # noqa: E402
from imgvec.pins import PinSpec  # noqa: E402
from imgvec.synth import make_device  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "docs" / "schema"
SET = ROOT / "samples" / "set"


def schema(name):
    return jsonschema.Draft202012Validator(json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8")))


MODEL, LAYOUT, SPEC = (schema(n) for n in ("device-model.schema.json", "dncad-device-layout.schema.json", "device-spec.schema.json"))


def errors(validator, doc):
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message[:80]}" for e in validator.iter_errors(doc)]


@pytest.fixture(scope="module")
def full_build(tmp_path_factory):
    """벡터화까지 거친 실제 결과 하나 (도형이 모델에 들어간 경우)."""
    s = make_device(seed=1, pin_sides="tb", n_pins=4)
    tmp = tmp_path_factory.mktemp("schema")
    img = tmp / "dev.png"
    Image.fromarray(s.rgb).save(img)
    spec = DeviceSpec(name="DEV", image="dev.png", mm_per_px=0.1, body_px=s.body_px, preset="lineart",
                      pins=[PinSpec(p["name"], p["side"], x_px=p["x_px"], y_px=p["y_px"]) for p in s.pins])
    return spec, build_device(load_image(img).rgb, spec)


def test_schema_files_are_valid_json_schemas():
    for name in ("device-model.schema.json", "dncad-device-layout.schema.json", "device-spec.schema.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8")))


def test_full_build_outputs_match_schemas(full_build):
    spec, res = full_build
    model = res.model.to_dict()
    assert model["graphics"], "도형이 있어야 primitive 스키마를 시험할 수 있다"
    assert not errors(MODEL, model)
    assert not errors(LAYOUT, res.layout)
    assert not errors(SPEC, spec.to_dict())
    kinds = {g["type"] for g in model["graphics"]}
    segs = {s["type"] for g in model["graphics"] if g["type"] == "path" for s in g["segs"]}
    assert "path" in kinds and "line" in segs


@pytest.mark.skipif(not (SET / "manifest.csv").exists(), reason="samples/set이 없습니다")
@pytest.mark.parametrize("path", sorted(SET.glob("*.spec.json")), ids=lambda p: p.name.replace(".spec.json", ""))
def test_sample_specs_models_and_layouts_match_schemas(path):
    assert not errors(SPEC, json.loads(path.read_text(encoding="utf-8")))
    spec = DeviceSpec.load(path)
    spec.graphics = False
    rgb = load_image(SET / spec.image).rgb if spec.body_px is None else None
    res = build_device(rgb, spec, remap_sides=REMAP_LR, stamp=False)
    assert not errors(MODEL, res.model.to_dict())
    assert not errors(LAYOUT, res.layout)


def test_schemas_reject_bad_documents(full_build):
    _, res = full_build
    good = res.model.to_dict()
    cases = []
    d = copy.deepcopy(good)
    del d["body"]
    cases.append(("본체 없음", MODEL, d))
    d = copy.deepcopy(good)
    d["pins"][0]["side"] = "middle"
    cases.append(("변 이름", MODEL, d))
    d = copy.deepcopy(good)
    d["pins"][0]["name"] = ""
    cases.append(("빈 이름", MODEL, d))
    d = copy.deepcopy(good)
    d["body"]["w"] = -3
    cases.append(("음수 폭", MODEL, d))
    d = copy.deepcopy(good)
    d["schema"] = "device-model/2.0"
    cases.append(("버전", MODEL, d))
    d = copy.deepcopy(good)
    d["graphics"].append({"type": "spline"})
    cases.append(("모르는 도형", MODEL, d))
    lay = copy.deepcopy(res.layout)
    lay["pins"][0]["rotation"] = 90
    cases.append(("회전 90", LAYOUT, lay))
    lay = copy.deepcopy(res.layout)
    lay["pins"][0]["side"] = "left"
    cases.append(("배치 명세의 왼쪽 변", LAYOUT, lay))
    for label, validator, doc in cases:
        assert errors(validator, doc), label


def test_hand_written_spec_with_aliases_is_accepted():
    doc = {"schema": "device-spec/1.0", "name": "X", "scale": {"pin_pitch_mm": 5.0},
           "pins": [{"name": "1", "side": "상", "offset_mm": 5}, {"name": "2", "side": "하", "x_px": 10, "y_px": 20, "group": "P1"}]}
    assert not errors(SPEC, doc)
    DeviceSpec.from_dict(doc)  # 읽기 쪽도 같은 문서를 받아들인다
