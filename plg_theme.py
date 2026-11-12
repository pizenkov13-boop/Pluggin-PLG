"""PLG design system — dark × Apple × studio. Tokens + ttk overrides + widgets."""

from __future__ import annotations

import math
import random
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from tkinter import ttk

from plg_paths import resource_path

# ── Color tokens ─────────────────────────────────────────────────────────────
BG = "#030303"
BG_ELEVATED = "#080808"
BG_CARD = "#0A0A0A"
BG_CARD_ALT = "#111111"
BG_INPUT = "#060606"
DIVIDER = "#1A1A1A"
BORDER = DIVIDER
BORDER_FOCUS = "#333333"

TEXT = "#FFFFFF"
TEXT_ON_ACCENT = "#030303"
TEXT_DIM = "#8A8A8A"
TEXT_FAINT = "#4A4A4A"
TEXT_MUTED = TEXT_DIM

ACCENT = "#FFFFFF"
ACCENT_HOVER = "#E8E8E8"
ACCENT_DIM = "#A8A8A8"

SUCCESS = "#3DDC84"
WARNING = "#FFB443"
ERROR = "#FF4D4D"
DANGER = ERROR
WARN = WARNING

# ── Spacing scale ────────────────────────────────────────────────────────────
SP_4 = 4
SP_8 = 8
SP_12 = 12
SP_16 = 16
SP_24 = 24
SP_32 = 32
SP_48 = 48

# ── Layout ───────────────────────────────────────────────────────────────────
WIN_W = 920
WIN_H = 640
WIN_MIN_W = 760
WIN_MIN_H = 560

CONTENT_W = 720
HEADER_H = 80
STATUS_BAR_H = 32

CARD_PAD = SP_24
CARD_GAP = SP_16
TEXTAREA_H = 112
TEXTAREA_PAD = SP_16
EYEBROW_MB = SP_12

BUTTON_H = 44
BUTTON_GAP = SP_12
BUTTON_HPAD = SP_32
REGEN_SIZE = 44

LOGO_H = 52
LOGO_PAD_LEFT = SP_24
HEADER_PAD_RIGHT = SP_24

WAVEFORM_H = 72
ERROR_TOP_H = 2
SPINNER_SIZE = 14
FL_DOT_SIZE = 7
FL_DOT_GAP = SP_8
STATUS_PAD = SP_16

FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_INPUT = ("Segoe UI", 13)
FONT_ERROR = ("Segoe UI", 11)
FONT_UI = ("Segoe UI", 10)

_HEADLINE_FAMILY = "Segoe UI Black"
_FONTS_LOADED = False


def ensure_fonts(root: tk.Misc) -> str:
    """Load Anton from assets/fonts; fallback Segoe UI Black."""
    global _HEADLINE_FAMILY, _FONTS_LOADED
    if _FONTS_LOADED:
        return _HEADLINE_FAMILY

    font_dir = resource_path("assets", "fonts")
    for name in ("Anton-Regular.ttf", "anton-regular.ttf", "Anton.ttf"):
        path = font_dir / name
        if not path.is_file():
            continue
        try:
            loaded = tkfont.Font(root=root, file=str(path), size=14)
            _HEADLINE_FAMILY = str(loaded.actual("family"))
            break
        except tk.TclError:
            continue

    _FONTS_LOADED = True
    return _HEADLINE_FAMILY


def headline_font(size: int = 13) -> tuple[str, int]:
    return (_HEADLINE_FAMILY, size)


def headline_font_sm(size: int = 11) -> tuple[str, int]:
    return (_HEADLINE_FAMILY, size)


def mono_font(*, size: int = 10) -> tuple[str, int]:
    try:
        tkfont.Font(family="Cascadia Mono", size=size)
        return ("Cascadia Mono", size)
    except tk.TclError:
        return ("Consolas", size)


def status_mono_font() -> tuple[str, int]:
    return mono_font(size=10)


# ── Signature motif ──────────────────────────────────────────────────────────
def diagonal_accent(
    canvas: tk.Canvas,
    x: float,
    y: float,
    h: float,
    *,
    color: str = ACCENT,
    stroke: int = 2,
    skew_deg: float = 12.0,
) -> int:
    dx = h * math.tan(math.radians(skew_deg))
    return canvas.create_line(x, y + h, x + dx, y, fill=color, width=stroke, capstyle="round")


