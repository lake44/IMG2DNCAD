"""imgvec — 그림 파일을 CAD용 벡터(선·호·원)로 변환하는 엔진."""
from .geom import ArcSeg, Circle, LineSeg, Path
from .params import Params
from .pipeline import VectorResult, convert_image

__version__ = "0.1.0"
__all__ = ["ArcSeg", "Circle", "LineSeg", "Path", "Params", "VectorResult", "convert_image", "__version__"]
