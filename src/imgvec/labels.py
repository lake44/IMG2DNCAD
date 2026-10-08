"""입력 이미지 → 라벨 지도(색 영역) → 경계 리본.

핵심 아이디어: 서로 다른 라벨 사이의 경계를 모두 '선'으로 본다.
굵기가 얇은 선(획)은 양쪽 경계가 가까워 모폴로지 닫힘으로 한 줄의 리본이 되고,
그 골격이 곧 획의 중심선이다. 넓은 영역은 경계가 한 줄이라 윤곽선 그대로 남는다.
"""
from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from skimage.filters import threshold_sauvola


# --------------------------------------------------------------------------- 종류 판별


def classify_kind(rgb: np.ndarray) -> str:
    """'lineart'(흑백 선화) 또는 'illustration'(색 영역 그림)으로 판별한다."""
    s = min(1.0, 600.0 / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1.0 else rgb
    sat = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)[..., 1].astype(np.float32) / 255.0
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    hist = np.bincount((gray >> 3).ravel(), minlength=32).astype(np.float64) / gray.size
    top2 = float(np.sort(hist)[::-1][:2].sum())
    return "lineart" if (float(sat.mean()) < 0.06 and top2 > 0.88) else "illustration"


# --------------------------------------------------------------------------- 보조


def _fill_invalid(lab: np.ndarray, invalid: np.ndarray) -> np.ndarray:
    """invalid 픽셀을 가장 가까운 유효 라벨로 채운다."""
    if not invalid.any():
        return lab
    if invalid.all():
        return lab
    idx = ndi.distance_transform_edt(invalid, return_distances=False, return_indices=True)
    out = lab.copy()
    out[invalid] = lab[idx[0][invalid], idx[1][invalid]]
    return out


def remove_small(mask: np.ndarray, min_area: int, connectivity: int = 8) -> np.ndarray:
    """면적이 min_area 미만인 연결 성분을 지운다."""
    if min_area <= 1:
        return mask
    n, cc, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=connectivity)
    if n <= 1:
        return mask
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    return keep[cc]


# --------------------------------------------------------------------------- 선화 라벨