class DiagonalSpinner(tk.Canvas):
    def __init__(self, parent: tk.Misc, *, bg: str = BG, **kwargs) -> None:
        super().__init__(
            parent,
            width=SPINNER_SIZE,
            height=SPINNER_SIZE,
            bg=bg,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self._phase = 0
        self._anim_id: str | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._phase = 0
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._anim_id:
            try:
                self.after_cancel(self._anim_id)
            except tk.TclError:
                pass
            self._anim_id = None
        self.delete("all")

    def _tick(self) -> None:
        if not self._running:
            return
        self.delete("all")
        diagonal_accent(self, 2 + (self._phase % 3) * 2, 1, SPINNER_SIZE - 2, color=TEXT)
        self._phase += 1
        self._anim_id = self.after(110, self._tick)


class WaveformStrip(tk.Canvas):
    """Studio waveform accent — fills space with musical texture."""

    def __init__(self, parent: tk.Misc, *, height: int = WAVEFORM_H, **kwargs) -> None:
        super().__init__(
            parent,
            height=height,
            bg=BG_CARD,
            highlightbackground=DIVIDER,
            highlightthickness=1,
            bd=0,
            **kwargs,
        )
        rng = random.Random(42)
        self._amps = [0.15 + rng.random() * 0.85 for _ in range(64)]
        self._busy = False
        self._phase = 0
        self._anim_id: str | None = None
        self.bind("<Configure>", lambda _e: self._paint())

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._animate()
        else:
            if self._anim_id:
                try:
                    self.after_cancel(self._anim_id)
                except tk.TclError:
                    pass
                self._anim_id = None
            self._paint()

    def _animate(self) -> None:
        if not self._busy:
            return
        self._phase = (self._phase + 1) % len(self._amps)
        self._paint(shift=self._phase)
        self._anim_id = self.after(90, self._animate)

    def _paint(self, shift: int = 0) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 200)
        h = max(self.winfo_height(), WAVEFORM_H)
        pad_x = SP_16
        inner_w = w - pad_x * 2
        n = len(self._amps)
        gap = 2
        bar_w = max(2, (inner_w - gap * (n - 1)) // n)
        mid = h // 2

        for i, amp in enumerate(self._amps):
            idx = (i + shift) % n
            level = amp * (1.0 if not self._busy else 0.55 + 0.45 * math.sin(idx * 0.4))
            bar_h = max(2, int((h - SP_24) * level * 0.5))
            x0 = pad_x + i * (bar_w + gap)
            tone = TEXT_FAINT if i % 7 else TEXT_DIM
            if self._busy and i % 5 == shift % 5:
                tone = SUCCESS
            self.create_rectangle(x0, mid - bar_h, x0 + bar_w, mid + bar_h, fill=tone, outline="")


# ── ttk theme ────────────────────────────────────────────────────────────────
def apply_theme(root: tk.Tk) -> ttk.Style:
    ensure_fonts(root)
    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, font=FONT_BODY, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=BG_CARD)

    _btn_pad = (BUTTON_HPAD, 11)
    _btn_font = headline_font_sm(11)

    style.configure(
        "Primary.TButton",
        background=ACCENT,
        foreground=TEXT_ON_ACCENT,
        font=_btn_font,
        padding=_btn_pad,
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        width=-1,
    )
    style.map(
        "Primary.TButton",
        background=[("active", ACCENT_HOVER), ("disabled", BG_CARD_ALT), ("!disabled", ACCENT)],
        foreground=[("disabled", TEXT_FAINT), ("!disabled", TEXT_ON_ACCENT)],
    )

    style.configure(
        "PrimaryDisabled.TButton",
        background=BG_CARD_ALT,
        foreground=TEXT_FAINT,
        font=_btn_font,
        padding=_btn_pad,
        borderwidth=1,
        relief="solid",
        focusthickness=0,
    )
    style.map(
        "PrimaryDisabled.TButton",
        background=[("active", BG_CARD_ALT), ("disabled", BG_CARD_ALT)],
        foreground=[("disabled", TEXT_FAINT)],
        bordercolor=[("disabled", DIVIDER)],
    )

    style.configure(
        "Secondary.TButton",
        background=BG,
        foreground=TEXT,
        font=_btn_font,
        padding=_btn_pad,
        borderwidth=1,
        relief="solid",
        focusthickness=0,
    )
    style.map(
        "Secondary.TButton",
        background=[("active", BG_CARD), ("disabled", BG), ("!disabled", BG)],
        foreground=[("disabled", TEXT_FAINT), ("!disabled", TEXT)],
        bordercolor=[("active", TEXT), ("disabled", DIVIDER), ("!disabled", DIVIDER)],
    )

    style.configure(
        "SecondarySuccess.TButton",
        background=BG,
        foreground=SUCCESS,
        font=_btn_font,
        padding=_btn_pad,
        borderwidth=1,
        relief="solid",
        focusthickness=0,
    )
    style.map(
        "SecondarySuccess.TButton",
        background=[("active", BG_CARD), ("disabled", BG)],
        foreground=[("disabled", TEXT_FAINT), ("!disabled", SUCCESS)],
        bordercolor=[("active", SUCCESS), ("disabled", DIVIDER), ("!disabled", SUCCESS)],
    )

    style.configure(
        "Ghost.TButton",
        background=BG,
        foreground=TEXT_DIM,
        font=FONT_SMALL,
        padding=(SP_12, SP_8),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
    )
    style.map(
        "Ghost.TButton",
        background=[("active", BG), ("disabled", BG)],
        foreground=[("active", TEXT), ("disabled", TEXT_FAINT), ("!disabled", TEXT_DIM)],
    )

    style.configure(
        "TEntry",
        fieldbackground=BG_INPUT,
        foreground=TEXT,
        bordercolor=DIVIDER,
        lightcolor=DIVIDER,
        darkcolor=DIVIDER,
        insertcolor=TEXT,
        padding=(SP_8, SP_8),
    )
    style.map("TEntry", fieldbackground=[("focus", BG_INPUT)], bordercolor=[("focus", BORDER_FOCUS)])

    style.configure(
        "TCombobox",
        fieldbackground=BG_INPUT,
        background=BG_CARD,
        foreground=TEXT,
        bordercolor=DIVIDER,
        arrowcolor=TEXT_DIM,
        padding=(SP_8, SP_8),
    )

    style.configure(
        "TCheckbutton",
        background=BG,
        foreground=TEXT_DIM,
        font=FONT_SMALL,
        focuscolor=BG,
    )
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", TEXT)])

    style.configure(
        "TProgressbar",
        background=ACCENT,
        troughcolor=BG_INPUT,
        bordercolor=DIVIDER,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
    )

    style.layout("TButton", [
        ("Button.border", {"sticky": "nswe", "children": [
            ("Button.focus", {"sticky": "nswe", "children": [
                ("Button.padding", {"sticky": "nswe", "children": [
                    ("Button.label", {"sticky": "nswe"}),
                ]}),
            ]}),
        ]}),
    ])

    return style


