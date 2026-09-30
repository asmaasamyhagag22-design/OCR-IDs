"""Minimal local demo for the frozen Fixed14-v3 research candidate.

Native Tkinter window only: no listening socket, no HTTP server, no external
request. The unchanged preprocessing pipeline runs once and v3 reads the exact
band it produced. Images, bands, and numbers stay in memory and are never
written to disk, printed, or logged. The existing v2 demo is untouched.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageTk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("DO_NOT_TRACK", "1")

from src.extraction import fixed14_v3_shadow as shadow  # noqa: E402
from src.extraction.fixed14_demo_adapter import decode_image  # noqa: E402

LOCAL_ONLY = True
LOCAL_URL = None
WINDOW_TITLE = "استخراج الرقم القومي"
FOOTER_TAG = "Fixed14-v3 · تجريبي"
RETAKE_TEXT = "أعد تصوير البطاقة كاملة وبوضوح"


@dataclass
class DemoState:
    image_rgb: np.ndarray | None = None
    national_id: str = ""
    processing: bool = False

    def clear(self) -> None:
        self.image_rgb = None
        self.national_id = ""
        self.processing = False


class V3DemoApp:
    BG, CARD, TEXT, MUTED, PRIMARY, ERROR = "#f5f7fb", "#ffffff", "#172133", "#8a93a3", "#1261a0", "#9c2f2f"

    def __init__(self, root, runtime=None) -> None:
        import tkinter as tk

        self.tk, self.root, self.runtime = tk, root, runtime
        self.state = DemoState()
        self._photos: dict[str, object] = {}
        self._closed = False

        root.title(WINDOW_TITLE)
        root.geometry("720x760")
        root.minsize(620, 680)
        root.configure(bg=self.BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        shell = tk.Frame(root, bg=self.BG, padx=36, pady=24)
        shell.pack(fill="both", expand=True)

        tk.Label(shell, text=WINDOW_TITLE, font=("Segoe UI", 20, "bold"), fg=self.TEXT, bg=self.BG,
                 anchor="e").pack(fill="x", pady=(0, 14))
        self.upload_button = tk.Button(shell, text="رفع صورة", command=self.choose_image,
                                       font=("Segoe UI", 12, "bold"), fg=self.PRIMARY, bg=self.CARD,
                                       relief="solid", bd=1, cursor="hand2", pady=10)
        self.upload_button.pack(fill="x")

        card = tk.Frame(shell, bg=self.CARD, highlightthickness=1, highlightbackground="#d8dee9")
        card.pack(fill="both", expand=True, pady=12)
        self.preview = tk.Label(card, text="", bg=self.CARD)
        self.preview.pack(fill="both", expand=True, padx=10, pady=10)

        self.extract_button = tk.Button(shell, text="استخراج", command=self.extract, state="disabled",
                                        font=("Segoe UI", 13, "bold"), fg="white", bg=self.PRIMARY,
                                        disabledforeground="#dce4ec", relief="flat", cursor="hand2", pady=10)
        self.extract_button.pack(fill="x")

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(shell, textvariable=self.status_var, font=("Segoe UI", 11, "bold"),
                                     fg=self.ERROR, bg=self.BG, anchor="e")
        self.status_label.pack(fill="x", pady=(8, 4))

        self.result_var = tk.StringVar(value="")
        tk.Entry(shell, textvariable=self.result_var, state="readonly", readonlybackground=self.CARD,
                 justify="center", font=("Consolas", 24, "bold"), fg=self.TEXT, relief="solid",
                 bd=1).pack(fill="x", ipady=10)
        self.band_preview = tk.Label(shell, text="", bg=self.BG)
        self.band_preview.pack(fill="x", pady=(6, 8))

        actions = tk.Frame(shell, bg=self.BG)
        actions.pack(fill="x")
        self.copy_button = tk.Button(actions, text="نسخ", command=self.copy_result, state="disabled",
                                     font=("Segoe UI", 11, "bold"), bg=self.CARD, fg=self.PRIMARY,
                                     relief="solid", bd=1, cursor="hand2", pady=7)
        self.copy_button.pack(side="right", fill="x", expand=True, padx=(6, 0))
        self.reset_button = tk.Button(actions, text="مسح", command=self.reset, font=("Segoe UI", 11),
                                      bg=self.CARD, fg=self.TEXT, relief="solid", bd=1, cursor="hand2", pady=7)
        self.reset_button.pack(side="left", fill="x", expand=True, padx=(0, 6))

        tk.Label(shell, text=FOOTER_TAG, font=("Segoe UI", 8), fg=self.MUTED, bg=self.BG).pack(pady=(12, 0))

    def choose_image(self) -> None:
        from tkinter import filedialog

        if self.state.processing:
            return
        path = filedialog.askopenfilename(filetypes=(("JPEG / PNG", "*.jpg *.jpeg *.png"), ("All files", "*.*")))
        if path:
            self.load_image(path)

    def load_image(self, path) -> bool:
        try:
            rgb = decode_image(path)
        except Exception:
            self.status_var.set("تعذر قراءة الصورة")
            return False
        self._clear_result()
        self.state.image_rgb = rgb
        self._show(self.preview, rgb, (620, 340), "source")
        self.extract_button.configure(state="normal")
        return True

    def extract(self) -> None:
        if self.state.processing or self.state.image_rgb is None:
            return
        self.state.processing = True
        for button in (self.extract_button, self.upload_button, self.reset_button):
            button.configure(state="disabled")
        self.extract_button.configure(text="…")
        threading.Thread(target=self._worker, args=(self.state.image_rgb.copy(),), daemon=True).start()

    def _worker(self, image) -> None:
        try:
            result = shadow.compare_image(image, self.runtime)
        except Exception:
            result = shadow.ShadowResult(shadow.MODEL_ERROR, "model_error", "", None, None)
        if not self._closed:
            self.root.after(0, self.show_result, result)

    def show_result(self, result: shadow.ShadowResult) -> None:
        self.state.processing = False
        self.extract_button.configure(text="استخراج")
        for button in (self.extract_button, self.upload_button, self.reset_button):
            button.configure(state="normal")
        value = result.v3_national_id if result.status == shadow.SHADOW_OK else ""
        self.state.national_id = value if len(value) == 14 else ""
        self.result_var.set(self.state.national_id)
        self.copy_button.configure(state="normal" if self.state.national_id else "disabled")
        if result.status == shadow.RETAKE_REQUIRED or not self.state.national_id:
            self.status_var.set(f"{shadow.RETAKE_REQUIRED} · {RETAKE_TEXT}")
            self.band_preview.configure(image="")
            self._photos.pop("band", None)
            return
        self.status_var.set("")
        self._show(self.band_preview, result.band, (576, 90), "band")

    def copy_result(self) -> None:
        if len(self.state.national_id) != 14:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.state.national_id)
        self.root.update_idletasks()

    def reset(self) -> None:
        if self.state.processing:
            return
        self.state.clear()
        self._clear_result()
        self.preview.configure(image="")
        self._photos.pop("source", None)
        self.extract_button.configure(state="disabled")
        try:
            self.root.clipboard_clear()
        except self.tk.TclError:
            pass

    def close(self) -> None:
        self._closed = True
        self.state.clear()
        self._photos.clear()
        self.root.destroy()

    def _clear_result(self) -> None:
        self.state.national_id = ""
        self.result_var.set("")
        self.status_var.set("")
        self.band_preview.configure(image="")
        self._photos.pop("band", None)
        self.copy_button.configure(state="disabled")

    def _show(self, widget, rgb, box, key) -> None:
        image = Image.fromarray(np.asarray(rgb, dtype=np.uint8))
        image.thumbnail(box, Image.Resampling.LANCZOS)
        self._photos[key] = ImageTk.PhotoImage(image)
        widget.configure(image=self._photos[key])


def create_app(root=None, runtime=None) -> V3DemoApp:
    import tkinter as tk

    return V3DemoApp(root or tk.Tk(), runtime or shadow.ShadowRuntime())


def main() -> int:
    try:
        shadow.verify_shadow_candidates()
        runtime = shadow.ShadowRuntime()
        runtime.ensure_shadow_loaded()
    except shadow.ShadowCandidateMismatch as exc:
        print(str(exc), file=sys.stderr)
        return 2
    app = create_app(runtime=runtime)
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
