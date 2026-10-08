"""꾸러미 (6단계): 판 번호와 담는 것이 어긋나지 않는지."""
import re
import tomllib
from pathlib import Path

import imgvec
from imgvec.dncad_backup import TEMPLATE_PATH, load_template

ROOT = Path(__file__).resolve().parent.parent


def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_the_same_in_both_places():
    assert pyproject()["project"]["version"] == imgvec.__version__


def test_console_command_points_at_the_cli():
    assert pyproject()["project"]["scripts"]["imgvec"] == "imgvec.cli:main"


def test_backup_template_ships_inside_the_package():
    """실행 파일·휠에 들어가야 하는 자료 — 꾸러미 안에 있고 읽힌다."""
    assert TEMPLATE_PATH.is_file()
    assert TEMPLATE_PATH.parent.name == "data" and TEMPLATE_PATH.parent.parent.name == "imgvec"
    assert "imgvec" in pyproject()["tool"]["setuptools"]["package-data"]
    assert load_template()["manifest"]["schemaVersion"] == "0070"


def test_locked_requirements_are_pinned_and_parseable():
    lines = [ln.strip() for ln in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "고정본이 비어 있습니다"
    for ln in lines:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][0-9A-Za-z_.\-]*", ln), f"고정되지 않은 줄: {ln}"


def test_declared_dependencies_are_all_installed():
    import importlib.metadata as md

    for dep in pyproject()["project"]["dependencies"]:
        name = re.split(r"[<>=!~ ]", dep, maxsplit=1)[0]
        md.version(name)          # 없으면 PackageNotFoundError


def test_pyinstaller_spec_and_entry_exist():
    spec = (ROOT / "packaging" / "imgvec.spec").read_text(encoding="utf-8")
    assert "collect_data_files(\"imgvec\")" in spec       # 백업 템플릿이 빠지면 kit·merge가 죽는다
    assert "tkinter" in spec                               # 작업대 화면
    entry = (ROOT / "packaging" / "entry.py").read_text(encoding="utf-8")
    assert "from imgvec.cli import main" in entry


def test_ci_workflows_run_lint_and_tests():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ruff check" in ci and "pytest" in ci
    rel = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "imgvec.spec" in rel and "python -m build" in rel
