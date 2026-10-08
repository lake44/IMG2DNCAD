"""접속점 이름 읽기 (4단계) — 그림에 적힌 단자 이름을 읽어 접속점에 붙인다.

자동 인식은 접속점 **자리**를 찾지만 이름은 모른다(1, 2, 3… 번호만 붙었다). 여기서 그 자리 옆의
글자를 읽어 원본 이름(`A1`·`T11`·`PE`·`31`…)을 되살린다.

어느 쪽 글자가 이름인가
    단자 이름은 그림마다 접속점의 **위**에 있기도 하고 **아래**에 있기도 하다. 그래서 양쪽 띠를
    모두 읽고, 접속점과 더 많이 짝지어지는 쪽을 고른다. 수가 같으면 본체 변에 가까운 쪽을 쓴다.
    (실측: 변압기 -T1은 바깥쪽 `1·2·3·PE`가 이름이고 안쪽 `230V·400V`는 탭 전압이다.
     안전 컨트롤러 -SC1은 바깥에 글자가 없고 안쪽 `A1·T11…`이 이름이다.)

이름으로 **자리까지** 정하는 길도 있다 — `pins_from_labels`. 제품 실물 사진처럼 단자 자리는
흐려도 번호는 선명할 때 쓴다.

엔진은 선택 사항이다 — 없으면 이름을 읽지 않고 번호를 그대로 둔다.
    pip install "imgvec[ocr]"      (rapidocr-onnxruntime)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from .detect import PinHit, _side_axes

# 단자 이름에 흔한 글자만 남긴다 (읽기 오류로 붙은 기호·공백을 턴다)
_KEEP = re.compile(r"[^0-9A-Za-z+\-/.']")
BAND_INNER = 0.05      # 접속점에서 이만큼(격자 대비) 떨어진 곳부터 글자를 찾는다
BAND_OUTER = 0.9       # 이만큼까지. 더 넓히면 "Safety Input 1" 같은 설명 글자가 섞인다 (실측)
BAND_MIN_RATIO = 0.08  # 띠 두께의 밑바닥 (본체 짧은 변 대비). 격자가 잘면 띠가 얇아져 글자가 잘린다
MIN_SCORE = 0.5
UPSCALE = 3            # 작은 글자를 키워서 읽는다
MIN_SLICE_PX = 28      # 접속점 하나를 다시 읽을 때 자를 최소 폭
WIDE_TEXT = 0.9        # 글자 폭이 이웃 접속점까지 거리의 이만큼을 넘으면 두 이름이 붙어 읽힌 것으로 본다


@dataclass
class TextHit:
    u: float             # 변을 따라 잰 글자 중심
    text: str
    score: float
    width: float = 0.0   # 변을 따라 잰 글자 폭 — 접속점 간격보다 넓으면 두 이름이 붙어 읽힌 것이다


class OcrUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


@lru_cache(maxsize=1)
def _engine() -> Any:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:  # pragma: no cover - 엔진이 없는 환경
        raise OcrUnavailable("글자 읽기 엔진이 없습니다. pip install \"imgvec[ocr]\"") from e
    return RapidOCR()


def clean(text: str) -> str:
    """읽은 글자에서 단자 이름이 될 부분만 남긴다."""
    return _KEEP.sub("", str(text)).strip()


def read_band(rgb: np.ndarray, box: tuple[int, int, int, int], *, horizontal: bool = True,
              min_score: float = MIN_SCORE) -> list[TextHit]:
    """그림의 한 띠를 읽는다. box = (x0, y0, x1, y1). horizontal이면 글자 중심의 x를, 아니면 y를 u로 쓴다."""
    from PIL import Image

    H, W = rgb.shape[:2]
    x0, y0, x1, y1 = (max(0, int(box[0])), max(0, int(box[1])), min(W, int(box[2])), min(H, int(box[3])))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    crop = Image.fromarray(rgb[y0:y1, x0:x1])
    crop = crop.resize((crop.width * UPSCALE, crop.height * UPSCALE), Image.LANCZOS)
    result, _ = _engine()(np.asarray(crop))
    out: list[TextHit] = []
    for item in result or []:
        pts, text, score = item[0], item[1], float(item[2])
        name = clean(text)
        if not name or score < min_score:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx = x0 + (min(xs) + max(xs)) / 2 / UPSCALE
        cy = y0 + (min(ys) + max(ys)) / 2 / UPSCALE
        w = (max(xs) - min(xs)) / UPSCALE if horizontal else (max(ys) - min(ys)) / UPSCALE
        out.append(TextHit(cx if horizontal else cy, name, score, w))
    return out


def _band_box(side: str, body: tuple[float, float, float, float], v_pin: float, pitch: float,
              outward: bool, u_range: tuple[float, float] | None = None) -> tuple[int, int, int, int]:
    """접속점 줄에서 바깥(또는 안쪽)으로 떨어진 글자 띠의 사각형. u_range를 주면 변의 그 토막만."""
    x0, y0, x1, y1 = body
    short = min(x1 - x0, y1 - y0)
    near = BAND_INNER * pitch
    far = max(BAND_OUTER * pitch, near + BAND_MIN_RATIO * short)
    _, u0, u1 = _side_axes(side, body)
    pad = 0.05 * (u1 - u0)
    if u_range is not None:
        u0, u1 = u_range
        pad = 0.0
    # v_pin은 '변에서 떨어진 거리'(바깥이 +). 그림 좌표로 바꾼다.
    if side in ("top", "bottom"):
        base = (y0 - v_pin) if side == "top" else (y1 + v_pin)
        step = -1 if (side == "top") == outward else 1
        a, b = base + step * near, base + step * far
        return (int(u0 - pad), int(min(a, b)), int(u1 + pad), int(max(a, b)))
    base = (x0 - v_pin) if side == "left" else (x1 + v_pin)
    step = -1 if (side == "left") == outward else 1
    a, b = base + step * near, base + step * far
    return (int(min(a, b)), int(u0 - pad), int(max(a, b)), int(u1 + pad))


def _neighbour_gap(us: list[float], k: int, pitch: float) -> float:
    """접속점 k에서 가장 가까운 이웃까지의 거리 (혼자면 격자)."""
    gaps = [abs(us[k] - us[j]) for j in (k - 1, k + 1) if 0 <= j < len(us)]
    return min(gaps) if gaps else pitch


def _match(texts: list[TextHit], us: list[float], pitch: float) -> dict[int, TextHit]:
    """글자를 가장 가까운 접속점에 짝지운다 (격자의 절반 안에 있을 때만, 하나씩)."""
    out: dict[int, TextHit] = {}
    for t in sorted(texts, key=lambda t: -t.score):
        best, bi = None, -1
        for i, u in enumerate(us):
            if i in out:
                continue
            d = abs(t.u - u)
            if best is None or d < best:
                best, bi = d, i
        if best is not None and best <= 0.5 * pitch:
            out[bi] = t
    return out


def read_pin_names(rgb: np.ndarray, body: tuple[float, float, float, float], hits: list[PinHit],
                   *, min_score: float = MIN_SCORE) -> dict[int, str]:
    """접속점마다 이름을 읽는다 → {hits의 차례: 이름}. 읽지 못한 접속점은 빠진다."""
    if not hits or not available():
        return {}
    names: dict[int, str] = {}
    for side in sorted({h.side for h in hits}):
        idx = [i for i, h in enumerate(hits) if h.side == side]
        f, _, _ = _side_axes(side, body)
        us = [f(hits[i].x_px, hits[i].y_px)[0] for i in idx]
        v_pin = float(np.median([f(hits[i].x_px, hits[i].y_px)[1] for i in idx]))
        pitch = float(np.median([hits[i].pitch for i in idx])) or 32.0
        horizontal = side in ("top", "bottom")
        picked: dict[int, TextHit] = {}
        best_key: tuple[int, int] | None = None
        for outward in (True, False):                      # 변에 가까운 쪽(바깥)을 먼저 본다
            box = _band_box(side, body, v_pin, pitch, outward)
            got = _match(read_band(rgb, box, horizontal=horizontal, min_score=min_score), us, pitch)
            key = (len(got), 1 if outward else 0)          # 많이 짝지은 쪽, 같으면 변에 가까운 쪽
            if got and (best_key is None or key > best_key):
                best_key, picked = key, got
        if best_key is not None:
            outward = bool(best_key[1])
            half = max(0.45 * pitch, MIN_SLICE_PX / 2)
            # **이웃 접속점까지의 거리**보다 넓게 읽힌 글자는 두 이름이 붙은 것이다 (`A1 A2` → `A1A2`).
            # 격자(pitch)로 재면 안 된다 — 격자가 잔 변에서는 정상 이름도 넓어 보인다.
            # 글자 수가 덮은 접속점 수로 나누어떨어지면 **고르게 쪼개** 나눠 준다 (`1323` → `13`·`23`).
            for k, t in list(picked.items()):
                gap = _neighbour_gap(us, k, pitch)
                if t.width <= WIDE_TEXT * gap:
                    continue
                lo, hi = t.u - t.width / 2, t.u + t.width / 2
                over = sorted((j for j, u in enumerate(us) if lo - 0.3 * gap <= u <= hi + 0.3 * gap),
                              key=lambda j: us[j])
                n = len(over)
                if n >= 2 and len(t.text) % n == 0 and all(j == k or j not in picked for j in over):
                    step = len(t.text) // n
                    del picked[k]
                    for i, j in enumerate(over):
                        picked[j] = TextHit(us[j], t.text[i * step:(i + 1) * step], t.score, gap * 0.5)
                else:
                    del picked[k]
            # 짝이 안 된 접속점은 **그 접속점 둘레만 좁게 잘라** 다시 읽는다.
            for k, u in enumerate(us):
                if k in picked:
                    continue
                box = _band_box(side, body, v_pin, pitch, outward, u_range=(u - half, u + half))
                got = read_band(rgb, box, horizontal=horizontal, min_score=min_score)
                near = [t for t in got
                        if abs(t.u - u) <= half and t.width <= WIDE_TEXT * _neighbour_gap(us, k, pitch)]
                if near:
                    picked[k] = max(near, key=lambda t: (t.score, -abs(t.u - u)))
        for k, t in picked.items():
            names[idx[k]] = t.text
    return names


# =============================================================================== 이름으로 자리를 정한다
NAME_OK = re.compile(r"^[A-Za-z]{0,2}[0-9]{1,3}[A-Za-z]?$|^PE$|^[+-]$", re.I)
LABEL_ROW_TOL = 10        # 글자를 같은 줄로 묶을 세로 허용 (px)
LABEL_MAX_GAP = 0.5       # 이름 줄이 변에서 이만큼(본체 높이 대비) 안에 있어야 한다
LABEL_SNAP = 12           # 구멍이 이만큼 안에 있으면 그 자리로 당긴다


def read_words(rgb: np.ndarray, box: tuple[float, float, float, float] | None = None,
               *, min_score: float = 0.4) -> list[tuple[float, float, float, float, str, float]]:
    """그림의 글자 덩어리 → (중심x, 중심y, 폭, 높이, 글자, 확신)."""
    from PIL import Image

    H, W = rgb.shape[:2]
    x0, y0, x1, y1 = box or (0, 0, W, H)
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return []
    crop = Image.fromarray(rgb[y0:y1, x0:x1])
    crop = crop.resize((crop.width * UPSCALE, crop.height * UPSCALE), Image.LANCZOS)
    result, _ = _engine()(np.asarray(crop))
    out = []
    for item in result or []:
        pts, txt, score = item[0], item[1], float(item[2])
        if score < min_score:
            continue
        xs = [p[0] / UPSCALE + x0 for p in pts]
        ys = [p[1] / UPSCALE + y0 for p in pts]
        out.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                    max(xs) - min(xs), max(ys) - min(ys), str(txt).strip(), score))
    return out


def _label_rows(rgb, body, tol: float = LABEL_ROW_TOL):
    """글자를 줄로 묶는다 → [(줄 중심y, [(중심x, 폭, 글자)…])]"""
    ws = [w for w in read_words(rgb) if body[0] - 40 <= w[0] <= body[2] + 40]
    rows: dict[float, list] = {}
    for x, y, w, _h, t, _s in sorted(ws, key=lambda z: z[1]):
        key = next((k for k in rows if abs(k - y) <= tol), None)
        if key is None:
            rows[y] = []
            key = y
        rows[key].append((x, w, t))
    return [(k, sorted(v)) for k, v in sorted(rows.items())]


def _split_chunk(text: str, n: int) -> list[str] | None:
    """붙어 읽힌 덩어리를 n조각으로. 공백으로 이미 나뉘어 있으면 그것부터."""
    parts = [p for p in re.split(r"\s+", text) if p]
    if len(parts) == n:
        return parts
    flat = "".join(parts)
    if n > 0 and len(flat) % n == 0:
        k = len(flat) // n
        return [flat[i * k:(i + 1) * k] for i in range(n)]
    return None


def _allot(widths: list[float], n: int) -> list[int]:
    """덩어리들에 접속점 수 n을 폭에 비례해 나눈다 (합이 정확히 n이 되게)."""
    total = sum(widths) or 1.0
    raw = [w / total * n for w in widths]
    out = [max(1, int(round(r))) for r in raw]
    while sum(out) > n and max(out) > 1:
        out[out.index(max(out))] -= 1
    while sum(out) < n:
        out[raw.index(max(raw))] += 1
    return out


def _hole_rows(prims, tol: float = 8.0):
    """벡터가 찾은 단자 구멍(원)을 줄로 묶는다 → [(y, [x…])]"""
    from .geom import Circle

    cs = [(c.cx, c.cy, c.r) for c in prims if isinstance(c, Circle)]
    if not cs:
        return []
    med = float(np.median([c[2] for c in cs]))
    cs = [c for c in cs if c[2] >= 0.6 * med]
    rows: dict[float, list[float]] = {}
    for x, y, _r in sorted(cs, key=lambda z: z[1]):
        key = next((k for k in rows if abs(k - y) <= tol), None)
        if key is None:
            rows[y] = []
            key = y
        rows[key].append(x)
    return [(k, sorted(v)) for k, v in sorted(rows.items()) if len(v) >= 2]


def _unstack(pins: list[tuple[str, float, float, str]],
             body: tuple[float, float, float, float]) -> list[tuple[str, float, float, str]]:
    """한 변에 줄이 여럿이면 **한 줄로 편다** — 변에 가까운 줄부터 이어서.

    DNCAD 블랙박스는 변마다 접속점이 한 줄이다. 단자대가 2열인 제품은 두 열의 x가 같아서 그대로 두면
    슬롯이 겹쳐 절반이 버려진다(실측: 24개 → 18개). 편 자리는 **본체 안에 고르게** 둔다 — 밖으로
    나가면 모델 검증이 '간격 0'으로 본다. 자리는 바뀌지만 **이름과 순서는 지킨다**.
    """
    x0, y0, x1, y1 = body
    out: list[tuple[str, float, float, str]] = []
    for side in ("top", "bottom", "left", "right"):
        mine = [p for p in pins if p[0] == side]
        if not mine:
            continue
        rows: dict[float, list] = {}
        for p in mine:
            key = next((k for k in rows if abs(k - p[2]) <= 4), None)
            if key is None:
                rows[p[2]] = []
                key = p[2]
            rows[key].append(p)
        if len(rows) <= 1:
            out += sorted(mine, key=lambda p: p[1])
            continue
        order = sorted(rows) if side in ("top", "left") else sorted(rows, reverse=True)
        seq = [p for k in order for p in sorted(rows[k], key=lambda p: p[1])]
        lo, hi = (x0, x1) if side in ("top", "bottom") else (y0, y1)
        step = (hi - lo) / (len(seq) + 1)
        near = order[0]
        for i, p in enumerate(seq, start=1):
            u = lo + step * i
            out.append((p[0], u, near, p[3]) if side in ("top", "bottom") else (p[0], near, u, p[3]))
    return out


def pins_from_labels(rgb: np.ndarray, prims: list, body: tuple[float, float, float, float]
                     ) -> list[tuple[str, float, float, str]]:
    """**이름으로 역으로 자리를 정한다** → [(변, x, y, 이름)].

    제품 실물 사진은 단자 자리가 흐리지만 **번호는 선명하다**. 그래서 이름 줄을 먼저 찾고, 거기서
    접속점을 만든다. 구멍(원)이 보이면 그 자리로 당겨 정확도를 올린다.
    (실측: Pilz PNOZ X3 사진 — 자리로 찾으면 11/24, 이름으로 찾으면 **24/24**.)

    도면 심볼에는 쓰지 않는다 — 전압 표시·설명 글자를 단자로 오인한다.
    """
    if not available():
        return []
    x0, y0, x1, y1 = body
    mid, height = (y0 + y1) / 2, y1 - y0
    holes = _hole_rows(prims)
    out: list[tuple[str, float, float, str]] = []
    for ly, chunks in _label_rows(rgb, body):
        side = "top" if ly < mid else "bottom"
        if abs(ly - (y0 if side == "top" else y1)) > LABEL_MAX_GAP * height:
            continue
        flat = "".join(re.split(r"\s+", "".join(t for _, _, t in chunks)))
        if not flat or not re.fullmatch(r"[A-Za-z0-9+\-/.]+", flat):
            continue
        # 이름은 단자의 **바깥쪽**에 적힌다 — 위 단자는 이름이 구멍 아래, 아래 단자는 구멍 위
        best = None
        for hy, hxs in holes:
            if (side == "top" and hy >= ly) or (side == "bottom" and hy <= ly):
                continue
            d = abs(hy - ly)
            if d < 0.25 * height and (best is None or d < best[0]):
                best = (d, hy, hxs)
        # 이름 하나가 몇 글자인가 — 이미 따로 떨어진 짧은 조각에서 배운다.
        # 다 붙어 있으면(`A1B113`) 그 줄에 보이는 구멍 수로 나눈다.
        pieces = [q for _, _, t in chunks for q in re.split(r"\s+", t) if q]
        solo = [len(q) for q in pieces if len(q) <= 3]
        if solo:
            k = min(solo)
            counts = [max(1, round(len("".join(re.split(r"\s+", t))) / k)) for _, _, t in chunks]
        elif best:
            counts = _allot([w for _, w, _ in chunks], len(best[2]))
        else:
            continue
        names: list[str] = []
        xs: list[float] = []
        ok = True
        for (cx, w, t), n in zip(chunks, counts):
            parts = _split_chunk(t, n)
            if parts is None:
                ok = False
                break
            left, step = cx - w / 2, w / n
            for i, part in enumerate(parts):
                names.append(part)
                xs.append(left + step * (i + 0.5))
        if not ok or len(names) < 3 or not all(NAME_OK.match(nm) for nm in names):
            continue
        py = best[1] if best else ly
        for nm, ux in zip(names, xs):
            if best:
                nearest = min(best[2], key=lambda c: abs(c - ux))
                if abs(nearest - ux) <= LABEL_SNAP:
                    ux = nearest
            out.append((side, float(ux), float(py), nm))
    return _unstack(out, body)
