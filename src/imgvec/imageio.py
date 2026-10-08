"""이미지 읽기·쓰기. 경로에 한글이 있어도 동작하도록 OpenCV의 파일 입출력은 쓰지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PDF_SUFFIXES = {".pdf"}


@dataclass
class LoadedImage:
    rgb: np.ndarray            # (H, W, 3) uint8
    dpi: float | None          # 파일에 기록된 DPI (신뢰하기 어려울 수 있음)
    source: str


def load_image(path: str | Path, page: int = 0, pdf_dpi: float = 300.0) -> LoadedImage:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"파일이 없습니다: {p}")
    suf = p.suffix.lower()
    if suf in PDF_SUFFIXES:
        try:
            import pypdfium2 as pdfium
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("PDF를 읽으려면 pypdfium2가 필요합니다: pip install pypdfium2") from e
        pdf = pdfium.PdfDocument(str(p))
        if not 0 <= page < len(pdf):
            raise ValueError(f"PDF 페이지 범위를 벗어났습니다: {page} (총 {len(pdf)}쪽)")
        im = pdf[page].render(scale=pdf_dpi / 72.0).to_pil().convert("RGB")
        return LoadedImage(np.asarray(im), float(pdf_dpi), str(p))

    im = Image.open(p)
    try:
        im.seek(page)
    except EOFError as e:
        raise ValueError(f"이미지에 {page}번째 프레임이 없습니다") from e
    dpi = None
    info_dpi = im.info.get("dpi")
    if info_dpi:
        try:
            v = float(info_dpi[0])
            dpi = v if v > 1 else None
        except (TypeError, ValueError, IndexError):
            dpi = None
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, rgba)
    return LoadedImage(np.asarray(im.convert("RGB")), dpi, str(p))


def save_png(path: str | Path, rgb: np.ndarray) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(rgb)).save(str(path))
