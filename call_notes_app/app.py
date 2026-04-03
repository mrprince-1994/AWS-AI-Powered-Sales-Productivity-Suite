import tkinter as tk
import os
from tkinter import messagebox, filedialog
import customtkinter as ctk
import threading
from transcription.transcriber import LiveTranscriber
from transcription.summarizer import generate_notes, generate_followup_email, generate_prep_summary, extract_competitors
from transcription.storage import save_notes, _md_to_docx, export_share_html
from transcription.history import save_session, list_sessions, get_all_customers, get_latest_meddpicc
from transcription.competitive_intel import save_competitor_mentions
from transcription.outlook_tasks import create_followup_task
from transcription.sift_insight import queue_sift_insight
from transcription.activity_logger import queue_activity
from transcription.agent_client import warmup as warmup_agent, shutdown as shutdown_agent
from transcription.meeting_assistant import MeetingAssistant, MEDDPICC_ELEMENTS, MEDDPICC_ABBREVIATIONS
from md_render import configure_tags, MarkdownStreamer
from retrieval.notes_retriever import scan_notes, ask_notes_agent, ask_research_agent, NOTE_SOURCES, dedupe_customers
from retrieval.chat_history import save_chat_session, list_chat_sessions, load_chat_session, delete_chat_session, _ensure_table

# --- Color Palette (modern chat UI) ---
BG_DARK = "#0a0a0a"        # App background — deeper black
BG_PANEL = "#111111"        # Panel/card backgrounds
BG_INPUT = "#161616"        # Input fields, text areas
BG_CARD = "#1a1a1a"         # Elevated cards, hover states
FG_TEXT = "#c8ccd0"         # Body text
FG_DIM = "#5a6270"          # Secondary/muted text
FG_BRIGHT = "#eef0f2"       # Headings, emphasis
ACCENT = "#10a37f"          # Primary accent green
ACCENT_HOVER = "#0d8c6d"    # Accent hover
ACCENT_GLOW = "#10a37f"     # Border glow color
GREEN = "#10a37f"           # Start/action buttons
GREEN_HOVER = "#0d8c6d"
RED = "#ef4444"             # Stop/delete
RED_HOVER = "#dc2626"
ORANGE = "#f59e0b"          # Status/warning
YELLOW = "#10a37f"          # Send button (matches accent)
YELLOW_HOVER = "#0d8c6d"
BORDER = "#1f2937"          # Subtle borders — slightly blue-tinted
BORDER_ACCENT = "#10a37f40" # Semi-transparent accent for card borders
USER_BUBBLE = "#1a1a1a"     # User message background
ASST_BUBBLE = "#111111"     # Assistant message background

# MEDDPICC element color mapping
MEDDPICC_COLORS = {
    "Metrics": "#10a37f", "Economic Buyer": "#f59e0b", "Decision Criteria": "#3b82f6",
    "Decision Process": "#8b5cf6", "Paper Process": "#ec4899", "Implicate the Pain": "#ef4444",
    "Champion": "#06b6d4", "Competition": "#f97316",
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ─── Cached Fonts (avoid GDI handle leak from repeated CTkFont allocations) ───
_font_cache = {}

def _font(family="Segoe UI", size=11, weight="normal", slant="roman"):
    """Return a cached CTkFont instance to avoid creating duplicate GDI handles."""
    key = (family, size, weight, slant)
    if key not in _font_cache:
        kwargs = {"family": family, "size": size}
        if weight == "bold":
            kwargs["weight"] = "bold"
        if slant == "italic":
            kwargs["slant"] = "italic"
        _font_cache[key] = ctk.CTkFont(**kwargs)
    return _font_cache[key]

# ─── Theme System ───────────────────────────────────────────────────
THEMES = {
    "dark": {
        "BG_DARK": "#0a0a0a", "BG_PANEL": "#111111", "BG_INPUT": "#161616",
        "BG_CARD": "#1a1a1a", "FG_TEXT": "#c8ccd0", "FG_DIM": "#5a6270",
        "FG_BRIGHT": "#eef0f2", "BORDER": "#1f2937",
    },
    "light": {
        "BG_DARK": "#f3f4f6", "BG_PANEL": "#ffffff", "BG_INPUT": "#f9fafb",
        "BG_CARD": "#e5e7eb", "FG_TEXT": "#1f2937", "FG_DIM": "#6b7280",
        "FG_BRIGHT": "#111827", "BORDER": "#d1d5db",
    },
}
_current_theme = "dark"


def get_theme():
    return _current_theme


def set_theme(name):
    global _current_theme, BG_DARK, BG_PANEL, BG_INPUT, BG_CARD, FG_TEXT, FG_DIM, FG_BRIGHT, BORDER
    _current_theme = name
    t = THEMES[name]
    BG_DARK = t["BG_DARK"]; BG_PANEL = t["BG_PANEL"]; BG_INPUT = t["BG_INPUT"]
    BG_CARD = t["BG_CARD"]; FG_TEXT = t["FG_TEXT"]; FG_DIM = t["FG_DIM"]
    FG_BRIGHT = t["FG_BRIGHT"]; BORDER = t["BORDER"]
    ctk.set_appearance_mode("dark" if name == "dark" else "light")


# ─── Toast Notification ────────────────────────────────────────────
class ToastNotification:
    """Lightweight toast popup that auto-dismisses."""

    _active_toasts = []

    def __init__(self, parent, message, duration=3000, color=ACCENT):
        self._parent = parent
        self._frame = ctk.CTkFrame(parent, fg_color=color, corner_radius=10,
                                    border_width=0)
        ctk.CTkLabel(self._frame, text=message, text_color="#ffffff",
                     font=_font(weight="bold"),
                     wraplength=350).pack(padx=16, pady=10)

        # Stack below existing toasts
        offset = len(ToastNotification._active_toasts) * 50
        ToastNotification._active_toasts.append(self)

        self._frame.place(relx=1.0, rely=1.0, x=-20, y=-(20 + offset), anchor="se")
        self._frame.lift()
        parent.after(duration, self._dismiss)

    def _dismiss(self):
        try:
            self._frame.place_forget()
            self._frame.destroy()
            if self in ToastNotification._active_toasts:
                ToastNotification._active_toasts.remove(self)
        except Exception:
            pass


def show_toast(parent, message, duration=3000, color=ACCENT):
    """Show a toast notification on the given parent widget."""
    ToastNotification(parent, message, duration, color)


class CollapsibleSection(ctk.CTkFrame):
    """A section with a clickable header that toggles content visibility."""

    def __init__(self, master, title, icon="", expanded=True, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._expanded = expanded
        self._title = title
        self._icon = icon

        self._header = ctk.CTkFrame(self, fg_color="transparent", cursor="hand2")
        self._header.pack(fill=tk.X)

        arrow = "▼" if expanded else "▶"
        self._toggle_label = ctk.CTkLabel(
            self._header, text=f"{arrow} {icon}  {title}",
            font=_font(weight="bold"), text_color=FG_BRIGHT,
            anchor="w", cursor="hand2")
        self._toggle_label.pack(side=tk.LEFT, padx=0)

        self._header.bind("<Button-1>", lambda e: self.toggle())
        self._toggle_label.bind("<Button-1>", lambda e: self.toggle())

        self._content = ctk.CTkFrame(self, fg_color="transparent")
        if expanded:
            self._content.pack(fill=tk.BOTH, expand=True)

    @property
    def content(self):
        return self._content

    def toggle(self):
        self._expanded = not self._expanded
        arrow = "▼" if self._expanded else "▶"
        self._toggle_label.configure(text=f"{arrow} {self._icon}  {self._title}")
        if self._expanded:
            self._content.pack(fill=tk.BOTH, expand=True)
        else:
            self._content.pack_forget()


class StyledText(tk.Text):
    """Modern dark-themed tk.Text with a CTk scrollbar, wrapped in a rounded frame."""

    def __init__(self, master, **kwargs):
        self._outer = ctk.CTkFrame(master, fg_color=BG_INPUT, corner_radius=14,
                                    border_width=1, border_color=BORDER)
        if 'font' not in kwargs:
            kwargs['font'] = ("Segoe UI", 10)
        super().__init__(self._outer, wrap=tk.WORD, bg=BG_INPUT, fg=FG_TEXT,
                         insertbackground=FG_BRIGHT, borderwidth=0, highlightthickness=0,
                         padx=16, pady=12, selectbackground=ACCENT,
                         selectforeground=BG_DARK, state=tk.DISABLED, **kwargs)
        sb = ctk.CTkScrollbar(self._outer, command=self.yview, fg_color=BG_INPUT,
                               button_color="#2a2a2a", button_hover_color=ACCENT,
                               width=8)
        self.configure(yscrollcommand=sb.set)
        super().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 3), pady=6)

    def pack(self, **kw):
        self._outer.pack(**kw)

    def grid(self, **kw):
        self._outer.grid(**kw)


def _make_wrapping_label(parent, text, text_color=FG_TEXT, font=None, **pack_kwargs):
    """Create a CTkLabel that auto-wraps text to fit its parent's width on resize."""
    if font is None:
        font = _font(size=12)
    lbl = ctk.CTkLabel(parent, text=text, text_color=text_color, font=font,
                        anchor=tk.W, wraplength=1)
    lbl._last_wrap_width = 0

    def _update_wrap(event):
        new_width = event.width - 20
        if new_width > 50 and abs(new_width - lbl._last_wrap_width) > 5:
            lbl._last_wrap_width = new_width
            lbl.configure(wraplength=new_width)

    # Bind to the PARENT's configure, not the label itself — avoids feedback loop
    parent.bind("<Configure>", lambda e: _update_wrap(e), add="+")
    lbl.pack(anchor=tk.W, fill=tk.X, **pack_kwargs)
    return lbl