# ── Widget helpers ───────────────────────────────────────────────────────────
def make_card(parent: tk.Misc, **kwargs) -> tk.Frame:
    return tk.Frame(
        parent,
        bg=BG_CARD,
        highlightbackground=DIVIDER,
        highlightthickness=1,
        **kwargs,
    )


def eyebrow_label(parent: tk.Misc, text: str, *, bg: str = BG_CARD) -> tk.Label:
    return tk.Label(
        parent,
        text=text.upper(),
        bg=bg,
        fg=TEXT_DIM,
        font=headline_font_sm(10),
    )


def ghost_link(parent: tk.Misc, text: str, command: Callable[[], None], *, bg: str = BG) -> tk.Label:
    link = tk.Label(parent, text=text, bg=bg, fg=TEXT_DIM, font=FONT_SMALL, cursor="hand2")

    def on_enter(_e: tk.Event) -> None:
        link.configure(fg=TEXT)

    def on_leave(_e: tk.Event) -> None:
        link.configure(fg=TEXT_DIM)

    link.bind("<Enter>", on_enter)
    link.bind("<Leave>", on_leave)
    link.bind("<Button-1>", lambda _e: command())
    return link


def status_pill(parent: tk.Misc, text: str, *, color: str = TEXT_DIM, bg: str = BG) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=bg,
        fg=color,
        font=FONT_SMALL,
        padx=SP_8,
        pady=SP_4,
        highlightbackground=DIVIDER,
        highlightthickness=1,
    )


