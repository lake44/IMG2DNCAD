"""명령줄: imgvec device build | batch | validate | from-svg | layout | pick | kit | read-bak | check-bak"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

from .calib import CalibrationError
from .device import BuildResult, DeviceBuildError, DeviceSpec, build_device, write_build_outputs
from .dncad_backup import KitError, black_box_to_layout, read_backup, read_black_boxes, verify_backup, write_backup
from .dncad_rules import DEFAULT_RULES, DncadRules
from .imageio import load_image
from .layout import REMAP_LR, dncad_layout
from .model import DeviceModel, ModelError
from .pins import PinsFileError, read_pins_csv
from .svgmodel import SvgModelError, svg_to_model
from .validate import has_errors, validate


def _print_issues(issues) -> None:
    for i in issues:
        print("  " + str(i))


def _rules(a: argparse.Namespace) -> DncadRules:
    return replace(DEFAULT_RULES, allow_duplicate_designations=True) if getattr(a, "allow_dup_names", False) else DEFAULT_RULES


def _summary(res: BuildResult) -> None:
    m = res.model
    n_err = sum(i.severity == "error" for i in res.issues)
    n_warn = sum(i.severity == "warn" for i in res.issues)
    box = res.layout["box"]
    print(f"장치 '{m.name}': 본체 {m.body.w:.1f} x {m.body.h:.1f} mm (출처: {res.body_source}), 접속점 {len(m.pins)}개, 외형 도형 {len(m.graphics)}개")
    print(f"  축척 {res.calibration.mm_per_px:.5f} mm/px ({res.calibration.method})")
    print(f"  검증: 오류 {n_err}, 경고 {n_warn}, 참고 {len(res.issues) - n_err - n_warn}")
    _print_issues([i for i in res.issues if i.severity != "info"])
    placed = len(res.layout["pins"])
    print(f"  DNCAD 배치 명세: 블랙박스 {box['w']} x {box['h']} 단위 ({box['w_mm']} x {box['h_mm']} mm), 접속점 {placed}개 배치" + (f", {len(res.layout['skipped_pins'])}개 제외" if res.layout["skipped_pins"] else ""))


def cmd_build(a: argparse.Namespace) -> int:
    spec_dir = Path(a.spec).parent if a.spec else Path(".")
    spec = DeviceSpec.load(a.spec) if a.spec else DeviceSpec(name="")
    if a.pins:
        spec.pins = read_pins_csv(a.pins)
    if not spec.pins:
        print("오류: 접속점이 없습니다. --pins 또는 --spec으로 지정하세요", file=sys.stderr)
        return 2
    if a.name:
        spec.name = a.name
    if a.mm_per_px:
        spec.mm_per_px = a.mm_per_px
    if a.ref:
        spec.ref = tuple(a.ref)  # type: ignore[assignment]
    if a.body_width_mm:
        spec.body_width_mm = a.body_width_mm
    if a.pin_pitch_mm:
        spec.pin_pitch_mm = a.pin_pitch_mm
    if a.body:
        spec.body_px = tuple(a.body)  # type: ignore[assignment]
    if a.preset:
        spec.preset = a.preset
    if a.no_graphics:
        spec.graphics = False
    image = a.image or (str((spec_dir / spec.image)) if spec.image else None)
    if image is None and (spec.graphics or spec.body_px is None):
        print("오류: 이미지 경로가 필요합니다 (인자 또는 명세의 image)", file=sys.stderr)
        return 2
    if not spec.name:
        spec.name = Path(image).stem if image else "device"
    rgb = load_image(image).rgb if image else None
    if spec.image is None and image:
        spec.image = Path(image).name
    res = build_device(rgb, spec, rules=_rules(a), remap_sides=REMAP_LR if a.remap_lr else None, fit=a.fit)
    base = Path(a.output) if a.output else Path("out") / "device" / spec.name
    paths = write_build_outputs(res, base)
    _summary(res)
    print("  → " + ", ".join(str(p) for p in (paths["model"], paths["svg"], paths["layout"])))
    return 0 if res.ok else 1


def cmd_batch(a: argparse.Namespace) -> int:
    import csv
    import time

    src = Path(a.folder)
    specs = sorted(src.glob("*.spec.json"))
    if not specs:
        print(f"오류: {src}에 *.spec.json이 없습니다", file=sys.stderr)
        return 2
    out_dir = Path(a.output or "out/device_batch")
    rows = []
    bad = 0
    for sp in specs:
        t0 = time.perf_counter()
        try:
            spec = DeviceSpec.load(sp)
            image = src / spec.image if spec.image else None
            rgb = load_image(image).rgb if image else None
            res = build_device(rgb, spec, rules=_rules(a), remap_sides=REMAP_LR if a.remap_lr else None, stamp=False)
            base = out_dir / sp.name.replace(".spec.json", "")
            write_build_outputs(res, base)
            n_err = sum(i.severity == "error" for i in res.issues)
            n_warn = sum(i.severity == "warn" for i in res.issues)
            box = res.layout["box"]
            rows.append([sp.name.replace(".spec.json", ""), spec.name, f"{res.model.body.w:.1f}x{res.model.body.h:.1f}", len(res.model.pins),
                         len(res.layout["pins"]), f"{box['w']}x{box['h']}", n_err, n_warn, len(res.model.graphics), res.body_source, round(time.perf_counter() - t0, 1), ""])
            flag = "통과" if res.ok else "오류"
            print(f"{rows[-1][0]:<28} 본체 {rows[-1][2]:>11} mm  접속점 {len(res.model.pins):>2} → 배치 {len(res.layout['pins']):>2}  블랙박스 {rows[-1][5]:>8}  오류 {n_err} 경고 {n_warn}  {flag}  {rows[-1][10]}초")
            bad += 0 if res.ok else 1
        except (ValueError, OSError) as e:  # 한 장치의 실패가 전체를 막지 않게
            bad += 1
            rows.append([sp.name, "", "", "", "", "", "", "", "", "", round(time.perf_counter() - t0, 1), str(e)])
            print(f"{sp.name}: 실패 - {e}", file=sys.stderr)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "장치", "본체 mm", "접속점", "배치된 접속점", "블랙박스(단위)", "오류", "경고", "외형 도형", "본체 출처", "초", "실패 사유"])
        w.writerows(rows)
    print(f"완료: {len(specs) - bad}/{len(specs)}종 통과 → {out_dir}")
    return 1 if bad else 0


def cmd_validate(a: argparse.Namespace) -> int:
    model = DeviceModel.load(a.model)
    issues = validate(model, _rules(a))
    print(f"장치 '{model.name}': 접속점 {len(model.pins)}개")
    _print_issues(issues or [])
    if not issues:
        print("  문제 없음")
    return 1 if has_errors(issues) else 0


def cmd_from_svg(a: argparse.Namespace) -> int:
    model = svg_to_model(a.svg, name=a.name)
    out = Path(a.output) if a.output else Path(a.svg).with_suffix(".device.json")
    model.dump(out)
    issues = validate(model, _rules(a))
    print(f"SVG에서 읽음: 본체 {model.body.w:.1f} x {model.body.h:.1f} mm, 접속점 {len(model.pins)}개, 도형 {len(model.graphics)}개, 글자 {len(model.texts)}개")
    _print_issues([i for i in issues if i.severity != "info"])
    print(f"  → {out}")
    return 1 if has_errors(issues) else 0


def cmd_layout(a: argparse.Namespace) -> int:
    model = DeviceModel.load(a.model)
    lay = dncad_layout(model, _rules(a), height_units=a.height, fit=a.fit, remap_sides=REMAP_LR if a.remap_lr else None)
    text = json.dumps(lay, ensure_ascii=False, indent=1)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"→ {a.output}")
    else:
        print(text)
    return 0


def cmd_pick(a: argparse.Namespace) -> int:
    from .picker import run_picker

    out = Path(a.output) if a.output else Path(a.image).with_suffix(".spec.json")
    existing = DeviceSpec.load(out) if out.exists() and a.edit else None
    run_picker(a.image, out, name=a.name or "", existing=existing)
    return 0


def _layout_paths(items: list[str]) -> list[Path]:
    out: list[Path] = []
    for it in items:
        p = Path(it)
        if p.is_dir():
            found = sorted(p.glob("*.layout.json"))
            if not found:
                raise KitError(f"{p}에 *.layout.json이 없습니다")
            out += found
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"{p}이(가) 없습니다")
    return out


_KIT_NOTICE = "⚠ 시험용입니다. 손으로 만든 백업을 DNCAD가 받아들이는지는 미확인이니 새(빈) 프로젝트로만 가져와 보세요."


def cmd_kit(a: argparse.Namespace) -> int:
    paths = _layout_paths(a.inputs)
    layouts = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    out = Path(a.output or "out/dncad_testkit.dncadbak")
    res = write_backup(out, layouts, tag_prefix=a.tag_prefix, project_name=a.project_name, labels=not a.no_labels,
                       describe=a.describe, seed=a.seed, rules=_rules(a))
    map_path = out.with_suffix(".map.csv")
    with map_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["태그", "장치", "원본 배치 명세", "쪽", "x(단위)", "y(단위)", "폭", "높이", "접속점"])
        for d, p in zip(res.devices, paths):
            w.writerow([d.tag, d.name, p.name, d.page + 1, d.x, d.y, d.w, d.h, d.pins])
    n_page = max(d.page for d in res.devices) + 1
    print(f"장치 {len(res.devices)}개, 접속점 {sum(d.pins for d in res.devices)}개, {n_page}쪽 → {out} ({len(res.data) / 1024:.0f} KB)")
    print(f"  태그 대응표 → {map_path}")
    for w_ in res.warnings:
        print("  경고: " + w_)
    _print_issues([i for i in res.issues if i.severity != "info"])
    print("  " + _KIT_NOTICE)
    return 0


def cmd_check_bak(a: argparse.Namespace) -> int:
    issues = verify_backup(a.backup, _rules(a))
    n_err = sum(i.severity == "error" for i in issues)
    n_warn = sum(i.severity == "warn" for i in issues)
    print(f"{a.backup}: 오류 {n_err}, 경고 {n_warn}, 참고 {len(issues) - n_err - n_warn}")
    _print_issues(issues)
    return 1 if n_err else 0


def cmd_read_bak(a: argparse.Namespace) -> int:
    bak = read_backup(a.backup)
    boxes = read_black_boxes(bak)
    m = bak.manifest
    print(f"{a.backup}: 스키마 {m.get('schemaVersion')}, 장치 {len(bak.rows.get('devices', []))}개 중 블랙박스 배치 {len(boxes)}개")
    out_dir = Path(a.output) if a.output else None
    for bb in boxes:
        lay = black_box_to_layout(bb)
        top = sum(p["side"] == "top" for p in lay["pins"])
        extra = f", 미배치 {len(bb.unplaced)}" if bb.unplaced else ""
        print(f"  {lay['name'] or '(태그 없음)':<12} {bb.w}x{bb.h} 단위 ({lay['box']['w_mm']} x {lay['box']['h_mm']} mm)  접속점 위 {top} / 아래 {len(lay['pins']) - top}{extra}")
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            safe = "".join(ch if ch.isalnum() or ch in "-_()" else "_" for ch in (lay["name"] or bb.device_id[:8]))
            (out_dir / f"{safe}.layout.json").write_text(json.dumps(lay, ensure_ascii=False, indent=1), encoding="utf-8")
    if out_dir:
        print(f"  → {out_dir} (배치 명세 {len(boxes)}개)")
    return 0


def add_device_parser(sub) -> None:
    d = sub.add_parser("device", help="장치 모델 만들기·검증 (2단계) / DNCAD 백업 시험 (실험)")
    ds = d.add_subparsers(dest="dcmd", required=True)

    b = ds.add_parser("build", help="그림 + 접속점 명세 → 장치 모델(JSON)·주석 SVG·DNCAD 배치 명세")
    b.add_argument("image", nargs="?", help="그림 파일 (명세에 image가 있으면 생략 가능)")
    b.add_argument("--spec", help="장치 명세 JSON (pick이 만들거나 직접 작성)")
    b.add_argument("--pins", help="접속점 CSV (명세의 pins를 대체)")
    b.add_argument("--name", help="장치 이름")
    b.add_argument("--mm-per-px", type=float, help="축척: 원본 픽셀 1개의 길이(mm)")
    b.add_argument("--ref", nargs=5, type=float, metavar=("X1", "Y1", "X2", "Y2", "MM"), help="축척: 두 점(px)과 실제 거리(mm)")
    b.add_argument("--body-width-mm", type=float, help="축척: 본체 가로 폭의 실제 길이(mm). --body가 필요")
    b.add_argument("--pin-pitch-mm", type=float, help="축척: 같은 줄 접속점의 실제 간격(mm). 접속점에 x_px·group 필요")
    b.add_argument("--body", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"), help="본체 사각형(px). 없으면 도형에서 추정")
    b.add_argument("--preset", choices=["auto", "lineart", "illustration", "photo"], help="벡터화 프리셋")
    b.add_argument("--no-graphics", action="store_true", help="외형 도형 없이 본체와 접속점만")
    b.add_argument("--remap-lr", action="store_true", help="왼쪽 변 접속점은 위 변, 오른쪽 변은 아래 변으로 옮겨 배치 명세를 만든다")
    b.add_argument("--fit", choices=["pins", "body"], default="pins", help="블랙박스 폭: pins=접속점에 맞춤(기본), body=실물 크기")
    b.add_argument("--allow-dup-names", action="store_true", help="같은 이름의 접속점을 오류가 아닌 경고로 (기존 DNCAD 백업에는 있음)")
    b.add_argument("-o", "--output", help="출력 경로 기본값(확장자 제외). 기본 out/device/<이름>")
    b.set_defaults(func=cmd_build)

    bt = ds.add_parser("batch", help="폴더의 *.spec.json을 모두 변환 (샘플 세트 점검용)")
    bt.add_argument("folder")
    bt.add_argument("-o", "--output", help="출력 폴더 (기본 out/device_batch)")
    bt.add_argument("--remap-lr", action="store_true")
    bt.add_argument("--allow-dup-names", action="store_true")
    bt.set_defaults(func=cmd_batch)

    v = ds.add_parser("validate", help="장치 모델 JSON 검증")
    v.add_argument("model")
    v.add_argument("--allow-dup-names", action="store_true")
    v.set_defaults(func=cmd_validate)

    f = ds.add_parser("from-svg", help="고친 주석 SVG를 장치 모델 JSON으로 되읽기")
    f.add_argument("svg")
    f.add_argument("--name")
    f.add_argument("-o", "--output")
    f.add_argument("--allow-dup-names", action="store_true")
    f.set_defaults(func=cmd_from_svg)

    ly = ds.add_parser("layout", help="장치 모델 → DNCAD 배치 명세 (슬롯·단위 좌표·회전)")
    ly.add_argument("model")
    ly.add_argument("--height", type=int, help="블랙박스 높이(단위). 기본 112")
    ly.add_argument("--fit", choices=["pins", "body"], default="pins")
    ly.add_argument("--remap-lr", action="store_true")
    ly.add_argument("--allow-dup-names", action="store_true")
    ly.add_argument("-o", "--output")
    ly.set_defaults(func=cmd_layout)

    k = ds.add_parser("kit", help="[실험·미검증] 배치 명세들 → 시험용 DNCAD 백업(.dncadbak)")
    k.add_argument("inputs", nargs="+", help="*.layout.json 파일 또는 그것들이 든 폴더")
    k.add_argument("-o", "--output", help="출력 .dncadbak (기본 out/dncad_testkit.dncadbak). 태그 대응표 .map.csv도 함께 씀")
    k.add_argument("--tag-prefix", default="-ZZ", help="장치 태그 접두 (기본 -ZZ → -ZZ01, -ZZ02 …. -ZZ는 DNCAD가 블랙박스에 붙이는 기본 글자)")
    k.add_argument("--project-name", help="백업 속 프로젝트 이름")
    k.add_argument("--no-labels", action="store_true", help="블랙박스 위 장치 이름 글자를 넣지 않음")
    k.add_argument("--describe", action="store_true", help="블랙박스 컴포넌트의 description에 장치 이름을 씀")
    k.add_argument("--seed", type=int, help="ID 난수 시드 (같은 입력·시드면 같은 ID이나 시각은 매번 다름)")
    k.add_argument("--allow-dup-names", action="store_true")
    k.set_defaults(func=cmd_kit)

    rb = ds.add_parser("read-bak", help="DNCAD 백업의 블랙박스 장치를 읽어 요약 (+ 배치 명세로 내보내기)")
    rb.add_argument("backup")
    rb.add_argument("-o", "--output", help="배치 명세(<태그>.layout.json)를 쓸 폴더")
    rb.set_defaults(func=cmd_read_bak)

    cb = ds.add_parser("check-bak", help="DNCAD 백업 점검 (ZIP·해시·행 수·참조·블랙박스 규칙)")
    cb.add_argument("backup")
    cb.add_argument("--allow-dup-names", action="store_true")
    cb.set_defaults(func=cmd_check_bak)

    pk = ds.add_parser("pick", help="화면에서 클릭해 접속점·본체·축척 지정 (Tkinter)")
    pk.add_argument("image")
    pk.add_argument("-o", "--output", help="저장할 명세 JSON (기본: <그림>.spec.json). 접속점 CSV도 함께 저장")
    pk.add_argument("--name")
    pk.add_argument("--edit", action="store_true", help="이미 있는 명세를 불러와 이어서 편집")
    pk.set_defaults(func=cmd_pick)


EXPECTED_ERRORS = (DeviceBuildError, CalibrationError, PinsFileError, ModelError, SvgModelError, KitError)
