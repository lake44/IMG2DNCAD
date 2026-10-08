"""명령줄 도구: imgvec convert / batch / info / samples"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from pathlib import Path

from . import __version__
from .export import write_primitives_json, write_report_json
from .imageio import IMAGE_SUFFIXES, PDF_SUFFIXES, load_image, save_png
from .labels import classify_kind
from .params import Params
from .pipeline import VectorResult, convert_image
from .svg import build_svg, write_svg


def _add_convert_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("축척 (하나만 지정; 없으면 px 단위로 출력)")
    g.add_argument("--mm-per-px", type=float, help="원본 픽셀 1개의 실제 길이(mm)")
    g.add_argument("--ref", nargs=5, type=float, metavar=("X1", "Y1", "X2", "Y2", "MM"),
                   help="원본 이미지의 두 점(px)과 그 사이 실제 거리(mm)로 축척 계산")
    g.add_argument("--dpi", type=float, help="이미지 해상도(DPI)로 축척 계산")
    g.add_argument("--use-file-dpi", action="store_true", help="파일에 기록된 DPI를 축척으로 사용 (기록값이 부정확할 수 있음)")
    q = p.add_argument_group("변환 설정")
    q.add_argument("--preset", choices=["auto", "lineart", "illustration", "photo"], default="auto",
                   help="그림 종류. auto=자동 판별 (기본)")
    q.add_argument("--tolerance-mm", type=float, help="피팅 허용 오차(mm). 기본 0.2")
    q.add_argument("--min-length-mm", type=float, help="이보다 짧은 선은 버림(mm). 기본 1.0")
    q.add_argument("--merge-px", type=float, help="획의 양쪽 경계를 한 줄로 합치는 반경(작업 px). 기본 자동")
    q.add_argument("--colors", type=int, help="일러스트에서 구분할 최대 색 수. 기본 10")
    q.add_argument("--ortho-snap-deg", type=float, help="수평·수직에 이 각도 이내면 맞춤 (0이면 끔)")
    q.add_argument("--no-arcs", action="store_true", help="호 대신 직선만 사용")
    q.add_argument("--invert", choices=["auto", "yes", "no"], help="선화에서 밝은 선/어두운 배경 처리")
    q.add_argument("--binarize", choices=["otsu", "sauvola"], help="선화 이진화 방식 (조명이 고르지 않으면 sauvola)")
    q.add_argument("--page", type=int, default=0, help="PDF/다중 프레임 이미지의 쪽 번호 (0부터)")
    q.add_argument("--pdf-dpi", type=float, default=300.0, help="PDF를 그릴 해상도. 기본 300")
    o = p.add_argument_group("출력")
    o.add_argument("--stroke-mm", type=float, default=0.25, help="SVG 선 굵기(mm)")
    o.add_argument("--json", action="store_true", help="도형 목록 JSON(<이름>.prims.json)도 저장")
    o.add_argument("--no-report", action="store_true", help="평가 리포트(<이름>.report.json)를 저장하지 않음")
    o.add_argument("--no-overlay", action="store_true", help="겹쳐보기 이미지(<이름>.overlay.png)를 저장하지 않음")
    o.add_argument("--force", action="store_true", help="imgvec이 만들지 않은 기존 SVG도 덮어씀")


def _params_from_args(a: argparse.Namespace, file_dpi: float | None, size: tuple[int, int]) -> Params:
    mm = a.mm_per_px
    if mm is None and a.ref:
        x1, y1, x2, y2, length = a.ref
        d = math.hypot(x2 - x1, y2 - y1)
        if d <= 0 or length <= 0:
            raise SystemExit("--ref: 두 점이 서로 달라야 하고 거리는 0보다 커야 합니다")
        mm = length / d
    if mm is None and a.dpi:
        mm = 25.4 / a.dpi
    if mm is None and a.use_file_dpi and file_dpi:
        mm = 25.4 / file_dpi
    ov = {
        "tolerance_mm": a.tolerance_mm,
        "min_length_mm": a.min_length_mm,
        "merge_px": a.merge_px,
        "colors": a.colors,
        "ortho_snap_deg": a.ortho_snap_deg,
        "allow_arcs": False if a.no_arcs else None,
        "invert": a.invert,
        "binarize": a.binarize,
    }
    return Params(preset=a.preset, mm_per_px=mm, overrides=ov)


def _summary_line(name: str, res: VectorResult, dt: float) -> str:
    m = res.report["metrics"]
    unit = m["unit"]
    return (
        f"{name}: {res.kind}, 도형 {len(res.prims)}개 (경로 {m['counts']['paths']}, 원 {m['counts']['circles']}), "
        f"피팅 편차 p99 {m['fit']['p99']}{unit}·최대 {m['fit']['max']}{unit}, 경계 재현율 {m['edge_coverage']:.0%}, {dt:.1f}초"
    )


def _read_scales(path: str | None) -> dict[str, float]:
    """파일명,mm_per_px 형식의 CSV (머리글 줄은 있어도 없어도 됨)."""
    out: dict[str, float] = {}
    if not path:
        return out
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            try:
                out[row[0].strip()] = float(row[1])
            except ValueError:
                continue  # 머리글 줄
    return out


def _ok_to_overwrite(path: Path) -> bool:
    """없거나 imgvec이 만든 파일이면 덮어써도 된다. 사용자가 만든 SVG는 지키려고 막는다."""
    if not path.exists():
        return True
    try:
        head = path.read_bytes()[:600].decode("utf-8", errors="ignore")
    except OSError:
        return False
    return "imgvec" in head


def _convert_one(src: Path, out_svg: Path, a: argparse.Namespace, scales: dict[str, float] | None = None) -> VectorResult:
    if not a.force and not _ok_to_overwrite(out_svg):
        raise ValueError(f"{out_svg} 파일이 이미 있고 imgvec이 만든 파일이 아닙니다. 다른 경로를 지정하거나 --force로 덮어쓰세요")
    img = load_image(src, page=a.page, pdf_dpi=a.pdf_dpi)
    H, W = img.rgb.shape[:2]
    params = _params_from_args(a, img.dpi, (W, H))
    if scales and src.name in scales:
        params.mm_per_px = scales[src.name]
    if src.suffix.lower() in PDF_SUFFIXES and params.mm_per_px is None:
        params.mm_per_px = 25.4 / a.pdf_dpi  # PDF는 쪽 크기가 곧 실제 크기
    t0 = time.perf_counter()
    res = convert_image(img.rgb, params)
    dt = time.perf_counter() - t0
    desc = f"imgvec {__version__}: {src.name}, preset={res.kind}"
    write_svg(out_svg, build_svg(res.prims, res.size[0], res.size[1], res.mm_per_px, a.stroke_mm, desc))
    stem = out_svg.with_suffix("")
    if not a.no_report:
        write_report_json(stem.with_name(stem.name + ".report.json"), res, str(src))
    if not a.no_overlay:
        save_png(stem.with_name(stem.name + ".overlay.png"), res.overlay())
    if a.json:
        write_primitives_json(stem.with_name(stem.name + ".prims.json"), res, str(src))
    res.report["timing_s"]["total"] = round(dt, 3)
    print(_summary_line(src.name, res, dt))
    if res.mm_per_px is None:
        print("  ※ 축척을 지정하지 않아 원본 px 단위로 출력했습니다 (--mm-per-px, --ref, --dpi 중 하나를 지정하세요)")
    return res


def cmd_convert(a: argparse.Namespace) -> int:
    src = Path(a.input)
    out = Path(a.output) if a.output else Path("out") / (src.stem + ".svg")
    _convert_one(src, out, a)
    print(f"  → {out}")
    return 0


def cmd_batch(a: argparse.Namespace) -> int:
    src_dir = Path(a.input)
    if not src_dir.is_dir():
        print(f"폴더가 아닙니다: {src_dir}", file=sys.stderr)
        return 2
    out_dir = Path(a.output or (src_dir / "imgvec_out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    suffixes = IMAGE_SUFFIXES | PDF_SUFFIXES
    files = sorted(p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in suffixes)
    if not files:
        print("변환할 이미지가 없습니다.", file=sys.stderr)
        return 1
    rows = []
    failed = 0
    scales = _read_scales(a.scales)
    for f in files:
        t0 = time.perf_counter()
        try:
            res = _convert_one(f, out_dir / (f.stem + ".svg"), a, scales)
            m = res.report["metrics"]
            rows.append([f.name, res.kind, len(res.prims), m["fit"]["max"], m["unit"], m["edge_coverage"], m["edge_precision"],
                         round(time.perf_counter() - t0, 2), ""])
        except Exception as e:  # noqa: BLE001 - 파일 하나의 실패가 전체를 막지 않도록
            failed += 1
            print(f"{f.name}: 실패 - {e}", file=sys.stderr)
            rows.append([f.name, "", 0, "", "", "", "", round(time.perf_counter() - t0, 2), str(e)])
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["파일", "종류", "도형수", "피팅최대편차", "단위", "경계재현율", "경계정밀도", "초", "오류"])
        w.writerows(rows)
    print(f"완료: {len(files) - failed}/{len(files)}개 → {out_dir}")
    return 1 if failed else 0


def cmd_info(a: argparse.Namespace) -> int:
    img = load_image(a.input, page=a.page, pdf_dpi=a.pdf_dpi)
    H, W = img.rgb.shape[:2]
    print(f"파일: {a.input}")
    print(f"크기: {W} x {H} px")
    print(f"기록된 DPI: {img.dpi if img.dpi else '없음'}" + (f"  (→ {25.4 / img.dpi:.4f} mm/px, 부정확할 수 있음)" if img.dpi else ""))
    print(f"자동 판별 종류: {classify_kind(img.rgb)}")
    return 0


def cmd_selftest(a: argparse.Namespace) -> int:
    """합성 샘플(정답 기하를 아는 그림)로 변환 정확도를 재고 기준 이내인지 판정한다."""
    from .synth import SAMPLE_SPECS, evaluate_vs_gt, make_device

    limit = a.limit_mm
    print(f"{'샘플':<16}{'크기(px)':>12}{'재현율 최대':>12}{'p99':>8}{'정밀도 최대':>12}{'p99':>8}  판정 (최대 편차 ≤ {limit} mm)")
    all_ok = True
    for spec in SAMPLE_SPECS:
        s = make_device(**spec)
        res = convert_image(s.rgb, Params(mm_per_px=s.mm_per_px))
        ev = evaluate_vs_gt(res.prims, s.gt, s.mm_per_px, s.text_boxes)
        r, p = ev["recall"], ev["precision"]
        ok = r["max"] <= limit and p["max"] <= limit
        all_ok &= ok
        size = f"{s.rgb.shape[1]}x{s.rgb.shape[0]}"
        print(f"{s.name:<16}{size:>12}{r['max']:>12.3f}{r['p99']:>8.3f}{p['max']:>12.3f}{p['p99']:>8.3f}  {'통과' if ok else '미달'}")
    print("모두 통과" if all_ok else "기준 미달 샘플이 있습니다")
    return 0 if all_ok else 1


def cmd_samples(a: argparse.Namespace) -> int:
    from .synth import write_samples

    for s in write_samples(a.output):
        print(f"생성: {s.name} ({s.rgb.shape[1]}x{s.rgb.shape[0]} px, {s.mm_per_px} mm/px)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="imgvec", description="그림 파일을 CAD용 벡터(선·호·원)로 변환합니다.")
    p.add_argument("--version", action="version", version=f"imgvec {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="이미지 1장을 SVG로 변환")
    c.add_argument("input", help="입력 이미지(PNG·JPG·BMP·TIFF·PDF 등)")
    c.add_argument("-o", "--output", help="출력 SVG 경로 (기본: out/<입력 이름>.svg)")
    _add_convert_options(c)
    c.set_defaults(func=cmd_convert)

    b = sub.add_parser("batch", help="폴더의 이미지를 모두 변환")
    b.add_argument("input", help="이미지가 든 폴더")
    b.add_argument("-o", "--output", help="출력 폴더 (기본: <입력>/imgvec_out)")
    b.add_argument("--scales", help="파일별 축척 CSV (열: 파일명,mm_per_px). 지정한 파일은 다른 축척 옵션보다 우선")
    _add_convert_options(b)
    b.set_defaults(func=cmd_batch)

    i = sub.add_parser("info", help="이미지 크기·DPI·종류 확인")
    i.add_argument("input")
    i.add_argument("--page", type=int, default=0)
    i.add_argument("--pdf-dpi", type=float, default=300.0)
    i.set_defaults(func=cmd_info)

    s = sub.add_parser("samples", help="정답이 있는 합성 샘플 이미지 생성")
    s.add_argument("output", nargs="?", default="samples/synthetic", help="출력 폴더")
    s.set_defaults(func=cmd_samples)

    t = sub.add_parser("selftest", help="합성 샘플로 변환 정확도(정답 대비 최대 편차) 점검")
    t.add_argument("--limit-mm", type=float, default=0.5, help="통과 기준(mm). 기본 0.5 (계획서 9장)")
    t.set_defaults(func=cmd_selftest)

    from .cli_device import add_device_parser

    add_device_parser(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as e:  # DeviceBuildError·PinsFileError·ModelError 등은 ValueError 계열
        print(f"오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