class CallNotesApp:
    def __init__(self, root):
        self.root = root
        # Support both CTk root window and CTkFrame (tab)
        self._is_root = isinstance(root, ctk.CTk)
        if self._is_root:
            self.root.title("Call Notes — Live Transcriber")
            self.root.geometry("1440x860")
            self.root.minsize(1100, 700)
            self.root.configure(fg_color=BG_DARK)

        self.transcriber = None
        self._current_transcript = ""
        self._current_notes = ""
        self._current_email = ""
        self._ai_enabled = True
        self._pending_questions = set()
        self._rendered_questions = set()  # Track question texts already shown in the coach panel
        self._generating = False
        self._locked_customer = ""
        self.meeting_assistant = MeetingAssistant(
            root=self.root,
            on_suggestions=self._render_suggestions,
            on_coverage=self._update_coverage_ui,
            on_status=self._update_meddpicc_status,
            on_summary=self._show_meddpicc_summary,
        )
        self._build_ui()
        self._load_devices()

        warmup_agent()
        if self._is_root:
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────── UI BUILD ───────────────────────────

    def _build_ui(self):
        # Title bar — only shown when running standalone (not in tab)
        if self._is_root:
            title_bar = ctk.CTkFrame(self.root, fg_color="transparent")
            title_bar.pack(fill=tk.X, padx=20, pady=(16, 0))
            ctk.CTkLabel(title_bar, text="🎙  Call Notes",
                         font=_font(size=20, weight="bold"),
                         text_color=FG_BRIGHT).pack(side=tk.LEFT)
            self.status_var = tk.StringVar(value="Ready")
            ctk.CTkLabel(title_bar, textvariable=self.status_var,
                         font=_font(size=12), text_color=ORANGE
                         ).pack(side=tk.RIGHT)
        else:
            self.status_var = tk.StringVar(value="Ready")

        # Recording indicator (pulsing red dot)
        self._recording_dot = None
        self._recording_pulse_on = False
        self._recording_pulse_id = None

        # 3-column layout using PanedWindow for resizable panels
        self._paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL,
                                      bg=BG_DARK, sashwidth=6, sashrelief=tk.FLAT,
                                      opaqueresize=True, bd=0)
        self._paned.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        # Build panels into the PanedWindow
        self._history_frame = ctk.CTkFrame(self._paned, fg_color="transparent")
        self._center_frame = ctk.CTkFrame(self._paned, fg_color="transparent")
        self._ai_frame = ctk.CTkFrame(self._paned, fg_color="transparent")

        self._paned.add(self._history_frame, minsize=180, width=220)
        self._paned.add(self._center_frame, minsize=400, width=700)
        self._paned.add(self._ai_frame, minsize=250, width=350)

        self._build_history_panel(self._history_frame)
        self._build_center_panel(self._center_frame)
        self._build_ai_panel(self._ai_frame)

        # Status bar at bottom (only in tab/embedded mode — standalone uses title bar)
        if not self._is_root:
            status_bar = ctk.CTkFrame(self.root, fg_color=BG_CARD, corner_radius=0, height=28)
            status_bar.pack(fill=tk.X, side=tk.BOTTOM)
            ctk.CTkLabel(status_bar, textvariable=self.status_var,
                         text_color=ORANGE, font=_font(),
                         anchor="w").pack(side=tk.LEFT, padx=12, pady=4)

        self.root.after(500, self._refresh_history)
    # ── Left: History ──
    def _build_history_panel(self, parent):
        card = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=14,
                             border_width=1, border_color=BORDER)
        card.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(card, text="📋  History",
                     font=_font(size=13, weight="bold"),
                     text_color=FG_BRIGHT).pack(anchor=tk.W, padx=14, pady=(14, 8))

        filt = ctk.CTkFrame(card, fg_color="transparent")
        filt.pack(fill=tk.X, padx=12, pady=(0, 8))
        ctk.CTkLabel(filt, text="Filter:", text_color=FG_DIM,
                     font=_font()).pack(side=tk.LEFT)
        self.history_filter_var = tk.StringVar(value="(All)")
        self.history_filter_combo = ctk.CTkComboBox(
            filt, variable=self.history_filter_var, width=140,
            fg_color=BG_INPUT, border_color=BORDER, button_color=BORDER,
            button_hover_color=ACCENT, dropdown_fg_color=BG_INPUT,
            dropdown_hover_color=ACCENT, text_color=FG_BRIGHT,
            font=_font(), state="readonly",
            command=lambda _: self._refresh_history())
        self.history_filter_combo.pack(side=tk.LEFT, padx=(6, 4))
        ctk.CTkButton(filt, text="⟳", width=32, height=28, fg_color=BG_INPUT,
                      hover_color=BG_CARD, text_color=ACCENT, corner_radius=6,
                      font=_font(size=13),
                      command=self._refresh_history).pack(side=tk.LEFT)

        lf = ctk.CTkFrame(card, fg_color=BG_INPUT, corner_radius=10,
                           border_width=1, border_color=BORDER)
        lf.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self.history_list = tk.Listbox(
            lf, exportselection=False, bg=BG_INPUT, fg=FG_TEXT,
            selectbackground=ACCENT, selectforeground=BG_DARK,
            font=("Segoe UI", 10), borderwidth=0, highlightthickness=0,
            activestyle="none", relief="flat")
        self.history_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.history_list.bind("<<ListboxSelect>>", self._on_history_select)
        self._history_items = []

    # ── Center: Controls + Transcript + Notes ──
    def _build_center_panel(self, parent):
        card = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=14,
                             border_width=1, border_color=BORDER)
        card.pack(fill=tk.BOTH, expand=True)

        # ── Fixed top section (controls + buttons) ──
        top_fixed = ctk.CTkFrame(card, fg_color="transparent")
        top_fixed.pack(fill=tk.X, side=tk.TOP)

        # ── Scrollable bottom section (checklist, transcript, notes, email) ──
        scroll_wrapper = ctk.CTkFrame(card, fg_color="transparent")
        scroll_wrapper.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        self._center_canvas = tk.Canvas(scroll_wrapper, bg=BG_PANEL, highlightthickness=0,
                                         borderwidth=0)
        self._center_scrollbar = ctk.CTkScrollbar(
            scroll_wrapper, command=self._center_canvas.yview,
            fg_color=BG_PANEL, button_color="#2a2a2a",
            button_hover_color=ACCENT, width=8)
        self._center_canvas.configure(yscrollcommand=self._center_scrollbar.set)

        self._center_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 3), pady=6)
        self._center_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._center_inner = ctk.CTkFrame(self._center_canvas, fg_color="transparent")
        self._center_canvas_window = self._center_canvas.create_window(
            (0, 0), window=self._center_inner, anchor="nw")

        def _on_center_configure(event):
            self._center_canvas.configure(scrollregion=self._center_canvas.bbox("all"))

        def _on_canvas_resize(event):
            self._center_canvas.itemconfig(self._center_canvas_window, width=event.width)

        self._center_inner.bind("<Configure>", _on_center_configure)
        self._center_canvas.bind("<Configure>", _on_canvas_resize)

        # Mousewheel scrolling — scoped to avoid cross-tab interference
        def _on_mousewheel(event):
            try:
                self._center_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass

        def _bind_mousewheel(event):
            self._center_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(event):
            self._center_canvas.unbind_all("<MouseWheel>")

        self._center_canvas.bind("<Enter>", _bind_mousewheel)
        self._center_canvas.bind("<Leave>", _unbind_mousewheel)

        # Fixed content goes into top_fixed, scrollable content into _center_inner
        inner = self._center_inner

        # Controls grid (fixed top)
        ctrl = ctk.CTkFrame(top_fixed, fg_color="transparent")
        ctrl.pack(fill=tk.X, padx=16, pady=(14, 8))

        for i, (label, var_name, placeholder) in enumerate([
            ("Customer Name:", "customer_var", "Enter customer name..."),
        ]):
            ctk.CTkLabel(ctrl, text=label, text_color=FG_DIM,
                         font=_font(size=12)).grid(row=i, column=0, sticky=tk.W, pady=3)
            setattr(self, var_name, tk.StringVar())
            ctk.CTkEntry(ctrl, textvariable=getattr(self, var_name), width=280,
                         fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
                         font=_font(), corner_radius=6,
                         placeholder_text=placeholder
                         ).grid(row=i, column=1, padx=(10, 0), sticky=tk.W, pady=3)

        # Audio device combos
        self.system_device_var = tk.StringVar()
        self.mic_device_var = tk.StringVar()
        for i, (label, var) in enumerate([
            ("System Audio:", self.system_device_var),
            ("Microphone:", self.mic_device_var),
        ], start=1):
            ctk.CTkLabel(ctrl, text=label, text_color=FG_DIM,
                         font=_font(size=12)).grid(row=i, column=0, sticky=tk.W, pady=3)
            combo = ctk.CTkComboBox(
                ctrl, variable=var, width=400, fg_color=BG_INPUT, border_color=BORDER,
                button_color=BORDER, button_hover_color=ACCENT,
                dropdown_fg_color=BG_INPUT, dropdown_hover_color=ACCENT,
                text_color=FG_BRIGHT, font=_font(size=10), state="readonly")
            combo.grid(row=i, column=1, padx=(10, 0), sticky=tk.W, pady=3)
            if i == 1:
                self.system_device_combo = combo
            else:
                self.mic_device_combo = combo

        # Buttons — Row 1: Recording controls (fixed top)
        row1 = ctk.CTkFrame(top_fixed, fg_color="transparent")
        row1.pack(fill=tk.X, padx=16, pady=(4, 4))

        self.start_btn = ctk.CTkButton(
            row1, text="▶  Start Recording", fg_color=GREEN, hover_color=GREEN_HOVER,
            text_color=BG_DARK, font=_font(size=12, weight="bold"),
            corner_radius=10, height=38, command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            row1, text="⏹  Stop & Generate", fg_color=RED, hover_color=RED_HOVER,
            text_color=BG_DARK, font=_font(size=12, weight="bold"),
            corner_radius=10, height=38, state=tk.DISABLED, command=self._stop)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        # Pulsing recording indicator
        self._recording_dot = ctk.CTkLabel(
            row1, text="  🔴 REC", text_color=RED,
            font=_font(weight="bold"))
        # Hidden by default — shown when recording starts

        self.prep_btn = ctk.CTkButton(
            row1, text="📋 Pre-Call Prep", fg_color=BG_INPUT, hover_color=BG_CARD,
            text_color=ACCENT, font=_font(weight="bold"), corner_radius=10,
            height=36, border_width=1, border_color=ACCENT,
            command=self._generate_prep)
        self.prep_btn.pack(side=tk.RIGHT)

        # Buttons — Row 2: Post-call actions (fixed top)
        row2 = ctk.CTkFrame(top_fixed, fg_color="transparent")
        row2.pack(fill=tk.X, padx=16, pady=(0, 8))

        ctk.CTkLabel(row2, text="Post-call:", text_color=FG_DIM,
                     font=_font(size=10)).pack(side=tk.LEFT, padx=(0, 6))

        self.export_docx_btn = ctk.CTkButton(
            row2, text="📄 DOCX", fg_color="#1f2937", hover_color=ACCENT,
            text_color=FG_BRIGHT, font=_font(size=10), corner_radius=8,
            height=30, width=70, border_width=1, border_color="#374151", state=tk.DISABLED,
            command=self._export_docx)
        self.export_docx_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.export_pdf_btn = ctk.CTkButton(
            row2, text="📑 PDF", fg_color="#1f2937", hover_color=ACCENT,
            text_color=FG_BRIGHT, font=_font(size=10), corner_radius=8,
            height=30, width=60, border_width=1, border_color="#374151", state=tk.DISABLED,
            command=self._export_pdf)
        self.export_pdf_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.share_html_btn = ctk.CTkButton(
            row2, text="🔗 Share", fg_color="#1f2937", hover_color=ACCENT,
            text_color=FG_BRIGHT, font=_font(size=10), corner_radius=8,
            height=30, width=70, border_width=1, border_color="#374151", state=tk.DISABLED,
            command=self._export_share_html)
        self.share_html_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.sift_btn = ctk.CTkButton(
            row2, text="📊 SIFT", fg_color="#1f2937", hover_color=ACCENT,
            text_color=FG_BRIGHT, font=_font(size=10), corner_radius=8,
            height=30, width=65, border_width=1, border_color="#374151", state=tk.DISABLED,
            command=self._submit_sift)
        self.sift_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.activity_btn = ctk.CTkButton(
            row2, text="📝 Activity", fg_color="#1f2937", hover_color=ACCENT,
            text_color=FG_BRIGHT, font=_font(size=10), corner_radius=8,
            height=30, width=80, border_width=1, border_color="#374151", state=tk.DISABLED,
            command=self._log_activity)
        self.activity_btn.pack(side=tk.LEFT)

        # Subtle separator between fixed and scrollable areas
        ctk.CTkFrame(top_fixed, fg_color=BORDER, height=1).pack(fill=tk.X, padx=16, pady=(4, 0))

        # ── Post-Call Progress Checklist ──
        self._checklist_frame = ctk.CTkFrame(inner, fg_color=BG_INPUT, corner_radius=10,
                                              border_width=1, border_color=BORDER)
        # Hidden by default — shown after Stop & Generate
        self._checklist_items = {}
        self._checklist_labels = {}
        self._checklist_steps = [
            ("notes", "Generate notes"),
            ("email", "Generate follow-up email"),
            ("save", "Save session"),
            ("competitors", "Extract competitor mentions"),
            ("outlook_task", "Create Outlook follow-up task"),
        ]
        checklist_header = ctk.CTkFrame(self._checklist_frame, fg_color="transparent")
        checklist_header.pack(fill=tk.X, padx=10, pady=(8, 4))
        ctk.CTkLabel(checklist_header, text="⏳  Post-Call Progress",
                     font=_font(weight="bold"),
                     text_color=FG_BRIGHT).pack(side=tk.LEFT)
        self._checklist_dismiss_btn = ctk.CTkButton(
            checklist_header, text="✕", width=24, height=24,
            fg_color="transparent", hover_color=BG_CARD, text_color=FG_DIM,
            font=_font(), corner_radius=4,
            command=self._hide_checklist)
        self._checklist_dismiss_btn.pack(side=tk.RIGHT)
        for key, label in self._checklist_steps:
            row = ctk.CTkFrame(self._checklist_frame, fg_color="transparent")
            row.pack(fill=tk.X, padx=14, pady=1)
            status_lbl = ctk.CTkLabel(row, text="⬜", width=20,
                                       font=_font())
            status_lbl.pack(side=tk.LEFT)
            text_lbl = ctk.CTkLabel(row, text=label, text_color=FG_DIM,
                                     font=_font(size=10))
            text_lbl.pack(side=tk.LEFT, padx=(4, 0))
            self._checklist_items[key] = "pending"
            self._checklist_labels[key] = (status_lbl, text_lbl)
        # Bottom padding
        ctk.CTkFrame(self._checklist_frame, fg_color="transparent", height=6).pack()

        # ── Collapsible: Transcript ──
        self._transcript_section = CollapsibleSection(inner, "Live Transcript", icon="🎙")
        self._transcript_section.pack(fill=tk.X, padx=16, pady=(4, 4))

        transcript_header = ctk.CTkFrame(self._transcript_section.content, fg_color="transparent")
        transcript_header.pack(fill=tk.X, pady=(0, 4))
        self.copy_transcript_btn = ctk.CTkButton(
            transcript_header, text="📋 Copy Transcript", width=130, height=28,
            fg_color="#1f2937", hover_color=ACCENT, text_color=FG_BRIGHT,
            font=_font(), corner_radius=6,
            border_width=1, border_color=BORDER, state=tk.DISABLED,
            command=self._copy_transcript)
        self.copy_transcript_btn.pack(side=tk.RIGHT)
        self.transcript_text = StyledText(self._transcript_section.content, height=12, font=("Consolas", 10))
        self.transcript_text.pack(fill=tk.X, pady=(0, 4))

        # ── Collapsible: Manual Notes ──
        self._manual_notes_section = CollapsibleSection(inner, "Manual Notes", icon="✏️")
        self._manual_notes_section.pack(fill=tk.X, padx=16, pady=(0, 4))

        manual_hint = ctk.CTkLabel(
            self._manual_notes_section.content,
            text="Attendee corrections, focus areas, or context for the AI — sent alongside the transcript.",
            text_color=FG_DIM, font=_font(size=10), anchor=tk.W)
        manual_hint.pack(fill=tk.X, pady=(0, 4))

        self.manual_notes_text = StyledText(
            self._manual_notes_section.content, height=4, font=("Segoe UI", 10))
        self.manual_notes_text.configure(state=tk.NORMAL)
        self.manual_notes_text.pack(fill=tk.X, pady=(0, 4))

        # ── Collapsible: Notes ──
        self._notes_section = CollapsibleSection(inner, "Generated Notes", icon="📝", expanded=False)
        self._notes_section.pack(fill=tk.X, padx=16, pady=(0, 4))
        self.notes_text = StyledText(self._notes_section.content, height=12)
        self.notes_text.pack(fill=tk.X, pady=(0, 4))

        # ── Collapsible: Follow-Up Email ──
        self._email_section = CollapsibleSection(inner, "Follow-Up Email", icon="📧", expanded=False)
        self._email_section.pack(fill=tk.X, padx=16, pady=(0, 14))

        email_header = ctk.CTkFrame(self._email_section.content, fg_color="transparent")
        email_header.pack(fill=tk.X, pady=(0, 4))
        self.outlook_draft_btn = ctk.CTkButton(
            email_header, text="📨 Outlook Draft", width=120, height=28,
            fg_color="#1f2937", hover_color=ACCENT, text_color=FG_BRIGHT,
            font=_font(), corner_radius=6,
            border_width=1, border_color=BORDER, state=tk.DISABLED,
            command=self._send_to_outlook_draft)
        self.outlook_draft_btn.pack(side=tk.RIGHT)
        self.email_text = StyledText(self._email_section.content, height=8, font=("Segoe UI", 10))
        self.email_text.pack(fill=tk.X, pady=(0, 4))


    # ── Right: Call Intelligence Panel ──
    def _build_ai_panel(self, parent):
        card = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=14,
                             border_width=1, border_color=BORDER)
        card.pack(fill=tk.BOTH, expand=True)

        # ── Collapsible Call Intelligence Section ──
        self._intel_section = CollapsibleSection(card, "Call Intelligence", icon="📋", expanded=False)
        self._intel_section.pack(fill=tk.X, padx=12, pady=(10, 4))

        # Header with clear button inside the collapsible content
        intel_top = ctk.CTkFrame(self._intel_section.content, fg_color="transparent")
        intel_top.pack(fill=tk.X, padx=2, pady=(4, 4))
        ctk.CTkButton(intel_top, text="Clear", width=60, height=28, fg_color=BG_INPUT,
                      hover_color=BG_CARD, text_color=ACCENT, corner_radius=6,
                      font=_font(),
                      command=self._clear_ai_answers).pack(side=tk.RIGHT)

        # AI text display (used by prep summaries)
        self.ai_text = StyledText(self._intel_section.content, height=10, font=("Segoe UI", 10))
        self.ai_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 4))
        configure_tags(self.ai_text)

        # ── MEDDPICC Coach Section ──
        self._meddpicc_section = CollapsibleSection(card, "MEDDPICC Coach", icon="🎯", expanded=True)
        self._meddpicc_section.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))

        # Coverage indicator row
        coverage_frame = ctk.CTkFrame(self._meddpicc_section.content, fg_color="transparent")
        coverage_frame.pack(fill=tk.X, padx=8, pady=(4, 4))
        self._coverage_labels = {}
        self._question_history_popup = None  # Track active popup
        for abbr, element in zip(MEDDPICC_ABBREVIATIONS, MEDDPICC_ELEMENTS):
            lbl = ctk.CTkLabel(coverage_frame, text=abbr, width=28, height=24,
                               fg_color=BG_DARK, corner_radius=4,
                               text_color=FG_DIM, cursor="hand2",
                               font=_font(size=10, weight="bold"))
            lbl.pack(side=tk.LEFT, padx=2)
            lbl.bind("<Button-1>", lambda e, el=element: self._show_question_history(el, e))
            self._coverage_labels[element] = lbl

        # Suggestions container — scrollable, expands to fill available space
        suggestions_outer = ctk.CTkFrame(self._meddpicc_section.content, fg_color="transparent")
        suggestions_outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._suggestions_canvas = tk.Canvas(suggestions_outer, bg=BG_PANEL,
                                              highlightthickness=0, borderwidth=0)
        self._suggestions_scrollbar = ctk.CTkScrollbar(
            suggestions_outer, command=self._suggestions_canvas.yview,
            fg_color=BG_PANEL, button_color="#2a2a2a",
            button_hover_color=ACCENT, width=6)
        self._suggestions_canvas.configure(yscrollcommand=self._suggestions_scrollbar.set)

        self._suggestions_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 2), pady=2)
        self._suggestions_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._suggestions_frame = ctk.CTkFrame(self._suggestions_canvas, fg_color="transparent")
        self._suggestions_canvas_window = self._suggestions_canvas.create_window(
            (0, 0), window=self._suggestions_frame, anchor="nw")

        def _on_suggestions_configure(event):
            self._suggestions_canvas.configure(scrollregion=self._suggestions_canvas.bbox("all"))

        def _on_suggestions_canvas_resize(event):
            self._suggestions_canvas.itemconfig(self._suggestions_canvas_window, width=event.width)

        self._suggestions_frame.bind("<Configure>", _on_suggestions_configure)
        self._suggestions_canvas.bind("<Configure>", _on_suggestions_canvas_resize)

        def _on_suggestions_mousewheel(event):
            self._suggestions_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_suggestions_mousewheel(event):
            self._suggestions_canvas.bind_all("<MouseWheel>", _on_suggestions_mousewheel)

        def _unbind_suggestions_mousewheel(event):
            self._suggestions_canvas.unbind_all("<MouseWheel>")

        self._suggestions_canvas.bind("<Enter>", _bind_suggestions_mousewheel)
        self._suggestions_canvas.bind("<Leave>", _unbind_suggestions_mousewheel)

        # Inactive state message
        self._meddpicc_status_label = ctk.CTkLabel(self._suggestions_frame,
            text="Activates during live calls",
            text_color=FG_DIM, font=_font(size=9, slant="italic"))
        self._meddpicc_status_label.pack(pady=4)

    # ─────────────────────────── CALLBACKS ───────────────────────────

    def _on_close(self):
        try:
            shutdown_agent()
        except Exception:
            pass
        if self._is_root:
            self.root.destroy()

    def _suggestions_wrap_width(self):
        """Get current wrap width for labels in the suggestions panel (one-shot, no binding)."""
        try:
            w = self._suggestions_canvas.winfo_width() - 60
            return max(w, 200)
        except Exception:
            return 280

    def _toggle_ai(self):
        pass  # No longer needed — kept for compatibility

    def _clear_ai_answers(self):
        self.ai_text.config(state=tk.NORMAL)
        self.ai_text.delete("1.0", tk.END)
        self.ai_text.config(state=tk.DISABLED)

    def _update_coverage_ui(self, coverage):
        """Update MEDDPICC coverage indicator labels."""
        for element, covered in coverage.items():
            lbl = self._coverage_labels.get(element)
            if lbl:
                if covered:
                    lbl.configure(fg_color=ACCENT, text_color=BG_DARK)
                else:
                    lbl.configure(fg_color=BG_DARK, text_color=FG_DIM)

    def _render_suggestions(self, suggestions):
        """Append new suggestions to the panel without destroying existing ones.

        New questions appear at the top. Addressed questions are removed.
        Existing unanswered questions stay in place — no flashing.
        """
        # Remove the "listening" placeholder and status label if present
        for widget in self._suggestions_frame.winfo_children():
            try:
                text = widget.cget("text") if hasattr(widget, "cget") else ""
                if "listening" in str(text).lower() or "activates during" in str(text).lower():
                    widget.destroy()
            except Exception:
                pass

        # Remove cards for questions that have been addressed
        all_questions = self.meeting_assistant.get_question_history()
        addressed_texts = {q["question"] for q in all_questions if q.get("addressed")}
        for widget in list(self._suggestions_frame.winfo_children()):
            q_text = getattr(widget, "_question_text", None)
            if q_text and q_text in addressed_texts:
                widget.destroy()
                self._rendered_questions.discard(q_text)

        # Determine which new questions to add
        new_questions = []
        for s in (suggestions or []):
            q_text = s.get("question", "")
            if q_text and q_text not in self._rendered_questions and q_text not in addressed_texts:
                new_questions.append(s)

        # Insert new question cards at the top (before existing children)
        # We pack them in reverse order with before= the first existing child
        first_existing = None
        children = self._suggestions_frame.winfo_children()
        for child in children:
            if isinstance(child, ctk.CTkFrame):
                first_existing = child
                break

        for s in reversed(new_questions):
            element = s.get("element", "")
            question = s.get("question", "")
            color = MEDDPICC_COLORS.get(element, BORDER)
            row = ctk.CTkFrame(self._suggestions_frame, fg_color=BG_CARD, corner_radius=8,
                               border_width=1, border_color=color)
            row._question_text = question  # Tag for later removal
            if first_existing:
                row.pack(fill=tk.X, pady=2, before=first_existing)
            else:
                row.pack(fill=tk.X, pady=2)
            first_existing = row  # Next one goes before this one
            ctk.CTkLabel(row, text=f"[{element}]",
                         text_color=color,
                         font=_font(weight="bold")).pack(anchor=tk.W, padx=10, pady=(6, 0))
            ctk.CTkLabel(row, text=question, text_color=FG_TEXT,
                         font=_font(size=12),
                         anchor=tk.W, wraplength=self._suggestions_wrap_width()).pack(
                anchor=tk.W, fill=tk.X, padx=10, pady=(2, 6))
            self._rendered_questions.add(question)

        # Ensure status label exists at the bottom
        has_status = False
        for widget in self._suggestions_frame.winfo_children():
            if widget is getattr(self, '_meddpicc_status_label', None):
                has_status = True
                break
        if not has_status:
            self._meddpicc_status_label = ctk.CTkLabel(self._suggestions_frame,
                text="", text_color=FG_DIM, font=_font(size=9, slant="italic"))
            self._meddpicc_status_label.pack(pady=0)

        # If panel is completely empty, show placeholder
        visible_cards = [w for w in self._suggestions_frame.winfo_children()
                         if isinstance(w, ctk.CTkFrame) and hasattr(w, '_question_text')]
        if not visible_cards and not new_questions:
            ctk.CTkLabel(self._suggestions_frame, text="No new suggestions — listening...",
                         text_color=FG_DIM, font=_font(slant="italic")).pack(pady=4)

    def _update_meddpicc_status(self, message):
        """Update the MEDDPICC status message."""
        if not message:
            return
        # Show status as a small label at the bottom of suggestions
        # Don't clear existing suggestions on error
        try:
            self._meddpicc_status_label.configure(text=message)
        except Exception:
            pass

    def _show_question_history(self, element, event=None):
        """Show a popup with all historical questions for a MEDDPICC element."""
        # Dismiss any existing popup
        if self._question_history_popup is not None:
            try:
                self._question_history_popup.destroy()
            except Exception:
                pass
            self._question_history_popup = None

        questions = self.meeting_assistant.get_question_history(element)
        color = MEDDPICC_COLORS.get(element, BORDER)

        # Create popup anchored near the clicked label
        popup = ctk.CTkToplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(fg_color=BG_PANEL)
        self._question_history_popup = popup

        # Position near the clicked label
        if event and event.widget:
            x = event.widget.winfo_rootx()
            y = event.widget.winfo_rooty() + event.widget.winfo_height() + 4
        else:
            x = self.root.winfo_rootx() + 100
            y = self.root.winfo_rooty() + 200
        popup.geometry(f"+{x}+{y}")
        popup.minsize(350, 100)

        # Outer frame with border
        outer = ctk.CTkFrame(popup, fg_color=BG_PANEL, corner_radius=10,
                              border_width=2, border_color=color)
        outer.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Header
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill=tk.X, padx=10, pady=(8, 4))
        ctk.CTkLabel(header, text=f"📋 {element}",
                     text_color=color,
                     font=_font(size=13, weight="bold")).pack(side=tk.LEFT)
        ctk.CTkButton(header, text="✕", width=22, height=22,
                      fg_color="transparent", hover_color=BG_CARD,
                      text_color=FG_DIM, font=_font(size=10),
                      corner_radius=4,
                      command=popup.destroy).pack(side=tk.RIGHT)

        # Question list
        if not questions:
            ctk.CTkLabel(outer, text="No questions generated yet for this element.",
                         text_color=FG_DIM, font=_font(slant="italic")
                         ).pack(padx=12, pady=(4, 10))
        else:
            for q in questions:
                addressed = q.get("addressed", False)
                q_color = ACCENT if addressed else FG_TEXT
                icon = "✅" if addressed else "💬"
                row = ctk.CTkFrame(outer, fg_color=BG_CARD if not addressed else "#0d2818",
                                   corner_radius=6)
                row.pack(fill=tk.X, padx=10, pady=2)
                ctk.CTkLabel(row, text=f"{icon} {q['question']}",
                             text_color=q_color,
                             font=_font(),
                             anchor=tk.W, wraplength=300).pack(
                    anchor=tk.W, fill=tk.X, padx=10, pady=5)

        # Summary line
        total = len(questions)
        addressed_count = sum(1 for q in questions if q.get("addressed"))
        if total > 0:
            ctk.CTkLabel(outer, text=f"{addressed_count}/{total} addressed",
                         text_color=FG_DIM, font=_font(size=9)).pack(pady=(2, 8))
        else:
            ctk.CTkFrame(outer, fg_color="transparent", height=4).pack()

        # Auto-dismiss when clicking elsewhere
        def _dismiss_on_focus_out(e):
            try:
                if popup.winfo_exists():
                    popup.destroy()
                    self._question_history_popup = None
            except Exception:
                pass

        popup.bind("<FocusOut>", _dismiss_on_focus_out)
        # Also dismiss after 10 seconds as a safety net
        popup.after(10000, lambda: _dismiss_on_focus_out(None))

    def _show_meddpicc_summary(self, summary_text):
        """Display post-call MEDDPICC summary in the suggestions area — compact format."""
        for widget in self._suggestions_frame.winfo_children():
            widget.destroy()

        # Title
        ctk.CTkLabel(self._suggestions_frame, text="MEDDPICC Coverage Summary",
                     text_color=FG_BRIGHT, font=_font(size=12, weight="bold")
                     ).pack(anchor=tk.W, pady=(4, 2))

        # Compact summary: one line per element, no follow-up lines
        for line in summary_text.split("\n"):
            line = line.strip()
            if not line or line.startswith("=") or line.startswith("Coverage:") or line.startswith("MEDDPICC"):
                continue
            if line.startswith("↳"):
                continue  # skip verbose follow-up lines
            text_color = ACCENT if line.startswith("✅") else (RED if line.startswith("❌") else FG_TEXT)
            ctk.CTkLabel(self._suggestions_frame, text=line,
                         text_color=text_color,
                         font=_font(),
                         anchor=tk.W, wraplength=self._suggestions_wrap_width()).pack(
                anchor=tk.W, fill=tk.X, padx=4, pady=1)

    def _render_historical_meddpicc(self, coverage, questions):
        """Render MEDDPICC coverage and question history for a loaded historical session."""
        from transcription.meeting_assistant import MEDDPICC_ELEMENTS as _ELEMENTS

        # Update coverage boxes
        for element in _ELEMENTS:
            info = coverage.get(element, {})
            covered = info.get("covered", False) if isinstance(info, dict) else False
            lbl = self._coverage_labels.get(element)
            if lbl:
                if covered:
                    lbl.configure(fg_color=ACCENT, text_color=BG_DARK)
                else:
                    lbl.configure(fg_color=BG_DARK, text_color=FG_DIM)

        # Render questions in the suggestions area
        for widget in self._suggestions_frame.winfo_children():
            widget.destroy()

        if not questions:
            covered_count = sum(1 for e in _ELEMENTS
                                if coverage.get(e, {}).get("covered", False))
            ctk.CTkLabel(self._suggestions_frame,
                         text=f"MEDDPICC: {covered_count}/8 covered · No questions recorded",
                         text_color=FG_DIM, font=_font(size=10)).pack(pady=4)
            return

        # Group questions by element
        from collections import defaultdict
        by_element = defaultdict(list)
        for q in questions:
            by_element[q.get("element", "Unknown")].append(q)

        for element in _ELEMENTS:
            qs = by_element.get(element, [])
            if not qs:
                continue
            info = coverage.get(element, {})
            covered = info.get("covered", False) if isinstance(info, dict) else False
            color = MEDDPICC_COLORS.get(element, BORDER)

            # Element header
            status_icon = "✅" if covered else "❌"
            ctk.CTkLabel(self._suggestions_frame,
                         text=f"{status_icon} {element}",
                         text_color=color,
                         font=_font(weight="bold")).pack(anchor=tk.W, padx=4, pady=(6, 2))

            # Evidence if covered
            if covered and isinstance(info, dict) and info.get("evidence"):
                ctk.CTkLabel(self._suggestions_frame,
                             text=f"   Evidence: {info['evidence']}",
                             text_color=FG_DIM,
                             font=_font(size=9, slant="italic")).pack(anchor=tk.W, padx=8, pady=0)

            for q in qs:
                addressed = q.get("addressed", False)
                q_color = ACCENT if addressed else FG_TEXT
                icon = "✅" if addressed else "💬"
                row = ctk.CTkFrame(self._suggestions_frame,
                                   fg_color="#0d2818" if addressed else BG_CARD,
                                   corner_radius=6)
                row.pack(fill=tk.X, padx=8, pady=1)
                ctk.CTkLabel(row, text=f"{icon} {q['question']}",
                             text_color=q_color,
                             font=_font(),
                             anchor=tk.W, wraplength=self._suggestions_wrap_width()).pack(
                    anchor=tk.W, fill=tk.X, padx=10, pady=4)

    def _check_transcript_for_questions(self, text):
        """Feed finalized transcript line to the MEDDPICC meeting assistant."""
        self.meeting_assistant.add_line(text)

    # ─────────────────────────── POST-CALL CHECKLIST ──────────────────────────

    def _show_checklist(self):
        """Reset and show the post-call progress checklist."""
        for key in self._checklist_items:
            self._checklist_items[key] = "pending"
            icon_lbl, text_lbl = self._checklist_labels[key]
            icon_lbl.configure(text="⬜")
            text_lbl.configure(text_color=FG_DIM)
        self._checklist_frame.pack(fill=tk.X, padx=16, pady=(0, 4),
                                    before=self._transcript_section)

    def _hide_checklist(self):
        """Hide the checklist."""
        self._checklist_frame.pack_forget()

    def _update_checklist(self, key, status):
        """Update a checklist item. status: 'running', 'done', 'failed', 'skipped'."""
        if key not in self._checklist_labels:
            return
        self._checklist_items[key] = status
        icon_lbl, text_lbl = self._checklist_labels[key]
        icons = {"running": "🔄", "done": "✅", "failed": "❌", "skipped": "⏭️"}
        colors = {"running": ORANGE, "done": ACCENT, "failed": RED, "skipped": FG_DIM}
        icon_lbl.configure(text=icons.get(status, "⬜"))
        text_lbl.configure(text_color=colors.get(status, FG_DIM))

    # ─────────────────────────── RECORDING INDICATOR ───────────────────────────

    def _start_recording_pulse(self):
        """Start the pulsing recording indicator."""
        self._recording_pulse_on = True
        if self._recording_dot:
            self._recording_dot.pack(side=tk.LEFT, padx=(4, 0))
        self._pulse_recording()

    def _pulse_recording(self):
        """Toggle visibility of the recording dot for a pulse effect."""
        if not self._recording_pulse_on:
            return
        try:
            current = self._recording_dot.cget("text_color")
            new_color = RED if current == BG_DARK or current == "#0a0a0a" else BG_DARK
            self._recording_dot.configure(text_color=new_color)
            self._recording_pulse_id = self.root.after(600, self._pulse_recording)
        except Exception:
            pass

    def _stop_recording_pulse(self):
        """Stop the pulsing recording indicator."""
        self._recording_pulse_on = False
        if self._recording_pulse_id:
            try:
                self.root.after_cancel(self._recording_pulse_id)
            except Exception:
                pass
            self._recording_pulse_id = None
        if self._recording_dot:
            self._recording_dot.pack_forget()

    # ─────────────────────────── WINDOW TITLE ───────────────────────────

    def _update_window_title(self, customer=None, recording=False):
        """Update the top-level window title with customer name and recording state."""
        try:
            toplevel = self.root.winfo_toplevel()
            if customer and recording:
                toplevel.title(f"🔴 Recording — {customer} — Call Notes")
            elif customer:
                toplevel.title(f"{customer} — Call Notes")
            else:
                toplevel.title("Call Notes — Live Transcriber")
        except Exception:
            pass

    # ─────────────────────────── TOAST HELPER ───────────────────────────

    def _toast(self, message, color=ACCENT):
        """Show a toast notification on the app's root widget."""
        try:
            toplevel = self.root.winfo_toplevel()
            show_toast(toplevel, message, color=color)
        except Exception:
            pass

    # ─────────────────────────── SHARE HTML ───────────────────────────

    def _export_share_html(self):
        """Export notes as a self-contained HTML file for sharing."""
        if not self._current_notes:
            messagebox.showinfo("Nothing to export", "No notes to export.")
            return
        customer = self.customer_var.get().strip() or "Notes"
        path = filedialog.asksaveasfilename(
            defaultextension=".html", filetypes=[("HTML File", "*.html")],
            initialfile=f"{customer}_call_summary.html")
        if path:
            export_share_html(customer, self._current_notes, path)
            self.status_var.set(f"Shared: {path}")
            self._toast(f"📤 Summary exported to {os.path.basename(path)}")


    # ─────────────────────────── HISTORY ───────────────────────────

    def _refresh_history(self):
        threading.Thread(target=self._load_history_bg, daemon=True).start()

    def _load_history_bg(self):
        try:
            customers = ["(All)"] + get_all_customers()
            selected = self.history_filter_var.get()
            customer = None if selected == "(All)" else selected
            items = list_sessions(customer)
            self.root.after(0, self._update_history_ui, customers, items)
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set(f"History error: {e}"))

    def _update_history_ui(self, customers, items):
        self.history_filter_combo.configure(values=customers)
        self._history_items = items
        self.history_list.delete(0, tk.END)
        for item in items:
            ts = item["timestamp"][:16].replace("T", " ")
            self.history_list.insert(tk.END, f"{item['customer_name']}  ·  {ts}")

    def _on_history_select(self, event):
        # Block history selection while notes are being generated
        if getattr(self, '_generating', False):
            return
        sel = self.history_list.curselection()
        if not sel:
            return
        item = self._history_items[sel[0]]
        self._current_transcript = item.get("transcript", "")
        self._current_notes = item.get("notes", "")
        self._current_email = item.get("followup_email", "")

        for widget, content in [(self.transcript_text, self._current_transcript),
                                 (self.notes_text, self._current_notes),
                                 (self.email_text, self._current_email)]:
            widget.config(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, content)
            widget.config(state=tk.DISABLED)

        self.customer_var.set(item["customer_name"])
        self.export_docx_btn.configure(state=tk.NORMAL)
        self.export_pdf_btn.configure(state=tk.NORMAL)
        self.share_html_btn.configure(state=tk.NORMAL if self._current_notes else tk.DISABLED)
        self.sift_btn.configure(state=tk.NORMAL if self._current_notes else tk.DISABLED)
        self.activity_btn.configure(state=tk.NORMAL if self._current_notes else tk.DISABLED)
        self.copy_transcript_btn.configure(state=tk.NORMAL if self._current_transcript else tk.DISABLED)
        self.outlook_draft_btn.configure(state=tk.NORMAL if self._current_email else tk.DISABLED)
        self.status_var.set(f"Loaded session from {item['timestamp'][:16]}")

        # Restore MEDDPICC state if available
        import json as _json
        meddpicc_raw = item.get("meddpicc_data", "")
        if meddpicc_raw:
            try:
                meddpicc = _json.loads(meddpicc_raw)
                self.meeting_assistant.load_state(meddpicc)
                # Show the questions in the suggestions area
                questions = meddpicc.get("questions", [])
                coverage = meddpicc.get("coverage", {})
                self._render_historical_meddpicc(coverage, questions)
            except (_json.JSONDecodeError, TypeError):
                pass
        else:
            # Clear MEDDPICC display for sessions without data
            self._update_coverage_ui({e: False for e in MEDDPICC_ELEMENTS})
            for widget in self._suggestions_frame.winfo_children():
                widget.destroy()
            ctk.CTkLabel(self._suggestions_frame, text="No MEDDPICC data for this session",
                         text_color=FG_DIM, font=_font(size=10, slant="italic")).pack(pady=4)

    # ─────────────────────────── EXPORT ───────────────────────────

    def _send_to_outlook_draft(self):
        email = self._current_email.strip()
        if not email:
            messagebox.showinfo("Nothing to send", "No follow-up email generated yet.")
            return
        import re

        # Strip markdown syntax
        clean = re.sub(r'\*\*(.+?)\*\*', r'\1', email)
        clean = re.sub(r'__(.+?)__', r'\1', clean)
        clean = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', clean)
        clean = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'\1', clean)
        clean = re.sub(r'^#{1,6}\s+', '', clean, flags=re.MULTILINE)
        clean = re.sub(r'`(.+?)`', r'\1', clean)

        # Parse subject line
        lines = clean.split("\n", 2)
        subject = ""
        body = clean
        if lines[0].lower().startswith("subject:"):
            subject = lines[0].split(":", 1)[1].strip()
            body = "\n".join(lines[1:]).strip()

        # Convert plain text to styled HTML for Outlook
        html_lines = []
        lines_list = body.split("\n")
        i = 0
        while i < len(lines_list):
            stripped = lines_list[i].strip()
            if not stripped:
                # Blank line = intentional paragraph break
                html_lines.append('<br>')
                i += 1
                continue

            if stripped.startswith("- "):
                # Collect consecutive bullet items
                bullets = []
                while i < len(lines_list) and lines_list[i].strip().startswith("- "):
                    bullets.append(lines_list[i].strip()[2:])
                    i += 1
                bullet_html = "".join(
                    f'<li style="margin: 0; padding: 0;">{b}</li>' for b in bullets
                )
                html_lines.append(
                    f'<ul style="margin: 0 0 0 24px; padding: 0;">{bullet_html}</ul>'
                )
            elif len(stripped) < 60 and not stripped.endswith(".") and not stripped.endswith(","):
                # Section header — eat any blank lines immediately after it
                html_lines.append(f'<p style="margin: 0;"><b>{stripped}</b></p>')
                i += 1
                while i < len(lines_list) and not lines_list[i].strip():
                    i += 1  # skip blank lines between header and content
            else:
                html_lines.append(f'<p style="margin: 0;">{stripped}</p>')
                i += 1

        html_body = (
            '<div style="font-family: Aptos, Calibri, sans-serif; font-size: 11pt; color: #1a1a1a; line-height: 1.5;">'
            + "\n".join(html_lines)
            + "</div>"
        )

        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)
            mail.Subject = subject
            # Display first so Outlook inserts the default signature
            mail.Display()
            # Prepend our email body before the signature
            mail.HTMLBody = html_body + mail.HTMLBody
            mail.Save()
            self.status_var.set("📨 Email saved to Outlook Drafts!")
            self._toast("📨 Email saved to Outlook Drafts!")
        except ImportError:
            messagebox.showerror("Missing Library",
                                 "Install pywin32: python -m pip install pywin32")
        except Exception as e:
            messagebox.showerror("Outlook Error", f"Could not create draft:\n{e}")

    def _copy_transcript(self):
        transcript = self._current_transcript.strip()
        if not transcript:
            messagebox.showinfo("Nothing to copy", "No transcript available.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(transcript)
        self.status_var.set("📋 Transcript copied to clipboard!")
        self._toast("📋 Transcript copied to clipboard!")

    def _export_docx(self):
        if not self._current_notes:
            messagebox.showinfo("Nothing to export", "No notes to export.")
            return
        customer = self.customer_var.get().strip() or "Notes"
        path = filedialog.asksaveasfilename(
            defaultextension=".docx", filetypes=[("Word Document", "*.docx")],
            initialfile=f"{customer}_notes.docx")
        if path:
            _md_to_docx(customer, self._current_notes, path)
            self.status_var.set(f"Exported: {path}")
            self._toast(f"📄 DOCX exported: {os.path.basename(path)}")

    def _export_pdf(self):
        if not self._current_notes:
            messagebox.showinfo("Nothing to export", "No notes to export.")
            return
        customer = self.customer_var.get().strip() or "Notes"
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF Document", "*.pdf")],
            initialfile=f"{customer}_notes.pdf")
        if not path:
            return
        try:
            from fpdf import FPDF
            pdf = FPDF()
            pdf.add_page()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.set_font("Helvetica", size=11)
            for line in self._current_notes.split("\n"):
                stripped = line.strip()
                if stripped.startswith("## "):
                    pdf.set_font("Helvetica", "B", 14)
                    pdf.cell(0, 10, stripped[3:], new_x="LMARGIN", new_y="NEXT")
                    pdf.set_font("Helvetica", size=11)
                elif stripped.startswith("# "):
                    pdf.set_font("Helvetica", "B", 16)
                    pdf.cell(0, 10, stripped[2:], new_x="LMARGIN", new_y="NEXT")
                    pdf.set_font("Helvetica", size=11)
                elif stripped:
                    pdf.multi_cell(0, 6, stripped)
                else:
                    pdf.ln(4)
            pdf.output(path)
            self.status_var.set(f"Exported: {path}")
        except ImportError:
            messagebox.showerror("Missing Library",
                                 "Install fpdf2: python -m pip install fpdf2")

    def _submit_sift(self):
        """Queue a SIFT insight from the currently loaded notes."""
        if not self._current_notes:
            messagebox.showinfo("No Notes", "Load a call from history first.")
            return
        customer = self.customer_var.get().strip() or "Unknown"
        self.sift_btn.configure(state=tk.DISABLED, text="📊 Extracting...")
        self.status_var.set("Extracting SIFT insight...")

        def _flash_btn(text, color):
            """Flash the button with a result state, then reset after 3s."""
            self.sift_btn.configure(text=text, fg_color=color, state=tk.DISABLED)
            self.root.after(3000, lambda: self.sift_btn.configure(
                state=tk.NORMAL, text="📊 SIFT", fg_color="#1f2937"))

        def _run():
            try:
                path = queue_sift_insight(customer, self._current_notes)
                if path == "DUPLICATE":
                    self.root.after(0, lambda: self.status_var.set(
                        f"⚠️ SIFT insight already queued for {customer}"))
                    self.root.after(0, lambda: _flash_btn("⚠️ Duplicate", "#92400e"))
                    return
                if not path:
                    self.root.after(0, lambda: self.status_var.set(
                        "SIFT extraction failed — check console"))
                    self.root.after(0, lambda: _flash_btn("❌ Failed", "#7f1d1d"))
                    return

                self.root.after(0, lambda: self.status_var.set(
                    f"✅ SIFT insight queued for {customer}"))
                self.root.after(0, lambda: self._toast(f"📊 SIFT insight queued for {customer}"))
                self.root.after(0, lambda: _flash_btn("✅ Queued", "#065f46"))

            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "SIFT Error", f"Failed to extract insight:\n{e}"))
                self.root.after(0, lambda: self.status_var.set("SIFT error"))
                self.root.after(0, lambda: _flash_btn("❌ Error", "#7f1d1d"))

        threading.Thread(target=_run, daemon=True).start()

    def _log_activity(self):
        """Queue an SA activity log from the currently loaded notes."""
        if not self._current_notes:
            messagebox.showinfo("No Notes", "Load a call from history first.")
            return
        customer = self.customer_var.get().strip() or "Unknown"
        self.activity_btn.configure(state=tk.DISABLED, text="📝 Extracting...")
        self.status_var.set("Extracting activity details...")

        def _flash_btn(text, color):
            """Flash the button with a result state, then reset after 3s."""
            self.activity_btn.configure(text=text, fg_color=color, state=tk.DISABLED)
            self.root.after(3000, lambda: self.activity_btn.configure(
                state=tk.NORMAL, text="📝 Activity", fg_color="#1f2937"))

        def _run():
            try:
                path = queue_activity(customer, self._current_notes)
                if path == "DUPLICATE":
                    self.root.after(0, lambda: self.status_var.set(
                        f"⚠️ Activity already queued for {customer}"))
                    self.root.after(0, lambda: _flash_btn("⚠️ Duplicate", "#92400e"))
                    return
                if not path:
                    self.root.after(0, lambda: self.status_var.set(
                        "Activity extraction failed — check console"))
                    self.root.after(0, lambda: _flash_btn("❌ Failed", "#7f1d1d"))
                    return

                self.root.after(0, lambda: self.status_var.set(
                    f"✅ Activity queued for {customer}"))
                self.root.after(0, lambda: self._toast(f"📝 Activity queued for {customer}"))
                self.root.after(0, lambda: _flash_btn("✅ Queued", "#065f46"))

            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Activity Error", f"Failed to extract activity:\n{e}"))
                self.root.after(0, lambda: self.status_var.set("Activity error"))
                self.root.after(0, lambda: _flash_btn("❌ Error", "#7f1d1d"))

        threading.Thread(target=_run, daemon=True).start()


    # ─────────────────────────── DEVICES ───────────────────────────

    def _load_devices(self):
        temp = LiveTranscriber()
        devices = temp.get_audio_devices()
        self._devices = devices
        names = [f"{i}: {name}" for i, name in devices]

        system_names = ["(None)"] + names
        mic_names = ["(None)"] + names

        self.system_device_combo.configure(values=system_names)
        self.mic_device_combo.configure(values=mic_names)

        cable_idx = None
        mic_idx = None
        for j, (i, name) in enumerate(devices):
            if "cable output" in name.lower() and "virtual cable" in name.lower() and cable_idx is None:
                cable_idx = j
            if "microphone" in name.lower() and mic_idx is None:
                mic_idx = j

        self.system_device_combo.set(system_names[cable_idx + 1] if cable_idx is not None else system_names[0])
        self.mic_device_combo.set(mic_names[mic_idx + 1] if mic_idx is not None else mic_names[0])

    def _get_selected_device(self, combo):
        val = combo.get()
        if val == "(None)" or not val:
            return None
        try:
            idx = int(val.split(":")[0])
            return idx
        except (ValueError, IndexError):
            return None

    # ─────────────────────────── TRANSCRIPT ───────────────────────────

    def _on_partial(self, text):
        self.root.after(0, self._safe_show_partial, text)

    def _on_final(self, text):
        self.root.after(0, self._safe_show_final, text)
        self._check_transcript_for_questions(text)

    def _is_transcript_at_bottom(self):
        """Check if the user is scrolled to (or near) the bottom of the transcript."""
        yview = self.transcript_text.yview()
        return yview[1] >= 0.95  # within 5% of the bottom

    def _safe_show_partial(self, text):
        self.transcript_text.config(state=tk.NORMAL)
        self.transcript_text.delete("partial_start", "end-1c")
        self.transcript_text.insert("partial_start", text)
        if self._is_transcript_at_bottom():
            self.transcript_text.see(tk.END)
        self.transcript_text.config(state=tk.DISABLED)

    def _safe_show_final(self, text):
        self.transcript_text.config(state=tk.NORMAL)
        self.transcript_text.delete("partial_start", "end-1c")
        self.transcript_text.insert("partial_start", text + "\n")
        self.transcript_text.mark_set("partial_start", "end-1c")
        if self._is_transcript_at_bottom():
            self.transcript_text.see(tk.END)
        self.transcript_text.config(state=tk.DISABLED)

    # ─────────────────────────── RECORDING ───────────────────────────

    def _start(self):
        customer = self.customer_var.get().strip()
        if not customer:
            messagebox.showwarning("Missing Info", "Please enter a customer name.")
            return

        system_dev = self._get_selected_device(self.system_device_combo)
        mic_dev = self._get_selected_device(self.mic_device_combo)

        if system_dev is None and mic_dev is None:
            messagebox.showwarning("No Device", "Please select at least one audio device.")
            return

        for w in (self.transcript_text, self.notes_text, self.email_text):
            w.config(state=tk.NORMAL)
            w.delete("1.0", tk.END)
            w.config(state=tk.DISABLED)

        self._current_transcript = ""
        self._current_notes = ""
        self._current_email = ""
        self._pending_questions.clear()
        self._rendered_questions.clear()
        self.export_docx_btn.configure(state=tk.DISABLED)
        self.export_pdf_btn.configure(state=tk.DISABLED)
        self.share_html_btn.configure(state=tk.DISABLED)
        self.outlook_draft_btn.configure(state=tk.DISABLED)
        self.copy_transcript_btn.configure(state=tk.DISABLED)

        self.transcriber = LiveTranscriber(
            system_device=system_dev, mic_device=mic_dev,
            on_partial=self._on_partial, on_final=self._on_final)
        self.transcriber.on_status = lambda msg: self.root.after(0, self.status_var.set, msg)
        try:
            self.transcriber.start()
        except Exception as e:
            messagebox.showerror("Audio Device Error",
                                 f"Failed to start recording:\n{e}\n\n"
                                 "Try reselecting your audio devices or check that "
                                 "VB-Cable / your microphone is connected.")
            self.transcriber = None
            self.status_var.set("Ready")
            return

        if not self.transcriber._running:
            # Both streams failed inside start()
            messagebox.showwarning("Audio Device Error",
                                    "Could not open any audio device.\n\n"
                                    "Check your device settings and try again.")
            self.transcriber = None
            self.status_var.set("Ready")
            return

        self.meeting_assistant.activate()

        # Seed MEDDPICC from previous calls with this customer
        previous_meddpicc = get_latest_meddpicc(customer)
        if previous_meddpicc:
            self.meeting_assistant.seed_from_previous(previous_meddpicc)

        self.transcript_text.config(state=tk.NORMAL)
        self.transcript_text.mark_set("partial_start", "end-1c")
        self.transcript_text.mark_gravity("partial_start", tk.LEFT)
        self.transcript_text.config(state=tk.DISABLED)

        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status_var.set("🔴 Recording...")
        self._start_recording_pulse()
        self._update_window_title(customer=customer, recording=True)

    def _stop(self):
        if not self.transcriber:
            return
        self.status_var.set("Stopping recording...")
        self.stop_btn.configure(state=tk.DISABLED)
        self._stop_recording_pulse()

        # Run the blocking stop + generate on a background thread
        # so the UI stays responsive while the Transcribe stream closes.
        threading.Thread(target=self._stop_and_generate, daemon=True).start()

    def _stop_and_generate(self):
        """Background thread: stop transcriber, then kick off note generation."""
        self.transcriber.stop()                       # blocks up to 10s
        self.meeting_assistant.deactivate()
        transcript = self.transcriber.get_full_transcript()
        self._current_transcript = transcript

        # UI updates must happen on the main thread
        def _continue_on_ui():
            self.start_btn.configure(state=tk.NORMAL)

            if not transcript:
                self.status_var.set("No speech detected.")
                self._update_window_title()
                messagebox.showinfo("Empty", "No transcript was captured.")
                return

            self._locked_customer = self.customer_var.get().strip() or "Unknown"
            self._generating = True

            self.status_var.set("Generating notes & follow-up email...")
            manual_notes = self.manual_notes_text.get("1.0", tk.END).strip()
            threading.Thread(target=self._generate_and_save,
                             args=(transcript, manual_notes),
                             daemon=True).start()

        self.root.after(0, _continue_on_ui)

    def _generate_and_save(self, transcript, manual_notes=""):
        customer = self._locked_customer
        self.root.after(0, self._prepare_notes_for_streaming)
        self.root.after(0, self._prepare_email_for_streaming)
        self.root.after(0, self._show_checklist)

        # Mark parallel steps as running
        self.root.after(0, lambda: self._update_checklist("notes", "running"))
        self.root.after(0, lambda: self._update_checklist("email", "running"))
        self.root.after(0, lambda: self._update_checklist("outlook_task", "running"))

        # Shared state for parallel results
        results = {"notes": None, "email": None, "filepath": None,
                   "notes_error": None, "email_error": None}

        def run_notes():
            try:
                def on_chunk(text):
                    self.root.after(0, self._append_notes_chunk, text)
                notes = generate_notes(transcript, customer, on_chunk=on_chunk, manual_notes=manual_notes)
                results["notes"] = notes
                self._current_notes = notes
                results["filepath"] = save_notes(customer, notes)
                self.root.after(0, lambda: self._update_checklist("notes", "done"))
            except Exception as e:
                results["notes_error"] = e
                self.root.after(0, lambda: self._update_checklist("notes", "failed"))

        def run_email():
            try:
                def on_email_chunk(text):
                    self.root.after(0, self._append_email_chunk, text)
                email = generate_followup_email(transcript, customer, on_chunk=on_email_chunk, manual_notes=manual_notes)
                results["email"] = email
                self._current_email = email
                self.root.after(0, lambda: self._update_checklist("email", "done"))
            except Exception as e:
                results["email_error"] = e
                self.root.after(0, lambda: self._update_checklist("email", "failed"))

        def run_outlook_task():
            try:
                if create_followup_task(customer):
                    self.root.after(0, lambda: self._update_checklist("outlook_task", "done"))
                else:
                    self.root.after(0, lambda: self._update_checklist("outlook_task", "failed"))
            except Exception as e:
                print(f"[outlook tasks] Error: {e}")
                self.root.after(0, lambda: self._update_checklist("outlook_task", "failed"))

        notes_thread = threading.Thread(target=run_notes, daemon=True)
        email_thread = threading.Thread(target=run_email, daemon=True)
        outlook_thread = threading.Thread(target=run_outlook_task, daemon=True)
        notes_thread.start()
        email_thread.start()
        outlook_thread.start()
        notes_thread.join()
        email_thread.join()
        # Don't wait for outlook task — it'll update the checklist when ready

        if results["notes_error"]:
            self.root.after(0, lambda: messagebox.showerror(
                "Error", f"Failed to generate notes:\n{results['notes_error']}"))
            self.root.after(0, lambda: self.status_var.set("Error generating notes."))
            self._generating = False
            self.root.after(0, lambda: self.status_var.set("Error generating notes."))
            return

        # Save session
        self.root.after(0, lambda: self._update_checklist("save", "running"))
        try:
            import json as _json
            meddpicc_data = _json.dumps(self.meeting_assistant.export_state())
            save_session(customer, transcript,
                         results["notes"] or "", results["filepath"] or "",
                         followup_email=results["email"] or "",
                         meddpicc_data=meddpicc_data)
            self.root.after(0, lambda: self._update_checklist("save", "done"))
        except Exception:
            self.root.after(0, lambda: self._update_checklist("save", "failed"))

        self.root.after(0, lambda: self.status_var.set(f"Notes saved: {results['filepath']}"))
        self.root.after(0, lambda: self.export_docx_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.export_pdf_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.share_html_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.sift_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.activity_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.copy_transcript_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.outlook_draft_btn.configure(
            state=tk.NORMAL if results["email"] else tk.DISABLED))
        self.root.after(0, self._refresh_history)
        self.root.after(0, lambda: self._toast(f"✅ Notes generated for {customer}"))
        self.root.after(0, lambda: self._update_window_title(customer=customer))

        if results["email_error"]:
            self.root.after(0, lambda: self.status_var.set(
                f"Notes saved but email failed: {results['email_error']}"))

        # Extract competitor mentions (still depends on notes)
        if results["notes"]:
            self.root.after(0, lambda: self._update_checklist("competitors", "running"))
            try:
                mentions = extract_competitors(results["notes"], customer)
                if mentions:
                    save_competitor_mentions(customer, mentions)
                    print(f"[competitive intel] Saved {len(mentions)} competitor mention(s)")
                self.root.after(0, lambda: self._update_checklist("competitors", "done"))
            except Exception as e:
                print(f"[competitive intel] Error: {e}")
                self.root.after(0, lambda: self._update_checklist("competitors", "failed"))

            # SA activity is queued on-demand via the "Queue Activity" button,
            # not automatically on stop. See _log_activity().
        else:
            self.root.after(0, lambda: self._update_checklist("competitors", "skipped"))

        # Queue MEDDPICC data for end-of-day AWSentral submission
        try:
            from transcription.meddpicc_queue import queue_meddpicc
            meddpicc_path = queue_meddpicc(customer, self.meeting_assistant.export_state())
            if meddpicc_path:
                print(f"[meddpicc] Queued for AWSentral: {meddpicc_path}")
        except Exception as e:
            print(f"[meddpicc] Queue error: {e}")

        # Unlock UI — generation complete
        self._generating = False

    def _prepare_notes_for_streaming(self):
        self.notes_text.config(state=tk.NORMAL)
        self.notes_text.delete("1.0", tk.END)

    def _generate_prep(self):
        customer = self.customer_var.get().strip()
        if not customer:
            messagebox.showwarning("Missing Info", "Enter a customer name first.")
            return

        self.prep_btn.configure(state=tk.DISABLED, text="⏳ Loading...")
        self.status_var.set(f"Generating pre-call prep for {customer}...")

        # Auto-expand Call Intelligence section for prep content
        if not self._intel_section._expanded:
            self._intel_section.toggle()

        # Clear AI panel and show status
        self.ai_text.config(state=tk.NORMAL)
        self.ai_text.delete("1.0", tk.END)
        self.ai_text.insert(tk.END, f"📋 Pre-Call Prep: {customer}\n", "question")
        self.ai_text.insert(tk.END, "⏳ Loading previous sessions...\n", "status")
        self.ai_text.config(state=tk.DISABLED)

        def run():
            try:
                sessions = list_sessions(customer)[:3]

                local_notes = []
                try:
                    all_notes = scan_notes()
                    customer_lower = customer.lower()
                    for note in all_notes:
                        if customer_lower in note.get("customer", "").lower():
                            fpath = note.get("filepath", "")
                            if fpath and os.path.exists(fpath):
                                try:
                                    if fpath.lower().endswith(".docx"):
                                        # Extract text from DOCX files properly
                                        from docx import Document
                                        doc = Document(fpath)
                                        content = "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:5000]
                                    else:
                                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                                            content = f.read()[:5000]
                                    if content.strip():
                                        local_notes.append({
                                            "timestamp": note.get("date", ""),
                                            "notes": content,
                                            "source": f"[{note.get('source', 'file')}] {note.get('filename', '')}",
                                        })
                                except Exception:
                                    pass
                    local_notes = local_notes[:5]
                except Exception:
                    pass

                all_prep_notes = list(sessions) + list(local_notes)

                if not all_prep_notes:
                    self.root.after(0, self._prep_no_history, customer)
                    return

                db_count = len(sessions)
                file_count = len(local_notes)
                self.root.after(0, lambda: self._prep_update_status(
                    f"Found {db_count} session(s) + {file_count} note file(s). Generating prep brief..."))

                self._prep_streaming_started = False
                self._prep_md_streamer = MarkdownStreamer(self.ai_text)

                def on_chunk(text):
                    self.root.after(0, self._prep_append_chunk, text)

                generate_prep_summary(all_prep_notes, customer, on_chunk=on_chunk)

                self.root.after(0, self._prep_finish)
            except Exception as e:
                self.root.after(0, lambda: self._prep_error(str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _prep_no_history(self, customer):
        self.ai_text.config(state=tk.NORMAL)
        pos = self.ai_text.search("⏳ Loading", "1.0", tk.END)
        if pos:
            self.ai_text.delete(pos, f"{pos} lineend+1c")
        self.ai_text.insert(tk.END, f"No previous sessions found for {customer}.\n", "status")
        self.ai_text.config(state=tk.DISABLED)
        self.prep_btn.configure(state=tk.NORMAL, text="📋 Pre-Call Prep")
        self.status_var.set("No history found for this customer.")

    def _prep_update_status(self, msg):
        self.ai_text.config(state=tk.NORMAL)
        pos = self.ai_text.search("⏳", "1.0", tk.END)
        if pos:
            self.ai_text.delete(pos, f"{pos} lineend+1c")
        self.ai_text.insert(tk.END, f"⏳ {msg}\n", "status")
        self.ai_text.config(state=tk.DISABLED)

    def _is_ai_text_at_bottom(self):
        """Check if the user is scrolled to (or near) the bottom of the AI panel."""
        yview = self.ai_text.yview()
        return yview[1] >= 0.95

    def _prep_append_chunk(self, text):
        self.ai_text.config(state=tk.NORMAL)
        if not self._prep_streaming_started:
            self._prep_streaming_started = True
            pos = self.ai_text.search("⏳", "1.0", tk.END)
            if pos:
                self.ai_text.delete(pos, f"{pos} lineend+1c")
        self._prep_md_streamer.feed(text)
        if self._is_ai_text_at_bottom():
            self.ai_text.see(tk.END)
        self.ai_text.config(state=tk.DISABLED)

    def _prep_finish(self):
        self.ai_text.config(state=tk.NORMAL)
        if hasattr(self, '_prep_md_streamer'):
            self._prep_md_streamer.flush()
        if self._is_ai_text_at_bottom():
            self.ai_text.see(tk.END)
        self.ai_text.config(state=tk.DISABLED)
        self.prep_btn.configure(state=tk.NORMAL, text="📋 Pre-Call Prep")
        self.status_var.set("Pre-call prep ready.")

    def _prep_error(self, error):
        self.ai_text.config(state=tk.NORMAL)
        self.ai_text.insert(tk.END, f"\n⚠️ Error: {error}\n", "status")
        self.ai_text.config(state=tk.DISABLED)
        self.prep_btn.configure(state=tk.NORMAL, text="📋 Pre-Call Prep")
        self.status_var.set("Prep generation failed.")

    def _append_notes_chunk(self, text):
        self.notes_text.config(state=tk.NORMAL)
        self.notes_text.insert(tk.END, text)
        self.notes_text.see(tk.END)

    def _prepare_email_for_streaming(self):
        self.email_text.config(state=tk.NORMAL)
        self.email_text.delete("1.0", tk.END)

    def _append_email_chunk(self, text):
        self.email_text.config(state=tk.NORMAL)
        self.email_text.insert(tk.END, text)
        self.email_text.see(tk.END)


class NotesRetrieverTab:
    """Tab 2 — multi-turn chat with historical call notes via Claude Opus 4.6."""

    def __init__(self, parent):
        self._notes_meta = []
        self._conversation_history = []
        self._streaming_started = False
        self._md_streamer = None
        self._is_responding = False
        self._current_session_ts = None
        self._session_items = []
        self._parent = parent
        self._build_ui(parent)
        # Delay background work until the main loop is running
        parent.after(100, self._start_background_init)

    def _start_background_init(self):
        """Start background threads after mainloop is running (avoids RuntimeError)."""
        threading.Thread(target=self._refresh_index, daemon=True).start()
        threading.Thread(target=self._init_history_table, daemon=True).start()

    def _build_ui(self, parent):
        # ── Top bar (spans full width) ──────────────────────────────────────
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill=tk.X, padx=16, pady=(14, 6))

        ctk.CTkLabel(top, text="📂  Historical Notes Retrieval",
                     font=_font(size=15, weight="bold"),
                     text_color=FG_BRIGHT).pack(side=tk.LEFT)

        ctk.CTkButton(
            top, text="＋ New Chat", width=110, height=30,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color=BG_DARK,
            border_width=0, corner_radius=6,
            font=_font(weight="bold"),
            command=self._new_chat).pack(side=tk.RIGHT, padx=(8, 0))

        ctk.CTkButton(
            top, text="⟳ Refresh Index", width=130, height=30,
            fg_color=BG_INPUT, hover_color=BG_CARD, text_color=ACCENT,
            border_width=1, border_color=BORDER, corner_radius=6,
            font=_font(),
            command=lambda: threading.Thread(target=self._refresh_index, daemon=True).start()
        ).pack(side=tk.RIGHT, padx=(8, 0))

        # Clickable index summary
        self.index_label = ctk.CTkLabel(
            top, text="Scanning...", text_color=ACCENT,
            font=_font(), cursor="hand2")
        self.index_label.pack(side=tk.RIGHT, padx=(0, 12))
        self.index_label.bind("<Button-1>", lambda e: self._toggle_index_panel())

        # Collapsible index panel (hidden by default)
        self._index_panel_visible = False
        self._index_panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=8,
                                          border_width=1, border_color=BORDER)
        index_header = ctk.CTkFrame(self._index_panel, fg_color="transparent")
        index_header.pack(fill=tk.X, padx=10, pady=(8, 4))
        ctk.CTkLabel(index_header, text="Indexed Notes",
                     font=_font(weight="bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        ctk.CTkButton(index_header, text="✕", width=28, height=24,
                      fg_color="transparent", hover_color=BG_INPUT,
                      text_color=FG_DIM, font=_font(),
                      command=self._toggle_index_panel).pack(side=tk.RIGHT)
        list_frame = ctk.CTkFrame(self._index_panel, fg_color=BG_INPUT, corner_radius=6)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.notes_list = tk.Listbox(
            list_frame, bg=BG_INPUT, fg=FG_TEXT,
            selectbackground=ACCENT, selectforeground=BG_DARK,
            font=("Segoe UI", 9), borderwidth=0, highlightthickness=0,
            activestyle="none", height=12)
        sb_idx = tk.Scrollbar(list_frame, command=self.notes_list.yview,
                               bg=BG_INPUT, troughcolor=BG_INPUT, bd=0)
        self.notes_list.configure(yscrollcommand=sb_idx.set)
        self.notes_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        sb_idx.pack(side=tk.RIGHT, fill=tk.Y)

        self._top_bar = top  # anchor for index panel insertion

        # ── Horizontal split: history sidebar + main content ───────────────
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        body.columnconfigure(0, weight=0)   # sidebar — fixed width
        body.columnconfigure(1, weight=1)   # main content
        body.rowconfigure(0, weight=1)

        # ── Left: Session History sidebar ──────────────────────────────────
        sidebar = ctk.CTkFrame(body, fg_color=BG_PANEL, corner_radius=10,
                                border_width=1, border_color=BORDER, width=320)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(16, 6), pady=(0, 8))
        sidebar.grid_propagate(False)

        sh_header = ctk.CTkFrame(sidebar, fg_color="transparent")
        sh_header.pack(fill=tk.X, padx=10, pady=(10, 4))
        ctk.CTkLabel(sh_header, text="🕘 History",
                     font=_font(size=12, weight="bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        ctk.CTkButton(sh_header, text="⟳", width=28, height=24,
                      fg_color="transparent", hover_color=BG_INPUT,
                      text_color=ACCENT, font=_font(size=12),
                      command=lambda: threading.Thread(
                          target=self._load_session_history, daemon=True).start()
                      ).pack(side=tk.RIGHT)

        self._sh_filter_var = tk.StringVar(value="retrieval")

        sh_list_frame = ctk.CTkFrame(sidebar, fg_color=BG_INPUT, corner_radius=6)
        sh_list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
        self._sh_listbox = tk.Listbox(
            sh_list_frame, bg=BG_INPUT, fg=FG_TEXT,
            selectbackground=ACCENT, selectforeground=BG_DARK,
            font=("Segoe UI", 9), borderwidth=0, highlightthickness=0,
            activestyle="none")
        sh_sb = tk.Scrollbar(sh_list_frame, command=self._sh_listbox.yview,
                              bg=BG_INPUT, troughcolor=BG_INPUT, bd=0)
        self._sh_listbox.configure(yscrollcommand=sh_sb.set)
        self._sh_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        sh_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._sh_listbox.bind("<<ListboxSelect>>", self._on_session_select)

        sh_btn_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        sh_btn_row.pack(fill=tk.X, padx=10, pady=(0, 10))
        ctk.CTkButton(sh_btn_row, text="🗑 Delete", width=90, height=26,
                      fg_color=BG_INPUT, hover_color=RED, text_color=FG_BRIGHT,
                      border_width=1, border_color=BORDER, corner_radius=6,
                      font=_font(size=10),
                      command=self._delete_selected_session).pack(side=tk.LEFT)

        # ── Right: main content ────────────────────────────────────────────
        main = ctk.CTkFrame(body, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=0)
        main.rowconfigure(2, weight=1)
        main.columnconfigure(0, weight=1)

        # Source + customer filter row
        filter_row = ctk.CTkFrame(main, fg_color="transparent")
        filter_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(filter_row, text="Source:",
                     text_color=FG_DIM, font=_font()).pack(side=tk.LEFT)
        self.source_filter_var = tk.StringVar(value="All Sources")
        source_options = ["All Sources"] + [label for _, label in NOTE_SOURCES]
        self.source_combo = ctk.CTkComboBox(
            filter_row, variable=self.source_filter_var, width=140,
            fg_color=BG_INPUT, border_color=BORDER, button_color=BORDER,
            button_hover_color=ACCENT, dropdown_fg_color=BG_INPUT,
            dropdown_hover_color=ACCENT, text_color=FG_BRIGHT,
            font=_font(), state="readonly",
            values=source_options,
            command=lambda _: self._new_chat())
        self.source_combo.pack(side=tk.LEFT, padx=(8, 16))
        ctk.CTkLabel(filter_row, text="Customer:",
                     text_color=FG_DIM, font=_font()).pack(side=tk.LEFT)
        self.customer_filter_var = tk.StringVar(value="(All)")
        self._customer_values = ["(All)"]
        self.customer_btn = ctk.CTkButton(
            filter_row, textvariable=self.customer_filter_var, width=200,
            fg_color=BG_INPUT, hover_color=BG_CARD, text_color=FG_BRIGHT,
            font=_font(), corner_radius=6,
            border_width=1, border_color=BORDER, anchor="w",
            command=self._open_customer_picker)
        self.customer_btn.pack(side=tk.LEFT, padx=(8, 0))
        ctk.CTkLabel(filter_row,
                     text="  (changing filters starts a new chat)",
                     text_color=FG_DIM, font=_font(size=10)).pack(side=tk.LEFT)

        # Suggested prompts
        prompts_frame = ctk.CTkFrame(main, fg_color="transparent")
        prompts_frame.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(prompts_frame, text="Suggestions:",
                     text_color=FG_DIM, font=_font(size=10)).pack(side=tk.LEFT)
        for prompt in [
            "What action items are outstanding?",
            "Summarize all calls with this customer",
            "What pricing was discussed?",
            "What follow-ups were promised?",
        ]:
            ctk.CTkButton(
                prompts_frame, text=prompt, height=24,
                fg_color=BG_CARD, hover_color=ACCENT, text_color=FG_BRIGHT,
                border_width=1, border_color=BORDER, corner_radius=12,
                font=_font(size=10),
                command=lambda p=prompt: self._use_suggestion(p)
            ).pack(side=tk.LEFT, padx=(6, 0))

        # Chat panel
        chat_outer = ctk.CTkFrame(main, fg_color=BG_PANEL, corner_radius=10,
                                   border_width=1, border_color=BORDER)
        chat_outer.grid(row=2, column=0, sticky="nsew", pady=(0, 0))
        chat_outer.rowconfigure(0, weight=1)
        chat_outer.rowconfigure(1, weight=0)
        chat_outer.columnconfigure(0, weight=1)

        chat_header = ctk.CTkFrame(chat_outer, fg_color="transparent")
        chat_header.grid(row=0, column=0, sticky="new", padx=12, pady=(10, 4))
        ctk.CTkLabel(chat_header, text="Chat",
                     font=_font(size=12, weight="bold"),
                     text_color=YELLOW).pack(side=tk.LEFT)
        self.turn_label = ctk.CTkLabel(
            chat_header, text="New conversation",
            text_color=FG_DIM, font=_font(size=10))
        self.turn_label.pack(side=tk.LEFT, padx=(10, 0))
        self.model_label = ctk.CTkLabel(
            chat_header, text="📋 Notes Retrieval  ·  Claude Opus 4.6  ·  Bedrock",
            text_color=FG_DIM, font=_font(size=10))
        self.model_label.pack(side=tk.RIGHT)

        self.chat_text = StyledText(chat_outer, font=("Segoe UI", 10))
        self.chat_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=(32, 4))
        configure_tags(self.chat_text)
        self.chat_text.tag_configure(
            "user_msg", foreground="#e5e7eb",
            font=("Segoe UI", 10), lmargin1=12, lmargin2=12, spacing1=4,
            background=USER_BUBBLE, relief="flat")
        self.chat_text.tag_configure(
            "user_label", foreground=ACCENT,
            font=("Segoe UI Semibold", 9), spacing1=10)
        self.chat_text.tag_configure(
            "assistant_label", foreground="#a78bfa",
            font=("Segoe UI Semibold", 9), spacing1=10)

        input_row = ctk.CTkFrame(chat_outer, fg_color=BG_CARD, corner_radius=0)
        input_row.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        input_row.columnconfigure(0, weight=1)

        self.input_var = tk.StringVar()
        self.input_entry = ctk.CTkEntry(
            input_row, textvariable=self.input_var,
            fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
            font=_font(size=12), corner_radius=6,
            placeholder_text="Ask about your historical call notes...")
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=8)
        self.input_entry.bind("<Return>", lambda e: self._send())

        self.send_btn = ctk.CTkButton(
            input_row, text="Send ↵", width=90, height=36,
            fg_color=YELLOW, hover_color=YELLOW_HOVER, text_color=BG_DARK,
            font=_font(size=12, weight="bold"), corner_radius=6,
            command=self._send)
        self.send_btn.grid(row=0, column=1, padx=(0, 10), pady=8)

    # ── Actions ──

    def _toggle_index_panel(self):
        if self._index_panel_visible:
            self._index_panel.pack_forget()
            self._index_panel_visible = False
        else:
            self._index_panel.pack(
                fill=tk.X, padx=16, pady=(0, 8),
                after=self._top_bar)
            self._index_panel_visible = True

    def _use_suggestion(self, prompt):
        self.input_var.set(prompt)
        self._send()

    def _open_customer_picker(self):
        """Open a scrollable, searchable popup for customer selection."""
        popup = tk.Toplevel()
        popup.title("Select Customer")
        popup.geometry("300x400")
        popup.configure(bg=BG_DARK)
        popup.transient()
        popup.grab_set()

        # Position near the button
        bx = self.customer_btn.winfo_rootx()
        by = self.customer_btn.winfo_rooty() + self.customer_btn.winfo_height()
        popup.geometry(f"+{bx}+{by}")

        # Search entry
        search_var = tk.StringVar()
        search_entry = ctk.CTkEntry(
            popup, textvariable=search_var, placeholder_text="Type to filter...",
            fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
            font=_font(), corner_radius=6, height=32)
        search_entry.pack(fill=tk.X, padx=8, pady=(8, 4))
        search_entry.focus()

        # Scrollable listbox
        lf = ctk.CTkFrame(popup, fg_color=BG_INPUT, corner_radius=8,
                           border_width=1, border_color=BORDER)
        lf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        listbox = tk.Listbox(
            lf, exportselection=False, bg=BG_INPUT, fg=FG_TEXT,
            selectbackground=ACCENT, selectforeground=BG_DARK,
            font=("Segoe UI", 11), borderwidth=0, highlightthickness=0,
            activestyle="none")
        scrollbar = ctk.CTkScrollbar(lf, command=listbox.yview, fg_color=BG_INPUT,
                                      button_color=BORDER, button_hover_color=ACCENT)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 2), pady=4)

        all_values = list(self._customer_values)

        def populate(filter_text=""):
            listbox.delete(0, tk.END)
            ft = filter_text.lower()
            for v in all_values:
                if not ft or ft in v.lower():
                    listbox.insert(tk.END, v)

        def on_search(*_):
            populate(search_var.get())

        def on_select(event=None):
            sel = listbox.curselection()
            if not sel:
                return
            chosen = listbox.get(sel[0])
            self.customer_filter_var.set(chosen)
            popup.destroy()
            self._new_chat()

        search_var.trace_add("write", on_search)
        listbox.bind("<<ListboxSelect>>", lambda e: popup.after(150, on_select))
        listbox.bind("<Return>", on_select)
        search_entry.bind("<Return>", lambda e: on_select())
        popup.bind("<Escape>", lambda e: popup.destroy())

        populate()

    def _new_chat(self):
        """Reset conversation history and clear the chat display."""
        self._conversation_history = []
        self._current_session_ts = None
        self._is_responding = False
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.delete("1.0", tk.END)
        self.chat_text.config(state=tk.DISABLED)
        self.send_btn.configure(state=tk.NORMAL, text="Send ↵")
        self.turn_label.configure(text="New conversation")
        self.input_entry.focus()

    # ── Session history ──

    def _init_history_table(self):
        """Ensure local SQLite tables exist (no-op if they already exist)."""
        try:
            _ensure_table()
            self._sh_listbox.after(0, lambda: threading.Thread(
                target=self._load_session_history, daemon=True).start())
        except Exception:
            pass

    def _sh_set_filter(self, val: str):
        self._sh_filter_var.set(val)
        threading.Thread(target=self._load_session_history, daemon=True).start()

    def _load_session_history(self):
        try:
            fval = self._sh_filter_var.get()
            stype = None if fval == "All" else fval
            items = list_chat_sessions(session_type=stype, limit=60)
            self._session_items = items
            self._sh_listbox.after(0, self._update_session_list_ui)
        except Exception:
            pass

    def _update_session_list_ui(self):
        self._sh_listbox.delete(0, tk.END)
        for item in self._session_items:
            customer = item.get("customer", "").strip()
            ts = item.get("timestamp", "")[:10]  # YYYY-MM-DD
            title = item.get("title", "—")[:35]
            if customer:
                label = f"{customer} — {ts}"
            else:
                label = f"{title} — {ts}"
            self._sh_listbox.insert(tk.END, label)

    def _on_session_select(self, event):
        sel = self._sh_listbox.curselection()
        if not sel:
            return
        item = self._session_items[sel[0]]
        threading.Thread(target=self._restore_session,
                         args=(item["session_type"], item["timestamp"]),
                         daemon=True).start()

    def _restore_session(self, session_type: str, timestamp: str):
        try:
            item = load_chat_session(session_type, timestamp)
            if not item:
                return
            history = item.get("conversation_history", [])
            self._sh_listbox.after(0, lambda: self._apply_restored_session(item, history))
        except Exception:
            pass

    def _apply_restored_session(self, item: dict, history: list):
        """Render a restored session into the chat display (read-only replay)."""
        self._conversation_history = list(history)
        self._current_session_ts = item.get("timestamp")
        self._is_responding = False

        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.delete("1.0", tk.END)

        # Replay turns from history
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Tool-use blocks — extract text parts only
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if not content:
                continue
            if role == "user":
                self.chat_text.insert(tk.END, "You\n", "user_label")
                self.chat_text.insert(tk.END, f"{content}\n\n", "user_msg")
            elif role == "assistant":
                self.chat_text.insert(tk.END, "Assistant\n", "assistant_label")
                streamer = MarkdownStreamer(self.chat_text)
                streamer.feed(content + "\n")
                streamer.flush()
                self.chat_text.insert(tk.END, "\n", "body")

        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

        turns = len(history) // 2
        ts_display = item.get("timestamp", "")[:16].replace("T", " ")
        self.turn_label.configure(
            text=f"{turns} turn{'s' if turns != 1 else ''}  ·  restored {ts_display}")
        self.send_btn.configure(state=tk.NORMAL, text="Send ↵")

    def _save_current_session(self):
        """Persist the current conversation to local SQLite."""
        if not self._conversation_history:
            return
        # Derive a title from the first user message (skip file index preamble)
        first_user = ""
        for m in self._conversation_history:
            if m["role"] == "user" and isinstance(m["content"], str):
                text = m["content"]
                # The first message may contain "Available files (N):\n...\n---\n\nActual question"
                if "---\n\n" in text:
                    text = text.split("---\n\n", 1)[-1]
                first_user = text.strip()
                break
        title = (first_user or "Chat session")[:80]
        customer = self.customer_filter_var.get()
        if customer == "(All)":
            customer = ""
        source = self.source_filter_var.get()

        # Ensure conversation_history is JSON-serializable
        # (strip any non-serializable content blocks)
        clean_history = []
        for msg in self._conversation_history:
            content = msg.get("content", "")
            if isinstance(content, str):
                clean_history.append({"role": msg["role"], "content": content})
            elif isinstance(content, list):
                # Extract text blocks only, skip tool_use/tool_result blocks
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif "text" in block and "type" not in block:
                            text_parts.append(block["text"])
                if text_parts:
                    clean_history.append({"role": msg["role"], "content": " ".join(text_parts)})
            else:
                clean_history.append({"role": msg["role"], "content": str(content)})

        try:
            ts = save_chat_session(
                session_type="retrieval",
                title=title,
                conversation_history=clean_history,
                customer=customer if customer != "(All)" else "",
                source_filter=source if source != "All Sources" else "",
                existing_timestamp=self._current_session_ts,
            )
            self._current_session_ts = ts
            # Refresh sidebar in background
            threading.Thread(target=self._load_session_history, daemon=True).start()
        except Exception as e:
            import traceback
            print(f"[session save error] {e}\n{traceback.format_exc()}")

    def _delete_selected_session(self):
        sel = self._sh_listbox.curselection()
        if not sel:
            return
        item = self._session_items[sel[0]]
        try:
            delete_chat_session(item["session_type"], item["timestamp"])
            threading.Thread(target=self._load_session_history, daemon=True).start()
        except Exception:
            pass

    def _refresh_index(self):
        try:
            all_notes = scan_notes()
            self._all_notes_cache = all_notes

            # Collect all raw customer names
            raw_names = list({n["customer"] for n in all_notes})

            # Fuzzy-deduplicate: 'Common Chain' and 'Common Chains' → one entry
            from retrieval.notes_retriever import dedupe_customers, _is_likely_customer
            canonical_map = dedupe_customers(raw_names)

            # Store the mapping so _get_active_notes can match against canonical names
            self._canonical_map = canonical_map

            # Unique canonical names for the dropdown — only real customer names
            canonical_set = {}
            for orig, canon in canonical_map.items():
                if not _is_likely_customer(canon):
                    continue
                key = canon.lower()
                if key not in canonical_set:
                    canonical_set[key] = canon
            customers = ["(All)"] + sorted(canonical_set.values(), key=str.lower)

            # Schedule UI update on main thread — retry if widget not ready yet
            def _schedule_update():
                try:
                    self.customer_btn.after(0, lambda: self._update_index_ui(all_notes, customers))
                except RuntimeError:
                    # Main thread not in main loop yet — retry after a short delay
                    import time
                    time.sleep(1)
                    try:
                        self.customer_btn.after(0, lambda: self._update_index_ui(all_notes, customers))
                    except Exception:
                        pass
            _schedule_update()
        except Exception as e:
            import traceback
            print(f"[refresh_index error] {e}\n{traceback.format_exc()}")

    def _update_index_ui(self, all_notes, customers):
        self._customer_values = customers
        count = len(all_notes)
        self.index_label.configure(
            text=f"{count} note{'s' if count != 1 else ''} indexed")
        self.notes_list.delete(0, tk.END)
        for note in all_notes:
            source_tag = f"[{note.get('source', '?')}]"
            label = f"{source_tag}  {note['customer']}  ·  {note['date'] or note['filename']}"
            self.notes_list.insert(tk.END, label)

    def _get_active_notes(self) -> list[dict]:
        """Return notes filtered by source and customer (case-insensitive, cross-source)."""
        all_notes = getattr(self, "_all_notes_cache", None) or scan_notes()
        canonical_map = getattr(self, "_canonical_map", {})

        source = self.source_filter_var.get()
        if source and source != "All Sources":
            all_notes = [n for n in all_notes if n.get("source") == source]

        customer = self.customer_filter_var.get()
        if customer and customer != "(All)":
            # Match against canonical name so 'Common Chain' also returns 'Common Chains' notes
            canon_selected = customer.lower()
            all_notes = [
                n for n in all_notes
                if (canonical_map.get(n["customer"], n["customer"])).lower() == canon_selected
                or canon_selected in n["customer"].lower()
                or n["customer"].lower() in canon_selected
            ]
        return all_notes

    def _send(self):
        if self._is_responding:
            return
        question = self.input_var.get().strip()
        if not question:
            return

        self.input_var.set("")
        self._is_responding = True
        self.send_btn.configure(state=tk.DISABLED, text="...")

        # Render user bubble + status placeholder
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.insert(tk.END, "You\n", "user_label")
        self.chat_text.insert(tk.END, f"{question}\n\n", "user_msg")
        self.chat_text.insert(tk.END, "Assistant\n", "assistant_label")
        # Mark where the thinking line lives so we can update it in-place
        self.chat_text.mark_set("thinking_start", "end-1c")
        self.chat_text.mark_gravity("thinking_start", tk.LEFT)
        self.chat_text.insert(tk.END, "\n", "status")   # placeholder line
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

        self._streaming_started = False
        self._md_streamer = MarkdownStreamer(self.chat_text)

        # Start animated thinking indicator
        self._start_thinking_animation()

        def on_chunk(text):
            self.chat_text.after(0, self._append_chunk, text)

        def on_done(answer, error):
            self.chat_text.after(0, self._finish, error)

        ask_notes_agent(
            question,
            self._get_active_notes(),
            self._conversation_history,
            on_chunk=on_chunk,
            callback=on_done,
        )

    # ── Thinking animation ──

    _RETRIEVAL_STEPS = [
        "🧠 Analyzing question...",
        "🔧 Selecting tools...",
        "📡 Querying sources...",
        "📖 Processing results...",
        "✍️  Composing answer...",
    ]
    _SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _start_thinking_animation(self):
        self._thinking_active = True
        self._thinking_tick = 0
        self._thinking_last_event = ""   # last on_chunk event text
        self._thinking_start_ms = int(self.chat_text.tk.call("clock", "milliseconds"))
        self._tick_thinking()

    def _stop_thinking_animation(self):
        self._thinking_active = False

    def _tick_thinking(self):
        if not self._thinking_active:
            return

        now_ms = int(self.chat_text.tk.call("clock", "milliseconds"))
        elapsed = (now_ms - self._thinking_start_ms) // 1000
        mins, secs = divmod(elapsed, 60)
        timer = f"{mins}:{secs:02d}" if mins else f"{secs}s"

        steps = self._RETRIEVAL_STEPS
        # Advance step every ~4 seconds, but cap at second-to-last until done
        step_idx = min(self._thinking_tick // 8, len(steps) - 2)
        step_text = steps[step_idx]

        spinner = self._SPINNER[self._thinking_tick % len(self._SPINNER)]

        # If a real event came in from on_chunk, show it instead of the canned step
        if self._thinking_last_event:
            line = f"{spinner} {self._thinking_last_event}  [{timer}]"
        else:
            line = f"{spinner} {step_text}  [{timer}]"

        self._thinking_tick += 1

        try:
            self.chat_text.config(state=tk.NORMAL)
            # Replace the thinking line in-place
            self.chat_text.delete("thinking_start", "thinking_start lineend")
            self.chat_text.insert("thinking_start", line, "status")
            self.chat_text.see(tk.END)
            self.chat_text.config(state=tk.DISABLED)
        except tk.TclError:
            return  # widget destroyed

        self.chat_text.after(500, self._tick_thinking)

    def _append_chunk(self, text):
        # Capture tool-call progress lines for the thinking animation
        stripped = text.strip()
        if stripped and not self._streaming_started:
            # Tool-specific progress lines — show in the spinner
            if any(stripped.startswith(p) for p in ("📂", "🔍", "🌐", "⏳", "💰")):
                self._thinking_last_event = stripped[:80]
                return   # don't write to chat yet, just update the spinner

        self._stop_thinking_animation()

        self.chat_text.config(state=tk.NORMAL)
        if not self._streaming_started:
            self._streaming_started = True
            # Remove the thinking line entirely
            try:
                self.chat_text.delete("thinking_start", "thinking_start lineend+1c")
            except tk.TclError:
                pass
        self._md_streamer.feed(text)
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

    def _finish(self, error):
        self._stop_thinking_animation()

        self.chat_text.config(state=tk.NORMAL)
        if error:
            # Remove thinking line and show error
            try:
                self.chat_text.delete("thinking_start", "thinking_start lineend+1c")
            except tk.TclError:
                pass
            self.chat_text.insert(tk.END, f"⚠️ {error}\n", "status")
        else:
            if not self._streaming_started:
                # Agent returned without streaming (AgentCore path) — remove thinking line
                try:
                    self.chat_text.delete("thinking_start", "thinking_start lineend+1c")
                except tk.TclError:
                    pass
            if self._md_streamer:
                self._md_streamer.flush()
            if self.chat_text.get("end-2c", "end-1c") != "\n":
                self.chat_text.insert(tk.END, "\n")
        self.chat_text.insert(tk.END, "\n", "body")  # spacer between turns
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

        # Update turn counter
        turns = len(self._conversation_history) // 2
        self.turn_label.configure(
            text=f"{turns} turn{'s' if turns != 1 else ''} in this conversation")

        self._is_responding = False
        self.send_btn.configure(state=tk.NORMAL, text="Send ↵")
        self.input_entry.focus()

        # Auto-save session after each completed turn
        if not error:
            threading.Thread(target=self._save_current_session, daemon=True).start()


class CustomerResearchTab:
    """Tab 3 — Customer research via web search + Claude."""

    def __init__(self, parent):
        self._conversation_history = []
        self._streaming_started = False
        self._md_streamer = None
        self._is_responding = False
        self._current_session_ts = None
        self._session_items = []
        self._build_ui(parent)
        threading.Thread(target=self._init_history_table, daemon=True).start()

    def _build_ui(self, parent):
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill=tk.X, padx=16, pady=(14, 6))

        ctk.CTkLabel(top, text="🌐  Customer Research",
                     font=_font(size=15, weight="bold"),
                     text_color=FG_BRIGHT).pack(side=tk.LEFT)

        ctk.CTkButton(
            top, text="＋ New Chat", width=110, height=30,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color=BG_DARK,
            border_width=0, corner_radius=6,
            font=_font(weight="bold"),
            command=self._new_chat).pack(side=tk.RIGHT, padx=(8, 0))

        # Body: sidebar + chat + brief panel
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        body.columnconfigure(0, weight=0)   # sidebar
        body.columnconfigure(1, weight=1)   # chat
        body.columnconfigure(2, weight=0)   # brief panel
        body.rowconfigure(0, weight=1)

        # Sidebar
        sidebar = ctk.CTkFrame(body, fg_color=BG_PANEL, corner_radius=10,
                                border_width=1, border_color=BORDER, width=220)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(16, 6), pady=(0, 8))
        sidebar.grid_propagate(False)

        sh_header = ctk.CTkFrame(sidebar, fg_color="transparent")
        sh_header.pack(fill=tk.X, padx=10, pady=(10, 4))
        ctk.CTkLabel(sh_header, text="🕘 History",
                     font=_font(size=12, weight="bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        ctk.CTkButton(sh_header, text="⟳", width=28, height=24,
                      fg_color="transparent", hover_color=BG_INPUT,
                      text_color=ACCENT, font=_font(size=12),
                      command=lambda: threading.Thread(
                          target=self._load_session_history, daemon=True).start()
                      ).pack(side=tk.RIGHT)

        sh_list_frame = ctk.CTkFrame(sidebar, fg_color=BG_INPUT, corner_radius=6)
        sh_list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
        self._sh_listbox = tk.Listbox(
            sh_list_frame, bg=BG_INPUT, fg=FG_TEXT,
            selectbackground=ACCENT, selectforeground=BG_DARK,
            font=("Segoe UI", 9), borderwidth=0, highlightthickness=0,
            activestyle="none")
        sh_sb = tk.Scrollbar(sh_list_frame, command=self._sh_listbox.yview,
                              bg=BG_INPUT, troughcolor=BG_INPUT, bd=0)
        self._sh_listbox.configure(yscrollcommand=sh_sb.set)
        self._sh_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        sh_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._sh_listbox.bind("<<ListboxSelect>>", self._on_session_select)

        sh_btn_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        sh_btn_row.pack(fill=tk.X, padx=10, pady=(0, 10))
        ctk.CTkButton(sh_btn_row, text="🗑 Delete", width=90, height=26,
                      fg_color=BG_INPUT, hover_color=RED, text_color=FG_BRIGHT,
                      border_width=1, border_color=BORDER, corner_radius=6,
                      font=_font(size=10),
                      command=self._delete_selected_session).pack(side=tk.LEFT)

        # Main chat area
        main = ctk.CTkFrame(body, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(0, 6), pady=0)
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        # Suggested prompts
        prompts_frame = ctk.CTkFrame(main, fg_color="transparent")
        prompts_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(prompts_frame, text="Suggestions:",
                     text_color=FG_DIM, font=_font(size=10)).pack(side=tk.LEFT)
        for prompt in [
            "What does this company do?",
            "Recent news and funding",
            "Key decision makers",
            "AWS services they might need",
        ]:
            ctk.CTkButton(
                prompts_frame, text=prompt, height=24,
                fg_color=BG_CARD, hover_color=ACCENT, text_color=FG_BRIGHT,
                border_width=1, border_color=BORDER, corner_radius=12,
                font=_font(size=10),
                command=lambda p=prompt: self._use_suggestion(p)
            ).pack(side=tk.LEFT, padx=(6, 0))

        # Chat panel
        chat_outer = ctk.CTkFrame(main, fg_color=BG_PANEL, corner_radius=10,
                                   border_width=1, border_color=BORDER)
        chat_outer.grid(row=1, column=0, sticky="nsew", pady=(0, 0))
        chat_outer.rowconfigure(0, weight=1)
        chat_outer.rowconfigure(1, weight=0)
        chat_outer.columnconfigure(0, weight=1)

        chat_header = ctk.CTkFrame(chat_outer, fg_color="transparent")
        chat_header.grid(row=0, column=0, sticky="new", padx=12, pady=(10, 4))
        ctk.CTkLabel(chat_header, text="Chat",
                     font=_font(size=12, weight="bold"),
                     text_color=YELLOW).pack(side=tk.LEFT)
        self.turn_label = ctk.CTkLabel(
            chat_header, text="New conversation",
            text_color=FG_DIM, font=_font(size=10))
        self.turn_label.pack(side=tk.LEFT, padx=(10, 0))
        ctk.CTkLabel(
            chat_header, text="🌐 Customer Research  ·  Claude Sonnet 4  ·  Web Search",
            text_color=FG_DIM, font=_font(size=10)).pack(side=tk.RIGHT)

        self.chat_text = StyledText(chat_outer, font=("Segoe UI", 10))
        self.chat_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=(32, 4))
        configure_tags(self.chat_text)
        self.chat_text.tag_configure("user_msg", foreground="#e5e7eb",
            font=("Segoe UI", 10), lmargin1=12, lmargin2=12, spacing1=4,
            background=USER_BUBBLE, relief="flat")
        self.chat_text.tag_configure("user_label", foreground=ACCENT,
            font=("Segoe UI Semibold", 9), spacing1=10)
        self.chat_text.tag_configure("assistant_label", foreground="#a78bfa",
            font=("Segoe UI Semibold", 9), spacing1=10)

        input_row = ctk.CTkFrame(chat_outer, fg_color=BG_CARD, corner_radius=0)
        input_row.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        input_row.columnconfigure(0, weight=1)

        self.input_var = tk.StringVar()
        self.input_entry = ctk.CTkEntry(
            input_row, textvariable=self.input_var,
            fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
            font=_font(size=12), corner_radius=6,
            placeholder_text="Research a customer (e.g. 'What's new with Acme Corp?')...")
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=8)
        self.input_entry.bind("<Return>", lambda e: self._send())

        self.send_btn = ctk.CTkButton(
            input_row, text="Send ↵", width=90, height=36,
            fg_color=YELLOW, hover_color=YELLOW_HOVER, text_color=BG_DARK,
            font=_font(size=12, weight="bold"), corner_radius=6,
            command=self._send)
        self.send_btn.grid(row=0, column=1, padx=(0, 10), pady=8)

        # ── Right: Customer Brief panel ──
        brief_panel = ctk.CTkFrame(body, fg_color=BG_PANEL, corner_radius=10,
                                    border_width=1, border_color=BORDER, width=240)
        brief_panel.grid(row=0, column=2, sticky="nsew", padx=(6, 16), pady=(0, 8))
        brief_panel.grid_propagate(False)

        ctk.CTkLabel(brief_panel, text="📄  Customer Brief",
                     font=_font(size=13, weight="bold"),
                     text_color=ACCENT).pack(anchor=tk.W, padx=12, pady=(12, 8))

        ctk.CTkLabel(brief_panel, text="Company Name:",
                     text_color=FG_DIM, font=_font()
                     ).pack(anchor=tk.W, padx=12, pady=(4, 0))
        self.brief_company_var = tk.StringVar()
        ctk.CTkEntry(brief_panel, textvariable=self.brief_company_var, width=210,
                     fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
                     font=_font(), corner_radius=6,
                     placeholder_text="e.g. Denali Therapeutics"
                     ).pack(padx=12, pady=(2, 6))

        ctk.CTkLabel(brief_panel, text="Domain:",
                     text_color=FG_DIM, font=_font()
                     ).pack(anchor=tk.W, padx=12, pady=(0, 0))
        self.brief_domain_var = tk.StringVar()
        ctk.CTkEntry(brief_panel, textvariable=self.brief_domain_var, width=210,
                     fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
                     font=_font(), corner_radius=6,
                     placeholder_text="e.g. denalitherapeutics.com"
                     ).pack(padx=12, pady=(2, 8))

        self.brief_generate_btn = ctk.CTkButton(
            brief_panel, text="📄  Create Customer Brief", width=210, height=36,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color=BG_DARK,
            font=_font(size=12, weight="bold"), corner_radius=8,
            command=self._generate_brief)
        self.brief_generate_btn.pack(padx=12, pady=(0, 8))

        self.brief_status_var = tk.StringVar(value="")
        self.brief_status_label = ctk.CTkLabel(
            brief_panel, textvariable=self.brief_status_var,
            text_color=FG_DIM, font=_font(size=10),
            wraplength=210)
        self.brief_status_label.pack(padx=12, pady=(0, 8))

    # ── Brief generation ──

    _BRIEF_STEPS = [
        "🔍 Researching company profile...",
        "👥 Identifying leadership team...",
        "💻 Analyzing technology landscape...",
        "🤖 Mapping AI/ML use cases...",
        "📊 Pulling AWS customer references...",
        "🗺️ Aligning AWS solutions...",
        "⚔️ Assessing competitive context...",
        "📝 Generating discovery questions...",
        "📋 Building meeting agenda...",
        "📄 Compiling document...",
    ]
    _BRIEF_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _generate_brief(self):
        company = self.brief_company_var.get().strip()
        domain = self.brief_domain_var.get().strip()
        if not company or not domain:
            from tkinter import messagebox
            messagebox.showwarning("Missing Info", "Please enter both company name and domain.")
            return
        self.brief_generate_btn.configure(state=tk.DISABLED, text="⏳ Generating...")
        self._brief_active = True
        self._brief_tick = 0
        self._brief_start_ms = int(self.brief_status_label.tk.call("clock", "milliseconds"))
        self._brief_last_status = ""
        self._tick_brief_animation()

        def run():
            try:
                from retrieval.customer_brief import generate_customer_brief

                def on_status(msg):
                    self._brief_last_status = msg

                filepath = generate_customer_brief(company, domain, on_status=on_status)
                self._brief_active = False
                self.brief_generate_btn.after(0, lambda: self.brief_status_var.set(f"✅ Saved:\n{filepath}"))
            except Exception as e:
                self._brief_active = False
                self.brief_generate_btn.after(0, lambda: self.brief_status_var.set(f"❌ Error: {e}"))
            finally:
                self._brief_active = False
                self.brief_generate_btn.after(0, lambda: self.brief_generate_btn.configure(
                    state=tk.NORMAL, text="📄  Create Customer Brief"))

        threading.Thread(target=run, daemon=True).start()

    def _tick_brief_animation(self):
        if not self._brief_active:
            return
        now_ms = int(self.brief_status_label.tk.call("clock", "milliseconds"))
        elapsed = (now_ms - self._brief_start_ms) // 1000
        mins, secs = divmod(elapsed, 60)
        timer = f"{mins}:{secs:02d}" if mins else f"{secs}s"

        # Advance step every ~5 seconds
        step_idx = min(self._brief_tick // 10, len(self._BRIEF_STEPS) - 1)
        step_text = self._BRIEF_STEPS[step_idx]
        spinner = self._BRIEF_SPINNER[self._brief_tick % len(self._BRIEF_SPINNER)]

        # Show real status from the backend if available
        if self._brief_last_status:
            display = f"{spinner} {self._brief_last_status}  [{timer}]"
        else:
            display = f"{spinner} {step_text}  [{timer}]"

        self._brief_tick += 1
        self.brief_status_var.set(display)
        self.brief_status_label.after(500, self._tick_brief_animation)

    # ── Actions ──

    def _new_chat(self):
        self._conversation_history = []
        self._current_session_ts = None
        self._is_responding = False
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.delete("1.0", tk.END)
        self.chat_text.config(state=tk.DISABLED)
        self.send_btn.configure(state=tk.NORMAL, text="Send ↵")
        self.turn_label.configure(text="New conversation")
        self.input_entry.focus()

    def _use_suggestion(self, prompt):
        self.input_var.set(prompt)
        self._send()

    def _send(self):
        if self._is_responding:
            return
        question = self.input_var.get().strip()
        if not question:
            return

        self.input_var.set("")
        self._is_responding = True
        self.send_btn.configure(state=tk.DISABLED, text="...")

        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.insert(tk.END, "You\n", "user_label")
        self.chat_text.insert(tk.END, f"{question}\n\n", "user_msg")
        self.chat_text.insert(tk.END, "Assistant\n", "assistant_label")
        self.chat_text.mark_set("thinking_start", "end-1c")
        self.chat_text.mark_gravity("thinking_start", tk.LEFT)
        self.chat_text.insert(tk.END, "\n", "status")
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

        self._streaming_started = False
        self._md_streamer = MarkdownStreamer(self.chat_text)
        self._start_thinking_animation()

        def on_chunk(text):
            self.chat_text.after(0, self._append_chunk, text)

        def on_done(answer, error):
            self.chat_text.after(0, self._finish, error)

        ask_research_agent(
            question, "", self._conversation_history,
            on_chunk=on_chunk, callback=on_done,
        )

    # ── Thinking animation ──

    _RESEARCH_STEPS = [
        "🌐 Connecting to search...",
        "🔎 Running web searches...",
        "📰 Analysing results...",
        "🧠 Building research brief...",
        "✍️  Composing answer...",
    ]
    _SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _start_thinking_animation(self):
        self._thinking_active = True
        self._thinking_tick = 0
        self._thinking_last_event = ""
        self._thinking_start_ms = int(self.chat_text.tk.call("clock", "milliseconds"))
        self._tick_thinking()

    def _stop_thinking_animation(self):
        self._thinking_active = False

    def _tick_thinking(self):
        if not self._thinking_active:
            return
        now_ms = int(self.chat_text.tk.call("clock", "milliseconds"))
        elapsed = (now_ms - self._thinking_start_ms) // 1000
        mins, secs = divmod(elapsed, 60)
        timer = f"{mins}:{secs:02d}" if mins else f"{secs}s"
        step_idx = min(self._thinking_tick // 8, len(self._RESEARCH_STEPS) - 2)
        step_text = self._RESEARCH_STEPS[step_idx]
        spinner = self._SPINNER[self._thinking_tick % len(self._SPINNER)]
        if self._thinking_last_event:
            line = f"{spinner} {self._thinking_last_event}  [{timer}]"
        else:
            line = f"{spinner} {step_text}  [{timer}]"
        self._thinking_tick += 1
        try:
            self.chat_text.config(state=tk.NORMAL)
            self.chat_text.delete("thinking_start", "thinking_start lineend")
            self.chat_text.insert("thinking_start", line, "status")
            self.chat_text.see(tk.END)
            self.chat_text.config(state=tk.DISABLED)
        except tk.TclError:
            return
        self.chat_text.after(500, self._tick_thinking)

    def _append_chunk(self, text):
        stripped = text.strip()
        if stripped and not self._streaming_started:
            if any(stripped.startswith(p) for p in ("📂", "🔍", "🌐", "⏳")):
                self._thinking_last_event = stripped[:60]
                return
        self._stop_thinking_animation()
        self.chat_text.config(state=tk.NORMAL)
        if not self._streaming_started:
            self._streaming_started = True
            try:
                self.chat_text.delete("thinking_start", "thinking_start lineend+1c")
            except tk.TclError:
                pass
        self._md_streamer.feed(text)
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

    def _finish(self, error):
        self._stop_thinking_animation()
        self.chat_text.config(state=tk.NORMAL)
        if error:
            try:
                self.chat_text.delete("thinking_start", "thinking_start lineend+1c")
            except tk.TclError:
                pass
            self.chat_text.insert(tk.END, f"⚠️ {error}\n", "status")
        else:
            if not self._streaming_started:
                try:
                    self.chat_text.delete("thinking_start", "thinking_start lineend+1c")
                except tk.TclError:
                    pass
            if self._md_streamer:
                self._md_streamer.flush()
            if self.chat_text.get("end-2c", "end-1c") != "\n":
                self.chat_text.insert(tk.END, "\n")
        self.chat_text.insert(tk.END, "\n", "body")
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

        turns = len(self._conversation_history) // 2
        self.turn_label.configure(
            text=f"{turns} turn{'s' if turns != 1 else ''} in this conversation")
        self._is_responding = False
        self.send_btn.configure(state=tk.NORMAL, text="Send ↵")
        self.input_entry.focus()

        if not error:
            threading.Thread(target=self._save_current_session, daemon=True).start()

    # ── Session history ──

    def _init_history_table(self):
        try:
            _ensure_table()
            self._sh_listbox.after(0, lambda: threading.Thread(
                target=self._load_session_history, daemon=True).start())
        except Exception:
            pass

    def _load_session_history(self):
        try:
            items = list_chat_sessions(session_type="research", limit=60)
            self._session_items = items
            self._sh_listbox.after(0, self._update_session_list_ui)
        except Exception:
            pass

    def _update_session_list_ui(self):
        self._sh_listbox.delete(0, tk.END)
        for item in self._session_items:
            ts = item.get("timestamp", "")[:10]  # YYYY-MM-DD
            title = item.get("title", "—")[:35]
            self._sh_listbox.insert(tk.END, f"{title}  ·  {ts}")

    def _on_session_select(self, event):
        sel = self._sh_listbox.curselection()
        if not sel:
            return
        item = self._session_items[sel[0]]
        threading.Thread(target=self._restore_session,
                         args=(item["session_type"], item["timestamp"]),
                         daemon=True).start()

    def _restore_session(self, session_type, timestamp):
        try:
            item = load_chat_session(session_type, timestamp)
            if not item:
                return
            history = item.get("conversation_history", [])
            self._sh_listbox.after(0, lambda: self._apply_restored_session(item, history))
        except Exception:
            pass

    def _apply_restored_session(self, item, history):
        self._conversation_history = list(history)
        self._current_session_ts = item.get("timestamp")
        self._is_responding = False
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.delete("1.0", tk.END)
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text")
            if not content:
                continue
            if role == "user":
                self.chat_text.insert(tk.END, "You\n", "user_label")
                self.chat_text.insert(tk.END, f"{content}\n\n", "user_msg")
            elif role == "assistant":
                self.chat_text.insert(tk.END, "Assistant\n", "assistant_label")
                streamer = MarkdownStreamer(self.chat_text)
                streamer.feed(content + "\n")
                streamer.flush()
                self.chat_text.insert(tk.END, "\n", "body")
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)
        turns = len(history) // 2
        ts_display = item.get("timestamp", "")[:16].replace("T", " ")
        self.turn_label.configure(
            text=f"{turns} turn{'s' if turns != 1 else ''}  ·  restored {ts_display}")
        self.send_btn.configure(state=tk.NORMAL, text="Send ↵")

    def _save_current_session(self):
        if not self._conversation_history:
            return
        first_user = next(
            (m["content"] for m in self._conversation_history
             if m["role"] == "user" and isinstance(m["content"], str)),
            "Chat session")
        # Extract a clean topic/customer name for the title
        topic = self._extract_topic(first_user)
        from datetime import datetime
        date_str = (self._current_session_ts or datetime.now().isoformat())[:10]
        title = f"{topic} - {date_str}"
        clean_history = []
        for msg in self._conversation_history:
            content = msg.get("content", "")
            if isinstance(content, str):
                clean_history.append({"role": msg["role"], "content": content})
            elif isinstance(content, list):
                text_parts = [b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                if text_parts:
                    clean_history.append({"role": msg["role"], "content": " ".join(text_parts)})
            else:
                clean_history.append({"role": msg["role"], "content": str(content)})
        try:
            ts = save_chat_session(
                session_type="research", title=title,
                conversation_history=clean_history,
                customer="", source_filter="",
                existing_timestamp=self._current_session_ts)
            self._current_session_ts = ts
            threading.Thread(target=self._load_session_history, daemon=True).start()
        except Exception:
            pass

    @staticmethod
    def _extract_topic(text: str) -> str:
        """Extract a customer/topic name from the first user message."""
        t = text.strip().rstrip("?").strip()
        for prefix in [
            "can you find me the latest news about ",
            "can you find latest information for ",
            "can you find me the latest information about ",
            "can you find information about ",
            "can you research ", "can you look up ",
            "what does ", "what is ", "who is ", "tell me about ",
            "research ", "look up ", "find info on ", "what's new with ",
            "summarize ", "what do you know about ",
            "find me information on ", "what do you know about ",
        ]:
            if t.lower().startswith(prefix):
                t = t[len(prefix):]
                break
        # Remove trailing clauses
        for sep in [" and help", " and draft", " and ", ". "]:
            idx = t.lower().find(sep)
            if idx > 3:
                t = t[:idx]
                break
        for suffix in [" do", " does"]:
            if t.lower().endswith(suffix):
                t = t[:-len(suffix)]
        t = t.strip()
        return t[:60] if t else text[:60]

    def _delete_selected_session(self):
        sel = self._sh_listbox.curselection()
        if not sel:
            return
        item = self._session_items[sel[0]]
        try:
            delete_chat_session(item["session_type"], item["timestamp"])
            threading.Thread(target=self._load_session_history, daemon=True).start()
        except Exception:
            pass


class InsightsTab:
    """Tab 4 — Call Analytics + Competitive Intelligence with matplotlib charts."""

    def __init__(self, parent):
        self._parent = parent
        self._build_ui(parent)
        threading.Thread(target=self._refresh_data, daemon=True).start()

    def _build_ui(self, parent):
        import matplotlib
        matplotlib.use("Agg")

        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill=tk.X, padx=16, pady=(14, 6))

        ctk.CTkLabel(top, text="📊  Trends & Insights",
                     font=_font(size=15, weight="bold"),
                     text_color=FG_BRIGHT).pack(side=tk.LEFT)

        ctk.CTkButton(
            top, text="⟳ Refresh", width=100, height=30,
            fg_color=BG_INPUT, hover_color=BG_CARD, text_color=ACCENT,
            border_width=1, border_color=BORDER, corner_radius=6,
            font=_font(),
            command=lambda: threading.Thread(target=self._refresh_data, daemon=True).start()
        ).pack(side=tk.RIGHT)

        self.status_label = ctk.CTkLabel(
            top, text="Loading...", text_color=FG_DIM,
            font=_font(size=10))
        self.status_label.pack(side=tk.RIGHT, padx=(0, 12))

        # Stats row
        self._stats_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self._stats_frame.pack(fill=tk.X, padx=16, pady=(0, 6))

        # Main body: charts left, trends right
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
        body.columnconfigure(0, weight=3)   # charts take more space
        body.columnconfigure(1, weight=2)   # trend panel
        body.rowconfigure(0, weight=1)

        # Left: 2x2 chart grid
        charts_frame = ctk.CTkFrame(body, fg_color="transparent")
        charts_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        charts_frame.columnconfigure(0, weight=1)
        charts_frame.columnconfigure(1, weight=1)
        charts_frame.rowconfigure(0, weight=1)
        charts_frame.rowconfigure(1, weight=1)

        self._chart_frames = []
        for r in range(2):
            for c in range(2):
                frame = ctk.CTkFrame(charts_frame, fg_color=BG_PANEL, corner_radius=14,
                                      border_width=1, border_color=BORDER)
                frame.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
                self._chart_frames.append(frame)

        # Right: Trend Generation panel (full height)
        trend_panel = ctk.CTkFrame(body, fg_color=BG_PANEL, corner_radius=14,
                                    border_width=1, border_color=BORDER)
        trend_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        trend_header = ctk.CTkFrame(trend_panel, fg_color="transparent")
        trend_header.pack(fill=tk.X, padx=14, pady=(12, 8))
        ctk.CTkLabel(trend_header, text="🔮  Trend Generation",
                     font=_font(size=13, weight="bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        self._trend_btn = ctk.CTkButton(
            trend_header, text="Generate Trends", width=130, height=30,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color=BG_DARK,
            font=_font(weight="bold"), corner_radius=6,
            command=self._generate_trends)
        self._trend_btn.pack(side=tk.RIGHT)

        self._trend_sift_btn = ctk.CTkButton(
            trend_header, text="📊 SIFT", width=70, height=30,
            fg_color="#1f2937", hover_color=ACCENT, text_color=FG_BRIGHT,
            font=_font(size=10), corner_radius=6,
            border_width=1, border_color="#374151", state=tk.DISABLED,
            command=self._submit_trend_sift)
        self._trend_sift_btn.pack(side=tk.RIGHT, padx=(0, 6))

        self._trend_text = StyledText(trend_panel, font=("Segoe UI", 10))
        self._trend_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        configure_tags(self._trend_text)

    def _make_stat_card(self, parent, label, value, color=ACCENT):
        card = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=12,
                             border_width=1, border_color=BORDER, width=140, height=75)
        card.pack(side=tk.LEFT, padx=(0, 8), fill=tk.Y)
        card.pack_propagate(False)
        # Colored accent line at top
        accent_bar = ctk.CTkFrame(card, fg_color=color, corner_radius=0, height=3)
        accent_bar.pack(fill=tk.X, padx=12, pady=(8, 0))
        ctk.CTkLabel(card, text=str(value), font=_font(size=24, weight="bold"),
                     text_color=color).pack(padx=12, pady=(4, 0))
        ctk.CTkLabel(card, text=label, font=_font(size=9),
                     text_color=FG_DIM).pack(padx=12, pady=(0, 8))

    def _generate_trends(self):
        self._trend_btn.configure(state=tk.DISABLED, text="⏳ Analyzing...")
        self._trend_sift_btn.configure(state=tk.DISABLED)
        self._trend_cancelled = False
        self._trend_text.config(state=tk.NORMAL)
        self._trend_text.delete("1.0", tk.END)
        self._trend_text.insert(tk.END, "Scanning call history and note files...\n")
        self._trend_text.config(state=tk.DISABLED)

        def run():
            try:
                from transcription.history import list_sessions
                from retrieval.notes_retriever import scan_notes
                import json
                import boto3
                from botocore.config import Config
                from config import AWS_REGION, CLAUDE_MODEL_ID

                # Gather data from local session history
                sessions = list_sessions()
                session_summaries = []
                for s in sessions[:30]:  # last 30 sessions
                    customer = s.get("customer_name", "Unknown")
                    notes = s.get("notes", "")[:2000]
                    ts = s.get("timestamp", "")[:10]
                    if notes:
                        session_summaries.append(f"[{customer} — {ts}]\n{notes}")

                # Gather data from local note files
                all_notes = scan_notes()
                file_summaries = []
                for note in all_notes[:20]:
                    fpath = note.get("filepath", "")
                    if fpath and os.path.exists(fpath):
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                                content = f.read()[:2000]
                            customer = note.get("customer", "Unknown")
                            file_summaries.append(f"[{customer} — file]\n{content}")
                        except Exception:
                            pass

                combined = "\n\n---\n\n".join(session_summaries + file_summaries[:10])

                self._parent.after(0, lambda: self._trend_update_status(
                    "Analyzing patterns across all calls..."))

                # Send to Claude for trend analysis
                client = boto3.client(
                    "bedrock-runtime", region_name=AWS_REGION,
                    config=Config(read_timeout=300),
                )

                prompt = """You are a sales intelligence analyst. Given notes from multiple customer calls,
identify 5-10 cross-cutting trends, patterns, or themes that appear across multiple conversations.

Focus on:
- Common customer pain points or challenges
- Recurring technology needs or interests
- Frequently mentioned competitors or alternatives
- Shared business priorities or strategic themes
- Common objections or concerns
- Emerging opportunities or market shifts

Use this markdown format for EACH trend:

## 1. Trend Title Here

1-2 sentences explaining the trend with specific examples from the calls. Reference customer names where relevant.

- **Appeared in:** ~X calls (Customer A, Customer B, Customer C)

Keep it actionable — these should help a sales team prioritize and prepare.

FORMATTING RULES:
- Use ## for each numbered trend title (e.g. ## 1. Agentic AI Adoption)
- Use regular paragraphs for the explanation
- Use - for bullet points, each on its own line
- Use **bold** for customer names and key terms
- Add a blank line between each section"""

                payload = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4096,
                    "system": prompt,
                    "messages": [{"role": "user", "content": f"Analyze these call notes for trends:\n\n{combined}"}],
                }

                self._trend_streaming_started = False
                self._trend_md_streamer = MarkdownStreamer(self._trend_text)

                response = client.invoke_model_with_response_stream(
                    modelId=CLAUDE_MODEL_ID,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(payload),
                )

                for event in response["body"]:
                    chunk = json.loads(event["chunk"]["bytes"])
                    if chunk.get("type") == "content_block_delta":
                        text = chunk["delta"].get("text", "")
                        if text:
                            self._parent.after(0, self._trend_append, text)

                self._parent.after(0, self._trend_finish)

            except Exception as e:
                self._parent.after(0, lambda: self._trend_error(str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _trend_update_status(self, msg):
        if getattr(self, '_trend_cancelled', False):
            return
        try:
            self._trend_text.config(state=tk.NORMAL)
            self._trend_text.delete("1.0", tk.END)
            self._trend_text.insert(tk.END, f"⏳ {msg}\n")
            self._trend_text.config(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _trend_append(self, text):
        if getattr(self, '_trend_cancelled', False):
            return
        try:
            self._trend_text.config(state=tk.NORMAL)
            if not self._trend_streaming_started:
                self._trend_streaming_started = True
                self._trend_text.delete("1.0", tk.END)
            self._trend_md_streamer.feed(text)
            if self._trend_text.yview()[1] >= 0.95:
                self._trend_text.see(tk.END)
            self._trend_text.config(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _trend_finish(self):
        try:
            self._trend_text.config(state=tk.NORMAL)
            if hasattr(self, '_trend_md_streamer'):
                self._trend_md_streamer.flush()
            if self._trend_text.yview()[1] >= 0.95:
                self._trend_text.see(tk.END)
            self._trend_text.config(state=tk.DISABLED)
            self._trend_btn.configure(state=tk.NORMAL, text="Generate Trends")
            self._trend_sift_btn.configure(state=tk.NORMAL)
        except tk.TclError:
            pass

    def _trend_error(self, error):
        try:
            self._trend_text.config(state=tk.NORMAL)
            self._trend_text.insert(tk.END, f"\n⚠️ Error: {error}\n")
            self._trend_text.config(state=tk.DISABLED)
            self._trend_btn.configure(state=tk.NORMAL, text="Generate Trends")
        except tk.TclError:
            pass

    def _submit_trend_sift(self):
        """Queue a SIFT insight from the generated trend analysis."""
        trend_content = self._trend_text.get("1.0", tk.END).strip()
        if not trend_content or trend_content.startswith("⏳"):
            from tkinter import messagebox
            messagebox.showinfo("No Trends", "Generate trends first before submitting to SIFT.")
            return

        self._trend_sift_btn.configure(state=tk.DISABLED, text="📊 Extracting...")

        def _flash_btn(text, color):
            self._trend_sift_btn.configure(text=text, fg_color=color, state=tk.DISABLED)
            self._parent.after(3000, lambda: self._trend_sift_btn.configure(
                state=tk.NORMAL, text="📊 SIFT", fg_color="#1f2937"))

        def _run():
            try:
                from transcription.sift_insight import queue_sift_trend_insight
                path = queue_sift_trend_insight(trend_content)
                if path == "DUPLICATE":
                    self._parent.after(0, lambda: _flash_btn("⚠️ Duplicate", "#92400e"))
                    return
                if not path:
                    self._parent.after(0, lambda: _flash_btn("❌ Failed", "#7f1d1d"))
                    return
                self._parent.after(0, lambda: _flash_btn("✅ Queued", "#065f46"))
                self._parent.after(0, lambda: show_toast(
                    self._parent, "📊 Cross-customer SIFT insight queued"))
            except Exception as e:
                print(f"[sift-trends] Error: {e}")
                self._parent.after(0, lambda: _flash_btn("❌ Error", "#7f1d1d"))

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_data(self):
        try:
            from transcription.history import list_sessions
            from transcription.competitive_intel import get_all_mentions, get_competitor_summary
            from datetime import datetime, timedelta
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            import numpy as np

            all_sessions = list_sessions()
            now = datetime.now()
            week_ago = (now - timedelta(days=7)).isoformat()
            month_ago = (now - timedelta(days=30)).isoformat()
            this_week = [s for s in all_sessions if s.get("timestamp", "") >= week_ago]
            this_month = [s for s in all_sessions if s.get("timestamp", "") >= month_ago]

            customer_counts = {}
            for s in all_sessions:
                c = s.get("customer_name", "Unknown")
                customer_counts[c] = customer_counts.get(c, 0) + 1
            top_customers = sorted(customer_counts.items(), key=lambda x: x[1], reverse=True)[:10]

            # Daily call counts for the past 14 days
            daily_counts = {}
            for i in range(14):
                day = (now - timedelta(days=13 - i)).strftime("%m/%d")
                daily_counts[day] = 0
            for s in all_sessions:
                ts = s.get("timestamp", "")[:10]
                try:
                    d = datetime.fromisoformat(ts)
                    if (now - d).days <= 13:
                        key = d.strftime("%m/%d")
                        if key in daily_counts:
                            daily_counts[key] += 1
                except Exception:
                    pass

            comp_summary = get_competitor_summary()
            all_mentions = get_all_mentions(limit=50)

            # Build charts on main thread
            self._parent.after(0, lambda: self._render_charts(
                all_sessions, this_week, this_month, customer_counts,
                top_customers, daily_counts, comp_summary, all_mentions, now))

        except Exception as e:
            self.status_label.after(0, lambda: self.status_label.configure(text=f"Error: {e}"))

    def _render_charts(self, all_sessions, this_week, this_month, customer_counts,
                       top_customers, daily_counts, comp_summary, all_mentions, now):
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        # Dark theme for matplotlib — polished modern look
        chart_bg = "#0a0a0a"
        chart_face = "#111111"
        chart_fg = "#e0e0e0"
        grid_color = "#1a2332"
        accent = "#10a37f"
        accent2 = "#6ee7b7"
        accent_light = "#10a37f50"
        red_color = "#ef4444"
        red_light = "#ef444450"
        label_size = 8
        title_size = 11

        # Clear stat cards
        for w in self._stats_frame.winfo_children():
            w.destroy()

        # Stat cards
        self._make_stat_card(self._stats_frame, "Total Calls", len(all_sessions))
        self._make_stat_card(self._stats_frame, "This Week", len(this_week), "#6ee7b7")
        self._make_stat_card(self._stats_frame, "This Month", len(this_month), "#60a5fa")
        self._make_stat_card(self._stats_frame, "Customers", len(customer_counts), "#fbbf24")
        comp_count = len(comp_summary) if comp_summary else 0
        self._make_stat_card(self._stats_frame, "Competitors", comp_count, "#ef4444")

        # Clear chart frames — close old matplotlib figures to free GDI handles
        for frame in self._chart_frames:
            for w in frame.winfo_children():
                w.destroy()
        import matplotlib.pyplot as plt
        plt.close('all')

        def embed_chart(frame, fig):
            canvas = FigureCanvasTkAgg(fig, master=frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Chart 1: Daily calls (area chart with gradient)
        fig1 = Figure(figsize=(5, 3), dpi=100, facecolor=chart_bg)
        ax1 = fig1.add_subplot(111)
        ax1.set_facecolor(chart_face)
        days = list(daily_counts.keys())
        counts = list(daily_counts.values())
        ax1.fill_between(range(len(days)), counts, alpha=0.15, color=accent)
        ax1.plot(range(len(days)), counts, color=accent, linewidth=2.5,
                 marker='o', markersize=5, markerfacecolor=accent2, markeredgecolor=accent,
                 markeredgewidth=1.5)
        ax1.set_xticks(range(0, len(days), 2))
        ax1.set_xticklabels([days[i] for i in range(0, len(days), 2)],
                            fontsize=7, color=chart_fg, fontfamily="Segoe UI")
        ax1.set_title("Calls — Last 14 Days", fontsize=title_size, color=chart_fg,
                      pad=10, fontfamily="Segoe UI", fontweight="bold")
        ax1.tick_params(colors=chart_fg, labelsize=7)
        ax1.grid(axis='y', color=grid_color, linewidth=0.5, alpha=0.5)
        ax1.spines['bottom'].set_color(grid_color)
        ax1.spines['left'].set_color(grid_color)
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)
        fig1.tight_layout(pad=1.5)
        embed_chart(self._chart_frames[0], fig1)

        # Chart 2: Top customers (horizontal bar with gradient)
        fig2 = Figure(figsize=(5, 3), dpi=100, facecolor=chart_bg)
        ax2 = fig2.add_subplot(111)
        ax2.set_facecolor(chart_face)
        if top_customers:
            names = [n[:18] for n, _ in reversed(top_customers)]
            vals = [v for _, v in reversed(top_customers)]
            bars = ax2.barh(range(len(names)), vals, color=accent, height=0.55,
                           edgecolor=accent2, linewidth=0.5, alpha=0.85)
            ax2.set_yticks(range(len(names)))
            ax2.set_yticklabels(names, fontsize=7, color=chart_fg, fontfamily="Segoe UI")
            for bar, val in zip(bars, vals):
                ax2.text(bar.get_width() + 0.15, bar.get_y() + bar.get_height()/2,
                        str(val), va='center', fontsize=8, color=accent2,
                        fontweight='bold', fontfamily="Segoe UI")
        ax2.set_title("Top Customers", fontsize=title_size, color=chart_fg,
                      pad=10, fontfamily="Segoe UI", fontweight="bold")
        ax2.tick_params(colors=chart_fg, labelsize=7)
        ax2.grid(axis='x', color=grid_color, linewidth=0.5, alpha=0.5)
        ax2.spines['bottom'].set_color(grid_color)
        ax2.spines['left'].set_color(grid_color)
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)
        fig2.tight_layout(pad=1.5)
        embed_chart(self._chart_frames[1], fig2)

        # Chart 3: Competitor frequency (horizontal bar — red theme)
        fig3 = Figure(figsize=(5, 3), dpi=100, facecolor=chart_bg)
        ax3 = fig3.add_subplot(111)
        ax3.set_facecolor(chart_face)
        if comp_summary:
            top_comp = list(comp_summary.items())[:10]
            cnames = [n[:18] for n, _ in reversed(top_comp)]
            cvals = [v for _, v in reversed(top_comp)]
            bars = ax3.barh(range(len(cnames)), cvals, color=red_color, height=0.55,
                           edgecolor="#fca5a5", linewidth=0.5, alpha=0.85)
            ax3.set_yticks(range(len(cnames)))
            ax3.set_yticklabels(cnames, fontsize=7, color=chart_fg, fontfamily="Segoe UI")
            for bar, val in zip(bars, cvals):
                ax3.text(bar.get_width() + 0.15, bar.get_y() + bar.get_height()/2,
                        str(val), va='center', fontsize=8, color="#fca5a5",
                        fontweight='bold', fontfamily="Segoe UI")
        else:
            ax3.text(0.5, 0.5, "No competitor data yet", ha='center', va='center',
                    fontsize=11, color=FG_DIM, transform=ax3.transAxes, fontfamily="Segoe UI")
        ax3.set_title("Competitor Mentions", fontsize=title_size, color=chart_fg,
                      pad=10, fontfamily="Segoe UI", fontweight="bold")
        ax3.tick_params(colors=chart_fg, labelsize=7)
        ax3.grid(axis='x', color=grid_color, linewidth=0.5, alpha=0.5)
        ax3.spines['bottom'].set_color(grid_color)
        ax3.spines['left'].set_color(grid_color)
        ax3.spines['top'].set_visible(False)
        ax3.spines['right'].set_visible(False)
        fig3.tight_layout(pad=1.5)
        embed_chart(self._chart_frames[2], fig3)

        # Chart 4: Sentiment breakdown (donut chart)
        fig4 = Figure(figsize=(5, 3), dpi=100, facecolor=chart_bg)
        ax4 = fig4.add_subplot(111)
        ax4.set_facecolor(chart_bg)
        if all_mentions:
            sentiments = {"positive": 0, "negative": 0, "neutral": 0}
            for m in all_mentions:
                s = m.get("sentiment", "neutral").lower()
                if s in sentiments:
                    sentiments[s] += 1
            labels = [k.title() for k, v in sentiments.items() if v > 0]
            sizes = [v for v in sentiments.values() if v > 0]
            colors_pie = ["#10a37f", "#ef4444", "#4b5563"][:len(labels)]
            explode = [0.02] * len(labels)
            if sizes:
                wedges, texts, autotexts = ax4.pie(
                    sizes, labels=labels, autopct='%1.0f%%', startangle=90,
                    colors=colors_pie, textprops={'color': chart_fg, 'fontsize': 9,
                                                   'fontfamily': 'Segoe UI'},
                    pctdistance=0.78, wedgeprops=dict(width=0.38, edgecolor=chart_bg,
                                                       linewidth=2),
                    explode=explode)
                for t in autotexts:
                    t.set_fontsize(9)
                    t.set_color("#ffffff")
                    t.set_fontweight("bold")
        else:
            ax4.text(0.5, 0.5, "No sentiment data yet", ha='center', va='center',
                    fontsize=11, color=FG_DIM, transform=ax4.transAxes, fontfamily="Segoe UI")
        ax4.set_title("Competitor Sentiment", fontsize=title_size, color=chart_fg,
                      pad=10, fontfamily="Segoe UI", fontweight="bold")
        fig4.tight_layout(pad=1.5)
        embed_chart(self._chart_frames[3], fig4)

        self.status_label.configure(text=f"Updated {now.strftime('%H:%M')}")


def main():
    root = ctk.CTk()
    root.title("Call Notes — Live Transcriber")
    root.geometry("1440x860")
    root.minsize(1100, 700)
    root.configure(fg_color=BG_DARK)

    # Top bar with theme toggle
    top_bar = ctk.CTkFrame(root, fg_color="transparent", height=36)
    top_bar.pack(fill=tk.X, padx=16, pady=(8, 0))

    def toggle_theme():
        new_theme = "light" if get_theme() == "dark" else "dark"
        set_theme(new_theme)
        root.configure(fg_color=BG_DARK)
        theme_btn.configure(text="🌙 Dark" if new_theme == "light" else "☀️ Light")

    theme_btn = ctk.CTkButton(
        top_bar, text="☀️ Light", width=80, height=28,
        fg_color=BG_INPUT, hover_color=BG_CARD, text_color=ACCENT,
        border_width=1, border_color=BORDER, corner_radius=6,
        font=_font(), command=toggle_theme)
    theme_btn.pack(side=tk.RIGHT)

    # Tab container
    tabview = ctk.CTkTabview(root, fg_color=BG_PANEL, segmented_button_fg_color=BG_CARD,
                              segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color=ACCENT_HOVER,
                              segmented_button_unselected_color=BG_CARD,
                              segmented_button_unselected_hover_color=BG_INPUT,
                              text_color=FG_BRIGHT, text_color_disabled=FG_DIM,
                              border_width=1, border_color=BORDER,
                              corner_radius=12)
    tabview.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

    tabview.add("🎙  Live Transcription")
    tabview.add("🔍  Notes Retrieval")
    tabview.add("🌐  Customer Research")
    tabview.add("📊  Trends & Insights")

    # Tab 1 — existing app (pass the tab frame as root)
    tab1_frame = tabview.tab("🎙  Live Transcription")
    app = CallNotesApp(tab1_frame)

    # Tab 2 — retrieval agent
    tab2_frame = tabview.tab("🔍  Notes Retrieval")
    NotesRetrieverTab(tab2_frame)

    # Tab 3 — customer research agent
    tab3_frame = tabview.tab("🌐  Customer Research")
    CustomerResearchTab(tab3_frame)

    # Tab 4 — insights dashboard
    tab4_frame = tabview.tab("📊  Trends & Insights")
    InsightsTab(tab4_frame)

    root.protocol("WM_DELETE_WINDOW", lambda: (shutdown_agent(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
