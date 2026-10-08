"""샘플 세트(20종) 생성 — 이미지 + 접속점 정답(명세 JSON·CSV) + manifest.csv.

사용:  python tools/make_sample_set.py [출력폴더=samples/set]
필요:  Microsoft Edge 또는 Chrome (합성 일러스트의 SVG를 PNG로 그릴 때만). 없으면 일러스트는 건너뛴다.
이미 있는 실제 사진(real_pilz_pnoz_x3p.jpg)과 릴레이 그림(real_relay_icon.png)은 건드리지 않는다.
"""
from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from illustrations import UNIT_MM, build, device_specs  # noqa: E402

from imgvec.device import DeviceSpec  # noqa: E402
from imgvec.pins import PinSpec, write_pins_csv  # noqa: E402
from imgvec.synth import degrade, make_block, make_device  # noqa: E402

SCALE = 4  # SVG 한 단위 = PNG 4 px
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

# 실제 사진 정답: 화면에서 겹쳐 확인한 단자 나사 중심 (행: y, 첫 나사 x, 간격 px)
PILZ_ROWS = {
    "P3": dict(side="top", y=86.0, x0=381.1, pitch=62.3, names=["A1", None, "S31", "S32", "13", "23", "33", "41"]),
    "P4": dict(side="top", y=213.0, x0=375.0, pitch=64.5, names=["S11", "S12", "S13", "S14", "S21", "S22", "S33", "S34"]),
    "P4b": dict(side="bottom", y=1130.0, x0=385.0, pitch=62.3, names=["A2", "Y30", "Y31", "Y32", "14", "24", "34", "42"]),
}
RELAY_PINS = [  # 사용자 SVG(hosoo_diagnostic_relay_icon.svg)의 단자 나사 중심 x4
    ("L1", "top", 180, 200, "T"), ("L2", "top", 400, 200, "T"), ("L3", "top", 620, 200, "T"),
    ("12", "bottom", 180, 1616, "B1"), ("11", "bottom", 400, 1616, "B1"), ("14", "bottom", 620, 1616, "B1"),
    ("22", "bottom", 180, 1744, "B2"), ("21", "bottom", 400, 1744, "B2"), ("24", "bottom", 620, 1744, "B2"),
]


def find_edge() -> str | None:
    return next((p for p in EDGE_CANDIDATES if Path(p).exists()), None)


def render_svg(svg_text: str, w: int, h: int, out_png: Path) -> bool:
    edge = find_edge()
    if edge is None:
        return False
    with tempfile.TemporaryDirectory() as td:
        svg = Path(td) / "x.svg"
        svg.write_text(svg_text, encoding="utf-8")
        cmd = [edge, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", f"--user-data-dir={td}/prof",
               f"--force-device-scale-factor={SCALE}", f"--window-size={w},{h}", f"--screenshot={out_png}", svg.as_uri()]
        subprocess.run(cmd, check=False, capture_output=True, timeout=120)
        # Edge는 프로세스가 끝난 뒤에도 잠깐 더 걸려 파일을 쓴다
        for _ in range(60):
            if out_png.exists() and out_png.stat().st_size > 1000:
                time.sleep(0.5)
                return True
            time.sleep(0.5)
    return out_png.exists()


class Sample:
    def __init__(self, sid, category, scale_basis, notes=""):
        self.id, self.category, self.scale_basis, self.notes = sid, category, scale_basis, notes
        self.file = ""
        self.spec: DeviceSpec | None = None
        self.w = self.h = 0

    def finish(self, out: Path, rgb: np.ndarray | None, ext: str, spec: DeviceSpec) -> None:
        self.file = f"{self.id}.{ext}"
        if rgb is not None:
            im = Image.fromarray(rgb)
            if ext == "jpg":
                im.save(out / self.file, quality=92)
            else:
                im.save(out / self.file)
        else:
            im = Image.open(out / self.file)
        self.w, self.h = im.size
        spec.image = self.file
        spec.name = spec.name or self.id
        self.spec = spec
        spec.dump(out / f"{self.id}.spec.json")
        write_pins_csv(out / f"{self.id}.pins.csv", spec.pins)


