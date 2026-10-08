"""작업대 화면 (5단계) — 그림 열기부터 DNCAD 백업까지 창 하나에서.

    imgvec device studio 그림.png

논리는 전부 studio.Workbench에 있고 여기는 화면만 입힌다. 그래서 화면 없는 곳에서도 같은 일을
`imgvec device run`(한 번에)이나 Workbench로 할 수 있다.

화면 구성
    위    모드(접속점·본체·축척 두 점) · 자동 인식 · 축척 · 장치 만들기 · 백업 · 저장
    가운데 그림 (휠=확대, 왼쪽 클릭=찍기, 오른쪽 클릭=지우기)
    오른쪽 접속점 목록 (더블클릭=이름 바꾸기, Delete=지우기)
    아래  상태 한 줄 (접속점 수 · 본체 · 축척 · 블랙박스 크기 · 검증 오류/경고)
"""
from __future__ import annotations

from pathlib import Path as FsPath

from .picker import draw_scene, next_name
from .studio import StudioError, Workbench

HELP = {
    "pin": "접속점: 단자 중심을 왼쪽 클릭 → 이름 입력 (오른쪽 클릭=가까운 점 지우기)",
    "body": "본체: 왼쪽 위에서 오른쪽 아래로 끌어서 사각형 지정",
    "ref": "축척: 실제 길이를 아는 두 점을 차례로 클릭 → mm 입력",
}


