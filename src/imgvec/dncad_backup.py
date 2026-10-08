"""DNCAD 백업(.dncadbak) 읽기·쓰기 — 장치 배치 명세를 DNCAD로 보내 보는 '시험용' 어댑터 (실험).

⚠ 미검증: 형식은 이 PC의 실제 백업(스키마 0070)에서 읽어 낸 것이다. 손으로 만든 백업을 DNCAD가
'백업 복원'으로 받아들이는지는 아직 확인하지 못했다. 새(빈) 프로젝트로 가져오는 시험에만 쓰고,
실제 프로젝트에는 쓰지 마라. 근거와 열린 질문은 docs/DNCAD-연동-조사서.md.

형식 (실측)
  ZIP = manifest.json + project.json + masters.json (모두 UTF-8, 공백 없는 JSON)
  manifest.json : format·formatVersion·schemaVersion·apiBuild·counts(표별 행 수)·masters·integrity(파일별 sha256)
  project.json  : {"rows": {표 이름: [행…]}}  표 25개, 쓰지 않는 표는 빈 목록
  masters.json  : globalDefinitions [{oldDefinitionId, key}] — 컴포넌트의 definition_id는 이 ID를 쓴다
장치 1개 = devices 1행 + components(블랙박스 1 + 접속점 n) + connection_points n
          + placements(1 + n) + graphic_anchors n
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .dncad_rules import DEFAULT_RULES, DncadRules
from .layout import SCHEMA as LAYOUT_SCHEMA
from .validate import Issue, has_errors

FORMAT = "dncad.backup"
TEMPLATE_PATH = Path(__file__).with_name("data") / "dncad_template.json"
MEMBERS = ("manifest.json", "project.json", "masters.json")
PAGE_W, PAGE_H = 1900, 1344          # A4 가로 297 x 210 mm (6.4 단위/mm)
MAX_MEMBER_BYTES = 256 * 1024 * 1024
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_BLACK_BOX, _DCP = "black_box", "device_connection_point"


class KitError(ValueError):
    pass


# --------------------------------------------------------------------------- 읽기
@dataclass
class Backup:
    manifest: dict
    rows: dict[str, list[dict]]
    masters: dict
    raw: dict[str, bytes] = field(repr=False, default_factory=dict)


def read_backup(source: bytes | str | Path) -> Backup:
    """백업 ZIP을 JSON으로만 읽는다 (실행·압축 해제 없음)."""
    buf: Any = io.BytesIO(bytes(source)) if isinstance(source, (bytes, bytearray)) else Path(source)
    try:
        with zipfile.ZipFile(buf) as z:
            missing = [n for n in MEMBERS if n not in z.namelist()]
            if missing:
                raise KitError(f"백업에 {', '.join(missing)}이(가) 없습니다")
            raw = {}
            for n in MEMBERS:
                if z.getinfo(n).file_size > MAX_MEMBER_BYTES:
                    raise KitError(f"{n}이(가) 너무 큽니다")
                raw[n] = z.read(n)
    except zipfile.BadZipFile as e:
        raise KitError(f"ZIP 파일이 아닙니다: {e}") from e
    try:
        manifest, project, masters = (json.loads(raw[n].decode("utf-8")) for n in MEMBERS)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise KitError(f"JSON을 읽을 수 없습니다: {e}") from e
    rows = project.get("rows") if isinstance(project, dict) else None
    if not isinstance(rows, dict) or not isinstance(manifest, dict) or not isinstance(masters, dict):
        raise KitError("project.json에 rows가 없거나 형식이 다릅니다")
    return Backup(manifest, rows, masters, raw)


@dataclass
class BBPin:
    designation: str
    x: int                       # 블랙박스 왼쪽 위 기준 상대 좌표(단위)
    y: int
    rotation: int
    mirrored: bool | None
    anchor: tuple[float, float]


@dataclass
class BlackBox:
    """블랙박스 배치 1개와 그 변 위에 놓인 장치 접속점. 한 장치가 블랙박스를 여러 개 가질 수 있다(실측 -TA02)."""
    tag: str | None
    device_id: str
    page_id: str | None
    x: int
    y: int
    w: int
    h: int
    pins: list[BBPin]
    problems: list[str]
    index: int = 0                                      # 장치 안에서 몇 번째 블랙박스인지
    count: int = 1                                      # 장치의 블랙박스 수
    unplaced: list[str] = field(default_factory=list)   # 쪽에 놓이지 않은 접속점 이름 (실측 -KZ01)
    device_cps: int = 0                                 # 장치 전체의 접속점 수 (다른 부품의 것 포함, 실측 -XD51)


def _group(items: Iterable[dict], key: str) -> dict[Any, list[dict]]:
    out: dict[Any, list[dict]] = {}
    for it in items:
        out.setdefault(it.get(key), []).append(it)
    return out


def _on_edge(pl: dict, box: BlackBox) -> bool:
    return (pl.get("page_id") == box.page_id and box.x <= pl["x"] <= box.x + box.w
            and pl["y"] in (box.y, box.y + box.h))


def read_black_boxes(b: Backup) -> list[BlackBox]:
    """블랙박스 컴포넌트를 가진 장치와 그 장치 접속점을 모은다. 접속점은 놓인 위치로 블랙박스에 짝짓는다."""
    key_of = {g.get("oldDefinitionId"): g.get("key") for g in b.masters.get("globalDefinitions", [])}
    rows = b.rows
    comps = _group(rows.get("components", []), "device_id")
    cps = _group(rows.get("connection_points", []), "component_id")
    pls = _group(rows.get("placements", []), "component_id")
    anchors = _group(rows.get("graphic_anchors", []), "placement_id")
    out: list[BlackBox] = []
    for dev in rows.get("devices", []):
        mine = comps.get(dev.get("id"), [])
        box_comps = [c for c in mine if key_of.get(c.get("definition_id")) == _BLACK_BOX]
        if not box_comps:
            continue
        device_cps = sum(len(cps.get(c["id"], [])) for c in mine)
        boxes: list[BlackBox] = []
        for bc in box_comps:
            for bp in pls.get(bc["id"], []):
                size = bp.get("size")
                if isinstance(size, dict) and all(isinstance(size.get(k), (int, float)) for k in ("w", "h")):
                    boxes.append(BlackBox(dev.get("tag"), dev["id"], bp.get("page_id"), int(bp["x"]), int(bp["y"]),
                                          int(size["w"]), int(size["h"]), [], [], device_cps=device_cps))
        if not boxes:
            out.append(BlackBox(dev.get("tag"), dev["id"], None, 0, 0, 0, 0, [], ["블랙박스의 배치가 없거나 크기(size)가 없습니다"], device_cps=device_cps))
            continue
        for i, bx in enumerate(boxes):
            bx.index, bx.count = i, len(boxes)
        for c in mine:
            if key_of.get(c.get("definition_id")) != _DCP:
                continue
            c_cps, c_pls = cps.get(c["id"], []), pls.get(c["id"], [])
            if len(c_cps) != 1:
                boxes[0].problems.append(f"접속점 부품 {str(c['id'])[:8]}의 접속점이 {len(c_cps)}개입니다 (1개여야 함)")
                continue
            name = str(c_cps[0].get("designation") or "")
            if not c_pls:
                boxes[0].unplaced.append(name)
                continue
            for pl in c_pls:
                owner = next((bx for bx in boxes if _on_edge(pl, bx)), None)
                if owner is None:
                    boxes[0].problems.append(f"접속점 '{name}'이 어느 블랙박스의 위·아래 변에도 놓여 있지 않습니다 ({pl['x']}, {pl['y']})")
                    continue
                an = anchors.get(pl["id"], [])
                if len(an) != 1:
                    owner.problems.append(f"접속점 '{name}': 그래픽 앵커가 {len(an)}개입니다 (1개여야 함)")
                anchor = (an[0].get("x", 0), an[0].get("y", 0)) if an else (0, 0)
                owner.pins.append(BBPin(name, int(pl["x"] - owner.x), int(pl["y"] - owner.y), int(pl.get("rotation") or 0),
                                        pl.get("mirrored"), anchor))
        out.extend(boxes)
    return out


def black_box_to_layout(bb: BlackBox, rules: DncadRules = DEFAULT_RULES) -> dict:
    """읽은 블랙박스를 dncad-device-layout/1.0 사전으로 (imgvec이 만든 배치 명세와 같은 모양)."""
    u = rules.units_per_mm
    side_of = {0: "top", 180: "bottom"}
    order = {0: 0, 180: 1}
    pins = [
        {"designation": p.designation, "side": side_of.get(p.rotation, f"rot{p.rotation}"),
         "slot": round(p.x / (rules.pin_pitch_mm * u)), "x": p.x, "y": p.y, "rotation": p.rotation,
         "mirrored": p.mirrored, "deviation_mm": 0.0, "group": ""}
        for p in sorted(bb.pins, key=lambda p: (order.get(p.rotation, 2), p.x))
    ]
    name = (bb.tag or "") + (f" ({bb.index + 1}/{bb.count})" if bb.count > 1 else "")
    return {
        "schema": LAYOUT_SCHEMA, "name": name, "units_per_mm": u,
        "origin": "box top-left, x right, y down",
        "box": {"w": bb.w, "h": bb.h, "w_mm": round(bb.w / u, 2), "h_mm": round(bb.h / u, 2)},
        "pins": pins, "skipped_pins": [], "issues": [],
    }


# --------------------------------------------------------------------------- 검사
def verify_backup(source: bytes | str | Path | Backup, rules: DncadRules = DEFAULT_RULES) -> list[Issue]:
    """형식(ZIP·해시·행 수·참조)과 장치 규칙(블랙박스·접속점)을 점검한다. 오류가 없어도 DNCAD가 받아들인다는 보증은 아니다."""
    out: list[Issue] = []

    def add(sev: str, code: str, msg: str, pin: str | None = None) -> None:
        out.append(Issue(sev, code, msg, pin))

    try:
        b = source if isinstance(source, Backup) else read_backup(source)
    except KitError as e:
        return [Issue("error", "E-ZIP", str(e))]
    m = b.manifest
    if m.get("format") != FORMAT or m.get("formatVersion") != 1:
        add("error", "E-FORMAT", f"형식이 다릅니다 (format={m.get('format')!r}, formatVersion={m.get('formatVersion')!r})")
    add("info", "I-SCHEMA", f"스키마 {m.get('schemaVersion')}, apiBuild {str(m.get('apiBuild'))[:8]}")
    integ = m.get("integrity") or {}
    for name in ("project.json", "masters.json"):
        if integ.get(name) != hashlib.sha256(b.raw.get(name, b"")).hexdigest():
            add("error", "E-HASH", f"{name}의 sha256이 manifest.integrity와 다릅니다")
    counts = m.get("counts") or {}
    for t in sorted(set(counts) | set(b.rows)):
        if counts.get(t) != len(b.rows.get(t, [])):
            add("error", "E-COUNT", f"표 {t}: manifest {counts.get(t)}행, 실제 {len(b.rows.get(t, []))}행")

    ids: dict[str, set] = {}
    for t, rows in b.rows.items():
        seen: set = set()
        for r in rows:
            rid = r.get("id") if isinstance(r, dict) else None
            if rid is None:
                continue
            if rid in seen:
                add("error", "E-ID-DUP", f"표 {t}에 같은 id가 있습니다: {rid}")
            seen.add(rid)
        ids[t] = seen
    defs = {g.get("oldDefinitionId") for g in b.masters.get("globalDefinitions", [])}
    refs = [("components", "device_id", ids.get("devices")), ("components", "definition_id", defs),
            ("connection_points", "component_id", ids.get("components")),
            ("placements", "component_id", ids.get("components")), ("placements", "page_id", ids.get("pages")),
            ("graphic_anchors", "placement_id", ids.get("placements")),
            ("graphic_anchors", "connection_point_id", ids.get("connection_points")),
            ("pages", "function_node_id", ids.get("structure_nodes")), ("pages", "location_node_id", ids.get("structure_nodes")),
            ("structure_nodes", "parent_node_id", ids.get("structure_nodes")), ("annotations", "page_id", ids.get("pages"))]
    for table, col, target in refs:
        bad = [r[col] for r in b.rows.get(table, []) if r.get(col) is not None and r[col] not in (target or set())]
        if bad:
            add("error", "E-FK", f"{table}.{col}가 존재하지 않는 대상을 가리킵니다 ({len(bad)}건, 예 {bad[0]})")
    keys = {g.get("key") for g in b.masters.get("globalDefinitions", [])}
    for need in (_BLACK_BOX, _DCP):
        if need not in keys:
            add("error", "E-DEF", f"masters.globalDefinitions에 '{need}'가 없습니다")

    boxes = read_black_boxes(b)
    if not boxes:
        add("warn", "W-NOBOX", "블랙박스 장치가 없습니다")
    for bb in boxes:
        _check_black_box(bb, rules, add)
    return out


def _check_black_box(bb: BlackBox, rules: DncadRules, add) -> None:
    where = f"[{bb.tag or bb.device_id[:8]}{f' {bb.index + 1}/{bb.count}' if bb.count > 1 else ''}] "
    for p in bb.problems:
        add("error", "E-STRUCT", where + p)
    if bb.unplaced:
        add("info", "I-UNPLACED", where + f"쪽에 놓이지 않은 접속점 {len(bb.unplaced)}개: {', '.join(bb.unplaced[:6])}")
    if not bb.w:
        return
    if not bb.pins and bb.device_cps == 0:
        add("error", "E-NOPIN", where + "접속점이 하나도 없습니다 (접속점 0개 블랙박스는 DNCAD 검증에서 막힙니다)")
    elif not bb.pins and bb.count == 1 and not bb.unplaced:
        add("info", "I-OTHERPINS", where + "장치 접속점은 없고 다른 부품의 접속점만 있습니다")
    g = rules.grid_mm * rules.units_per_mm
    if bb.x % g or bb.y % g:
        add("warn", "W-GRID", where + f"블랙박스 위치 ({bb.x}, {bb.y})가 격자 {g:g} 단위에 맞지 않습니다")
    names: dict[str, int] = {}
    per_side: dict[int, list[int]] = {}
    for p in bb.pins:
        if not p.designation.strip():
            add("error", "E-NAME", where + "이름이 빈 접속점이 있습니다")
        names[p.designation] = names.get(p.designation, 0) + 1
        if p.rotation not in (0, 180):
            add("warn", "W-SIDE", where + f"접속점 '{p.designation}'의 회전 {p.rotation}은 DNCAD에서 확인되지 않았습니다 (실측은 0·180)")
        elif p.y != (0 if p.rotation == 0 else bb.h):
            add("error", "E-EDGE", where + f"접속점 '{p.designation}'이 블랙박스 {'위' if p.rotation == 0 else '아래'} 변 위에 있지 않습니다 (상대 y={p.y})")
        if not 0 <= p.x <= bb.w:
            add("error", "E-OUT", where + f"접속점 '{p.designation}'이 블랙박스 폭(0~{bb.w}) 밖입니다 (상대 x={p.x})")
        if p.x % g:
            add("warn", "W-GRID", where + f"접속점 '{p.designation}'의 위치 x={p.x}가 격자에 맞지 않습니다")
        if tuple(p.anchor) != (0, 0):
            add("warn", "W-ANCHOR", where + f"접속점 '{p.designation}'의 앵커가 (0, 0)이 아닙니다: {p.anchor}")
        per_side.setdefault(p.rotation, []).append(p.x)
    for name, n in names.items():
        if n > 1:
            sev, code = ("warn", "W-DUP") if rules.allow_duplicate_designations else ("error", "E-DUP")
            add(sev, code, where + f"접속점 이름 '{name}'이 {n}번 중복됩니다")
    pitch = rules.pin_pitch_mm * rules.units_per_mm
    for rot, xs in per_side.items():
        xs.sort()
        if any(b - a < pitch for a, b in zip(xs, xs[1:])):
            add("warn", "W-PITCH", where + f"회전 {rot} 변의 접속점 간격이 {pitch:g} 단위 미만인 곳이 있습니다")


# --------------------------------------------------------------------------- 쓰기
@lru_cache(maxsize=1)
def _load_default_template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def load_template(path: str | Path | None = None) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else _load_default_template()


class _Ids:
    """시간순 UUIDv7과 created_at. 행마다 1 ms씩 늘려 만든 순서를 남긴다 (seed·now를 주면 결과가 같다)."""

    def __init__(self, seed: int | None, now: datetime | None) -> None:
        self.rng = random.Random(seed)
        self.base_ms = round((now or datetime.now(timezone.utc)).timestamp() * 1000)
        self.n = 0

    def new(self) -> tuple[str, str]:
        self.n += 1
        ms = self.base_ms + self.n
        rnd = self.rng.getrandbits(74)  # rand_a 12비트 + rand_b 62비트
        value = ((ms & (2**48 - 1)) << 80) | (0x7 << 76) | ((rnd >> 62) << 64) | (0b10 << 62) | (rnd & (2**62 - 1))
        return str(uuid.UUID(int=value)), (_EPOCH + timedelta(milliseconds=ms)).isoformat(timespec="microseconds")

    def exported_at(self) -> str:
        return (_EPOCH + timedelta(milliseconds=self.base_ms + self.n + 1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _Writer:
    """행을 만들어 표별 목록에 쌓는다. 열 이름과 순서는 실제 백업(스키마 0070)의 행과 같다."""

    def __init__(self, tables: list[str], ids: _Ids, defs: dict[str, str]) -> None:
        self.rows: dict[str, list[dict]] = {t: [] for t in tables}
        self.ids, self.defs = ids, defs

    def device(self, tag: str) -> str:
        did, ts = self.ids.new()
        self.rows["devices"].append({"id": did, "tag": tag, "created_at": ts, "naming_scheme": None, "cable_length_m": None,
                                     "function_node_id": None, "location_node_id": None, "parent_device_id": None, "main_component_id": None})
        return did

    def component(self, device_id: str, key: str, description: str | None = None) -> str:
        cid, ts = self.ids.new()
        self.rows["components"].append({"id": cid, "device_id": device_id, "created_at": ts, "properties": {}, "description": description,
                                        "definition_id": self.defs[key], "rated_current": None, "rated_voltage": None,
                                        "function_class": None, "definition_overrides": {}, "source_part_assignment_id": None})
        return cid

    def placement(self, page_id: str, component_id: str, x: int, y: int, rotation: int, *, size: dict | None = None,
                  main: bool | None = None, mirrored: bool | None = None) -> str:
        plid, ts = self.ids.new()
        self.rows["placements"].append({"x": x, "y": y, "id": plid, "role": "PRIMARY", "size": size, "view": None, "page_id": page_id,
                                        "mirrored": mirrored, "rotation": rotation, "pin_label": None, "created_at": ts,
                                        "visibility": True, "component_id": component_id, "plc_channels": None, "main_function": main,
                                        "text_position": None, "mirror_position": None, "potential_label_position": None})
        return plid

    def pin(self, device_id: str, page_id: str, box_x: int, box_y: int, p: dict) -> None:
        """장치 접속점 1개 = 부품 + 접속점 + 배치 + 그래픽 앵커(0, 0)."""
        cid = self.component(device_id, _DCP)
        cpid, ts = self.ids.new()
        self.rows["connection_points"].append({"id": cpid, "cp_type": "electrical", "ip_name": None, "ip_group": None, "created_at": ts,
                                               "designation": p["designation"], "component_id": cid, "potential_id": None})
        plid = self.placement(page_id, cid, box_x + p["x"], box_y + p["y"], p["rotation"], mirrored=p.get("mirrored"))
        aid, _ = self.ids.new()
        self.rows["graphic_anchors"].append({"x": 0, "y": 0, "id": aid, "placement_id": plid, "connection_point_id": cpid})


@dataclass
class KitDevice:
    tag: str
    name: str
    page: int                    # 0부터
    x: int
    y: int
    w: int
    h: int
    pins: int


@dataclass
class KitResult:
    data: bytes
    devices: list[KitDevice]
    counts: dict[str, int]
    warnings: list[str]
    issues: list[Issue]


def _check_layout(lay: dict, rules: DncadRules) -> None:
    name = lay.get("name") or "(이름 없음)"
    if lay.get("schema") != LAYOUT_SCHEMA:
        raise KitError(f"'{name}': 배치 명세 형식이 아닙니다 (schema={lay.get('schema')!r}, 기대 {LAYOUT_SCHEMA})")
    box = lay.get("box") or {}
    w, h = box.get("w"), box.get("h")
    if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
        raise KitError(f"'{name}': 블랙박스 크기가 올바르지 않습니다 ({w}x{h})")
    pins = lay.get("pins") or []
    if not pins:
        raise KitError(f"'{name}': 접속점이 하나도 없습니다 (접속점 0개 블랙박스는 DNCAD 검증에서 막힙니다)")
    seen: dict[str, int] = {}
    for p in pins:
        d = p.get("designation")
        if not isinstance(d, str) or not d.strip():
            raise KitError(f"'{name}': 이름이 빈 접속점이 있습니다")
        seen[d] = seen.get(d, 0) + 1
        side = p.get("side")
        if side not in ("top", "bottom") or p.get("rotation") != (0 if side == "top" else 180):
            raise KitError(f"'{name}': 접속점 '{d}'의 변·회전이 올바르지 않습니다 ({side}, {p.get('rotation')})")
        if p.get("y") != (0 if side == "top" else h) or not isinstance(p.get("x"), int) or not 0 <= p["x"] <= w:
            raise KitError(f"'{name}': 접속점 '{d}'가 블랙박스 {side} 변 위에 있지 않습니다 (x={p.get('x')}, y={p.get('y')})")
    dup = [d for d, n in seen.items() if n > 1]
    if dup and not rules.allow_duplicate_designations:
        raise KitError(f"'{name}': 접속점 이름이 중복됩니다: {', '.join(dup[:5])} (허용하려면 allow_duplicate_designations)")


def plan_pages(
    sizes: list[tuple[int, int]],
    *,
    page_w: int = PAGE_W, page_h: int = PAGE_H,
    margin_x: int = 160, margin_top: int = 192, margin_bottom: int = 192,
    gap_x: int = 96, gap_y: int = 64, label_h: int = 48, stub: int = 64,
) -> tuple[list[tuple[int, int, int]], list[str]]:
    """블랙박스 (w, h) 목록을 줄 단위로 쪽에 채워 넣는다. 반환: [(쪽, x, y)], 경고.
    각 블랙박스 위에 이름표(label_h), 아래에 접속점 줄기 여유(stub)를 둔다. 모든 좌표는 16 단위 격자 위."""
    x_max, y_max = page_w - margin_x, page_h - margin_bottom
    page, cx, cy, row_h = 0, margin_x, margin_top, 0
    out: list[tuple[int, int, int]] = []
    warns: list[str] = []
    for i, (w, h) in enumerate(sizes):
        if cx > margin_x and cx + w > x_max:        # 줄바꿈
            cy, cx, row_h = cy + row_h + gap_y, margin_x, 0
        block = label_h + h + stub
        if cy + block > y_max and cy > margin_top:  # 새 쪽
            page, cx, cy, row_h = page + 1, margin_x, margin_top, 0
        if margin_x + w > x_max:
            warns.append(f"{i + 1}번째 블랙박스 폭 {w}이(가) 쪽 폭을 넘어 쪽 밖으로 나갑니다")
        if cy + block > y_max:
            warns.append(f"{i + 1}번째 블랙박스 높이 {h}이(가) 쪽 높이를 넘습니다")
        out.append((page, cx, cy + label_h))
        cx += w + gap_x
        row_h = max(row_h, block)
    return out, warns


def build_backup(
    layouts: Iterable[dict],
    *,
    tags: list[str] | None = None,
    tag_prefix: str = "-ZZ",   # DNCAD가 블랙박스에 붙이는 기본 글자 (심볼 색인의 -ZZ001)
    project_name: str | None = None,
    labels: bool = True,
    describe: bool = False,
    seed: int | None = None,
    now: datetime | None = None,
    rules: DncadRules = DEFAULT_RULES,
    template: dict | None = None,
) -> KitResult:
    """배치 명세(dncad-device-layout/1.0)들을 담은 시험용 DNCAD 백업을 만든다. 새 ID·시각으로 처음부터 만든다.
    labels   블랙박스 위에 장치 이름 글자(주석 TEXT)를 둔다.
    describe 블랙박스 컴포넌트의 description에 장치 이름을 쓴다 (실제 백업은 모두 비워 둠 → 기본 꺼짐)."""
    tpl = template or load_template()
    lays = list(layouts)
    if not lays:
        raise KitError("장치가 하나도 없습니다")
    tag_list = list(tags) if tags else [f"{tag_prefix}{i:02d}" for i in range(1, len(lays) + 1)]
    if len(tag_list) != len(lays):
        raise KitError(f"태그 {len(tag_list)}개와 장치 {len(lays)}개의 수가 다릅니다")
    if len(set(tag_list)) != len(tag_list):
        raise KitError("태그가 중복됩니다")
    for lay in lays:
        _check_layout(lay, rules)
    defs = {g["key"]: g["oldDefinitionId"] for g in tpl["globalDefinitions"]}
    for need in (_BLACK_BOX, _DCP):
        if need not in defs:
            raise KitError(f"템플릿의 전역 정의에 '{need}'가 없습니다")
    ids = _Ids(seed, now)
    writer = _Writer(tpl["tables"], ids, defs)
    rows = writer.rows
    warns: list[str] = []

    # 프로젝트·구조·쪽: 템플릿 뼈대를 새 ID로 복사
    pid, pts = ids.new()
    rows["projects"].append({**tpl["project"], "id": pid, "created_at": pts, "name": project_name or tpl["project"]["name"]})
    node_map: dict[str, str] = {}
    for n in tpl["structure_nodes"]:
        nid, nts = ids.new()
        node_map[n["id"]] = nid
        rows["structure_nodes"].append({**n, "id": nid, "created_at": nts, "parent_node_id": node_map.get(n.get("parent_node_id"))})
    pos, plan_warns = plan_pages([(lay["box"]["w"], lay["box"]["h"]) for lay in lays])
    warns += plan_warns
    page_ids: list[str] = []
    for k in range(max(p for p, _, _ in pos) + 1):
        gid, gts = ids.new()
        page_ids.append(gid)
        pg = tpl["page"]
        rows["pages"].append({**pg, "id": gid, "created_at": gts, "name": f"{pg['name']} {k + 1}", "ordinal": k, "sheet_no": k + 1,
                              "function_node_id": node_map.get(pg.get("function_node_id")),
                              "location_node_id": node_map.get(pg.get("location_node_id"))})

    kit: list[KitDevice] = []
    z = 0
    for lay, tag, (pg_i, bx, by) in zip(lays, tag_list, pos):
        w, h = lay["box"]["w"], lay["box"]["h"]
        page_id = page_ids[pg_i]
        did = writer.device(tag)
        name = lay.get("name") or ""
        bb = writer.component(did, _BLACK_BOX, name if (describe and name) else None)
        writer.placement(page_id, bb, bx, by, 0, size={"h": h, "w": w}, main=True)
        for p in lay["pins"]:
            writer.pin(did, page_id, bx, by, p)
        if labels and name:
            tid, tts = ids.new()
            rows["annotations"].append({"x": bx + w // 2, "y": by - 32, "id": tid, "kind": "TEXT", "text": name,
                                        "style": {"font": "malgun", "align": "center", "color": "yellow"}, "page_id": page_id,
                                        "z_order": z, "geometry": None, "rotation": 0, "created_at": tts})
            z += 1
        kit.append(KitDevice(tag, name, pg_i, bx, by, w, h, len(lay["pins"])))

    masters = {"symbols": [], "globalDefinitions": tpl["globalDefinitions"], "parts": [], "forms": []}
    project_bytes = _dump({"rows": rows})
    masters_bytes = _dump(masters)
    tm = tpl["manifest"]
    manifest = {
        "format": tm["format"], "formatVersion": tm["formatVersion"], "schemaVersion": tm["schemaVersion"], "apiBuild": tm["apiBuild"],
        "exportedAt": ids.exported_at(),
        "source": {"projectName": rows["projects"][0]["name"]},
        "counts": {t: len(rows[t]) for t in tpl["tables"]},
        "masters": {"symbols": 0, "parts": 0, "forms": 0, "globalDefinitionKeys": sorted(defs), "tagLetterSet": tm["tagLetterSet"]},
        "integrity": {"project.json": hashlib.sha256(project_bytes).hexdigest(), "masters.json": hashlib.sha256(masters_bytes).hexdigest()},
        "warnings": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for member, payload in (("manifest.json", _dump(manifest)), ("project.json", project_bytes), ("masters.json", masters_bytes)):
            info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))   # 항상 같은 바이트가 나오게
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, payload)
    data = buf.getvalue()
    issues = verify_backup(data, rules)
    if has_errors(issues):
        raise KitError("만든 백업이 자체 점검을 통과하지 못했습니다: " + "; ".join(str(i) for i in issues if i.severity == "error"))
    return KitResult(data, kit, manifest["counts"], warns, issues)


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_backup(path: str | Path, layouts: Iterable[dict], **kw: Any) -> KitResult:
    res = build_backup(layouts, **kw)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(res.data)
    return res
