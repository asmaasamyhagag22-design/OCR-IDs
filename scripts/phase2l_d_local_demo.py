"""Launch the local Arabic-first Fixed14-v2 research demo.

Tkinter is the intentional no-new-dependency fallback.  The application opens
a native local window and creates no listening socket, public link, or upload
service.
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

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("DO_NOT_TRACK", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

from src.extraction.fixed14_demo_adapter import (  # noqa: E402
    MODEL_LABEL,
    SUCCESS,
    FrozenCandidateMismatch,
    decode_image,
    extract_national_id_from_image,
    verify_frozen_candidate,
)


LOCAL_ONLY = True
BIND_HOST = "127.0.0.1"
LOCAL_URL = None  # Native Tkinter fallback: deliberately no HTTP server.


@dataclass
class DemoState:
    image_rgb: np.ndarray | None = None
    national_id: str = ""
    processing: bool = False

    def clear(self) -> None:
        self.image_rgb = None
        self.national_id = ""
        self.processing = False


class Fixed14DemoApp:
    """Minimal native GUI; OCR remains in the reusable adapter module."""

    BG = "#f5f7fb"
    CARD = "#ffffff"
    PRIMARY = "#1261a0"
    TEXT = "#172133"
    MUTED = "#5c6678"
    ERROR = "#9c2f2f"

    def __init__(self, root) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.state = DemoState()
        self._preview_photo = None
        self._closed = False

        root.title("استخراج الرقم القومي — Fixed14-v2 Research Demo")
        root.geometry("760x800")
        root.minsize(680, 720)
        root.configure(bg=self.BG)
        root.protocol("WM_DELETE_WINDOW", self.close)

        shell = tk.Frame(root, bg=self.BG, padx=42, pady=28)
        shell.pack(fill="both", expand=True)

        tk.Label(
            shell,
            text="استخراج الرقم القومي من البطاقة",
            font=("Segoe UI", 22, "bold"),
            fg=self.TEXT,
            bg=self.BG,
            anchor="e",
            justify="right",
        ).pack(fill="x", pady=(0, 18))

        self.upload_button = tk.Button(
            shell,
            text="رفع صورة البطاقة — JPEG أو PNG",
            command=self.choose_image,
            font=("Segoe UI", 13, "bold"),
            fg=self.PRIMARY,
            bg=self.CARD,
            activebackground="#eaf3fb",
            relief="solid",
            bd=1,
            cursor="hand2",
            pady=12,
        )
        self.upload_button.pack(fill="x")

        preview_card = tk.Frame(shell, bg=self.CARD, highlightthickness=1, highlightbackground="#d8dee9")
        preview_card.pack(fill="both", expand=True, pady=16)
        self.preview = tk.Label(
            preview_card,
            text="معاينة الصورة",
            font=("Segoe UI", 14),
            fg=self.MUTED,
            bg=self.CARD,
            anchor="center",
        )
        self.preview.pack(fill="both", expand=True, padx=12, pady=12)

        self.extract_button = tk.Button(
            shell,
            text="استخراج الرقم القومي",
            command=self.extract,
            state="disabled",
            font=("Segoe UI", 14, "bold"),
            fg="white",
            bg=self.PRIMARY,
            disabledforeground="#dce4ec",
            activebackground="#0f4f83",
            relief="flat",
            cursor="hand2",
            pady=12,
        )
        self.extract_button.pack(fill="x")

        self.progress = ttk.Progressbar(shell, mode="indeterminate")
        self.status_var = tk.StringVar(value="اختر صورة بطاقة للبدء.")
        self.status_label = tk.Label(
            shell,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            fg=self.MUTED,
            bg=self.BG,
            anchor="e",
            justify="right",
        )
        self.status_label.pack(fill="x", pady=(8, 14))

        tk.Label(
            shell,
            text="الرقم القومي",
            font=("Segoe UI", 12, "bold"),
            fg=self.TEXT,
            bg=self.BG,
            anchor="e",
        ).pack(fill="x")

        self.result_var = tk.StringVar(value="")
        self.result_entry = tk.Entry(
            shell,
            textvariable=self.result_var,
            state="readonly",
            readonlybackground=self.CARD,
            justify="center",
            font=("Consolas", 23, "bold"),
            fg=self.TEXT,
            relief="solid",
            bd=1,
        )
        self.result_entry.pack(fill="x", ipady=10, pady=(6, 5))

        tk.Label(
            shell,
            text=MODEL_LABEL,
            font=("Segoe UI", 9, "bold"),
            fg=self.PRIMARY,
            bg=self.BG,
            anchor="e",
        ).pack(fill="x", pady=(0, 10))

        actions = tk.Frame(shell, bg=self.BG)
        actions.pack(fill="x")
        self.copy_button = tk.Button(
            actions,
            text="نسخ الرقم",
            command=self.copy_result,
            state="disabled",
            font=("Segoe UI", 11, "bold"),
            bg=self.CARD,
            fg=self.PRIMARY,
            relief="solid",
            bd=1,
            cursor="hand2",
            pady=8,
        )
        self.copy_button.pack(side="right", fill="x", expand=True, padx=(6, 0))
        self.reset_button = tk.Button(
            actions,
            text="مسح وتجربة صورة أخرى",
            command=self.reset,
            font=("Segoe UI", 11),
            bg=self.CARD,
            fg=self.TEXT,
            relief="solid",
            bd=1,
            cursor="hand2",
            pady=8,
        )
        self.reset_button.pack(side="left", fill="x", expand=True, padx=(0, 6))

        tk.Label(
            shell,
            text="Research Demo — يُرجى مراجعة الرقم قبل استخدامه",
            font=("Segoe UI", 10),
            fg=self.MUTED,
            bg=self.BG,
        ).pack(fill="x", pady=(18, 0))

    def choose_image(self) -> None:
        from tkinter import filedialog

        if self.state.processing:
            return
        path = filedialog.askopenfilename(
            title="اختر صورة البطاقة",
            filetypes=(("JPEG and PNG", "*.jpg *.jpeg *.png"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            rgb = decode_image(path)
        except Exception:
            self.status_var.set("تعذر قراءة الصورة. استخدم ملف JPEG أو PNG صالحاً.")
            self.status_label.configure(fg=self.ERROR)
            return
        self.state.image_rgb = rgb
        self.state.national_id = ""
        self.result_var.set("")
        self.copy_button.configure(state="disabled")
        self._show_preview(rgb)
        self.extract_button.configure(state="normal")
        self.status_var.set("الصورة جاهزة للاستخراج.")
        self.status_label.configure(fg=self.MUTED)

    def _show_preview(self, rgb: np.ndarray) -> None:
        image = Image.fromarray(rgb)
        image.thumbnail((620, 360), Image.Resampling.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(image)
        self.preview.configure(image=self._preview_photo, text="")

    def extract(self) -> None:
        if self.state.processing or self.state.image_rgb is None:
            return
        self.state.processing = True
        self.extract_button.configure(state="disabled", text="جارٍ الاستخراج…")
        self.upload_button.configure(state="disabled")
        self.reset_button.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        self.progress.pack(fill="x", before=self.status_label, pady=(8, 0))
        self.progress.start(12)
        self.status_var.set("جارٍ تجهيز البطاقة وتشغيل النموذج المحلي…")
        self.status_label.configure(fg=self.MUTED)
        image = self.state.image_rgb.copy()
        threading.Thread(target=self._extract_worker, args=(image,), daemon=True).start()

    def _extract_worker(self, image: np.ndarray) -> None:
        result = extract_national_id_from_image(image)
        if not self._closed:
            self.root.after(0, self._finish_extract, result)

    def _finish_extract(self, result) -> None:
        self.state.processing = False
        self.progress.stop()
        self.progress.pack_forget()
        self.extract_button.configure(state="normal", text="استخراج الرقم القومي")
        self.upload_button.configure(state="normal")
        self.reset_button.configure(state="normal")
        if result.status == SUCCESS:
            self.state.national_id = result.national_id
            self.result_var.set(result.national_id)
            self.copy_button.configure(state="normal")
            self.status_label.configure(fg="#267247")
        else:
            self.state.national_id = ""
            self.result_var.set("")
            self.copy_button.configure(state="disabled")
            self.status_label.configure(fg=self.ERROR)
        self.status_var.set(result.message)

    def copy_result(self) -> None:
        if len(self.state.national_id) != 14:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.state.national_id)
        self.root.update_idletasks()
        self.status_var.set("تم نسخ الرقم. يُرجى مراجعته قبل الاستخدام.")

    def reset(self) -> None:
        if self.state.processing:
            return
        self.state.clear()
        self._preview_photo = None
        self.preview.configure(image="", text="معاينة الصورة")
        self.result_var.set("")
        self.extract_button.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        self.status_var.set("اختر صورة بطاقة للبدء.")
        self.status_label.configure(fg=self.MUTED)
        try:
            self.root.clipboard_clear()
        except self.tk.TclError:
            pass

    def close(self) -> None:
        self._closed = True
        self.state.clear()
        self._preview_photo = None
        self.root.destroy()


def create_app(root=None) -> Fixed14DemoApp:
    import tkinter as tk

    root = root or tk.Tk()
    return Fixed14DemoApp(root)


def main() -> int:
    try:
        verify_frozen_candidate()
    except FrozenCandidateMismatch:
        print("FIXED14_DEMO_FROZEN_CANDIDATE_MISMATCH", file=sys.stderr)
        return 2
    app = create_app()
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
