"""PyInstaller 실행 파일의 들머리. 콘솔 한글이 깨지지 않게 UTF-8로 맞춘 뒤 CLI를 부른다."""
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from imgvec.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
