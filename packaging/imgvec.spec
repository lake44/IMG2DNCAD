# PyInstaller 설정 — 파이썬이 없는 PC에서도 도는 실행 파일을 만든다.
#   python -m pip install pyinstaller
#   python -m PyInstaller --noconfirm packaging/imgvec.spec
#   → dist/imgvec/imgvec.exe  (폴더째 복사해 쓴다. 폴더 방식이라 첫 실행이 한 파일(onefile)보다 빠르다)
#
# 담는 것: DNCAD 백업 템플릿(data/*.json)과 작업대 화면에 필요한 Tkinter.
# 빼는 것: 시험·문서 도구. (PyInstaller는 만든 PC의 운영체제용만 만든다 — 윈도우 실행 파일은 윈도우에서)
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 - SPECPATH는 PyInstaller가 넣어 준다

a = Analysis(                                    # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=collect_data_files("imgvec"),          # src/imgvec/data/dncad_template.json
    hiddenimports=["tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.simpledialog", "PIL.ImageTk"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "ruff", "matplotlib", "IPython", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)                                # noqa: F821
exe = EXE(                                       # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="imgvec",
    console=True,                                # 명령줄 도구다 — 콘솔 창을 띄운다
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="imgvec")   # noqa: F821