def first_run_banner(
    parent: tk.Misc,
    message: str,
    *,
    on_action: Callable[[], None],
    on_dismiss: Callable[[], None],
) -> tk.Frame:
    wrap = tk.Frame(parent, bg=BG_CARD, highlightbackground=DIVIDER, highlightthickness=1)
    accent = tk.Canvas(wrap, width=SP_12, bg=BG_CARD, highlightthickness=0, bd=0)
    accent.pack(side="left", fill="y")
    accent.bind("<Configure>", lambda _e: _paint_banner_accent(accent))

    body = tk.Frame(wrap, bg=BG_CARD)
    body.pack(side="left", fill="x", expand=True, padx=(SP_8, SP_12), pady=SP_12)
    tk.Label(body, text=message, bg=BG_CARD, fg=TEXT, font=FONT_SMALL, wraplength=CONTENT_W - SP_48, justify="left").pack(
        anchor="w"
    )
    link = tk.Label(body, text="Set up now →", bg=BG_CARD, fg=TEXT, font=FONT_UI, cursor="hand2")
    link.pack(anchor="w", pady=(SP_4, 0))
    link.bind("<Button-1>", lambda _e: on_action())

    dismiss = tk.Label(wrap, text="×", bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 14), cursor="hand2", padx=SP_12)
    dismiss.pack(side="right")
    dismiss.bind("<Button-1>", lambda _e: on_dismiss())
    return wrap


def _paint_banner_accent(canvas: tk.Canvas) -> None:
    canvas.delete("all")
    h = canvas.winfo_height() or SP_32
    diagonal_accent(canvas, 4, 4, max(h - SP_8, SP_16), stroke=3)


def bind_text_shortcuts(
    widget: tk.Text,
    *,
    before_edit: Callable[[], None] | None = None,
) -> None:
    def copy(_event: tk.Event | None = None) -> str:
        try:
            if widget.tag_ranges("sel"):
                widget.clipboard_clear()
                widget.clipboard_append(widget.get("sel.first", "sel.last"))
        except tk.TclError:
            pass
        return "break"

    def cut(_event: tk.Event | None = None) -> str:
        if before_edit:
            before_edit()
        copy()
        try:
            if widget.tag_ranges("sel"):
                widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        return "break"

    def paste(_event: tk.Event | None = None) -> str:
        if before_edit:
            before_edit()
        try:
            text = widget.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            if widget.tag_ranges("sel"):
                widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        widget.insert("insert", text)
        return "break"

    def select_all(_event: tk.Event | None = None) -> str:
        widget.tag_add("sel", "1.0", "end-1c")
        return "break"

    for seq in ("<Control-c>", "<Control-C>", "<Control-x>", "<Control-X>", "<Control-v>", "<Control-V>",
                "<Shift-Insert>", "<Control-a>", "<Control-A>"):
        widget.bind(seq, {"<Control-c>": copy, "<Control-C>": copy, "<Control-x>": cut, "<Control-X>": cut,
                          "<Control-v>": paste, "<Control-V>": paste, "<Shift-Insert>": paste,
                          "<Control-a>": select_all, "<Control-A>": select_all}[seq])

    menu = tk.Menu(widget, tearoff=0, bg=BG_CARD, fg=TEXT, activebackground=DIVIDER, activeforeground=TEXT)
    menu.add_command(label="Cut", command=cut)
    menu.add_command(label="Copy", command=copy)
    menu.add_command(label="Paste", command=paste)
    menu.add_command(label="Select All", command=select_all)
    widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))


def bind_entry_shortcuts(widget: tk.Entry | ttk.Entry) -> None:
    def paste(_event: tk.Event | None = None) -> str:
        try:
            text = widget.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        widget.insert("insert", text)
        return "break"

    def copy(_event: tk.Event | None = None) -> str:
        try:
            widget.clipboard_clear()
            widget.clipboard_append(widget.selection_get())
        except tk.TclError:
            pass
        return "break"

    def cut(_event: tk.Event | None = None) -> str:
        copy()
        try:
            widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        return "break"

    def select_all(_event: tk.Event | None = None) -> str:
        widget.selection_range(0, "end")
        widget.icursor("end")
        return "break"

    for seq, fn in (("<Control-v>", paste), ("<Control-V>", paste), ("<Shift-Insert>", paste),
                    ("<Control-c>", copy), ("<Control-C>", copy), ("<Control-x>", cut), ("<Control-X>", cut),
                    ("<Control-a>", select_all), ("<Control-A>", select_all)):
        widget.bind(seq, fn)
