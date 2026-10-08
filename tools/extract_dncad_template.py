"""DNCAD 백업(.dncadbak)에서 최소 뼈대를 뽑아 src/imgvec/data/dncad_template.json 으로 저장한다.

백업 형식(실측): ZIP = manifest.json + project.json({rows: 표별 행 목록}) + masters.json.
템플릿에는 (1) 백업 머리글(schemaVersion·apiBuild) (2) 전역 정의 키↔ID 대응(black_box 등)
(3) 빈 프로젝트의 뼈대 행(프로젝트·구조 노드·쪽 1개)과 (4) 표 이름 목록만 담는다. 장치·배선 등 도면 내용은 담지 않는다.

사용: python tools/extract_dncad_template.py <백업.dncadbak>   (장치가 들어 있는 최신 백업이어야 전역 정의가 채워져 있다)
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "imgvec" / "data" / "dncad_template.json"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    z = zipfile.ZipFile(sys.argv[1])
    man = json.loads(z.read("manifest.json").decode("utf-8"))
    rows = json.loads(z.read("project.json").decode("utf-8"))["rows"]
    masters = json.loads(z.read("masters.json").decode("utf-8"))
    gd = masters.get("globalDefinitions") or []
    keys = {g["key"] for g in gd}
    need = {"black_box", "device_connection_point"}
    if not need <= keys:
        print(f"오류: 전역 정의에 {sorted(need - keys)}가 없습니다. 장치가 들어 있는 백업을 쓰세요")
        return 1
    page = rows["pages"][0]
    template = {
        "about": "imgvec이 만든 DNCAD 백업 템플릿 (도면 내용 없음). tools/extract_dncad_template.py로 생성",
        "manifest": {
            "format": man["format"],
            "formatVersion": man["formatVersion"],
            "schemaVersion": man["schemaVersion"],
            "apiBuild": man["apiBuild"],
            "tagLetterSet": man.get("masters", {}).get("tagLetterSet", "practice"),
        },
        "tables": list(rows.keys()),
        "globalDefinitions": gd,
        "project": {**rows["projects"][0], "name": "imgvec 시험 프로젝트"},
        "structure_nodes": rows["structure_nodes"],
        "page": {**page, "name": "imgvec 시험"},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(template, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {OUT}  (스키마 {man['schemaVersion']}, 표 {len(rows)}개, 전역 정의 {len(gd)}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
