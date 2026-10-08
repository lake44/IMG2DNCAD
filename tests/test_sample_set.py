"""샘플 세트(samples/set, 20종)를 장치 모델·배치 명세까지 변환하는 회귀 시험.

벡터화는 건너뛴다(graphics=False) — 그건 test_pipeline이 본다. 여기서는 정답 접속점 → 축척 → 본체 → 검증 → 배치 명세 → 시험용 백업의 흐름을 본다.
"""
import csv
from pathlib import Path

import pytest

from imgvec.dncad_backup import build_backup, read_backup, read_black_boxes
from imgvec.device import DeviceSpec, build_device
from imgvec.imageio import load_image
from imgvec.layout import REMAP_LR
from imgvec.validate import has_errors

SET = Path(__file__).resolve().parent.parent / "samples" / "set"
pytestmark = pytest.mark.skipif(not (SET / "manifest.csv").exists(), reason="samples/set이 없습니다")


def _rows() -> list[dict]:
    if not (SET / "manifest.csv").exists():
        return []
    with (SET / "manifest.csv").open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


ROWS = _rows()


def build(row):
    spec = DeviceSpec.load(SET / f"{row['id']}.spec.json")
    spec.graphics = False
    rgb = load_image(SET / spec.image).rgb if spec.body_px is None else None   # 본체를 지정하지 않았으면 도형에서 추정해야 하므로 그림이 필요
    return spec, build_device(rgb, spec, remap_sides=REMAP_LR, stamp=False)


# 제조사 카탈로그 사진(제3자 저작물)은 공개 저장소에 올리지 않는다 — 명세·핀 CSV만 있고 그림은 없을 수 있다 (samples/set/README.md)
NOT_IN_REPO = {"real_pilz_pnoz_x3p.jpg", "deg_pilz_lowres.jpg"}


def test_manifest_lists_twenty_samples_with_files():
    assert len(ROWS) == 20
    for r in ROWS:
        assert (SET / r["파일"]).exists() or r["파일"] in NOT_IN_REPO, r["id"]
        assert (SET / f"{r['id']}.spec.json").exists() and (SET / f"{r['id']}.pins.csv").exists(), r["id"]


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_sample_builds_validates_and_lays_out(row):
    spec, res = build(row)
    errors = [str(i) for i in res.issues if i.severity == "error"]
    assert not has_errors(res.issues), errors
    m = res.model
    # 접속점 수와 변별 수가 manifest와 같다
    assert len(m.pins) == int(row["접속점수"])
    for side, col in (("top", "위"), ("bottom", "아래"), ("left", "왼쪽"), ("right", "오른쪽")):
        assert sum(p.side == side for p in m.pins) == int(row[col]), (side, row["id"])
    # 모두 한 번씩, 변마다 슬롯은 겹치지 않게 배치된다
    lay = res.layout
    assert len(lay["pins"]) == len(m.pins) and not lay["skipped_pins"]
    for side in ("top", "bottom"):
        slots = [p["slot"] for p in lay["pins"] if p["side"] == side]
        assert len(slots) == len(set(slots))
        assert all(s >= 1 for s in slots)
    box = lay["box"]
    assert box["w"] % 16 == 0 and box["h"] == 112
    assert all(0 < p["x"] <= box["w"] - 16 for p in lay["pins"]), "접속점이 블랙박스 오른쪽 가장자리에 너무 붙었습니다"
    # 같은 변·같은 단자열 안에서는 그림에서의 왼쪽→오른쪽 순서가 슬롯 순서로 남는다 (옮겨 온 접속점은 제외)
    slot_of = {p["designation"]: p for p in lay["pins"]}
    key = lambda p: (p.side, p.group)  # noqa: E731
    groups: dict[tuple[str, str], list] = {}
    for p in m.pins:
        if p.side in ("top", "bottom") and "moved_from" not in slot_of[p.name]:
            groups.setdefault(key(p), []).append(p)
    for (side, group), ps in groups.items():
        ps.sort(key=lambda p: p.offset())
        slots = [slot_of[p.name]["slot"] for p in ps]
        assert slots == sorted(slots), (row["id"], side, group)


def test_all_samples_make_one_valid_test_kit():
    layouts = []
    for r in ROWS:
        _, res = build(r)
        layouts.append(res.layout)
    kit = build_backup(layouts, seed=1)
    assert not [i for i in kit.issues if i.severity != "info"]
    boxes = read_black_boxes(read_backup(kit.data))
    assert len(boxes) == 20 and sum(len(b.pins) for b in boxes) == sum(int(r["접속점수"]) for r in ROWS)
