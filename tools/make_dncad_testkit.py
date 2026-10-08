"""DNCAD 가져오기 시험 키트를 만든다 (0단계 '손으로 만든 파일로 가져오기 시험').

만드는 것 (기본 out/dncad_testkit/):
  01_FA01형_한장치.dncadbak   실제 -FA01(160 x 112, 위 4개·아래 2개)과 같은 모양의 장치 1개. 가장 작은 시험
  02_샘플20종.dncadbak        samples/set의 샘플 20종 (--remap-lr 적용), 여러 쪽
  03_대조용_재포장.dncadbak   (--control 백업을 줄 때만) 실제 백업을 내용 그대로 다시 묶은 파일. 01이 실패할 때
                              '묶는 방식'이 문제인지 '내용'이 문제인지 가르는 대조군
  README.txt                  시험 방법과 알려 줄 것

사용: python tools/make_dncad_testkit.py [--control <실제 백업.dncadbak>] [-o 폴더]
"""
from __future__ import annotations

import argparse
import csv
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from imgvec.device import DeviceSpec, build_device  # noqa: E402
from imgvec.dncad_backup import MEMBERS, build_backup, read_backup, read_black_boxes, verify_backup  # noqa: E402
from imgvec.imageio import load_image  # noqa: E402
from imgvec.layout import REMAP_LR, SCHEMA  # noqa: E402

SET = ROOT / "samples" / "set"

README = """imgvec 시험 키트 — DNCAD로 가져오기 시험

[무엇을 시험하나]
imgvec이 '손으로 만든' DNCAD 백업(.dncadbak)을 DNCAD가 받아들이는지, 받아들이면 블랙박스와
장치 접속점이 제대로 놓이는지 확인합니다. 이 파일들은 시험용이며, 형식은 실제 백업(스키마 0070)을
읽어 알아낸 것입니다. DNCAD가 받아들인다는 보증은 아직 없습니다.

[주의]
- 반드시 '새 프로젝트로 가져오기'로만 시험하세요. 현재 프로젝트를 덮어쓰는 복원이라면 쓰지 마세요.
- 가져오기 전에 지금 작업 중인 프로젝트의 백업을 한 번 받아 두세요.
- DNCAD 버전이 바뀌어 스키마가 0070이 아니면 거부될 수 있습니다. 그때는 DNCAD에서 새 백업을 받아
  python tools/extract_dncad_template.py <새 백업> 으로 템플릿을 갱신하고 이 키트를 다시 만드세요.

[순서]
1. 01_FA01형_한장치.dncadbak 을 새 프로젝트로 가져옵니다. (가장 작은 시험)
2. 되면 02_샘플20종.dncadbak 도 가져옵니다.
3. 01이 거부되면 03_대조용_재포장.dncadbak (있을 때)을 가져옵니다.
   - 03도 거부: 파일을 '묶는 방식'이 문제입니다. (ZIP 안의 순서·압축 방식 등)
   - 03은 되고 01만 거부: 안의 '내용'이 문제입니다. 오류 문구가 단서입니다.

[확인할 것]  (01 기준)
- 가져오기가 끝까지 되는가? 되지 않으면 화면에 나온 오류 문구를 그대로.
- 장치 -ZZ01 이 1개 있고, 블랙박스(160 x 112 단위)가 쪽 위쪽 왼쪽 부근에 놓였는가?
- 접속점이 위 변에 1·2·3·4, 아래 변에 PE·PE' 로 놓였는가? 위치와 이름이 맞는가?
- DNCAD의 검증(관문)을 돌리면 통과하는가? 접속점 0개 같은 오류가 나오는가?
- 02: 장치 20개가 {pages}쪽에 나뉘어 놓이고, 각 블랙박스 위에 장치 이름 글자가 보이는가?
  (02_샘플20종.map.csv 에 태그와 샘플 이름의 대응이 있습니다)

[기대 그림]
*.기대모습-N쪽.svg 는 DNCAD에서 이렇게 보여야 한다는 그림입니다 (브라우저로 열기). 노란 사각형이 블랙박스,
빨간 점이 장치 접속점, 노란 글자가 장치 이름입니다. DNCAD 화면과 견주어 어긋난 곳을 알려 주세요.

[알려 줄 것]
성공/실패, 실패하면 오류 문구 전체(가능하면 화면 캡처), 눈으로 본 어긋남(위치·이름·방향).
"""


def layout_of(spec_path: Path):
    spec = DeviceSpec.load(spec_path)
    spec.graphics = False   # 배치 명세에는 외형 도형이 필요 없다 (벡터화는 건너뛴다)
    rgb = load_image(spec_path.parent / spec.image).rgb if spec.body_px is None else None
    res = build_device(rgb, spec, remap_sides=REMAP_LR, stamp=False)
    if not res.ok:
        raise SystemExit(f"{spec_path.name}: 검증 오류 — {[str(i) for i in res.issues if i.severity == 'error']}")
    return res.layout