def pins_from(items) -> list[PinSpec]:
    return [PinSpec(name=n, side=s, x_px=round(x, 2), y_px=round(y, 2), group=g) for n, s, x, y, g in items]


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "set"
    out.mkdir(parents=True, exist_ok=True)
    samples: list[Sample] = []

    # ---------------------------------------------------------------- 1. 실제 제품 사진 (사용자 제공, 제3자 저작물이라 저장소에는 없다 → 있을 때만)
    if (out / "real_pilz_pnoz_x3p.jpg").exists():
        s = Sample("real_pilz_pnoz_x3p", "제품 사진", "본체 폭 45 mm(제조사 자료 값, 미확인)", "Pilz PNOZ X3P 안전릴레이 카탈로그 사진. 단자 23개(상단 2열 15개 + 하단 8개)")
        items = []
        for g, r in PILZ_ROWS.items():
            for i, nm in enumerate(r["names"]):
                if nm:
                    items.append((nm, r["side"], r["x0"] + r["pitch"] * i, r["y"], g))
        spec = DeviceSpec(name="PNOZ X3P", body_width_mm=45.0, body_px=(308, 8, 890, 1192), preset="illustration", pins=pins_from(items))
        s.finish(out, None, "jpg", spec)
        samples.append(s)
    else:
        print(f"건너뜀: {out / 'real_pilz_pnoz_x3p.jpg'} 없음 (제조사 사진이라 저장소에 넣지 않음. 같은 이름으로 넣으면 이 샘플과 열화본이 만들어진다)")

    # ---------------------------------------------------------------- 2. 사용자 SVG를 그린 릴레이 그림
    s = Sample("real_relay_icon", "제품 일러스트", "본체 폭 22.5 mm 가정", "hosoo_diagnostic_relay_icon.svg(사용자 제공)을 4배로 그린 그림. 단자 9개")
    spec = DeviceSpec(name="DIAG RELAY", body_width_mm=22.5, body_px=(24, 16, 776, 1840), preset="illustration", pins=pins_from([(n, sd, x, y, g) for n, sd, x, y, g in RELAY_PINS]))
    s.finish(out, None, "png", spec)
    samples.append(s)

    # ---------------------------------------------------------------- 3. 합성 일러스트 6종 (SVG → Edge 렌더)
    ills = {}
    for isp in device_specs():
        svg, gt = build(isp)
        png = out / f"{isp.id}.png"
        w, h = (int(round(v)) for v in gt["size"])
        ok = png.exists() or render_svg(svg, w, h, png)
        if not ok:
            print(f"건너뜀(Edge 없음): {isp.id}")
            continue
        x0, y0, x1, y1 = (v * SCALE for v in gt["body"])
        pin_items = [(p["name"], p["side"], p["x"] * SCALE, p["y"] * SCALE, p["group"]) for p in gt["pins"]]
        s = Sample(isp.id, "제품 일러스트", "단자 간격 5.08 mm 기준(0.0635 mm/px)", "합성: 하우징·단자 플러그·인쇄 핀 이름")
        spec = DeviceSpec(name=isp.id.upper().replace("ILL_", ""), mm_per_px=UNIT_MM / SCALE, body_px=(x0, y0, x1, y1), preset="illustration", pins=pins_from(pin_items))
        s.finish(out, None, "png", spec)
        samples.append(s)
        ills[isp.id] = (s, spec)

    # ---------------------------------------------------------------- 4. 선화 심볼 4종
    def line_sample(sid, notes, **kw):
        sy = make_device(**kw)
        s = Sample(sid, "선화 심볼", f"{sy.mm_per_px} mm/px 지정", notes)
        spec = DeviceSpec(name=sid, mm_per_px=sy.mm_per_px, body_px=sy.body_px, preset="lineart",
                          pins=pins_from([(p["name"], p["side"], p["x_px"], p["y_px"], p["group"]) for p in sy.pins]))
        s.finish(out, sy.rgb, "png", spec)
        samples.append(s)
        return sy

    base_lr = line_sample("line_dev_lr", "좌우 변에 단자 원(DNCAD가 확인하지 못한 배치) — --remap-lr 시험용", seed=11, n_pins=4)
    base_tb = line_sample("line_dev_tb", "위·아래 변에 단자 원, 간격 7.5 mm", seed=12, n_pins=4, pin_sides="tb")
    line_sample("line_dev_tb_pitch5", "위·아래 변, 간격 5 mm(DNCAD 슬롯과 일치), 핀 8쌍", seed=13, n_pins=8, pin_sides="tb", pin_pitch_mm=5.0, mm_per_px=0.1)
    line_sample("line_dev_lr_thin", "좌우 변, 가는 선(0.3 mm), 핀 6쌍", seed=14, n_pins=6, stroke_mm=0.3)

    # ---------------------------------------------------------------- 5. 블록도 2종
    def block_sample(sid, notes, **kw):
        sy = make_block(**kw)
        s = Sample(sid, "블록도", f"{sy.mm_per_px} mm/px 지정", notes)
        spec = DeviceSpec(name=sid, mm_per_px=sy.mm_per_px, body_px=sy.body_px, preset="lineart",
                          pins=pins_from([(p["name"], p["side"], p["x_px"], p["y_px"], p["group"]) for p in sy.pins]))
        s.finish(out, sy.rgb, "png", spec)
        samples.append(s)
        return sy

    blk_plc = block_sample("block_plc", "PLC 입출력 블록: 위 8·아래 8, 이름은 상자 안쪽", seed=21, top=tuple(f"I{i}" for i in range(8)), bottom=tuple(f"Q{i}" for i in range(8)), title="PLC DI/DO", pitch_mm=8.0, mm_per_px=0.08)
    block_drive = block_sample("block_inverter", "인버터 블록: 위 R S T, 아래 U V W PE", seed=22, top=("R", "S", "T"), bottom=("U", "V", "W", "PE"), title="INVERTER", pitch_mm=12.0)

    # ---------------------------------------------------------------- 6. 열화본 6종 (스캔·사진 모사). 정답은 그대로(축소만 배율 반영)
    def degraded(src_sample: Sample, sid: str, notes: str, **kw):
        src = Image.open(out / src_sample.file).convert("RGB")
        arr = degrade(np.asarray(src), **kw)
        k = kw.get("scale", 1.0)
        sp = src_sample.spec
        assert sp is not None
        pins = [PinSpec(p.name, p.side, x_px=round(p.x_px * k, 2), y_px=round(p.y_px * k, 2), group=p.group) for p in sp.pins]
        bp = tuple(round(v * k, 2) for v in sp.body_px) if sp.body_px else None
        mm = sp.mm_per_px / k if sp.mm_per_px else None
        spec = DeviceSpec(name=sid, mm_per_px=mm, body_width_mm=sp.body_width_mm, body_px=bp, preset=sp.preset, pins=pins)  # type: ignore[arg-type]
        s = Sample(sid, "열화본", f"{src_sample.id}와 같은 기준", notes)
        s.finish(out, arr, "jpg" if kw.get("jpeg_quality") else "png", spec)
        samples.append(s)

    by_id = {x.id: x for x in samples}
    degraded(by_id["real_relay_icon"], "deg_relay_icon_jpeg", "릴레이 그림 + 조명 불균일 + 잡음 + JPEG q50", shading=0.25, noise=6.0, jpeg_quality=50)
    if "real_pilz_pnoz_x3p" in by_id:
        degraded(by_id["real_pilz_pnoz_x3p"], "deg_pilz_lowres", "Pilz 사진을 0.5배로 줄이고 블러 + JPEG q45 (저해상도 카탈로그)", scale=0.5, blur=0.8, jpeg_quality=45)
    if "ill_safety_relay" in by_id:
        degraded(by_id["ill_safety_relay"], "deg_ill_safety_blur", "합성 안전릴레이 + 블러 + 잡음 + JPEG q92", blur=1.4, noise=8.0, jpeg_quality=92)
    if "ill_contactor" in by_id:
        degraded(by_id["ill_contactor"], "deg_ill_contactor_shading", "합성 접촉기 + 조명 불균일 + 잡음 + JPEG q60", shading=0.35, noise=10.0, jpeg_quality=60)
    degraded(by_id["line_dev_tb"], "deg_line_scan", "선화 + 스캔 느낌(블러 1.5, 잡음 12, 조명 0.35)", blur=1.5, noise=12.0, shading=0.35)
    degraded(by_id["block_plc"], "deg_block_jpeg", "블록도 + JPEG q45", blur=0.7, jpeg_quality=45)

    # ---------------------------------------------------------------- manifest
    with (out / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "분류", "파일", "너비px", "높이px", "축척(mm/px)", "축척 근거", "접속점수", "위", "아래", "왼쪽", "오른쪽", "프리셋", "비고"])
        for s in samples:
            sp = s.spec
            assert sp is not None
            cnt = {k: sum(1 for p in sp.pins if p.side == k) for k in ("top", "bottom", "left", "right")}
            mm = f"{sp.mm_per_px:.5f}" if sp.mm_per_px else ""
            w.writerow([s.id, s.category, s.file, s.w, s.h, mm, s.scale_basis, len(sp.pins), cnt["top"], cnt["bottom"], cnt["left"], cnt["right"], sp.preset, s.notes])
    print(f"샘플 {len(samples)}종 → {out}")
    _ = (base_lr, base_tb, blk_plc, block_drive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
