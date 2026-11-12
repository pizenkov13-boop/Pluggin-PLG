"""PLG Pluggin' — desktop application."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pathlib import Path

from app_config import (
    get_auto_open_fl,
    get_samples_dir,
    has_api_key,
    load_environment,
    settings_snapshot,
    write_env_file,
)
from backend_core import (
    PROJECT_DIR,
    ensure_midi_library,
    ensure_samples_library,
    resolve_midi_dir,
    resolve_samples_dir,
    run_pipeline,
)
from beat_quota import BeatQuotaExceeded, consume_beat, ensure_can_consume_beat, format_quota_label, get_quota_snapshot
from fl_launch import open_beat_in_fl
from fl_setup import install_all, is_fl_bridge_ready
from library_catalog import save_catalog, scan_library
from llm_client import format_llm_error, provider_label
from logo_loader import load_logo_photo
from starter_kit import ensure_starter_kit
from theme_install import install_themes
from plg_theme import (
    ACCENT,
    BG,
    BG_CARD,
    BG_INPUT,
    BUTTON_GAP,
    BUTTON_H,
    CARD_GAP,
    CARD_PAD,
    CONTENT_W,
    DiagonalSpinner,
    DIVIDER,
    ERROR,
    ERROR_TOP_H,
    EYEBROW_MB,
    first_run_banner,
    FL_DOT_GAP,
    FL_DOT_SIZE,
    FONT_ERROR,
    FONT_INPUT,
    FONT_SMALL,
    ghost_link,
    HEADER_H,
    HEADER_PAD_RIGHT,
    LOGO_H,
    LOGO_PAD_LEFT,
    REGEN_SIZE,
    SP_4,
    SP_8,
    STATUS_BAR_H,
    STATUS_PAD,
    SUCCESS,
    TEXT,
    TEXT_DIM,
    TEXT_FAINT,
    TEXTAREA_H,
    TEXTAREA_PAD,
    WaveformStrip,
    WIN_H,
    WIN_MIN_H,
    WIN_MIN_W,
    WIN_W,
    WAVEFORM_H,
    apply_theme,
    bind_entry_shortcuts,
    bind_text_shortcuts,
    eyebrow_label,
    headline_font,
    make_card,
    status_mono_font,
)

PROMPT_PLACEHOLDER = "trap beat, dark melody, hard 808s..."
PATTERN_JSON = PROJECT_DIR / "output_pattern.json"
SESSION_LOG = PROJECT_DIR / "plg_session.log"


def setup_gui_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | PLG GUI | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(SESSION_LOG, encoding="utf-8"),
        ],
        force=True,
    )


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: PlgApp, *, first_run: bool = False) -> None:
        super().__init__(parent)
        self.parent = parent
        self.first_run = first_run
        self.title("PLG Setup" if first_run else "PLG Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        snapshot = settings_snapshot()
        self._provider = tk.StringVar(value=snapshot["provider"])
        self._openrouter_key = tk.StringVar(value=snapshot.get("openrouter_key", ""))
        self._gemini_key = tk.StringVar(value=snapshot["gemini_key"])
        self._anthropic_key = tk.StringVar(value=snapshot["anthropic_key"])
        self._samples_dir = tk.StringVar(value=snapshot["samples_dir"])
        self._auto_open_fl = tk.BooleanVar(value=snapshot["auto_open_fl"] == "true")

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if first_run:
            self.wait_window()
        else:
            self.geometry("520x420")

    def _build(self) -> None:
        wrap = tk.Frame(self, bg=BG, padx=24, pady=20)
        wrap.pack(fill="both", expand=True)

        title = "Welcome to PLG" if self.first_run else "Settings"
        tk.Label(wrap, text=title, bg=BG, fg=TEXT, font=headline_font(18)).pack(anchor="w")
        tk.Label(
            wrap,
            text="API keys stay on your PC in .env — not in the cloud.",
            bg=BG,
            fg=TEXT_DIM,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 16))

        self._field(wrap, "AI provider", ttk.Combobox(
            wrap,
            textvariable=self._provider,
            values=("openrouter", "gemini", "anthropic"),
            state="readonly",
            width=42,
        ))
        openrouter_entry = ttk.Entry(wrap, textvariable=self._openrouter_key, show="*", width=44)
        self._field(wrap, "OpenRouter / aitunnel API key", openrouter_entry)
        bind_entry_shortcuts(openrouter_entry)

        gemini_entry = ttk.Entry(wrap, textvariable=self._gemini_key, show="*", width=44)
        self._field(wrap, "Gemini API key (legacy)", gemini_entry)
        bind_entry_shortcuts(gemini_entry)

        anthropic_entry = ttk.Entry(wrap, textvariable=self._anthropic_key, show="*", width=44)
        self._field(wrap, "Anthropic API key (release)", anthropic_entry)
        bind_entry_shortcuts(anthropic_entry)

        samples_row = tk.Frame(wrap, bg=BG)
        samples_row.pack(fill="x", pady=(0, 10))
        tk.Label(samples_row, text="Sample library folder", bg=BG, fg=TEXT_DIM, font=("Segoe UI", 9)).pack(anchor="w")
        entry_row = tk.Frame(samples_row, bg=BG)
        entry_row.pack(fill="x", pady=(4, 0))
        samples_entry = ttk.Entry(entry_row, textvariable=self._samples_dir, width=34)
        samples_entry.pack(side="left", fill="x", expand=True)
        bind_entry_shortcuts(samples_entry)
        ttk.Button(entry_row, text="Browse", command=self._browse_samples).pack(side="left", padx=(8, 0))

        auto_row = tk.Frame(wrap, bg=BG)
        auto_row.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(
            auto_row,
            text="Open FL Studio automatically after CREATE BEAT",
            variable=self._auto_open_fl,
        ).pack(anchor="w")

        btn_row = tk.Frame(wrap, bg=BG)
        btn_row.pack(fill="x", pady=(16, 0))
        ttk.Button(btn_row, text="Save", style="Primary.TButton", command=self._save).pack(side="left")
        if not self.first_run:
            ttk.Button(btn_row, text="Cancel", style="Ghost.TButton", command=self.destroy).pack(side="left", padx=(8, 0))

    def _field(self, parent: tk.Frame, label: str, widget: tk.Widget) -> None:
        tk.Label(parent, text=label, bg=BG, fg=TEXT_DIM, font=("Segoe UI", 9)).pack(anchor="w")
        widget.pack(anchor="w", pady=(4, 10))

    def _browse_samples(self) -> None:
        path = filedialog.askdirectory(initialdir=self._samples_dir.get() or str(PROJECT_DIR))
        if path:
            self._samples_dir.set(path)

    def _save(self) -> None:
        provider = self._provider.get().strip().lower() or "openrouter"
        openrouter_key = self._openrouter_key.get().strip()
        gemini_key = self._gemini_key.get().strip()
        anthropic_key = self._anthropic_key.get().strip()

        if provider in ("openrouter", "or") and not openrouter_key:
            messagebox.showwarning("PLG", "Add OpenRouter/aitunnel API key.", parent=self)
            return
        if provider in ("anthropic", "claude") and not anthropic_key:
            messagebox.showwarning("PLG", "Add Anthropic API key or switch provider to openrouter.", parent=self)
            return
        if provider in ("gemini", "google") and not gemini_key:
            messagebox.showwarning("PLG", "Add Gemini API key or switch provider to openrouter.", parent=self)
            return

        write_env_file(
            {
                "PLG_LLM_PROVIDER": provider,
                "OPENROUTER_API_KEY": openrouter_key,
                "GEMINI_API_KEY": gemini_key,
                "ANTHROPIC_API_KEY": anthropic_key,
                "PLG_SAMPLES_DIR": self._samples_dir.get().strip(),
                "PLG_AUTO_OPEN_FL": "true" if self._auto_open_fl.get() else "false",
            }
        )
        self.parent._refresh_after_settings()
        if self.first_run:
            self.grab_release()
            self.destroy()
        else:
            messagebox.showinfo("PLG", "Settings saved.", parent=self)
            self.destroy()

    def _on_close(self) -> None:
        if self.first_run and not has_api_key():
            if messagebox.askyesno(
                "PLG",
                "API key is required to generate beats.\nQuit PLG?",
                parent=self,
            ):
                self.parent.destroy()
            return
        self.destroy()


class PlgApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        load_environment()
        setup_gui_logging()
        ensure_starter_kit()
        self.title("PLG — Pluggin'")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.minsize(WIN_MIN_W, WIN_MIN_H)
        self._logo_image: tk.PhotoImage | None = None
        self._busy = False
        self._gen_started_at: float | None = None
        self._gen_tick_id: str | None = None
        self._ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._beat_ready = False
        self._last_prompt = ""
        self._placeholder_active = True
        self._first_run_dismissed = False
        self._quota = tk.StringVar(value="")
        self._fl_bridge_label = tk.StringVar(value="FL Bridge: —")
        self._provider_badge = tk.StringVar(value=provider_label())
        self._empty_lib_warned = False

        apply_theme(self)
        self._build_menu()
        self._build_ui()
        self._boot_message()
        self.after(100, self._process_ui_queue)

    def _process_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "success":
                    self._on_success(payload)  # type: ignore[arg-type]
                elif kind == "error":
                    self._on_error(payload)  # type: ignore[arg-type]
                elif kind == "fl_done":
                    self._on_fl_opened(payload)  # type: ignore[arg-type]
                elif kind == "fl_error":
                    self._stop_busy()
                    exc = payload
                    if isinstance(exc, FileNotFoundError):
                        self.set_status("Create a beat first", tone="error")
                    else:
                        self.set_status("FL error", tone="error")
                    messagebox.showwarning("PLG", str(exc))
        except queue.Empty:
            pass
        self.after(100, self._process_ui_queue)

    def _build_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0, bg=BG_CARD, fg=TEXT, activebackground=DIVIDER, activeforeground=TEXT)
        self.config(menu=menu)

        file_menu = tk.Menu(menu, tearoff=0, bg=BG_CARD, fg=TEXT, activebackground=DIVIDER, activeforeground=TEXT)
        file_menu.add_command(label="Library", command=self.open_samples)
        file_menu.add_command(label="Import Kit Folder...", command=self.import_kit)
        file_menu.add_separator()
        file_menu.add_command(label="Build Guide", command=self.open_guide)
        file_menu.add_command(label="MIDI Folder", command=self.open_midi)
        file_menu.add_command(label="Output Folder", command=self.open_output_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menu.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menu, tearoff=0, bg=BG_CARD, fg=TEXT, activebackground=DIVIDER, activeforeground=TEXT)
        tools_menu.add_command(label="Settings...", command=self.open_settings)
        tools_menu.add_command(label="Install FL Scripts", command=self.install_fl_scripts)
        tools_menu.add_command(label="Install FL Themes", command=self.install_fl_themes)
        tools_menu.add_separator()
        menu.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menu, tearoff=0, bg=BG_CARD, fg=TEXT, activebackground=DIVIDER, activeforeground=TEXT)
        help_menu.add_command(label="Quick Start", command=self.show_quick_start)
        help_menu.add_command(label="Where is everything? (START_HERE)", command=self.open_start_here)
        help_menu.add_command(label="FL Bridge Guide", command=self.open_fl_bridge_doc)
        help_menu.add_command(label="Don FL Workflow", command=self.open_fl_workflows_doc)
        menu.add_cascade(label="Help", menu=help_menu)

    def _build_ui(self) -> None:
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)

        self._build_status(shell)
        self._build_header(shell)
        self._build_main(shell)

        self.bind("<Control-Return>", lambda _e: self.on_create())
        self.bind("<Control-KP_Enter>", lambda _e: self.on_create())

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=BG, height=HEADER_H)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        tk.Frame(header, bg=DIVIDER, height=1).pack(side="bottom", fill="x")

        row = tk.Frame(header, bg=BG)
        row.pack(fill="both", expand=True, padx=(LOGO_PAD_LEFT, HEADER_PAD_RIGHT))

        logo_cell = tk.Frame(row, bg=BG)
        logo_cell.pack(side="left", fill="y", pady=(HEADER_H - LOGO_H) // 2)

        self._logo_image = load_logo_photo(self, max_height=LOGO_H)
        if self._logo_image is not None:
            tk.Label(logo_cell, image=self._logo_image, bg=BG, borderwidth=0).pack(anchor="w")
        else:
            tk.Label(logo_cell, text="PLG", bg=BG, fg=TEXT, font=headline_font(28)).pack(anchor="w")

        right = tk.Frame(row, bg=BG)
        right.pack(side="right", fill="y", pady=SP_8)

        tk.Label(
            right,
            textvariable=self._provider_badge,
            bg=BG,
            fg=TEXT_DIM,
            font=FONT_SMALL,
        ).pack(anchor="e")

        fl_row = tk.Frame(right, bg=BG)
        fl_row.pack(anchor="e", pady=(SP_4, 0))
        self._header_fl_dot = tk.Canvas(fl_row, width=FL_DOT_SIZE, height=FL_DOT_SIZE, bg=BG, highlightthickness=0, bd=0)
        self._header_fl_dot.pack(side="left")
        tk.Label(
            fl_row,
            textvariable=self._fl_bridge_label,
            bg=BG,
            fg=TEXT_DIM,
            font=FONT_SMALL,
        ).pack(side="left", padx=(FL_DOT_GAP, 0))

        ghost_link(right, "Settings", self.open_settings, bg=BG).pack(anchor="e", pady=(SP_8, 0))

    def _first_run_message(self) -> str:
        parts: list[str] = []
        if not has_api_key():
            parts.append("Add your Gemini API key to generate beats.")
        if not is_fl_bridge_ready(PROJECT_DIR):
            parts.append("Install FL scripts so OPEN IN FL works.")
        return " ".join(parts)

    def _needs_first_run_banner(self) -> bool:
        if self._first_run_dismissed:
            return False
        return not has_api_key() or not is_fl_bridge_ready(PROJECT_DIR)

    def _refresh_first_run_banner(self) -> None:
        if not hasattr(self, "_first_run_slot"):
            return
        for child in self._first_run_slot.winfo_children():
            child.destroy()
        if self._needs_first_run_banner():
            self._first_run_slot.pack(side="top", fill="x", pady=(0, CARD_GAP))
            first_run_banner(
                self._first_run_slot,
                self._first_run_message(),
                on_action=self.open_settings,
                on_dismiss=self._dismiss_first_run,
            ).pack(fill="x")
        else:
            self._first_run_slot.pack_forget()

    def _dismiss_first_run(self) -> None:
        self._first_run_dismissed = True
        self._refresh_first_run_banner()

    def _build_main(self, parent: tk.Frame) -> None:
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both", expand=True)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=0)
        outer.grid_rowconfigure(2, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        center_wrap = tk.Frame(outer, bg=BG)
        center_wrap.grid(row=1, column=0)

        stack = tk.Frame(center_wrap, bg=BG, width=CONTENT_W)
        stack.pack(anchor="center")

        self._first_run_slot = tk.Frame(stack, bg=BG, width=CONTENT_W)
        self._refresh_first_run_banner()

        self._card_shell = tk.Frame(stack, bg=BG, width=CONTENT_W)
        self._card_shell.pack(side="top", fill="x", pady=(0, CARD_GAP))
        self._card_shell.pack_propagate(False)

        self._card_error_bar = tk.Frame(self._card_shell, bg=ERROR, height=ERROR_TOP_H)

        self._card_widget = make_card(self._card_shell)
        self._card_widget.pack(side="top", fill="x")

        inner = tk.Frame(self._card_widget, bg=BG_CARD)
        inner.pack(fill="x", padx=CARD_PAD, pady=CARD_PAD)

        eyebrow_label(inner, "DESCRIBE YOUR BEAT", bg=BG_CARD).pack(anchor="w", pady=(0, EYEBROW_MB))

        self._textarea_wrap = tk.Frame(
            inner,
            bg=BG_INPUT,
            height=TEXTAREA_H,
            highlightbackground=DIVIDER,
            highlightthickness=1,
        )
        self._textarea_wrap.pack(side="top", fill="x")
        self._textarea_wrap.pack_propagate(False)

        self.prompt_box = tk.Text(
            self._textarea_wrap,
            height=4,
            bg=BG_INPUT,
            fg=TEXT_FAINT,
            insertbackground=ACCENT,
            relief="flat",
            highlightthickness=0,
            font=FONT_INPUT,
            wrap="word",
            padx=TEXTAREA_PAD,
            pady=TEXTAREA_PAD,
        )
        self.prompt_box.pack(fill="both", expand=True)
        self.prompt_box.insert("1.0", PROMPT_PLACEHOLDER)
        self.prompt_box.bind("<FocusIn>", self._clear_placeholder)
        self.prompt_box.bind("<FocusOut>", self._restore_placeholder)
        self.prompt_box.bind("<KeyRelease>", self._on_prompt_changed)
        bind_text_shortcuts(self.prompt_box, before_edit=self._clear_placeholder)

        self._inline_error = tk.Label(
            inner,
            text="",
            bg=BG_CARD,
            fg=ERROR,
            font=FONT_ERROR,
            wraplength=CONTENT_W - CARD_PAD * 4,
            justify="left",
        )

        self._button_row = tk.Frame(inner, bg=BG_CARD, height=BUTTON_H)
        self._button_row.pack(side="top", fill="x", pady=(CARD_GAP, 0))
        self._button_row.pack_propagate(False)

        btn_inner = tk.Frame(self._button_row, bg=BG_CARD)
        btn_inner.pack(side="left", fill="y")

        self.create_btn = ttk.Button(
            btn_inner,
            text="CREATE BEAT",
            style="PrimaryDisabled.TButton",
            command=self.on_create,
            state="disabled",
        )
        self.create_btn.pack(side="left")

        self.fl_btn = ttk.Button(
            btn_inner,
            text="OPEN IN FL",
            style="Secondary.TButton",
            command=self.open_in_fl_studio,
            state="disabled",
        )
        self.fl_btn.pack(side="left", padx=(BUTTON_GAP, 0))

        self._regen_wrap = tk.Frame(btn_inner, width=REGEN_SIZE, height=BUTTON_H, bg=BG_CARD)
        self._regen_wrap.pack(side="left", padx=(BUTTON_GAP, 0))
        self._regen_wrap.pack_propagate(False)

        self.regen_btn = tk.Label(
            self._regen_wrap,
            text="↻",
            bg=BG_CARD,
            fg=TEXT_FAINT,
            font=("Segoe UI", 18),
            cursor="arrow",
        )
        self.regen_btn.pack(expand=True)
        self.regen_btn.bind("<Enter>", lambda _e: self.regen_btn.configure(fg=TEXT) if self._regen_enabled else None)
        self.regen_btn.bind(
            "<Leave>",
            lambda _e: self.regen_btn.configure(fg=TEXT_DIM if self._regen_enabled else TEXT_FAINT),
        )
        self.regen_btn.bind("<Button-1>", lambda _e: self.on_regenerate() if self._regen_enabled and not self._busy else None)
        self._regen_enabled = False

        self._spinner = DiagonalSpinner(btn_inner, bg=BG_CARD)
        self._spinner.pack_forget()

        self._waveform = WaveformStrip(stack, height=WAVEFORM_H)
        self._waveform.pack(side="top", fill="x")
        self._waveform.pack_propagate(False)

    def _build_status(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=BG, height=STATUS_BAR_H, highlightbackground=DIVIDER, highlightthickness=1)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)

        tk.Frame(bar, bg=DIVIDER, height=1).pack(side="top", fill="x")

        inner = tk.Frame(bar, bg=BG)
        inner.pack(fill="both", expand=True, padx=STATUS_PAD)

        tk.Label(inner, textvariable=self._quota, bg=BG, fg=TEXT_DIM, font=status_mono_font()).pack(
            side="left", anchor="w"
        )

        right = tk.Frame(inner, bg=BG)
        right.pack(side="right")

        self._fl_dot = tk.Canvas(right, width=FL_DOT_SIZE, height=FL_DOT_SIZE, bg=BG, highlightthickness=0, bd=0)
        self._fl_dot.pack(side="left")
        tk.Label(
            right,
            textvariable=self._fl_bridge_label,
            bg=BG,
            fg=TEXT_DIM,
            font=status_mono_font(),
        ).pack(side="left", padx=(FL_DOT_GAP, 0))
        self._draw_fl_dot()

    def _confirm_empty_library_create(self) -> bool:
        if self._library_has_audio():
            return True
        if self._empty_lib_warned:
            return True
        self._empty_lib_warned = True
        return True

    def _library_has_audio(self) -> bool:
        try:
            catalog = scan_library(get_samples_dir())
            return catalog.get("audio_total", 0) > 0
        except OSError:
            return False

    def _sync_beat_state(self) -> None:
        self._beat_ready = PATTERN_JSON.is_file()
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        if self._busy:
            return
        has_prompt = bool(self._prompt_text())
        if has_prompt:
            self.create_btn.configure(state="normal", style="Primary.TButton", text="CREATE BEAT")
        else:
            self.create_btn.configure(state="disabled", style="PrimaryDisabled.TButton", text="CREATE BEAT")
        if self._beat_ready:
            self.fl_btn.configure(state="normal", style="SecondarySuccess.TButton")
            self.regen_btn.configure(fg=TEXT_DIM, cursor="hand2")
            self._regen_enabled = True
        else:
            self.fl_btn.configure(state="disabled", style="Secondary.TButton")
            self.regen_btn.configure(fg=TEXT_FAINT, cursor="arrow")
            self._regen_enabled = False

    def _on_prompt_changed(self, _event: tk.Event | None = None) -> None:
        if not self._busy:
            self._update_action_buttons()

    def _clear_card_error(self) -> None:
        self._card_error_bar.pack_forget()
        self._inline_error.pack_forget()
        self._inline_error.configure(text="")

    def _set_card_error(self, message: str) -> None:
        self._card_error_bar.pack(side="top", fill="x", before=self._card_widget)
        self._inline_error.configure(text=message)
        self._inline_error.pack(anchor="w", pady=(SP_8, 0), before=self._button_row)

    def _draw_fl_dot(self) -> None:
        ready = is_fl_bridge_ready(PROJECT_DIR)
        fill = SUCCESS if ready else ERROR
        label = "FL Bridge: Connected" if ready else "FL Bridge: Not connected"
        self._fl_bridge_label.set(label)
        for dot in (getattr(self, "_fl_dot", None), getattr(self, "_header_fl_dot", None)):
            if dot is None:
                continue
            dot.delete("all")
            dot.create_oval(0, 0, FL_DOT_SIZE, FL_DOT_SIZE, fill=fill, outline=fill)

    def _show_busy_spinner(self) -> None:
        self._regen_wrap.pack_forget()
        self._spinner.pack(side="left", padx=(BUTTON_GAP, 0))
        self._spinner.start()
        self._waveform.set_busy(True)

    def _hide_busy_spinner(self) -> None:
        self._spinner.stop()
        self._spinner.pack_forget()
        self._regen_wrap.pack(side="left", padx=(BUTTON_GAP, 0))
        self._waveform.set_busy(False)

    def _clear_placeholder(self, _event: tk.Event | None = None) -> None:
        if not self._placeholder_active:
            return
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.configure(fg=TEXT)
        self._placeholder_active = False

    def _restore_placeholder(self, _event: tk.Event | None = None) -> None:
        if self.prompt_box.get("1.0", "end").strip():
            return
        self._placeholder_active = True
        self.prompt_box.insert("1.0", PROMPT_PLACEHOLDER)
        self.prompt_box.configure(fg=TEXT_FAINT)

    def _prompt_text(self) -> str:
        if self._placeholder_active:
            return ""
        return self.prompt_box.get("1.0", "end").strip()

    def _refresh_beat_quota(self) -> None:
        if not has_api_key():
            self._quota.set("")
            return
        self._quota.set(format_quota_label())

    def _boot_message(self) -> None:
        ensure_samples_library(get_samples_dir(), quiet=True)
        ensure_midi_library(resolve_midi_dir(), quiet=True)
        self._provider_badge.set(provider_label())
        self._sync_beat_state()
        self._refresh_first_run_banner()
        self._refresh_beat_quota()
        self._draw_fl_dot()
        self._update_action_buttons()
        if self._beat_ready:
            self._load_beat_meta()
            if self._library_has_audio():
                self.set_status("Beat ready — open in FL Studio", tone="ok")
            else:
                self.set_status("Beat ready — starter sounds load in FL", tone="ok")
        elif has_api_key():
            if self._library_has_audio():
                self.set_status("Ready — describe your beat", tone="idle")
            else:
                self.set_status("Ready — starter pack included, describe your beat", tone="idle")
        else:
            self.set_status("Add API key to generate beats", tone="idle")

    def _load_beat_meta(self) -> None:
        try:
            data = json.loads(PATTERN_JSON.read_text(encoding="utf-8"))
            bpm = data.get("bpm", "—")
            style = data.get("style", "—")
            self.set_meta(bpm, style, ready=True)
        except (OSError, json.JSONDecodeError, ValueError):
            self.set_meta()

    def _refresh_after_settings(self) -> None:
        ensure_samples_library(get_samples_dir(), quiet=True)
        ensure_midi_library(resolve_midi_dir(), quiet=True)
        self._provider_badge.set(provider_label())
        self._refresh_first_run_banner()
        self._refresh_beat_quota()
        self._draw_fl_dot()
        self.set_status("Settings saved", tone="idle")

    def set_status(self, text: str, *, tone: str | None = None) -> None:
        logging.debug("PLG status: %s (%s)", text, tone)

    def set_meta(self, bpm: str | float = "—", style: str = "—", *, ready: bool = False) -> None:
        del bpm, style, ready

    def open_settings(self) -> None:
        SettingsDialog(self, first_run=False)

    def show_quick_start(self) -> None:
        messagebox.showinfo(
            "PLG Quick Start",
            "1. Describe your beat → CREATE BEAT (Ctrl+Enter)\n"
            "2. OPEN IN FL — 3 channels + starter sounds + notes\n"
            "3. Play in FL Studio\n\n"
            "Starter sounds are BUNDLED — no download needed.\n\n"
            "Sell / Gumroad .exe: run build_plg.bat → dist\\PLG.exe\n"
            "Full map: Help → Where is everything? (START_HERE)",
        )

    def open_start_here(self) -> None:
        path = PROJECT_DIR / "START_HERE.md"
        if path.is_file():
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("PLG", "START_HERE.md not found in the PLG folder.")

    def open_fl_workflows_doc(self) -> None:
        path = PROJECT_DIR / "FL_WORKFLOWS.md"
        if path.is_file():
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("PLG", "FL_WORKFLOWS.md not found.")

    def open_fl_bridge_doc(self) -> None:
        path = PROJECT_DIR / "FL_BRIDGE.md"
        if path.is_file():
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("PLG", "FL_BRIDGE.md not found in the project folder.")

    def install_fl_themes(self) -> None:
        try:
            result = install_themes()
            themes = result.get("themes") or []
            specs = result.get("specs") or []
            self.set_status("FL theme specs installed", tone="ok")
            messagebox.showinfo(
                "PLG Themes",
                f"Installed {len(specs)} color spec(s) to FL Themes/PLG/\n"
                f"Installed {len(themes)} .flstheme file(s).\n\n"
                "Open FL → theme editor → apply hex values from THEMES.md\n"
                "or the JSON files in your FL Themes/PLG folder.",
            )
        except OSError as exc:
            messagebox.showerror("PLG", str(exc))

    def on_regenerate(self) -> None:
        if not self._regen_enabled:
            return
        if not self._last_prompt and not self._prompt_text():
            self._set_card_error("Describe your beat first.")
            return
        snap = get_quota_snapshot()
        if not snap.get("skipped"):
            days = snap["days_until_reset"]
            day_word = "day" if days == 1 else "days"
            if not messagebox.askyesno(
                "Regenerate beat",
                "Regenerating uses 1 beat from your plan.\n\n"
                f"{snap['remaining']} of {snap['limit']} beats left · resets in {days} {day_word}.\n\n"
                "Continue?",
            ):
                return
        if self._last_prompt and not self._prompt_text():
            self.prompt_box.delete("1.0", "end")
            self.prompt_box.insert("1.0", self._last_prompt)
            self.prompt_box.configure(fg=TEXT)
            self._placeholder_active = False
        self.on_create()

    def on_create(self) -> None:
        if self._busy:
            return

        if not has_api_key():
            self.open_settings()
            return

        try:
            ensure_can_consume_beat()
        except BeatQuotaExceeded as exc:
            self._set_card_error(str(exc))
            self._refresh_beat_quota()
            return

        if not self._confirm_empty_library_create():
            return

        prompt = self._prompt_text()
        if not prompt:
            self._set_card_error("Describe your beat first.")
            return

        self._clear_card_error()
        self._last_prompt = prompt
        self._busy = True
        self._gen_started_at = time.time()
        self.create_btn.configure(state="disabled", text="GENERATING…")
        self.fl_btn.configure(state="disabled")
        self.prompt_box.configure(state="disabled", insertofftime=0)
        self._show_busy_spinner()
        self.set_status(f"Starting · {provider_label()}", tone="busy")
        self._start_gen_tick()

        def worker() -> None:
            try:
                logging.info("CREATE BEAT start: %s", prompt[:80])
                pattern = run_pipeline(prompt)
                logging.info("CREATE BEAT ok: bpm=%s style=%s", pattern.get("bpm"), pattern.get("style"))
                self._ui_queue.put(("success", pattern))
            except Exception as exc:
                logging.exception("CREATE BEAT failed")
                self._ui_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _start_gen_tick(self) -> None:
        self._stop_gen_tick()

        def tick() -> None:
            if not self._busy or self._gen_started_at is None:
                return
            elapsed = int(time.time() - self._gen_started_at)
            self.set_status(f"Generating · {elapsed}s · {provider_label()}", tone="busy")
            if elapsed >= 75:
                self.set_status(
                    f"Still working · {elapsed}s — check internet / API key",
                    tone="busy",
                )
            self._gen_tick_id = self.after(1000, tick)

        self._gen_tick_id = self.after(1000, tick)

    def _stop_gen_tick(self) -> None:
        if self._gen_tick_id is not None:
            try:
                self.after_cancel(self._gen_tick_id)
            except tk.TclError:
                pass
            self._gen_tick_id = None
        self._gen_started_at = None

    def _stop_busy(self) -> None:
        self._stop_gen_tick()
        self._busy = False
        self.create_btn.configure(state="normal", text="CREATE BEAT")
        self.prompt_box.configure(state="normal", insertofftime=300)
        self._hide_busy_spinner()
        self._sync_beat_state()

    def _on_success(self, pattern: dict) -> None:
        self._stop_busy()
        try:
            consume_beat()
        except BeatQuotaExceeded:
            pass
        self._refresh_beat_quota()
        self._beat_ready = True
        bpm = pattern.get("bpm", "—")
        style = pattern.get("style", "unknown")
        self._clear_card_error()
        self.set_status("Beat ready", tone="ok")
        self.set_meta(bpm, style, ready=True)
        self._update_action_buttons()
        self._refresh_first_run_banner()

        if get_auto_open_fl():
            threading.Thread(target=self._open_fl_worker, daemon=True).start()

    def _open_fl_worker(self) -> None:
        try:
            logging.info("OPEN IN FL start")
            result = open_beat_in_fl(PROJECT_DIR)
            logging.info("OPEN IN FL ok: imported=%s method=%s", result.get("imported"), result.get("import_method"))
            self._ui_queue.put(("fl_done", result))
        except (FileNotFoundError, ValueError, OSError) as exc:
            logging.exception("OPEN IN FL failed")
            self._ui_queue.put(("fl_error", exc))

    def _on_fl_opened(self, result: dict) -> None:
        method = str(result.get("import_method", ""))
        if result.get("imported") and method == "flp_session":
            self.set_status("FL Studio · 3 channels ready — load your sounds", tone="ok")
        elif result.get("imported"):
            self.set_status("FL Studio · 3 tracks imported", tone="ok")
        else:
            self.set_status("FL Studio opened", tone="ok")
        self._draw_fl_dot()
        self._refresh_first_run_banner()

    def _on_error(self, exc: Exception) -> None:
        self._stop_busy()
        self.set_status("Error", tone="error")
        self._set_card_error(format_llm_error(exc))

    def open_guide(self) -> None:
        path = PROJECT_DIR / "build_guide.txt"
        if not path.exists():
            messagebox.showinfo("PLG", "Create a beat first.")
            return
        os.startfile(path)  # type: ignore[attr-defined]

    def open_samples(self) -> None:
        path = get_samples_dir()
        ensure_samples_library(path, quiet=True)
        os.startfile(path)  # type: ignore[attr-defined]
        self._refresh_first_run_banner()

    def import_kit(self) -> None:
        source = filedialog.askdirectory(title="Select a kit pack / kit download folder")
        if not source:
            return
        try:
            from organize_kit import copy_kit_as_is

            library = get_samples_dir()
            copied = copy_kit_as_is(Path(source), library)
            total = copied.get("imported", 0)
            if total == 0:
                messagebox.showwarning(
                    "PLG",
                    "No supported files found (.wav .mid .flp .fst .fxp ...).",
                )
                return
            catalog = scan_library(library)
            save_catalog(catalog, PROJECT_DIR / "sample_catalog.json")
            self._empty_lib_warned = False
            self._refresh_first_run_banner()
            dest = copied.get("destination", "imports")
            messagebox.showinfo("PLG", f"Imported {total} files into {dest} (as-is).")
        except (OSError, FileNotFoundError) as exc:
            messagebox.showerror("PLG", str(exc))

    def install_fl_scripts(self) -> None:
        try:
            installed = install_all(PROJECT_DIR)
            pack = installed.get("script_pack") or []
            self._draw_fl_dot()
            self._refresh_first_run_banner()
            messagebox.showinfo(
                "PLG",
                f"FL scripts installed.\n\n"
                f"Importer: {installed['plugin_script']}\n"
                f"Script pack: {len(pack)} tools in Piano roll → Scripts → PLG\n\n"
                "OPEN IN FL loads PLG_Session.flp automatically.",
            )
        except OSError as exc:
            messagebox.showerror("PLG", str(exc))

    def open_midi(self) -> None:
        path = PROJECT_DIR / "output_midi"
        path.mkdir(exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]

    def open_output_folder(self) -> None:
        from plg_paths import projects_dir

        os.startfile(projects_dir())  # type: ignore[attr-defined]

    def open_in_fl_studio(self, *, silent: bool = False) -> None:
        try:
            self.set_status("Opening FL Studio…", tone="busy")
            threading.Thread(target=self._open_fl_worker, daemon=True).start()
        except OSError as exc:
            messagebox.showerror("PLG", str(exc))


def main() -> None:
    app = PlgApp()
    app.mainloop()


if __name__ == "__main__":
    main()
