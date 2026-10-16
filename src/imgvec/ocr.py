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

from .detect import PinHit, _side_axes, lattice_fit

# 단자 이름에 흔한 글자만 남긴다 (읽기 오류로 붙은 기호·공백을 턴다)
_KEEP = re.compile(r"[^0-9A-Za-z+\-/.']")
BAND_INNER = 0.05      # 접속점에서 이만큼(격자 대비) 떨어진 곳부터 글자를 찾는다
BAND_OUTER = 0.9       # 이만큼까지. 더 넓히면 "Safety Input 1" 같은 설명 글자가 섞인다 (실측)
BAND_MIN_RATIO = 0.08  # 띠 두께의 밑바닥 (본체 짧은 변 대비). 격자가 잘면 띠가 얇아져 글자가 잘린다
MIN_SCORE = 0.5
UPSCALE = 3            # 작은 글자를 키워서 읽는다
MIN_SLICE_PX = 28      # 접속점 하나를 다시 읽을 때 자를 최소 폭
WIDE_TEXT = 0.9        # 글자 폭이 이웃 접속점까지 거리의 이만큼을 넘으면 두 이름이 붙어 읽힌 것으로 본다
RETRY_SCORE = 0.95     # 전체 읽기 결과가 이보다 확신이 낮으면 그 변을 띠로 다시 읽는다


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