def fa01_layout() -> dict:
    def pin(name, side, slot, h=112):
        return {"designation": name, "side": side, "slot": slot, "x": 32 * slot, "y": 0 if side == "top" else h,
                "rotation": 0 if side == "top" else 180, "mirrored": None, "deviation_mm": 0.0, "group": ""}

    pins = [pin("1", "top", 1), pin("2", "top", 2), pin("3", "top", 3), pin("4", "top", 4), pin("PE", "bottom", 3), pin("PE'", "bottom", 4)]
    return {"schema": SCHEMA, "name": "FA01형 (실제 -FA01과 같은 모양)", "units_per_mm": 6.4, "origin": "box top-left, x right, y down",
            "box": {"w": 160, "h": 112, "w_mm": 25.0, "h_mm": 17.5}, "pins": pins, "skipped_pins": [], "issues": []}


def preview_svgs(data: bytes) -> dict[int, str]:
    """만든 백업을 다시 읽어 쪽마다 '이렇게 보여야 한다'는 기대 그림(SVG, 단위 좌표)을 그린다."""
    from xml.sax.saxutils import escape

    bak = read_backup(data)
    pages = [p["id"] for p in sorted(bak.rows["pages"], key=lambda p: p["ordinal"])]
    out: dict[int, str] = {}
    for k, pid in enumerate(pages):
        parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm" viewBox="0 0 1900 1344" font-family="sans-serif">',
                 '<rect x="0" y="0" width="1900" height="1344" fill="#fff" stroke="#999" stroke-width="2"/>',
                 f'<text x="20" y="36" font-size="22" fill="#888">기대 모습 {k + 1}쪽 — DNCAD에서 가져온 결과와 견주어 보세요 (단위 좌표)</text>']
        for bb in read_black_boxes(bak):
            if bb.page_id != pid:
                continue
            parts.append(f'<rect x="{bb.x}" y="{bb.y}" width="{bb.w}" height="{bb.h}" fill="#fffbe6" stroke="#222" stroke-width="2"/>')
            parts.append(f'<text x="{bb.x + 6}" y="{bb.y + bb.h / 2 + 5}" font-size="16" fill="#222">{escape(bb.tag or "")}</text>')
            for p in bb.pins:
                x, y = bb.x + p.x, bb.y + p.y
                ty = y + 16 if p.rotation == 0 else y - 7
                parts.append(f'<circle cx="{x}" cy="{y}" r="4.5" fill="#d33"/>')
                parts.append(f'<text x="{x}" y="{ty}" font-size="12" text-anchor="middle" fill="#900">{escape(p.designation)}</text>')
        for a in bak.rows["annotations"]:
            if a["page_id"] == pid and a["kind"] == "TEXT":
                parts.append(f'<text x="{a["x"]}" y="{a["y"]}" font-size="16" text-anchor="middle" fill="#b8860b">{escape(a["text"])}</text>')
        parts.append("</svg>")
        out[k + 1] = "\n".join(parts) + "\n"
    return out


def repack(src: Path, dst: Path) -> None:
    """실제 백업의 세 파일을 내용 그대로 다시 ZIP으로 묶는다 (imgvec이 묶는 방식과 같은 방식으로)."""
    bak = read_backup(src)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in MEMBERS:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, bak.raw[name])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default=str(ROOT / "out" / "dncad_testkit"))
    ap.add_argument("--control", help="대조용으로 다시 묶을 실제 백업(.dncadbak)")
    a = ap.parse_args()
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)

    one = build_backup([fa01_layout()], project_name="imgvec 시험 01 (한 장치)")
    (out / "01_FA01형_한장치.dncadbak").write_bytes(one.data)

    specs = sorted(SET.glob("*.spec.json"))
    if not specs:
        raise SystemExit(f"{SET}에 *.spec.json이 없습니다")
    layouts = [layout_of(p) for p in specs]
    many = build_backup(layouts, project_name="imgvec 시험 02 (샘플 20종)")
    (out / "02_샘플20종.dncadbak").write_bytes(many.data)
    with (out / "02_샘플20종.map.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["태그", "장치", "샘플 명세", "쪽", "x(단위)", "y(단위)", "폭", "높이", "접속점"])
        for d, p in zip(many.devices, specs):
            w.writerow([d.tag, d.name, p.name, d.page + 1, d.x, d.y, d.w, d.h, d.pins])

    for stem, kit in (("01_FA01형_한장치", one), ("02_샘플20종", many)):   # 기대 그림
        for n, svg in preview_svgs(kit.data).items():
            (out / f"{stem}.기대모습-{n}쪽.svg").write_text(svg, encoding="utf-8")

    lines = [f"01  장치 1개, 접속점 {one.devices[0].pins}개", f"02  장치 {len(many.devices)}개, 접속점 {sum(d.pins for d in many.devices)}개, {max(d.page for d in many.devices) + 1}쪽"]
    if a.control:
        repack(Path(a.control), out / "03_대조용_재포장.dncadbak")
        lines.append("03  대조용: 실제 백업을 내용 그대로 다시 묶음")
    (out / "README.txt").write_text(README.format(pages=max(d.page for d in many.devices) + 1), encoding="utf-8-sig")

    for f in sorted(out.glob("*.dncadbak")):
        if f.name.startswith("03_"):
            print(f"  {f.name:<28} {f.stat().st_size / 1024:6.0f} KB  (대조용: 실제 백업 그대로라 점검하지 않음)")
            continue
        bad = [i for i in verify_backup(f) if i.severity == "error"]
        print(f"  {f.name:<28} {f.stat().st_size / 1024:6.0f} KB  자체 점검 오류 {len(bad)}")
    print("\n".join(lines))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
