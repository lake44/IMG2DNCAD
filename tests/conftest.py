import numpy as np
import pytest
from PIL import Image

from imgvec.synth import make_device


@pytest.fixture(scope="session")
def device_clean():
    return make_device(seed=1)


@pytest.fixture(scope="session")
def device_jpeg():
    return make_device(seed=4, blur=0.8, jpeg_quality=55)


@pytest.fixture(scope="session")
def device_coarse():
    return make_device(seed=5, mm_per_px=0.25)


@pytest.fixture()
def png_path(tmp_path, device_clean):
    p = tmp_path / "dev.png"
    Image.fromarray(device_clean.rgb).save(p)
    return p


def pixel_path(points: np.ndarray, noise: float = 0.25, seed: int = 0) -> np.ndarray:
    """골격 픽셀 경로 흉내: 점에 작은 잡음을 더하고 픽셀 중심 격자로 반올림하지는 않는다."""
    rng = np.random.default_rng(seed)
    return points + rng.normal(0.0, noise, points.shape)