def run_studio(source: str | FsPath, *, name: str = "", out_dir: str | FsPath | None = None,
               preset: str = "auto", auto: bool = True,
               auto_close_ms: int | None = None, on_ready=None) -> None:
    """source: 그림 또는 *.spec.json. auto면 열자마자 자동 인식을 한 번 돌린다.
    auto_close_ms·on_ready는 화면 시험용 (picker.run_picker와 같은 뜻)."""
    import tkinter as tk
    import traceback
    from tkinter import filedialog, messagebox, simpledialog

    from PIL import Image

    wb = Workbench()
    src = FsPath(source)
    if src.suffix == ".json" or src.name.endswith(".spec.json"):
        wb.open_spec(src)
    else:
        wb.open_image(src, name=name, preset=preset)
    if wb.rgb is None:
        raise StudioError(f"그림을 찾지 못했습니다: {src}")
    base_dir = FsPath(out_dir) if out_dir else src.parent
    img = Image.fromarray(wb.rgb)

    root = tk.Tk()
    root.title(f"imgvec 작업대 — {src.name}")
    errors: list[BaseException] = []

    def on_callback_error(exc, val, tb) -> None:
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
    zoom = [min(1.0, 900.0 / max(img.size))]
    photo_ref: list = [None]
    drag: dict = {}
    ref_pts: list[tuple[float, float]] = []
    sel = [-1]

    # ------------------------------------------------------------------ 윗줄
    top = tk.Frame(root)
    top.pack(side="top", fill="x")
    for text, val in (("접속점(P)", "pin"), ("본체(B)", "body"), ("축척 두 점(R)", "ref")):
        tk.Radiobutton(top, text=text, variable=mode, value=val, command=lambda: redraw()).pack(side="left", padx=3)

    def do_auto() -> None:
        try:
            note = wb.auto_detect()
        except StudioError as e:
            messagebox.showwarning("자동 인식", str(e))
            return
        redraw()
        messagebox.showinfo("자동 인식", f"접속점 {note['pins']}개를 찾았습니다"
                            + (f" (간격 {note['pitch_px']:.0f}px)" if note.get("pitch_px") else "")
                            + "\n\n초안입니다. 빠진 자리는 찍고, 이름은 오른쪽 목록에서 고치세요.")

    def do_scale() -> None:
        v = simpledialog.askfloat("축척", "같은 줄 접속점 간격(mm)  예: 5.08\n"
                                  "(다른 방법은 '축척 두 점' 모드나 명세의 scale)", minvalue=0.0001, parent=root)
        if v:
            wb.set_scale(pin_pitch_mm=v)
            redraw()

    def do_build() -> None:
        try:
            res = wb.build()
        except (StudioError, Exception) as e:  # noqa: BLE001 - 어떤 실패든 사람에게 보여 준다
            messagebox.showerror("장치 만들기", str(e))
            redraw()
            return
        redraw()
        errs = [str(i) for i in res.issues if i.severity == "error"]
        warns = [str(i) for i in res.issues if i.severity == "warn"]
        box = res.layout["box"]
        msg = f"블랙박스 {box['w']} x {box['h']} 단위, 접속점 {len(res.layout['pins'])}개 배치"
        if errs:
            messagebox.showerror("검증 오류", msg + "\n\n" + "\n".join(errs[:8]))
        elif warns:
            messagebox.showwarning("경고", msg + "\n\n" + "\n".join(warns[:8]))
        else:
            messagebox.showinfo("장치 만들기", msg + "\n오류·경고 없음")

    def do_save() -> None:
        try:
            if wb.result is None:
                wb.build()
            base = base_dir / (wb.spec.name or src.stem)
            spec_path, csv_path = wb.save_spec(str(base) + ".spec.json")
            files = wb.save_outputs(base)
            messagebox.showinfo("저장했습니다", "\n".join([str(spec_path), str(csv_path)] + [str(v) for v in files.values()]))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("저장", str(e))

    def do_backup() -> None:
        try:
            if wb.result is None:
                wb.build()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("백업", str(e))
            return
        merge = messagebox.askyesno("DNCAD 백업", "이미 있는 프로젝트 백업에 더할까요?\n\n"
                                    "예 = 기존 .dncadbak을 고르고 거기에 더합니다 (원본은 읽기만)\n"
                                    "아니오 = 새 프로젝트로 복원할 백업을 만듭니다")
        base_bak = None
        if merge:
            picked = filedialog.askopenfilename(title="바탕이 될 백업", filetypes=[("DNCAD 백업", "*.dncadbak")])
            if not picked:
                return
            base_bak = picked
        out = filedialog.asksaveasfilename(title="저장할 백업", defaultextension=".dncadbak",
                                           initialfile=f"{wb.spec.name or src.stem}.dncadbak",
                                           filetypes=[("DNCAD 백업", "*.dncadbak")])
        if not out:
            return
        try:
            res = wb.export_backup(out, base=base_bak)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("백업", str(e))
            return
        note = "원본 프로젝트를 먼저 백업하고 새 프로젝트로 복원하세요." if base_bak else \
               "새 프로젝트로 복원하세요 (확인: 2026-10-08)."
        messagebox.showinfo("백업을 만들었습니다", f"{out}\n장치 {res.counts['devices']}개 · {res.counts['pages']}쪽\n\n{note}")

    for text, fn in (("자동 인식(A)", do_auto), ("축척…", do_scale), ("장치 만들기(G)", do_build),
                     ("저장(S)", do_save), ("DNCAD 백업…", do_backup)):
        tk.Button(top, text=text, command=fn).pack(side="left", padx=3)
    tk.Button(top, text="되돌리기 없음 · 지우기=오른쪽 클릭", state="disabled", relief="flat").pack(side="right")

    # ------------------------------------------------------------------ 가운데·오른쪽
    body_frame = tk.Frame(root)
    body_frame.pack(fill="both", expand=True)
    canvas = tk.Canvas(body_frame, bg="#888", highlightthickness=0, cursor="crosshair")
    vbar = tk.Scrollbar(body_frame, orient="vertical", command=canvas.yview)
    hbar = tk.Scrollbar(root, orient="horizontal", command=canvas.xview)
    canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)

    side = tk.Frame(body_frame, width=200)
    side.pack(side="right", fill="y")
    tk.Label(side, text="접속점 (더블클릭=이름)").pack(anchor="w")
    listbox = tk.Listbox(side, width=26, exportselection=False)
    listbox.pack(fill="both", expand=True)
    vbar.pack(side="right", fill="y")
    hbar.pack(side="bottom", fill="x")
    canvas.pack(side="left", fill="both", expand=True)
    tk.Label(root, textvariable=status, anchor="w", fg="#333").pack(side="bottom", fill="x")

    def refresh_list() -> None:
        listbox.delete(0, "end")
        for p in wb.spec.pins:
            listbox.insert("end", f"{p.name:<10} {p.side or '?':<6} {p.x_px:.0f},{p.y_px:.0f}")
        if 0 <= sel[0] < len(wb.spec.pins):
            listbox.selection_clear(0, "end")
            listbox.selection_set(sel[0])

    def redraw() -> None:
        photo_ref[0] = draw_scene(canvas, img, zoom[0], body=wb.spec.body_px, ref=wb.spec.ref,
                                  pins=[(p.x_px, p.y_px, p.name) for p in wb.spec.pins], highlight=sel[0])
        refresh_list()
        status.set(wb.status() + "   |   " + HELP[mode.get()])

    def to_img(ev) -> tuple[float, float]:
        return canvas.canvasx(ev.x) / zoom[0], canvas.canvasy(ev.y) / zoom[0]

    def on_press(ev) -> None:
        x, y = to_img(ev)
        if mode.get() == "pin":
            prev = wb.spec.pins[-1].name if wb.spec.pins else None
            got = simpledialog.askstring("접속점 이름", "이름:", initialvalue=next_name(prev), parent=root)
            if got and got.strip():
                wb.add_pin(x, y, got.strip())
                redraw()
        elif mode.get() == "body":
            drag["start"] = (x, y)
        else:
            ref_pts.append((x, y))
            if len(ref_pts) == 2:
                mm = simpledialog.askfloat("실제 거리", "두 점 사이의 실제 거리(mm):", minvalue=0.001, parent=root)
                if mm:
                    wb.set_scale(ref=(ref_pts[0][0], ref_pts[0][1], ref_pts[1][0], ref_pts[1][1], mm))
                ref_pts.clear()
                redraw()

    def on_release(ev) -> None:
        if mode.get() == "body" and "start" in drag:
            x, y = to_img(ev)
            x0, y0 = drag.pop("start")
            if abs(x - x0) > 3 and abs(y - y0) > 3:
                wb.set_body(x0, y0, x, y)
            redraw()

    def on_right(ev) -> None:
        x, y = to_img(ev)
        if wb.remove_pin_near(x, y, max_dist=15 / zoom[0]):
            sel[0] = -1
            redraw()

    def on_wheel(ev) -> None:
        zoom[0] = min(8.0, max(0.1, zoom[0] * (1.15 if ev.delta > 0 else 1 / 1.15)))
        redraw()

    def on_list_select(_ev=None) -> None:
        cur = listbox.curselection()
        sel[0] = cur[0] if cur else -1
        redraw()

    def on_list_rename(_ev=None) -> None:
        cur = listbox.curselection()
        if not cur:
            return
        i = cur[0]
        got = simpledialog.askstring("이름 바꾸기", "이름:", initialvalue=wb.spec.pins[i].name, parent=root)
        if got and got.strip():
            wb.spec.pins[i].name = got.strip()
            wb.result = None
            redraw()

    def on_list_delete(_ev=None) -> None:
        cur = listbox.curselection()
        if cur:
            wb.spec.pins.pop(cur[0])
            wb.result = None
            sel[0] = -1
            redraw()

    canvas.bind("<Button-1>", on_press)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<Button-3>", on_right)
    canvas.bind("<MouseWheel>", on_wheel)
    listbox.bind("<<ListboxSelect>>", on_list_select)
    listbox.bind("<Double-Button-1>", on_list_rename)
    listbox.bind("<Delete>", on_list_delete)
    root.bind("<Key-p>", lambda e: (mode.set("pin"), redraw()))
    root.bind("<Key-b>", lambda e: (mode.set("body"), redraw()))
    root.bind("<Key-r>", lambda e: (mode.set("ref"), redraw()))
    root.bind("<Key-a>", lambda e: do_auto())
    root.bind("<Key-g>", lambda e: do_build())
    root.bind("<Key-s>", lambda e: do_save())
    root.geometry("1180x840")

    if auto and not wb.spec.pins:
        try:
            wb.auto_detect()
        except StudioError:
            pass
    redraw()
    if on_ready is not None:
        root.after(100, lambda: on_ready({"root": root, "canvas": canvas, "listbox": listbox, "mode": mode,
                                          "wb": wb, "zoom": zoom, "auto": do_auto, "build": do_build,
                                          "save": do_save, "backup": do_backup, "redraw": redraw}))
    if auto_close_ms is not None:
        root.after(auto_close_ms, root.destroy)
    root.mainloop()
    if auto_close_ms is not None and errors:
        raise errors[0]