def read_bands(rgb: np.ndarray, boxes: list[tuple[int, int, int, int]], *, horizontal: bool = True,
               min_score: float = MIN_SCORE, workers: int = 4) -> list[list[TextHit]]:
    """여러 띠를 **동시에** 읽는다 → 띠별 글자 목록.

    읽기는 호출당 1.1초가 고정으로 드는데 그 대부분이 한 코어에서 도는 추론이다. 띠를 한 장으로
    이어 붙여 한 번에 읽어도 보았으나, 캔버스가 커지면 인식기가 글자를 다르게 잘라 이름이 틀렸다
    (`Y1` → `rossfault`). 그래서 **정확도는 그대로 두고 동시에** 읽는다.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not boxes:
        return []
    if len(boxes) == 1 or workers <= 1:
        return [read_band(rgb, b, horizontal=horizontal, min_score=min_score) for b in boxes]
    with ThreadPoolExecutor(max_workers=min(workers, len(boxes))) as pool:
        return list(pool.map(lambda b: read_band(rgb, b, horizontal=horizontal, min_score=min_score), boxes))


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


def _in_box(words, box: tuple[int, int, int, int], *, horizontal: bool) -> list[TextHit]:
    """한 번 읽어 둔 글자 목록에서 그 띠 안에 든 것만 고른다."""
    x0, y0, x1, y1 = box
    out: list[TextHit] = []
    for cx, cy, w, h, text, score in words:
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        name = clean(text)
        # 전체를 한 번에 읽으면 설명 글자가 한 덩어리로 들어온다("Safety Input 1", "Cross fault …").
        # 단자 이름 꼴이 아니면 버린다 — 그 자리는 아래에서 좁게 다시 읽는다.
        if not name or not NAME_OK.match(name):
            continue
        out.append(TextHit(cx if horizontal else cy, name, score, w if horizontal else h))
    return out


def read_pin_names(rgb: np.ndarray, body: tuple[float, float, float, float], hits: list[PinHit],
                   *, min_score: float = MIN_SCORE) -> dict[int, str]:
    """접속점마다 이름을 읽는다 → {hits의 차례: 이름}. 읽지 못한 접속점은 빠진다.

    그림을 **한 번만** 읽고 띠별로 걸러 쓴다 — 띠마다 따로 읽으면 호출당 1.1초가 고정으로 들어
    변 2개짜리 그림이 4.3초였다(실측). 한 번 읽기는 1.6~2.3초다. 그래도 짝이 안 된 접속점은
    그 자리만 좁게 **다시** 읽는다 — 지역 확대가 작은 글자에 도움이 되기 때문이다.
    """
    if not hits or not available():
        return {}
    sides = sorted({h.side for h in hits})
    # 변마다 바깥·안쪽 띠를 모아 **한 번에** 읽는다 (변 2개면 호출 4번 → 1번)
    geo, boxes = {}, []
    for side in sides:
        f, _, _ = _side_axes(side, body)
        idx = [i for i, h in enumerate(hits) if h.side == side]
        us = [f(hits[i].x_px, hits[i].y_px)[0] for i in idx]
        v_pin = float(np.median([f(hits[i].x_px, hits[i].y_px)[1] for i in idx]))
        pitch = float(np.median([hits[i].pitch for i in idx])) or 32.0
        geo[side] = (idx, us, v_pin, pitch)
        boxes += [_band_box(side, body, v_pin, pitch, True), _band_box(side, body, v_pin, pitch, False)]
    flat_h = all(s in ("top", "bottom") for s in sides)
    if flat_h:
        read = read_bands(rgb, boxes, horizontal=True, min_score=min_score)
    else:   # 좌·우 변은 띠가 세로라 따로 읽는다 (드물다)
        read = [read_band(rgb, b, horizontal=(s in ("top", "bottom")), min_score=min_score)
                for s in sides for b in (boxes[2 * sides.index(s)], boxes[2 * sides.index(s) + 1])]
    band_texts = {s: (read[2 * i], read[2 * i + 1]) for i, s in enumerate(sides)}
    names: dict[int, str] = {}
    for side in sides:
        idx, us, v_pin, pitch = geo[side]
        horizontal = side in ("top", "bottom")
        picked: dict[int, TextHit] = {}
        best_key: tuple[int, int] | None = None
        for outward, texts in zip((True, False), band_texts[side]):   # 변에 가까운 쪽(바깥)을 먼저 본다
            got = _match(texts, us, pitch)
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
NAME_OK = re.compile(r"^[A-Za-z]{0,2}[0-9]{1,3}[A-Za-z]?'?$|^(PE|GND|FG|SG|N|L)'?$|^[+-]$", re.I)
LABEL_ROW_TOL = 10        # 글자를 같은 줄로 묶을 세로 허용 (px)
LABEL_MAX_GAP = 0.5       # 이름 줄이 변에서 이만큼(본체 높이 대비) 안에 있어야 한다
LABEL_SNAP = 12           # 구멍이 이만큼 안에 있으면 그 자리로 당긴다
LABEL_ROWS_PER_SIDE = 2   # 변마다 **변에 가까운** 이만큼의 줄만 단자 이름으로 본다 (2열 단자대까지)
LABEL_MIN_FIT = 0.5       # 남은 이름 자리가 한 격자 위에 이만큼은 맞아야 한다
# 전원·모터 단자는 한 글자다 (U V W · R S T · L N · P · B · E). 이름으로 자리를 정할 때만 받는다 —
# 자리로 찾는 길(`read_pin_names`)에서 받으면 설명 글자의 낱자가 이름으로 들어온다.
SOLO_OK = re.compile(r"^[BEGKLMNPRSTUVW]'?$")


def _name_ok(q: str) -> bool:
    return bool(NAME_OK.match(q) or SOLO_OK.match(q))


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


# 커넥터 핀은 단자 이름 대신 **무슨 커넥터인지** 적힌다 (실측: SV1의 X1~X6 자리에
# 'USB connector' · 'RS232/485 connector' · 'Safety function connector' · 'Interface connector' ·
# 'Encoder connection'). 그 글자의 x 범위에 든 접속점은 일반 단자가 아니라 커넥터 핀이다.
CONNECTOR_WORDS = re.compile(r"connect|커넥터|플러그|plug|socket", re.I)


def connector_spans(rgb: np.ndarray, body: tuple[float, float, float, float],
                    words: list | None = None) -> list[tuple[float, float, float]]:
    """커넥터라고 적힌 글자 → (왼쪽x, 오른쪽x, 중심y). 본체에서 멀면 버린다."""
    ws = read_words(rgb) if words is None else words
    x0, y0, x1, y1 = body
    h = y1 - y0
    out = []
    for x, y, w, _hh, t, _sc in ws:
        if not CONNECTOR_WORDS.search(t):
            continue
        if x0 - 40 <= x <= x1 + 40 and y0 - 0.5 * h <= y <= y1 + 0.5 * h:
            out.append((x - w / 2, x + w / 2, y))
    return out


def mark_connectors(pins: list, body: tuple[float, float, float, float],
                    spans: list[tuple[float, float, float]]) -> int:
    """커넥터 글자와 같은 변·같은 x에 있는 접속점의 종류를 `connector`로 → 바꾼 수.

    글자가 본체 가운데선 어느 쪽에 있는지로 변을 가른다 — 위 변 단자의 설명은 위쪽 절반에 적힌다.
    """
    x0, y0, x1, y1 = body
    mid = (y0 + y1) / 2
    n = 0
    for p in pins:
        if p.side not in ("top", "bottom") or p.x_px is None:
            continue
        for sx0, sx1, sy in spans:
            if (sy < mid if p.side == "top" else sy >= mid) and sx0 <= p.x_px <= sx1:
                p.kind = "connector"
                n += 1
                break
    return n


def _label_rows(rgb, body, tol: float = LABEL_ROW_TOL, words: list | None = None):
    """글자를 줄로 묶는다 → [(줄 중심y, [(중심x, 폭, 글자)…])]"""
    ws = [w for w in (read_words(rgb) if words is None else words) if body[0] - 40 <= w[0] <= body[2] + 40]
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
            out.append((p[0], u, near, *p[3:]) if side in ("top", "bottom") else (p[0], near, u, *p[3:]))
    return out


def pins_from_labels(rgb: np.ndarray, prims: list, body: tuple[float, float, float, float],
                     words: list | None = None, spans: list | None = None
                     ) -> list[tuple[str, float, float, str, str]]:
    """**이름으로 역으로 자리를 정한다** → [(변, x, y, 이름, 종류)].

    제품 실물 사진은 단자 자리가 흐리지만 **번호는 선명하다**. 그래서 이름 줄을 먼저 찾고, 거기서
    접속점을 만든다. 구멍(원)이 보이면 그 자리로 당겨 정확도를 올린다.
    (실측: Pilz PNOZ X3 사진 — 자리로 찾으면 11/24, 이름으로 찾으면 **24/24**.)

    도면 심볼에는 쓰지 않는다 — 전압 표시·설명 글자를 단자로 오인한다.
    """
    if not available():
        return []
    x0, y0, x1, y1 = body
    mid, height = (y0 + y1) / 2, y1 - y0

    def kind_at(side: str, ux: float) -> str:
        """커넥터 글자와 같은 변·같은 x면 커넥터 핀. **자리를 펴기 전** x로 봐야 맞는다."""
        for sx0, sx1, sy in spans or ():
            if (sy < mid if side == "top" else sy >= mid) and sx0 <= ux <= sx1:
                return "connector"
        return "electrical"

    holes = _hole_rows(prims)
    out: list[tuple[str, float, float, str]] = []
    rows = []
    for ly, chunks in _label_rows(rgb, body, words=words):
        side = "top" if ly < mid else "bottom"
        gap = abs(ly - (y0 if side == "top" else y1))
        if gap > LABEL_MAX_GAP * height:
            continue
        rows.append((side, gap, ly, chunks))
    # 변마다 **변에 가까운 줄**만 쓴다 — 그림 안쪽 라벨(접점표·설명)을 단자로 삼지 않는다.
    keep_rows = []
    for which in ("top", "bottom"):
        mine = sorted((r for r in rows if r[0] == which), key=lambda r: r[1])
        keep_rows += mine[:LABEL_ROWS_PER_SIDE]
    for side, _gap, ly, chunks in keep_rows:

        # 이름은 단자의 **바깥쪽**에 적힌다 — 위 단자는 이름이 구멍 아래, 아래 단자는 구멍 위
        best = None
        for hy, hxs in holes:
            if (side == "top" and hy >= ly) or (side == "bottom" and hy <= ly):
                continue
            d = abs(hy - ly)
            if d < 0.25 * height and (best is None or d < best[0]):
                best = (d, hy, hxs)
        # 한 줄에 단자 이름과 설명이 섞인다("L1 L2 L3 … USB connector"). 덩어리마다 이름 꼴만 건진다.
        # 이름 하나가 몇 글자인지는 그 줄에서 **따로 떨어진 짧은 이름**에게 배운다. 한 글자에게는 배우지
        # 않는다 — `U`로 배우면 설명 글자까지 한 글자씩 쪼개 이름으로 삼는다 (실측: SV1 아래 변).
        pieces = [q for _, _, t in chunks for q in re.split(r"\s+", t) if q]
        solo = [len(q) for q in pieces if _name_ok(q) and 2 <= len(q) <= 3]
        k = min(solo) if solo else 0
        got: list[tuple[str, float]] = []
        for cx, w, t in chunks:
            parts = [q for q in re.split(r"\s+", t) if q]
            seq = parts
            if k and not all(_name_ok(q) for q in parts):   # 붙어 읽힌 덩어리(`142434`)를 쪼갠다
                n = int(round(len("".join(parts)) / k))
                sp = _split_chunk(t, n) if n >= 2 else None
                if sp and all(_name_ok(q) for q in sp):
                    seq = sp
            step = w / len(seq)
            got += [(q, cx - w / 2 + step * (i + 0.5)) for i, q in enumerate(seq) if _name_ok(q)]
        if len(got) >= 3:
            got.sort(key=lambda z: z[1])
            fit, _p = lattice_fit(np.array([u for _q, u in got]), x1 - x0)
            if fit >= LABEL_MIN_FIT:
                py0 = best[1] if best else ly
                for q, ux in got:
                    if best:
                        nearest = min(best[2], key=lambda c: abs(c - ux))
                        if abs(nearest - ux) <= LABEL_SNAP:
                            ux = nearest
                    out.append((side, float(ux), float(py0), q, kind_at(side, ux)))
                continue
        # 이름 꼴이 하나도 안 나오면 줄이 통째로 붙어 읽힌 것이다(`A1B113`) — 보이는 구멍 수로 나눈다.
        if not best:
            continue
        counts = _allot([w for _, w, _ in chunks], len(best[2]))
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
        if not ok or len(names) < 3 or not all(_name_ok(nm) for nm in names):
            continue
        for nm, ux in zip(names, xs):
            nearest = min(best[2], key=lambda c: abs(c - ux))
            if abs(nearest - ux) <= LABEL_SNAP:
                ux = nearest
            out.append((side, float(ux), float(best[1]), nm, kind_at(side, ux)))
    return _unstack(out, body)