def labels_lineart(rgb: np.ndarray, cfg: dict) -> tuple[np.ndarray, dict]:
    """흑백 선화: 잉크=1, 배경=0."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    g = cv2.GaussianBlur(gray, (0, 0), 0.8)
    info: dict = {}
    if cfg.get("binarize") == "sauvola":
        win = int(max(25, min(g.shape) // 20) | 1)
        t = threshold_sauvola(g, window_size=win, k=0.2)
        ink = g < t
        info["binarize"] = f"sauvola(window={win})"
    else:
        thr, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        ink = g < thr
        info["binarize"] = f"otsu({thr:.0f})"
    inv = cfg.get("invert", "auto")
    if inv == "yes" or (inv == "auto" and ink.mean() > 0.5):
        ink = ~ink
        info["inverted"] = True
    ms = int(cfg.get("min_region_px", 10))
    ink = remove_small(ink, ms)
    ink = ~remove_small(~ink, ms)  # 잉크 속의 작은 구멍 메우기
    return ink.astype(np.int32), info


# --------------------------------------------------------------------------- 일러스트 라벨


def extract_palette(rgb: np.ndarray, k_max: int, sep: float, min_frac: float) -> np.ndarray:
    """우세한 색을 골라 Lab 팔레트(K, 3)를 만든다.

    5비트 색 히스토그램에서 주변보다 뚜렷이 솟은 봉우리를 큰 순서로 고르되,
    이미 고른 색과 Lab 거리가 sep 미만이면 건너뛴다. 안티앨리어싱 중간색은 봉우리가 아니므로 빠진다.
    """
    q = (rgb >> 3).astype(np.int64)
    hist = np.bincount(((q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]).ravel(), minlength=32768).astype(np.float64)
    h3 = hist.reshape(32, 32, 32)
    pad = np.pad(h3, 1)
    nbmax = np.zeros_like(h3)
    for dz in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dz == dy == dx == 0:
                    continue
                nbmax = np.maximum(nbmax, pad[1 + dz : 33 + dz, 1 + dy : 33 + dy, 1 + dx : 33 + dx])
    total = float(hist.sum())
    min_count = max(2.0, min_frac * total)
    flat = hist
    peak = (h3 > 1.3 * nbmax).ravel() & (flat >= min_count)
    order_all = np.argsort(-flat)
    order = [i for i in order_all if peak[i]]
    if len(order) < 2:  # 매끈한 분포(사진 등): 빈도 순으로 대체
        order = [i for i in order_all if flat[i] >= min_count]

    def bin_lab(idx: np.ndarray) -> np.ndarray:
        r = ((idx >> 10) & 31) * 8 + 4
        g = ((idx >> 5) & 31) * 8 + 4
        b = (idx & 31) * 8 + 4
        rgbf = (np.column_stack((r, g, b)).astype(np.float32) / 255.0).reshape(-1, 1, 3)
        return cv2.cvtColor(rgbf, cv2.COLOR_RGB2Lab).reshape(-1, 3).astype(np.float64)

    cand = bin_lab(np.asarray(order, dtype=np.int64))
    pal: list[np.ndarray] = []
    for c in cand:
        if all(float(np.linalg.norm(c - p)) >= sep for p in pal):
            pal.append(c)
            if len(pal) >= k_max:
                break
    if not pal:
        pal = [bin_lab(np.asarray([int(order_all[0])]))[0]]
    pal_arr = np.asarray(pal)

    # 실제 픽셀로 팔레트 보정 (Lloyd 2회)
    rng = np.random.default_rng(0)
    flat_rgb = rgb.reshape(-1, 3)
    pick = rng.choice(len(flat_rgb), size=min(len(flat_rgb), 200_000), replace=False)
    lab = cv2.cvtColor((flat_rgb[pick].astype(np.float32) / 255.0).reshape(-1, 1, 3), cv2.COLOR_RGB2Lab).reshape(-1, 3)
    for _ in range(2):
        a = cKDTree(pal_arr).query(lab)[1]
        for k in range(len(pal_arr)):
            m = a == k
            if m.any():
                pal_arr[k] = lab[m].mean(axis=0)
    return pal_arr


def labels_illustration(rgb: np.ndarray, cfg: dict) -> tuple[np.ndarray, dict]:
    """색 영역 그림: 팔레트 최근접 양자화 후 얇은 띠·작은 점 정리."""
    img = rgb
    dn = float(cfg.get("denoise", 0.0))
    if dn > 0:
        img = cv2.bilateralFilter(img, 0, dn, 2.0)
    pal = extract_palette(img, int(cfg["colors"]), float(cfg["palette_sep"]), float(cfg["min_color_frac"]))
    lab_img = cv2.cvtColor(img.astype(np.float32) / 255.0, cv2.COLOR_RGB2Lab)
    H, W = img.shape[:2]
    labels = cKDTree(pal).query(lab_img.reshape(-1, 3))[1].reshape(H, W).astype(np.int32)

    k_all = len(pal)
    band = int(cfg.get("thin_band_px", 0))
    for _ in range(2):
        if band >= 2:
            se = np.ones((band + 1, band + 1), np.uint8)
            invalid = np.zeros(labels.shape, bool)
            for k in range(k_all):
                m = (labels == k).astype(np.uint8)
                if not m.any():
                    continue
                op = cv2.morphologyEx(m, cv2.MORPH_OPEN, se)
                invalid |= (m > 0) & (op == 0)
            labels = _fill_invalid(labels, invalid)
        ms = int(cfg.get("min_region_px", 0))
        if ms > 1:
            invalid = np.zeros(labels.shape, bool)
            for k in range(k_all):
                m = labels == k
                if not m.any():
                    continue
                invalid |= m & ~remove_small(m, ms, connectivity=4)
            labels = _fill_invalid(labels, invalid)
    return labels, {"palette_size": int(k_all)}


# --------------------------------------------------------------------------- 경계 리본


def boundary_mask(lab: np.ndarray) -> np.ndarray:
    """서로 다른 라벨이 맞닿은 모든 지점(양쪽 픽셀 포함). 8-연결."""
    H, W = lab.shape
    b = np.zeros((H, W), bool)
    dx = lab[:, 1:] != lab[:, :-1]
    dy = lab[1:, :] != lab[:-1, :]
    b[:, 1:] |= dx
    b[:, :-1] |= dx
    b[1:, :] |= dy
    b[:-1, :] |= dy
    d1 = lab[1:, 1:] != lab[:-1, :-1]
    d2 = lab[1:, :-1] != lab[:-1, 1:]
    b[1:, 1:] |= d1
    b[:-1, :-1] |= d1
    b[1:, :-1] |= d2
    b[:-1, 1:] |= d2
    return b


def estimate_merge_px(kind: str, labels: np.ndarray) -> float:
    """쌍을 이루는 경계를 한 줄로 합칠 닫힘 반경(작업 px)을 추정한다.

    경계 사이의 빈 폭(획의 안쪽 폭)이 2k 이하이면 닫힘이 두 경계를 한 줄로 합친다.
    획 폭의 최빈값을 재서 k를 정하므로, 획보다 넓은 구조(슬롯, 막대 등)는 합쳐지지 않는다.
    """
    from .skeleton import skeleton_of

    H, W = labels.shape
    if kind == "lineart":
        ink = labels == 1
        if ink.sum() < 20:
            return 2.0
        dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
        sk = skeleton_of(ink)
        h = float(np.median(dist[sk])) if sk.any() else 1.0
        return float(min(16.0, max(2.0, np.ceil(h))))
    free = ~boundary_mask(labels)
    if free.sum() < 100:
        return 3.0
    dist = cv2.distanceTransform(free.astype(np.uint8), cv2.DIST_L2, 3)
    sk = skeleton_of(free)
    vals = dist[sk]
    vals = vals[(vals >= 1.0) & (vals <= 14.0)]
    if len(vals) < 50:
        return float(min(8.0, max(2.0, round(0.0035 * max(H, W)))))
    hist = np.bincount(np.round(vals).astype(int), minlength=16).astype(float)
    hist[0] = 0.0
    hist = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
    mode = int(np.argmax(hist))
    return float(min(10.0, max(2.0, mode + 1.0)))


def fill_small_holes(mask: np.ndarray, max_area: float) -> np.ndarray:
    """리본 안의 작은 구멍(교차점에서 생기는 뾰족한 틈 등)을 메운다. 영상 가장자리와 닿은 배경은 유지."""
    inv = (~mask).astype(np.uint8)
    n, cc, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    if n <= 1:
        return mask
    H, W = mask.shape
    x, y = stats[:, cv2.CC_STAT_LEFT], stats[:, cv2.CC_STAT_TOP]
    w, h = stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT]
    area = stats[:, cv2.CC_STAT_AREA]
    touches = (x == 0) | (y == 0) | (x + w >= W) | (y + h >= H)
    fill = (area < max_area) & ~touches
    fill[0] = False
    return mask | fill[cc]


def make_ribbon(boundary: np.ndarray, merge_px: float) -> np.ndarray:
    k = int(round(merge_px))
    if k < 1:
        return boundary
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    ribbon = cv2.morphologyEx(boundary.astype(np.uint8), cv2.MORPH_CLOSE, se).astype(bool)
    return fill_small_holes(ribbon, np.pi * (2.0 * k) ** 2)
