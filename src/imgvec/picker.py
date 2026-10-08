"""화면 클릭으로 접속점·본체·축척을 지정하는 간단한 도구 (Tkinter).

`imgvec device pick 그림.png` → 클릭한 내용을 장치 명세(JSON)와 접속점 CSV로 저장한다.
논리(PickerState)와 화면(PickerApp)을 나눠, 논리는 화면 없이 시험한다.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path as FsPath

from .device import DeviceSpec
from .pins import PinSpec, write_pins_csv


def next_name(prev: str | None) -> str:
    """직전 이름에서 다음 이름을 짐작한다: A1→A2, S11→S12, 13→14, PE→PE1."""
    if not prev:
        return "1"
    m = re.match(r"^(.*?)(\d+)(\D*)$", prev)
    if not m:
        return prev + "1"
    head, num, tail = m.groups()
    return f"{head}{int(num) + 1:0{len(num)}d}{tail}"


@dataclass
class PickerState:
    """클릭으로 모은 값. 좌표는 모두 원본 이미지 px(연속 좌표)."""

    pins: list[dict] = field(default_factory=list)       # name, x_px, y_px, group
    body: tuple[float, float, float, float] | None = None
    ref: tuple[float, float, float, float, float] | None = None
    group: str = ""
    _undo: list[tuple[str, object]] = field(default_factory=list)

    def add_pin(self, x: float, y: float, name: str) -> None:
        self.pins.append({"name": name, "x_px": round(x, 2), "y_px": round(y, 2), "group": self.group})
        self._undo.append(("pin", None))

    def remove_nearest(self, x: float, y: float, max_dist: float) -> bool:
        if not self.pins:
            return False
        k = min(range(len(self.pins)), key=lambda i: math.hypot(self.pins[i]["x_px"] - x, self.pins[i]["y_px"] - y))
        if math.hypot(self.pins[k]["x_px"] - x, self.pins[k]["y_px"] - y) > max_dist:
            return False
        self._undo.append(("del", (k, self.pins.pop(k))))
        return True

    def set_body(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self._undo.append(("body", self.body))
        self.body = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def set_ref(self, x1: float, y1: float, x2: float, y2: float, mm: float) -> None:
        self._undo.append(("ref", self.ref))
        self.ref = (x1, y1, x2, y2, mm)

    def undo(self) -> str | None:
        if not self._undo:
            return None
        kind, data = self._undo.pop()
        if kind == "pin":
            self.pins.pop()
        elif kind == "del":
            k, pin = data  # type: ignore[misc]
            self.pins.insert(k, pin)
        elif kind == "body":
            self.body = data  # type: ignore[assignment]
        elif kind == "ref":
            self.ref = data  # type: ignore[assignment]
        return kind

    def last_name(self) -> str | None:
        return self.pins[-1]["name"] if self.pins else None

    def to_spec(self, name: str, image: str | None, preset: str = "auto") -> DeviceSpec:
        specs = [PinSpec(p["name"], None, x_px=p["x_px"], y_px=p["y_px"], group=p.get("group", ""), line=i) for i, p in enumerate(self.pins, start=1)]
        return DeviceSpec(name=name, image=image, ref=self.ref, body_px=self.body, preset=preset, pins=specs)


def save_picked(state: PickerState, spec_path: str | FsPath, name: str, image: str | None) -> tuple[FsPath, FsPath]:
    spec_path = FsPath(spec_path)
    spec = state.to_spec(name, image)
    spec.dump(spec_path)
    base = spec_path.name[: -len(".spec.json")] if spec_path.name.endswith(".spec.json") else spec_path.stem
    csv_path = spec_path.with_name(base + ".pins.csv")   # 그림.spec.json → 그림.pins.csv (샘플 세트와 같은 짝 이름)
    write_pins_csv(csv_path, spec.pins)
    return spec_path, csv_path


# ======================================================================== 화면


def run_picker(
    image_path: str | FsPath,
    spec_path: str | FsPath,
    name: str = "",
    existing: DeviceSpec | None = None,
    *,
    auto_close_ms: int | None = None,
    on_ready=None,
) -> None:
    """auto_close_ms·on_ready는 화면 시험용: 창을 투명하게 띄우고, 100 ms 뒤 on_ready(핸들 사전)를 부른 뒤 auto_close_ms에 닫는다.
    그 사이 이벤트 처리 중 생긴 예외는 창을 닫은 뒤 다시 일으킨다."""
    import tkinter as tk
    import traceback
    from tkinter import messagebox, simpledialog

    from PIL import Image, ImageTk

    from .imageio import load_image

    img = Image.fromarray(load_image(image_path).rgb)
    W, H = img.size
    state = PickerState()
    if existing:
        state.body = existing.body_px
        state.ref = existing.ref
        state.pins = [{"name": s.name, "x_px": s.x_px, "y_px": s.y_px, "group": s.group} for s in existing.pins if s.x_px is not None and s.y_px is not None]

    root = tk.Tk()
    root.title(f"imgvec 접속점 지정 — {FsPath(image_path).name}")
    errors: list[BaseException] = []

    def on_callback_error(exc, val, tb) -> None:   # 기본 동작(표준 오류에 출력)에 더해 기록한다
        traceback.print_exception(exc, val, tb)
        errors.append(val)

    root.report_callback_exception = on_callback_error
    if auto_close_ms is not None:
        try:
            root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
    mode = tk.StringVar(value="pin")
    status = tk.StringVar()
    zoom = [min(1.0, 900.0 / max(W, H))]
    photo_ref: list = [None]
    drag: dict = {}

    top = tk.Frame(root)
    top.pack(side="top", fill="x")
    for text, val in (("접속점(P)", "pin"), ("본체 사각형(B)", "body"), ("축척 두 점(R)", "ref")):
        tk.Radiobutton(top, text=text, variable=mode, value=val, command=lambda: status.set(HELP[mode.get()])).pack(side="left", padx=4)
    tk.Label(top, text="그룹:").pack(side="left", padx=(12, 2))
    group_var = tk.StringVar()
    tk.Entry(top, textvariable=group_var, width=8).pack(side="left")
    tk.Button(top, text="되돌리기(U)", command=lambda: (state.undo(), redraw())).pack(side="left", padx=8)
    tk.Button(top, text="저장(S)", command=lambda: save()).pack(side="left")
    tk.Label(root, textvariable=status, anchor="w", fg="#333").pack(side="bottom", fill="x")

    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)
    canvas = tk.Canvas(frame, bg="#888", highlightthickness=0, cursor="crosshair")
    hbar = tk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
    vbar = tk.Scrollbar(frame, orient="vertical", command=canvas.yview)
    canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
    hbar.pack(side="bottom", fill="x")
    vbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    HELP = {
        "pin": "접속점: 단자 중심을 클릭 → 이름 입력 (오른쪽 클릭=가까운 점 삭제). 휠=확대·축소",
        "body": "본체: 왼쪽 위에서 오른쪽 아래로 끌어서 사각형 지정",
        "ref": "축척: 실제 길이를 아는 두 점을 차례로 클릭 → mm 입력",
    }
    status.set(HELP["pin"])
    ref_pts: list[tuple[float, float]] = []

    def to_img(ev) -> tuple[float, float]:
        return canvas.canvasx(ev.x) / zoom[0], canvas.canvasy(ev.y) / zoom[0]

    def redraw() -> None:
        z = zoom[0]
        canvas.delete("all")
        photo_ref[0] = ImageTk.PhotoImage(img.resize((max(1, int(W * z)), max(1, int(H * z))), Image.LANCZOS))
        canvas.create_image(0, 0, image=photo_ref[0], anchor="nw")
        canvas.configure(scrollregion=(0, 0, W * z, H * z))
        if state.body:
            x0, y0, x1, y1 = (v * z for v in state.body)
            canvas.create_rectangle(x0, y0, x1, y1, outline="#0a0", width=2)
        if state.ref:
            a, b, c, d, mm = state.ref
            canvas.create_line(a * z, b * z, c * z, d * z, fill="#06f", width=2)
            canvas.create_text((a + c) / 2 * z, (b + d) / 2 * z - 8, text=f"{mm:g} mm", fill="#06f")
        for p in state.pins:
            x, y = p["x_px"] * z, p["y_px"] * z
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5, outline="#d00", width=2)
            canvas.create_text(x + 8, y - 8, text=p["name"], fill="#d00", anchor="sw", font=("Malgun Gothic", 10, "bold"))
        status.set(f"접속점 {len(state.pins)}개 · 본체 {'지정됨' if state.body else '미지정'} · 축척 {'지정됨' if state.ref else '미지정'}   |   {HELP[mode.get()]}")

    def on_press(ev) -> None:
        x, y = to_img(ev)
        if mode.get() == "pin":
            state.group = group_var.get().strip()
            name_in = simpledialog.askstring("접속점 이름", "이름:", initialvalue=next_name(state.last_name()), parent=root)
            if name_in and name_in.strip():
                state.add_pin(x, y, name_in.strip())
                redraw()
        elif mode.get() == "body":
            drag["start"] = (x, y)
        else:
            ref_pts.append((x, y))
            if len(ref_pts) == 2:
                mm = simpledialog.askfloat("실제 거리", "두 점 사이의 실제 거리(mm):", minvalue=0.001, parent=root)
                if mm:
                    state.set_ref(ref_pts[0][0], ref_pts[0][1], ref_pts[1][0], ref_pts[1][1], mm)
                ref_pts.clear()
                redraw()

    def on_release(ev) -> None:
        if mode.get() == "body" and "start" in drag:
            x, y = to_img(ev)
            x0, y0 = drag.pop("start")
            if abs(x - x0) > 3 and abs(y - y0) > 3:
                state.set_body(x0, y0, x, y)
            redraw()

    def on_right(ev) -> None:
        x, y = to_img(ev)
        if state.remove_nearest(x, y, max_dist=15 / zoom[0]):
            redraw()

    def on_wheel(ev) -> None:
        zoom[0] = min(8.0, max(0.1, zoom[0] * (1.15 if ev.delta > 0 else 1 / 1.15)))
        redraw()

    def save() -> None:
        if not state.pins:
            messagebox.showwarning("저장", "접속점이 하나도 없습니다")
            return
        s, c = save_picked(state, spec_path, name or FsPath(image_path).stem, FsPath(image_path).name)
        messagebox.showinfo("저장했습니다", f"{s}\n{c}")

    canvas.bind("<Button-1>", on_press)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<Button-3>", on_right)
    canvas.bind("<MouseWheel>", on_wheel)
    root.bind("<Key-u>", lambda e: (state.undo(), redraw()))
    root.bind("<Key-s>", lambda e: save())
    root.bind("<Key-p>", lambda e: (mode.set("pin"), redraw()))
    root.bind("<Key-b>", lambda e: (mode.set("body"), redraw()))
    root.bind("<Key-r>", lambda e: (mode.set("ref"), redraw()))
    root.geometry("1000x820")
    redraw()
    if on_ready is not None:
        root.after(100, lambda: on_ready({"root": root, "canvas": canvas, "mode": mode, "group": group_var, "state": state, "save": save, "zoom": zoom}))
    if auto_close_ms is not None:
        root.after(auto_close_ms, root.destroy)
    root.mainloop()
    if auto_close_ms is not None and errors:
        raise errors[0]
