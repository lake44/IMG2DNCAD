"""명령줄: imgvec device studio | run | auto | pick | build | batch | validate | from-svg | layout | kit | merge | read-bak | check-bak"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

from .calib import CalibrationError
from .device import BuildResult, DeviceBuildError, DeviceSpec, auto_spec, build_device, write_build_outputs
from .dncad_backup import (PAGE_H, PAGE_W, KitError, black_box_to_layout, read_backup, read_black_boxes, verify_backup,
                            write_backup, write_merged_backup)
from .dncad_rules import DEFAULT_RULES, DncadRules
from .imageio import load_image
from .layout import REMAP_LR, dncad_layout
from .model import DeviceModel, ModelError
from .pins import PinsFileError, read_pins_csv
from .studio import StudioError
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


def cmd_studio(a: argparse.Namespace) -> int:
    """창 하나에서 그림 → 자동 인식 → 손질 → 장치 → DNCAD 백업 (5단계)."""
    from .studio_ui import run_studio

    run_studio(a.source, name=a.name or "", out_dir=a.out_dir, preset=a.preset, auto=not a.no_auto)
    return 0


def _norm_tag(tag: str | None) -> str | None:
    """DNCAD 장치 태그는 늘 '-'로 시작한다. 명령줄에서 `--tag -T1`은 argparse가 옵션으로 보므로
    `--tag T1`로 줘도 되게 앞의 '-'를 붙여 준다 (`--tag=-T1`도 그대로 받는다)."""
    if not tag:
        return tag
    t = tag.strip()
    return t if t.startswith("-") else "-" + t


def cmd_run(a: argparse.Namespace) -> int:
    """화면 없이 같은 길을 한 번에: 그림 → 자동 인식 → 장치 → (원하면) DNCAD 백업."""
    from .studio import run_pipeline

    img = Path(a.image)
    base = Path(a.output) if a.output else Path("out") / (a.name or img.stem)
    wb, files = run_pipeline(img, name=a.name or img.stem, preset=a.preset, out_base=base, read_names=not a.no_names,
                             mm_per_px=a.mm_per_px, body_width_mm=a.body_width_mm, pin_pitch_mm=a.pin_pitch_mm,
                             backup=a.backup, merge_into=a.merge_into, tag=_norm_tag(a.tag),
                             try_bodies=a.try_bodies, from_labels=a.from_labels, connectors=a.connectors)
    print(f"{img.name} → {wb.status()}")
    for k, v in files.items():
        print(f"  {k:7} {v}")
    named = wb.note.get("named", 0)
    if named:
        print(f"  이름 읽음: {named}/{len(wb.spec.pins)}개 — " + ", ".join(p.name for p in wb.spec.pins[:8])
              + (" …" if len(wb.spec.pins) > 8 else ""))
    else:
        print("  ⚠ 접속점 이름은 자동 번호입니다. 원본 이름이 필요하면 `imgvec device studio`로 고치세요.")
    return 1 if not wb.ok else 0


def cmd_pick(a: argparse.Namespace) -> int:
    from .picker import run_picker

    out = Path(a.output) if a.output else Path(a.image).with_suffix(".spec.json")
    existing = DeviceSpec.load(out) if out.exists() and a.edit else None
    run_picker(a.image, out, name=a.name or "", existing=existing)
    return 0


def _write_pins_csv(spec: DeviceSpec, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "side", "offset_mm", "x_mm", "y_mm", "x_px", "y_px", "kind", "label", "group"])
        for q in spec.pins:
            w.writerow([q.name, q.side or "", "", "", "", q.x_px, q.y_px,
                        q.kind if q.kind != "electrical" else "", "", q.group or ""])


def cmd_auto(a: argparse.Namespace) -> int:
    """그림만 보고 본체·접속점을 찾아 명세 초안을 쓴다 (4단계). 맞지 않으면 `device pick --edit`로 고친다."""
    img = Path(a.image)
    rgb = load_image(img).rgb
    spec, note = auto_spec(rgb, name=a.name or img.stem, image=img.name, preset=a.preset,
                           pin_prefix=a.pin_prefix, read_names=not a.no_names, try_bodies=a.try_bodies,
                           from_labels=a.from_labels, connectors=a.connectors)
    if a.mm_per_px:
        spec.mm_per_px = a.mm_per_px
    elif a.body_width_mm:
        spec.body_width_mm = a.body_width_mm
    elif a.pin_pitch_mm:
        spec.pin_pitch_mm = a.pin_pitch_mm
    out = Path(a.output) if a.output else img.with_suffix(".spec.json")
    spec.dump(out)
    csv_path = out.with_name(out.name.replace(".spec.json", "") + ".pins.csv")
    _write_pins_csv(spec, csv_path)
    b = spec.body_px
    print(f"{img.name}: {note['preset']} · 본체 {b[0]:.0f},{b[1]:.0f}–{b[2]:.0f},{b[3]:.0f} ({note['body_source']})"
          f" · 접속점 {note['pins']}개" + (f" 간격 {note['pitch_px']:.0f}px" if note["pitch_px"] else ""))
    if note["pin_kinds"]:
        print("  단서: " + ", ".join(f"{k} {v}개" for k, v in sorted(note["pin_kinds"].items())))
    if note.get("named"):
        print(f"  이름 읽음: {note['named']}/{note['pins']}개 — " + ", ".join(p.name for p in spec.pins[:8])
              + (" …" if len(spec.pins) > 8 else ""))
    elif note.get("ocr") == "엔진 없음":
        print("  이름은 번호만 붙었습니다. 글자를 읽으려면: pip install \"imgvec[ocr]\"")
    print(f"  명세 → {out}\n  접속점 CSV → {csv_path}")
    if not spec.pins:
        print("  ⚠ 접속점을 하나도 찾지 못했습니다. `imgvec device pick`으로 찍어 주세요.")
    if not (spec.mm_per_px or spec.body_width_mm or spec.pin_pitch_mm or spec.ref):
        print("  ⚠ 축척은 그림만으로 알 수 없습니다. --mm-per-px · --body-width-mm · --pin-pitch-mm 중 하나를 주거나 명세의 scale을 채우세요.")
    print("  ⚠ 자동 인식은 초안입니다. 이름과 빠진 자리는 `imgvec device pick <그림> --edit`로 고치세요.")
    return 0


def cmd_autotest(a: argparse.Namespace) -> int:
    """정답이 있는 명세 폴더로 자동 인식의 정확도를 잰다."""
    import numpy as np

    from .detect import detect_body, detect_device, detect_pins, name_pins
    from .device import label_map
    from .params import Params
    from .pipeline import convert_image

    folder = Path(a.folder)
    specs = sorted(folder.glob("*.spec.json"))
    if not specs:
        print(f"{folder}에 *.spec.json이 없습니다", file=sys.stderr)
        return 2
    tol = a.tol
    print(f"{'샘플':26} {'정답':>4} {'검출':>4} {'맞춤':>4} {'헛검출':>6} {'변틀림':>6} {'본체오차':>8} {'핀오차중앙':>10}")
    tot = {"truth": 0, "hit": 0, "extra": 0, "side": 0}
    body_errs = []
    for sp in specs:
        spec = DeviceSpec.load(sp)
        img = folder / (spec.image or "")
        if not spec.image or not img.exists():
            print(f"{sp.stem.replace('.spec', ''):26} 그림 없음 — 건너뜀")
            continue
        rgb = load_image(img).rgb
        H, W = rgb.shape[:2]
        vec = convert_image(rgb, Params(preset=spec.preset))
        labels, _ = label_map(rgb, spec.preset)
        if a.try_bodies:
            box, found = detect_device(vec.prims, labels, (W, H))
        else:
            box = detect_body(vec.prims, (W, H))
            found = None
        truth_box = spec.body_px
        be = max(abs(x - y) for x, y in zip(box, truth_box)) if (box and truth_box) else float("nan")
        if box and truth_box:
            body_errs.append(be)
        hits = name_pins(found if found is not None
                         else detect_pins(vec.prims, labels, box or (0, 0, float(W), float(H)), (W, H)))
        used, errs, side_bad = set(), [], 0
        for q in spec.pins:
            best, bi = None, -1
            for i, h in enumerate(hits):
                if i in used or q.x_px is None:
                    continue
                if a.by_side:
                    # 배치 명세가 실제로 쓰는 값: 어느 변인가 + 변을 따라 어디인가 (변까지 거리는 버려진다)
                    if h.side != q.side:
                        continue
                    d = abs((h.x_px - q.x_px) if q.side in ("top", "bottom") else (h.y_px - q.y_px))
                else:
                    d = float(np.hypot(h.x_px - q.x_px, h.y_px - q.y_px))
                if best is None or d < best:
                    best, bi = d, i
            if best is not None and best <= tol:
                used.add(bi)
                errs.append(best)
                if hits[bi].side != q.side:
                    side_bad += 1
        tot["truth"] += len(spec.pins)
        tot["hit"] += len(used)
        tot["extra"] += len(hits) - len(used)
        tot["side"] += side_bad
        print(f"{sp.stem.replace('.spec', ''):26} {len(spec.pins):>4} {len(hits):>4} {len(used):>4} "
              f"{len(hits) - len(used):>6} {side_bad:>6} {be:>8.1f} {(np.median(errs) if errs else float('nan')):>10.1f}")
    pct = 100 * tot["hit"] / tot["truth"] if tot["truth"] else 0
    how = "변+자리" if a.by_side else "2차원 거리"
    print(f"\n맞춘 접속점 {tot['hit']}/{tot['truth']} ({pct:.0f}%) · 헛검출 {tot['extra']} · 변 틀림 {tot['side']}"
          f" · 본체 최대 변 오차 중앙값 {(np.median(body_errs) if body_errs else float('nan')):.1f}px"
          f"  [{how}, 허용 {tol}px]")
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


_KIT_NOTICE = "새 프로젝트로 복원해 쓰세요 (확인: 2026-10-08, 장치 1개 백업이 모습 그대로 복원됨)."
_MERGE_NOTICE = "⚠ 합친 백업을 DNCAD가 받아들이는지는 아직 확인하지 못했습니다. 원본 프로젝트를 먼저 백업하고, 복원은 새 프로젝트로 하세요."


def _write_map_csv(out: Path, res, names: list[str]) -> Path:
    """태그 대응표: 어느 배치 명세가 어느 태그·쪽·자리로 갔는지."""
    map_path = out.with_suffix(".map.csv")
    with map_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["태그", "장치", "원본 배치 명세", "쪽", "x(단위)", "y(단위)", "폭", "높이", "접속점"])
        for d, name in zip(res.devices, names):
            w.writerow([d.tag, d.name, name, d.page + 1, d.x, d.y, d.w, d.h, d.pins])
    return map_path


def _report_kit(out: Path, res, map_path: Path, notice: str, *, page_label: str = "") -> None:
    n_page = max(d.page for d in res.devices) + 1
    print(f"장치 {len(res.devices)}개, 접속점 {sum(d.pins for d in res.devices)}개, {page_label}{n_page}쪽 → {out} ({len(res.data) / 1024:.0f} KB)")
    print(f"  태그 대응표 → {map_path}")
    for w_ in res.warnings:
        print("  경고: " + w_)
    _print_issues([i for i in res.issues if i.severity != "info"])
    print("  " + notice)


def cmd_kit(a: argparse.Namespace) -> int:
    paths = _layout_paths(a.inputs)
    layouts = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    out = Path(a.output or "out/dncad_testkit.dncadbak")
    res = write_backup(out, layouts, tag_prefix=_norm_tag(a.tag_prefix), project_name=a.project_name, labels=not a.no_labels,
                       describe=a.describe, seed=a.seed, rules=_rules(a))
    _report_kit(out, res, _write_map_csv(out, res, [p.name for p in paths]), _KIT_NOTICE)
    return 0


def cmd_plan(a: argparse.Namespace) -> int:
    """배치 명세 → 사용자의 생성기(api-build)가 먹는 명세. 편집기 API로 바로 그리는 길(방식 A)."""
    from .dncad_plan import write_plan

    paths = _layout_paths(a.inputs)
    layouts = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    tags = a.tags or [_norm_tag(p.name.split(".")[0]) for p in paths]
    if len(tags) != len(layouts):
        print(f"오류: 태그 {len(tags)}개와 장치 {len(layouts)}개의 수가 다릅니다", file=sys.stderr)
        return 2
    out = Path(a.output or "out/계획.json")
    plan = write_plan(out, zip(layouts, [_norm_tag(t) for t in tags]), name=a.name,
                      origin=(a.at[0], a.at[1]), gap=a.gap)
    boxes = [o for o in plan["ops"] if o["op"] == "box"]
    print(f"장치 {len(boxes)}개, 명령 {len(plan['ops'])}개 → {out}")
    for b in boxes:
        n = sum(1 for o in plan["ops"] if o["op"] == "place" and b["x1"] <= o["x"] <= b["x2"] and b["y1"] <= o["y"] <= b["y2"])
        print(f"  {b['tag']:<10} ({b['x1']},{b['y1']})–({b['x2']},{b['y2']})  접속점 {n}개")
    print("  다음: node documents/scripts/dncad/api-build.mjs --project <id> --page \"<쪽>\" --name <이름> " + str(out))
    return 0


def cmd_merge(a: argparse.Namespace) -> int:
    paths = _layout_paths(a.inputs)
    layouts = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    out = Path(a.output) if a.output else Path(a.backup).with_name(Path(a.backup).stem + "+imgvec.dncadbak")
    if out.resolve() == Path(a.backup).resolve():
        print("오류: 원본 백업을 덮어쓸 수 없습니다. -o로 다른 파일을 지정하세요.", file=sys.stderr)
        return 2
    before = read_backup(a.backup)
    n_dev, n_page = len(before.rows.get("devices", [])), len(before.rows.get("pages", []))
    print(f"{a.backup}: 장치 {n_dev}개, {n_page}쪽 (스키마 {before.manifest.get('schemaVersion')}) 에 더합니다")
    res = write_merged_backup(out, before, layouts, tag_prefix=_norm_tag(a.tag_prefix),
                              tags=[_norm_tag(t) for t in a.tags] if a.tags else None, page_name=a.page_name,
                              labels=not a.no_labels, describe=a.describe, seed=a.seed, rules=_rules(a),
                              page_w=a.page_w, page_h=a.page_h)
    _report_kit(out, res, _write_map_csv(out, res, [p.name for p in paths]), _MERGE_NOTICE, page_label="새 ")
    print(f"  합친 뒤: 장치 {res.counts['devices']}개, {res.counts['pages']}쪽")
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
    d = sub.add_parser("device", help="장치 모델 만들기·검증 (2단계) / DNCAD 백업 만들기·합치기 (3단계)")
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

    k = ds.add_parser("kit", help="배치 명세들 → 새 프로젝트로 복원할 DNCAD 백업(.dncadbak)")
    k.add_argument("inputs", nargs="+", help="*.layout.json 파일 또는 그것들이 든 폴더")
    k.add_argument("-o", "--output", help="출력 .dncadbak (기본 out/dncad_testkit.dncadbak). 태그 대응표 .map.csv도 함께 씀")
    k.add_argument("--tag-prefix", default="-ZZ", help="장치 태그 접두 (기본 -ZZ → -ZZ01, -ZZ02 …. -ZZ는 DNCAD가 블랙박스에 붙이는 기본 글자)")
    k.add_argument("--project-name", help="백업 속 프로젝트 이름")
    k.add_argument("--no-labels", action="store_true", help="블랙박스 위 장치 이름 글자를 넣지 않음")
    k.add_argument("--describe", action="store_true", help="블랙박스 컴포넌트의 description에 장치 이름을 씀")
    k.add_argument("--seed", type=int, help="ID 난수 시드 (같은 입력·시드면 같은 ID이나 시각은 매번 다름)")
    k.add_argument("--allow-dup-names", action="store_true")
    k.set_defaults(func=cmd_kit)

    pl = ds.add_parser("plan", help="배치 명세 → 생성기(api-build) 명세 — 편집기 API로 바로 그리기")
    pl.add_argument("inputs", nargs="+", help="*.layout.json 파일 또는 그것들이 든 폴더")
    pl.add_argument("-o", "--output", help="출력 명세 JSON (기본 out/계획.json)")
    pl.add_argument("--name", help="명세 이름 (기본: 파일 이름)")
    pl.add_argument("--tags", nargs="+", help="장치 태그 (기본: 배치 명세 파일 이름). 앞의 '-'는 없어도 됩니다")
    pl.add_argument("--at", nargs=2, type=int, default=[320, 256], metavar=("X", "Y"), help="첫 장치의 왼쪽 위 (기본 320 256)")
    pl.add_argument("--gap", type=int, default=128, help="장치 사이 간격 (기본 128)")
    pl.set_defaults(func=cmd_plan)

    mg = ds.add_parser("merge", help="이미 있는 DNCAD 백업에 장치를 더해 새 백업을 만든다 (원본은 그대로)")
    mg.add_argument("backup", help="바탕이 되는 .dncadbak (읽기만 함)")
    mg.add_argument("inputs", nargs="+", help="*.layout.json 파일 또는 그것들이 든 폴더")
    mg.add_argument("-o", "--output", help="출력 .dncadbak (기본 <바탕이름>+imgvec.dncadbak). 태그 대응표 .map.csv도 함께 씀")
    mg.add_argument("--tag-prefix", default="-ZZ", help="장치 태그 접두 (기본 -ZZ). 기존 장치와 겹치지 않는 번호를 골라 붙인다")
    mg.add_argument("--tags", nargs="+", help="태그를 직접 지정 (장치 수와 같아야 하고, 기존 태그와 겹치면 멈춘다). 앞의 '-'는 없어도 됩니다")
    mg.add_argument("--page-name", help="새로 붙이는 쪽 이름 (기본: 본보기 쪽 이름 + ' (imgvec)')")
    mg.add_argument("--page-w", type=int, default=PAGE_W, help=f"새 쪽의 작도 폭(단위, 기본 {PAGE_W} = A4 가로 297 mm)")
    mg.add_argument("--page-h", type=int, default=PAGE_H, help=f"새 쪽의 작도 높이(단위, 기본 {PAGE_H} = 210 mm)")
    mg.add_argument("--no-labels", action="store_true", help="블랙박스 위 장치 이름 글자를 넣지 않음")
    mg.add_argument("--describe", action="store_true", help="블랙박스 컴포넌트의 description에 장치 이름을 씀")
    mg.add_argument("--seed", type=int, help="ID 난수 시드")
    mg.add_argument("--allow-dup-names", action="store_true")
    mg.set_defaults(func=cmd_merge)

    rb = ds.add_parser("read-bak", help="DNCAD 백업의 블랙박스 장치를 읽어 요약 (+ 배치 명세로 내보내기)")
    rb.add_argument("backup")
    rb.add_argument("-o", "--output", help="배치 명세(<태그>.layout.json)를 쓸 폴더")
    rb.set_defaults(func=cmd_read_bak)

    cb = ds.add_parser("check-bak", help="DNCAD 백업 점검 (ZIP·해시·행 수·참조·블랙박스 규칙)")
    cb.add_argument("backup")
    cb.add_argument("--allow-dup-names", action="store_true")
    cb.set_defaults(func=cmd_check_bak)

    au = ds.add_parser("auto", help="그림만 보고 본체·접속점을 찾아 명세 초안 만들기 (4단계)")
    au.add_argument("image")
    au.add_argument("-o", "--output", help="저장할 명세 JSON (기본: <그림>.spec.json). 접속점 CSV도 함께 저장")
    au.add_argument("--name", help="장치 이름 (기본: 그림 파일 이름)")
    au.add_argument("--preset", default="auto", choices=["auto", "lineart", "illustration", "photo"])
    au.add_argument("--pin-prefix", default="", help="접속점 이름 앞에 붙일 글자 (글자를 못 읽었을 때 쓰는 번호에 붙는다)")
    au.add_argument("--no-names", action="store_true", help="접속점 옆 글자를 읽지 않고 번호만 붙인다")
    au.add_argument("--try-bodies", action="store_true",
                    help="본체 후보를 여럿 놓고 접속점을 가장 많이 찾는 것을 고른다 — 그림에 액자가 있을 때(PDF 캡처)")
    au.add_argument("--from-labels", action="store_true",
                    help="접속점을 자리가 아니라 **이름(번호)**에서 만든다 — 제품 실물 사진용. 도면 심볼에는 쓰지 마세요")
    au.add_argument("--connectors", action="store_true",
                    help="'USB connector'처럼 커넥터라고 적힌 글자 아래·위의 접속점을 커넥터 핀으로 표시한다"
                         " (그림 전체를 한 번 더 읽어 장당 3.4~5.5초 더 걸린다. --from-labels면 늘 함께 가른다)")
    au.add_argument("--mm-per-px", type=float, help="축척: 픽셀 1개의 길이(mm)")
    au.add_argument("--body-width-mm", type=float, help="축척: 본체 폭의 실제 길이(mm)")
    au.add_argument("--pin-pitch-mm", type=float, help="축척: 같은 줄 접속점 간격(mm)")
    au.set_defaults(func=cmd_auto)

    at = ds.add_parser("autotest", help="정답이 있는 명세 폴더로 자동 인식 정확도 재기")
    at.add_argument("folder", help="*.spec.json과 그림이 함께 있는 폴더 (예: samples/set)")
    at.add_argument("--tol", type=float, default=15.0, help="맞춤으로 인정할 거리(px, 기본 15)")
    at.add_argument("--try-bodies", action="store_true", help="본체 후보를 여럿 놓고 고른다 — 그림에 액자가 있을 때")
    at.add_argument("--xy", dest="by_side", action="store_false",
                    help="변·자리가 아니라 2차원 거리로 잰다 (기본은 배치 명세가 쓰는 변+자리 기준)")
    at.set_defaults(func=cmd_autotest)

    st = ds.add_parser("studio", help="작업대 창: 그림 → 자동 인식 → 손질 → 장치 → DNCAD 백업 (5단계, Tkinter)")
    st.add_argument("source", help="그림 파일 또는 *.spec.json")
    st.add_argument("--name", help="장치 이름")
    st.add_argument("--out-dir", help="저장할 폴더 (기본: 원본 옆)")
    st.add_argument("--preset", default="auto", choices=["auto", "lineart", "illustration", "photo"])
    st.add_argument("--no-auto", action="store_true", help="열 때 자동 인식을 돌리지 않음")
    st.set_defaults(func=cmd_studio)

    rn = ds.add_parser("run", help="화면 없이 한 번에: 그림 → 장치 → (원하면) DNCAD 백업")
    rn.add_argument("image")
    rn.add_argument("-o", "--output", help="출력 이름 앞머리 (기본: out/<그림 이름>)")
    rn.add_argument("--name", help="장치 이름")
    rn.add_argument("--preset", default="auto", choices=["auto", "lineart", "illustration", "photo"])
    rn.add_argument("--mm-per-px", type=float)
    rn.add_argument("--body-width-mm", type=float)
    rn.add_argument("--pin-pitch-mm", type=float)
    rn.add_argument("--backup", help="만들 .dncadbak (새 프로젝트로 복원)")
    rn.add_argument("--merge-into", help="이 .dncadbak에 더한다 (원본은 읽기만)")
    rn.add_argument("--no-names", action="store_true", help="접속점 옆 글자를 읽지 않고 번호만 붙인다")
    rn.add_argument("--try-bodies", action="store_true", help="본체 후보를 여럿 놓고 고른다 — 그림에 액자가 있을 때")
    rn.add_argument("--from-labels", action="store_true", help="접속점을 이름(번호)에서 만든다 — 제품 실물 사진용")
    rn.add_argument("--connectors", action="store_true", help="'USB connector'처럼 커넥터라고 적힌 글자 아래·위의 접속점을 커넥터 핀으로 표시한다 (그림 전체를 한 번 더 읽어 장당 3.4~5.5초 더 걸린다. --from-labels면 늘 함께 가른다)")
    rn.add_argument("--tag", help="장치 태그 (기본 -ZZ01). 앞의 '-'는 없어도 붙여 줍니다 — `--tag T1` = `--tag=-T1`")
    rn.set_defaults(func=cmd_run)

    pk = ds.add_parser("pick", help="화면에서 클릭해 접속점·본체·축척 지정 (Tkinter)")
    pk.add_argument("image")
    pk.add_argument("-o", "--output", help="저장할 명세 JSON (기본: <그림>.spec.json). 접속점 CSV도 함께 저장")
    pk.add_argument("--name")
    pk.add_argument("--edit", action="store_true", help="이미 있는 명세를 불러와 이어서 편집")
    pk.set_defaults(func=cmd_pick)


EXPECTED_ERRORS = (DeviceBuildError, CalibrationError, PinsFileError, ModelError, SvgModelError, KitError, StudioError)
