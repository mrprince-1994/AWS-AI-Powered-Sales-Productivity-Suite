import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
import threading
from transcriber import LiveTranscriber
from summarizer import generate_notes
from storage import save_notes, _md_to_docx
from history import save_session, list_sessions, get_all_customers
from question_detector import is_aws_aiml_question, extract_question
from agent_client import ask_agent, warmup as warmup_agent, shutdown as shutdown_agent
from md_render import configure_tags, MarkdownStreamer
from notes_retriever import scan_notes, ask_notes_agent, ask_research_agent, NOTE_SOURCES, dedupe_customers

# --- Color Palette ---
BG_DARK = "#0f0f1a"
BG_PANEL = "#1a1a2e"
BG_INPUT = "#252540"
BG_CARD = "#1e1e35"
FG_TEXT = "#d8ddf4"
FG_DIM = "#a6adc8"
FG_BRIGHT = "#ffffff"
ACCENT = "#89b4fa"
ACCENT_HOVER = "#74c7ec"
GREEN = "#a6e3a1"
GREEN_HOVER = "#b5f0b0"
RED = "#f38ba8"
RED_HOVER = "#f5a0b8"
ORANGE = "#fab387"
YELLOW = "#f9e2af"
YELLOW_HOVER = "#fce8b8"
BORDER = "#313150"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class StyledText(tk.Text):
    """Dark-themed tk.Text with a CTk scrollbar, wrapped in a rounded frame."""

    def __init__(self, master, **kwargs):
        self._outer = ctk.CTkFrame(master, fg_color=BG_INPUT, corner_radius=8,
                                    border_width=1, border_color=BORDER)
        super().__init__(self._outer, wrap=tk.WORD, bg=BG_INPUT, fg=FG_TEXT,
                         insertbackground=FG_BRIGHT, borderwidth=0, highlightthickness=0,
                         padx=10, pady=8, selectbackground=ACCENT,
                         selectforeground=BG_DARK, state=tk.DISABLED, **kwargs)
        sb = ctk.CTkScrollbar(self._outer, command=self.yview, fg_color=BG_INPUT,
                               button_color=BORDER, button_hover_color=ACCENT)
        self.configure(yscrollcommand=sb.set)
        super().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 2), pady=4)

    def pack(self, **kw):
        self._outer.pack(**kw)

    def grid(self, **kw):
        self._outer.grid(**kw)


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
        self._ai_enabled = True
        self._pending_questions = set()
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
                         font=ctk.CTkFont("Segoe UI", 20, "bold"),
                         text_color=FG_BRIGHT).pack(side=tk.LEFT)
            self.status_var = tk.StringVar(value="Ready")
            ctk.CTkLabel(title_bar, textvariable=self.status_var,
                         font=ctk.CTkFont("Segoe UI", 12), text_color=ORANGE
                         ).pack(side=tk.RIGHT)
        else:
            self.status_var = tk.StringVar(value="Ready")

        # 3-column layout
        cols = ctk.CTkFrame(self.root, fg_color="transparent")
        cols.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        cols.columnconfigure(1, weight=3)
        cols.columnconfigure(2, weight=2)
        cols.rowconfigure(0, weight=1)

        self._build_history_panel(cols)
        self._build_center_panel(cols)
        self._build_ai_panel(cols)

        # Status bar at bottom (only in tab/embedded mode — standalone uses title bar)
        if not self._is_root:
            status_bar = ctk.CTkFrame(self.root, fg_color=BG_CARD, corner_radius=0, height=28)
            status_bar.pack(fill=tk.X, side=tk.BOTTOM)
            ctk.CTkLabel(status_bar, textvariable=self.status_var,
                         text_color=ORANGE, font=ctk.CTkFont("Segoe UI", 11),
                         anchor="w").pack(side=tk.LEFT, padx=12, pady=4)

        self.root.after(500, self._refresh_history)
    # ── Left: History ──
    def _build_history_panel(self, parent):
        card = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=12,
                             border_width=1, border_color=BORDER)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        ctk.CTkLabel(card, text="📋  History",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=ACCENT).pack(anchor=tk.W, padx=14, pady=(14, 8))

        filt = ctk.CTkFrame(card, fg_color="transparent")
        filt.pack(fill=tk.X, padx=12, pady=(0, 8))
        ctk.CTkLabel(filt, text="Filter:", text_color=FG_DIM,
                     font=ctk.CTkFont("Segoe UI", 11)).pack(side=tk.LEFT)
        self.history_filter_var = tk.StringVar(value="(All)")
        self.history_filter_combo = ctk.CTkComboBox(
            filt, variable=self.history_filter_var, width=140,
            fg_color=BG_INPUT, border_color=BORDER, button_color=BORDER,
            button_hover_color=ACCENT, dropdown_fg_color=BG_INPUT,
            dropdown_hover_color=ACCENT, text_color=FG_BRIGHT,
            font=ctk.CTkFont("Segoe UI", 11), state="readonly",
            command=lambda _: self._refresh_history())
        self.history_filter_combo.pack(side=tk.LEFT, padx=(6, 4))
        ctk.CTkButton(filt, text="⟳", width=32, height=28, fg_color=BG_INPUT,
                      hover_color=BG_CARD, text_color=ACCENT, corner_radius=6,
                      font=ctk.CTkFont("Segoe UI", 13),
                      command=self._refresh_history).pack(side=tk.LEFT)

        lf = ctk.CTkFrame(card, fg_color=BG_INPUT, corner_radius=8,
                           border_width=1, border_color=BORDER)
        lf.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self.history_list = tk.Listbox(
            lf, exportselection=False, bg=BG_INPUT, fg=FG_TEXT,
            selectbackground=ACCENT, selectforeground=BG_DARK,
            font=("Segoe UI", 10), borderwidth=0, highlightthickness=0,
            activestyle="none")
        self.history_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.history_list.bind("<<ListboxSelect>>", self._on_history_select)
        self._history_items = []

    # ── Center: Controls + Transcript + Notes ──
    def _build_center_panel(self, parent):
        card = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=12,
                             border_width=1, border_color=BORDER)
        card.grid(row=0, column=1, sticky="nsew", padx=8)

        # Controls grid
        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill=tk.X, padx=16, pady=(14, 8))

        for i, (label, var_name, placeholder) in enumerate([
            ("Customer Name:", "customer_var", "Enter customer name..."),
        ]):
            ctk.CTkLabel(ctrl, text=label, text_color=FG_DIM,
                         font=ctk.CTkFont("Segoe UI", 12)).grid(row=i, column=0, sticky=tk.W, pady=3)
            setattr(self, var_name, tk.StringVar())
            ctk.CTkEntry(ctrl, textvariable=getattr(self, var_name), width=280,
                         fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
                         font=ctk.CTkFont("Segoe UI", 11), corner_radius=6,
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
                         font=ctk.CTkFont("Segoe UI", 12)).grid(row=i, column=0, sticky=tk.W, pady=3)
            combo = ctk.CTkComboBox(
                ctrl, variable=var, width=400, fg_color=BG_INPUT, border_color=BORDER,
                button_color=BORDER, button_hover_color=ACCENT,
                dropdown_fg_color=BG_INPUT, dropdown_hover_color=ACCENT,
                text_color=FG_BRIGHT, font=ctk.CTkFont("Segoe UI", 10), state="readonly")
            combo.grid(row=i, column=1, padx=(10, 0), sticky=tk.W, pady=3)
            if i == 1:
                self.system_device_combo = combo
            else:
                self.mic_device_combo = combo

        # Buttons
        bf = ctk.CTkFrame(card, fg_color="transparent")
        bf.pack(fill=tk.X, padx=16, pady=(4, 10))

        self.start_btn = ctk.CTkButton(
            bf, text="▶  Start Recording", fg_color=GREEN, hover_color=GREEN_HOVER,
            text_color=BG_DARK, font=ctk.CTkFont("Segoe UI", 12, "bold"),
            corner_radius=8, height=36, command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            bf, text="⏹  Stop & Generate", fg_color=RED, hover_color=RED_HOVER,
            text_color=BG_DARK, font=ctk.CTkFont("Segoe UI", 12, "bold"),
            corner_radius=8, height=36, state=tk.DISABLED, command=self._stop)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 16))

        self.export_docx_btn = ctk.CTkButton(
            bf, text="📄 Export DOCX", fg_color=BG_INPUT, hover_color=BG_CARD,
            text_color=FG_TEXT, font=ctk.CTkFont("Segoe UI", 11), corner_radius=8,
            height=34, border_width=1, border_color=BORDER, state=tk.DISABLED,
            command=self._export_docx)
        self.export_docx_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.export_pdf_btn = ctk.CTkButton(
            bf, text="📑 Export PDF", fg_color=BG_INPUT, hover_color=BG_CARD,
            text_color=FG_TEXT, font=ctk.CTkFont("Segoe UI", 11), corner_radius=8,
            height=34, border_width=1, border_color=BORDER, state=tk.DISABLED,
            command=self._export_pdf)
        self.export_pdf_btn.pack(side=tk.LEFT)

        # Transcript
        ctk.CTkLabel(card, text="Live Transcript",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(anchor=tk.W, padx=16, pady=(4, 4))
        self.transcript_text = StyledText(card, height=8, font=("Consolas", 10))
        self.transcript_text.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))

        # Notes
        ctk.CTkLabel(card, text="Generated Notes",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(anchor=tk.W, padx=16, pady=(2, 4))
        self.notes_text = StyledText(card, height=8)
        self.notes_text.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))


    # ── Right: AI Answers ──
    def _build_ai_panel(self, parent):
        card = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=12,
                             border_width=1, border_color=BORDER)
        card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        ctk.CTkLabel(card, text="🤖  AI Answers",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=YELLOW).pack(anchor=tk.W, padx=14, pady=(14, 8))

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill=tk.X, padx=12, pady=(0, 6))

        self.ai_toggle_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(top, text="Auto-detect questions",
                        variable=self.ai_toggle_var, command=self._toggle_ai,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        text_color=FG_TEXT, font=ctk.CTkFont("Segoe UI", 11)
                        ).pack(side=tk.LEFT)
        ctk.CTkButton(top, text="Clear", width=60, height=28, fg_color=BG_INPUT,
                      hover_color=BG_CARD, text_color=ACCENT, corner_radius=6,
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=self._clear_ai_answers).pack(side=tk.RIGHT)

        # Manual question entry
        ask = ctk.CTkFrame(card, fg_color="transparent")
        ask.pack(fill=tk.X, padx=12, pady=(0, 6))
        self.manual_question_var = tk.StringVar()
        entry = ctk.CTkEntry(ask, textvariable=self.manual_question_var,
                             fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
                             font=ctk.CTkFont("Segoe UI", 11), corner_radius=6,
                             placeholder_text="Ask a question...")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        entry.bind("<Return>", lambda e: self._ask_manual_question())
        ctk.CTkButton(ask, text="Ask", width=60, height=32, fg_color=YELLOW,
                      hover_color=YELLOW_HOVER, text_color=BG_DARK,
                      font=ctk.CTkFont("Segoe UI", 12, "bold"), corner_radius=6,
                      command=self._ask_manual_question).pack(side=tk.RIGHT)

        # AI text display
        self.ai_text = StyledText(card, height=10, font=("Segoe UI", 10))
        self.ai_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        configure_tags(self.ai_text)

    # ─────────────────────────── CALLBACKS ───────────────────────────

    def _on_close(self):
        try:
            shutdown_agent()
        except Exception:
            pass
        if self._is_root:
            self.root.destroy()

    def _toggle_ai(self):
        self._ai_enabled = self.ai_toggle_var.get()

    def _clear_ai_answers(self):
        self.ai_text.config(state=tk.NORMAL)
        self.ai_text.delete("1.0", tk.END)
        self.ai_text.config(state=tk.DISABLED)
        self._pending_questions.clear()

    def _ask_manual_question(self):
        q = self.manual_question_var.get().strip()
        if not q:
            return
        self.manual_question_var.set("")
        self._submit_question(q)

    def _submit_question(self, question):
        q_key = question.lower().strip()
        if q_key in self._pending_questions:
            return
        self._pending_questions.add(q_key)

        self.ai_text.config(state=tk.NORMAL)
        if self.ai_text.get("1.0", "end-1c").strip():
            self.ai_text.insert(tk.END, "\n" + "─" * 50 + "\n", "separator")
        self.ai_text.insert(tk.END, f"❓ {question}\n", "question")
        self.ai_text.insert(tk.END, "⏳ Searching AWS docs...\n", "status")
        self.ai_text.see(tk.END)
        self.ai_text.config(state=tk.DISABLED)

        self._ai_streaming_started = False
        self._ai_md_streamer = MarkdownStreamer(self.ai_text)

        def on_chunk(text):
            self.root.after(0, self._append_ai_chunk, text)

        def on_result(answer, error):
            self.root.after(0, self._finish_ai_answer, error)

        ask_agent(question, callback=on_result, on_chunk=on_chunk)

    def _append_ai_chunk(self, text):
        self.ai_text.config(state=tk.NORMAL)
        if not self._ai_streaming_started:
            self._ai_streaming_started = True
            pos = self.ai_text.search("⏳ Searching AWS docs...", "1.0", tk.END)
            if pos:
                line_end = self.ai_text.index(f"{pos} lineend+1c")
                self.ai_text.delete(pos, line_end)
        self._ai_md_streamer.feed(text)
        self.ai_text.see(tk.END)
        self.ai_text.config(state=tk.DISABLED)

    def _finish_ai_answer(self, error):
        self.ai_text.config(state=tk.NORMAL)
        if error:
            pos = self.ai_text.search("⏳ Searching AWS docs...", "1.0", tk.END)
            if pos:
                line_end = self.ai_text.index(f"{pos} lineend+1c")
                self.ai_text.delete(pos, line_end)
            self.ai_text.insert(tk.END, f"⚠️ {error}\n", "status")
        else:
            if hasattr(self, "_ai_md_streamer"):
                self._ai_md_streamer.flush()
            content = self.ai_text.get("end-2c", "end-1c")
            if content != "\n":
                self.ai_text.insert(tk.END, "\n")
        self.ai_text.see(tk.END)
        self.ai_text.config(state=tk.DISABLED)

    def _check_transcript_for_questions(self, text):
        if not self._ai_enabled:
            return
        if is_aws_aiml_question(text):
            question = extract_question(text)
            if question:
                self._submit_question(question)


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
        sel = self.history_list.curselection()
        if not sel:
            return
        item = self._history_items[sel[0]]
        self._current_transcript = item.get("transcript", "")
        self._current_notes = item.get("notes", "")

        for widget, content in [(self.transcript_text, self._current_transcript),
                                 (self.notes_text, self._current_notes)]:
            widget.config(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, content)
            widget.config(state=tk.DISABLED)

        self.customer_var.set(item["customer_name"])
        self.export_docx_btn.configure(state=tk.NORMAL)
        self.export_pdf_btn.configure(state=tk.NORMAL)
        self.status_var.set(f"Loaded session from {item['timestamp'][:16]}")

    # ─────────────────────────── EXPORT ───────────────────────────

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

    def _safe_show_partial(self, text):
        self.transcript_text.config(state=tk.NORMAL)
        self.transcript_text.delete("partial_start", "end-1c")
        self.transcript_text.insert("partial_start", text)
        self.transcript_text.see(tk.END)
        self.transcript_text.config(state=tk.DISABLED)

    def _safe_show_final(self, text):
        self.transcript_text.config(state=tk.NORMAL)
        self.transcript_text.delete("partial_start", "end-1c")
        self.transcript_text.insert("partial_start", text + "\n")
        self.transcript_text.mark_set("partial_start", "end-1c")
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

        for w in (self.transcript_text, self.notes_text):
            w.config(state=tk.NORMAL)
            w.delete("1.0", tk.END)
            w.config(state=tk.DISABLED)

        self._current_transcript = ""
        self._current_notes = ""
        self._pending_questions.clear()
        self.export_docx_btn.configure(state=tk.DISABLED)
        self.export_pdf_btn.configure(state=tk.DISABLED)

        self.transcriber = LiveTranscriber(
            system_device=system_dev, mic_device=mic_dev,
            on_partial=self._on_partial, on_final=self._on_final)
        self.transcriber.start()

        self.transcript_text.config(state=tk.NORMAL)
        self.transcript_text.mark_set("partial_start", "end-1c")
        self.transcript_text.mark_gravity("partial_start", tk.LEFT)
        self.transcript_text.config(state=tk.DISABLED)

        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status_var.set("🔴 Recording...")

    def _stop(self):
        if not self.transcriber:
            return
        self.status_var.set("Stopping recording...")
        self.transcriber.stop()
        transcript = self.transcriber.get_full_transcript()
        self._current_transcript = transcript

        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

        if not transcript:
            self.status_var.set("No speech detected.")
            messagebox.showinfo("Empty", "No transcript was captured.")
            return

        self.status_var.set("Generating notes with Claude...")
        threading.Thread(target=self._generate_and_save, args=(transcript,),
                         daemon=True).start()

    def _generate_and_save(self, transcript):
        customer = self.customer_var.get().strip()
        self.root.after(0, self._prepare_notes_for_streaming)

        def on_chunk(text):
            self.root.after(0, self._append_notes_chunk, text)

        try:
            notes = generate_notes(transcript, customer, on_chunk=on_chunk)
            self._current_notes = notes
            filepath = save_notes(customer, notes)
            try:
                save_session(customer, transcript, notes, filepath)
            except Exception:
                pass
            self.root.after(0, lambda: self.status_var.set(f"Notes saved: {filepath}"))
            self.root.after(0, lambda: self.export_docx_btn.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.export_pdf_btn.configure(state=tk.NORMAL))
            self.root.after(0, self._refresh_history)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror(
                "Error", f"Failed to generate notes:\n{e}"))
            self.root.after(0, lambda: self.status_var.set("Error generating notes."))

    def _prepare_notes_for_streaming(self):
        self.notes_text.config(state=tk.NORMAL)
        self.notes_text.delete("1.0", tk.END)

    def _append_notes_chunk(self, text):
        self.notes_text.config(state=tk.NORMAL)
        self.notes_text.insert(tk.END, text)
        self.notes_text.see(tk.END)


class NotesRetrieverTab:
    """Tab 2 — multi-turn chat with historical call notes via Claude Opus 4.6."""

    def __init__(self, parent):
        self._notes_meta = []
        self._conversation_history = []
        self._streaming_started = False
        self._md_streamer = None
        self._is_responding = False
        self._agent_mode = "retrieval"   # "retrieval" | "research"
        self._build_ui(parent)
        threading.Thread(target=self._refresh_index, daemon=True).start()

    def _build_ui(self, parent):
        # Top bar
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill=tk.X, padx=16, pady=(14, 6))

        ctk.CTkLabel(top, text="📂  Historical Notes Retrieval",
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=FG_BRIGHT).pack(side=tk.LEFT)

        # New Chat button
        ctk.CTkButton(
            top, text="＋ New Chat", width=110, height=30,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color=BG_DARK,
            border_width=0, corner_radius=6,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._new_chat).pack(side=tk.RIGHT, padx=(8, 0))

        self.refresh_btn = ctk.CTkButton(
            top, text="⟳ Refresh Index", width=130, height=30,
            fg_color=BG_INPUT, hover_color=BG_CARD, text_color=ACCENT,
            border_width=1, border_color=BORDER, corner_radius=6,
            font=ctk.CTkFont("Segoe UI", 11),
            command=lambda: threading.Thread(target=self._refresh_index, daemon=True).start())
        self.refresh_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # Agent mode toggle — Retrieval ↔ Research
        toggle_frame = ctk.CTkFrame(top, fg_color=BG_CARD, corner_radius=8,
                                     border_width=1, border_color=BORDER)
        toggle_frame.pack(side=tk.RIGHT, padx=(0, 12))
        self._retrieval_btn = ctk.CTkButton(
            toggle_frame, text="📋 Notes Retrieval", width=140, height=28,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=BG_DARK,
            corner_radius=6, border_width=0,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._set_agent_mode("retrieval"))
        self._retrieval_btn.pack(side=tk.LEFT, padx=3, pady=3)
        self._research_btn = ctk.CTkButton(
            toggle_frame, text="🌐 Customer Research", width=150, height=28,
            fg_color="transparent", hover_color=BG_INPUT, text_color=FG_DIM,
            corner_radius=6, border_width=0,
            font=ctk.CTkFont("Segoe UI", 11),
            command=lambda: self._set_agent_mode("research"))
        self._research_btn.pack(side=tk.LEFT, padx=(0, 3), pady=3)

        # Clickable index summary — click to expand/collapse the index panel
        self.index_label = ctk.CTkLabel(
            top, text="Scanning...", text_color=ACCENT,
            font=ctk.CTkFont("Segoe UI", 11), cursor="hand2")
        self.index_label.pack(side=tk.RIGHT, padx=(0, 12))
        self.index_label.bind("<Button-1>", lambda e: self._toggle_index_panel())

        # Collapsible index panel (hidden by default)
        self._index_panel_visible = False
        self._index_panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=8,
                                          border_width=1, border_color=BORDER)
        # Don't pack yet — toggled on demand

        index_header = ctk.CTkFrame(self._index_panel, fg_color="transparent")
        index_header.pack(fill=tk.X, padx=10, pady=(8, 4))
        ctk.CTkLabel(index_header, text="Indexed Notes",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        ctk.CTkButton(index_header, text="✕", width=28, height=24,
                      fg_color="transparent", hover_color=BG_INPUT,
                      text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 11),
                      command=self._toggle_index_panel).pack(side=tk.RIGHT)

        list_frame = ctk.CTkFrame(self._index_panel, fg_color=BG_INPUT, corner_radius=6)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.notes_list = tk.Listbox(
            list_frame, bg=BG_INPUT, fg=FG_TEXT,
            selectbackground=ACCENT, selectforeground=BG_DARK,
            font=("Segoe UI", 9), borderwidth=0, highlightthickness=0,
            activestyle="none", height=12)
        sb = tk.Scrollbar(list_frame, command=self.notes_list.yview,
                          bg=BG_INPUT, troughcolor=BG_INPUT, bd=0)
        self.notes_list.configure(yscrollcommand=sb.set)
        self.notes_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Keep a reference so _toggle_index_panel can insert after it
        self._top_bar = top

        # Directory info bar
        dir_bar = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=6)
        dir_bar.pack(fill=tk.X, padx=16, pady=(0, 8))
        for _, label in NOTE_SOURCES:
            ctk.CTkLabel(dir_bar, text=f"📁  {label}",
                         text_color=FG_DIM, font=ctk.CTkFont("Consolas", 10),
                         anchor="w").pack(fill=tk.X, padx=10, pady=(4, 0))
        ctk.CTkFrame(dir_bar, fg_color="transparent", height=4).pack()

        # Source + customer filter row
        filter_row = ctk.CTkFrame(parent, fg_color="transparent")
        filter_row.pack(fill=tk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(filter_row, text="Source:",
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 11)).pack(side=tk.LEFT)
        self.source_filter_var = tk.StringVar(value="All Sources")
        source_options = ["All Sources"] + [label for _, label in NOTE_SOURCES]
        self.source_combo = ctk.CTkComboBox(
            filter_row, variable=self.source_filter_var, width=140,
            fg_color=BG_INPUT, border_color=BORDER, button_color=BORDER,
            button_hover_color=ACCENT, dropdown_fg_color=BG_INPUT,
            dropdown_hover_color=ACCENT, text_color=FG_BRIGHT,
            font=ctk.CTkFont("Segoe UI", 11), state="readonly",
            values=source_options,
            command=lambda _: self._new_chat())
        self.source_combo.pack(side=tk.LEFT, padx=(8, 16))

        ctk.CTkLabel(filter_row, text="Customer:",
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 11)).pack(side=tk.LEFT)
        self.customer_filter_var = tk.StringVar(value="(All)")
        self.customer_combo = ctk.CTkComboBox(
            filter_row, variable=self.customer_filter_var, width=200,
            fg_color=BG_INPUT, border_color=BORDER, button_color=BORDER,
            button_hover_color=ACCENT, dropdown_fg_color=BG_INPUT,
            dropdown_hover_color=ACCENT, text_color=FG_BRIGHT,
            font=ctk.CTkFont("Segoe UI", 11), state="readonly",
            values=["(All)"],
            command=lambda _: self._new_chat())
        self.customer_combo.pack(side=tk.LEFT, padx=(8, 0))
        ctk.CTkLabel(filter_row,
                     text="  (changing filters starts a new chat)",
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10)).pack(side=tk.LEFT)

        # Suggested prompts
        prompts_frame = ctk.CTkFrame(parent, fg_color="transparent")
        prompts_frame.pack(fill=tk.X, padx=16, pady=(0, 8))
        ctk.CTkLabel(prompts_frame, text="Suggestions:",
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10)).pack(side=tk.LEFT)
        for prompt in [
            "What action items are outstanding?",
            "Summarize all calls with this customer",
            "What pricing was discussed?",
            "What follow-ups were promised?",
        ]:
            ctk.CTkButton(
                prompts_frame, text=prompt, height=24,
                fg_color=BG_CARD, hover_color=BG_INPUT, text_color=FG_DIM,
                border_width=1, border_color=BORDER, corner_radius=12,
                font=ctk.CTkFont("Segoe UI", 10),
                command=lambda p=prompt: self._use_suggestion(p)
            ).pack(side=tk.LEFT, padx=(6, 0))

        # Chat panel — full width now that the index list is hidden by default
        chat_outer = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=10,
                                   border_width=1, border_color=BORDER)
        chat_outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 0))
        chat_outer.rowconfigure(0, weight=1)
        chat_outer.rowconfigure(1, weight=0)
        chat_outer.columnconfigure(0, weight=1)

        # Chat header
        chat_header = ctk.CTkFrame(chat_outer, fg_color="transparent")
        chat_header.grid(row=0, column=0, sticky="new", padx=12, pady=(10, 4))
        ctk.CTkLabel(chat_header, text="Chat",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=YELLOW).pack(side=tk.LEFT)
        self.turn_label = ctk.CTkLabel(
            chat_header, text="New conversation",
            text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10))
        self.turn_label.pack(side=tk.LEFT, padx=(10, 0))
        self.model_label = ctk.CTkLabel(chat_header, text="📋 Notes Retrieval  ·  Claude Opus 4.6  ·  Bedrock",
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10))
        self.model_label.pack(side=tk.RIGHT)

        # Chat display
        self.chat_text = StyledText(chat_outer, font=("Segoe UI", 10))
        self.chat_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=(32, 4))
        configure_tags(self.chat_text)
        self.chat_text.tag_configure(
            "user_msg", foreground=ACCENT,
            font=("Segoe UI Semibold", 10), lmargin1=8, lmargin2=8, spacing1=6)
        self.chat_text.tag_configure(
            "user_label", foreground=ACCENT,
            font=("Segoe UI", 9), spacing1=8)
        self.chat_text.tag_configure(
            "assistant_label", foreground=YELLOW,
            font=("Segoe UI", 9), spacing1=8)

        # Input row pinned to bottom
        input_row = ctk.CTkFrame(chat_outer, fg_color=BG_CARD, corner_radius=0)
        input_row.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        input_row.columnconfigure(0, weight=1)

        self.input_var = tk.StringVar()
        self.input_entry = ctk.CTkEntry(
            input_row, textvariable=self.input_var,
            fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
            font=ctk.CTkFont("Segoe UI", 12), corner_radius=6,
            placeholder_text="Ask a follow-up or start a new question...")
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=8)
        self.input_entry.bind("<Return>", lambda e: self._send())

        self.send_btn = ctk.CTkButton(
            input_row, text="Send ↵", width=90, height=36,
            fg_color=YELLOW, hover_color=YELLOW_HOVER, text_color=BG_DARK,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), corner_radius=6,
            command=self._send)
        self.send_btn.grid(row=0, column=1, padx=(0, 10), pady=8)

    # ── Actions ──

    def _set_agent_mode(self, mode: str):
        """Switch between 'retrieval' and 'research' agent modes."""
        self._agent_mode = mode
        if mode == "retrieval":
            self._retrieval_btn.configure(fg_color=ACCENT, text_color=BG_DARK,
                                           font=ctk.CTkFont("Segoe UI", 11, "bold"))
            self._research_btn.configure(fg_color="transparent", text_color=FG_DIM,
                                          font=ctk.CTkFont("Segoe UI", 11))
            self.model_label.configure(text="📋 Notes Retrieval  ·  Claude Opus 4.6  ·  Bedrock")
            self.input_entry.configure(placeholder_text="Ask about your historical call notes...")
        else:
            self._research_btn.configure(fg_color=YELLOW, text_color=BG_DARK,
                                          font=ctk.CTkFont("Segoe UI", 11, "bold"))
            self._retrieval_btn.configure(fg_color="transparent", text_color=FG_DIM,
                                           font=ctk.CTkFont("Segoe UI", 11))
            self.model_label.configure(text="🌐 Customer Research  ·  Claude Sonnet 4.6  ·  Web Search")
            self.input_entry.configure(placeholder_text="Research a customer (e.g. 'What's new with BQE?')...")
        self._new_chat()

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

    def _new_chat(self):
        """Reset conversation history and clear the chat display."""
        self._conversation_history = []
        self._is_responding = False
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.delete("1.0", tk.END)
        self.chat_text.config(state=tk.DISABLED)
        self.send_btn.configure(state=tk.NORMAL, text="Send ↵")
        self.turn_label.configure(text="New conversation")
        self.input_entry.focus()

    def _refresh_index(self):
        all_notes = scan_notes()
        self._all_notes_cache = all_notes

        # Collect all raw customer names
        raw_names = list({n["customer"] for n in all_notes})

        # Fuzzy-deduplicate: 'Common Chain' and 'Common Chains' → one entry
        from notes_retriever import dedupe_customers
        canonical_map = dedupe_customers(raw_names)

        # Store the mapping so _get_active_notes can match against canonical names
        self._canonical_map = canonical_map

        # Unique canonical names for the dropdown
        canonical_set = {}
        for orig, canon in canonical_map.items():
            key = canon.lower()
            if key not in canonical_set:
                canonical_set[key] = canon
        customers = ["(All)"] + sorted(canonical_set.values(), key=str.lower)

        self.customer_combo.after(0, lambda: self._update_index_ui(all_notes, customers))

    def _update_index_ui(self, all_notes, customers):
        self.customer_combo.configure(values=customers)
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

        # Render user bubble
        thinking_msg = (
            "⏳ Reading notes and thinking..."
            if self._agent_mode == "retrieval"
            else "🌐 Searching the web..."
        )
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.insert(tk.END, "You\n", "user_label")
        self.chat_text.insert(tk.END, f"{question}\n\n", "user_msg")
        self.chat_text.insert(tk.END, "Assistant\n", "assistant_label")
        self.chat_text.insert(tk.END, f"{thinking_msg}\n", "status")
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

        self._streaming_started = False
        self._md_streamer = MarkdownStreamer(self.chat_text)

        def on_chunk(text):
            self.chat_text.after(0, self._append_chunk, text)

        def on_done(answer, error):
            self.chat_text.after(0, self._finish, error)

        if self._agent_mode == "retrieval":
            ask_notes_agent(
                question,
                self._get_active_notes(),
                self._conversation_history,
                on_chunk=on_chunk,
                callback=on_done,
            )
        else:
            customer = self.customer_filter_var.get()
            if customer == "(All)":
                customer = ""
            ask_research_agent(
                question,
                customer,
                self._conversation_history,
                on_chunk=on_chunk,
                callback=on_done,
            )

    def _append_chunk(self, text):
        self.chat_text.config(state=tk.NORMAL)
        if not self._streaming_started:
            self._streaming_started = True
            for thinking_msg in ["⏳ Reading notes and thinking...",
                                  "🌐 Searching the web...",
                                  "🔍 Querying retrieval agent...",
                                  "🌐 Querying research agent..."]:
                pos = self.chat_text.search(thinking_msg, "1.0", tk.END)
                if pos:
                    self.chat_text.delete(pos, f"{pos} lineend+1c")
                    break
        self._md_streamer.feed(text)
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

    def _finish(self, error):
        self.chat_text.config(state=tk.NORMAL)
        if error:
            pos = self.chat_text.search("⏳ Reading notes and thinking...", "1.0", tk.END)
            if pos:
                self.chat_text.delete(pos, f"{pos} lineend+1c")
            self.chat_text.insert(tk.END, f"⚠️ {error}\n", "status")
        else:
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


def main():
    root = ctk.CTk()
    root.title("Call Notes — Live Transcriber")
    root.geometry("1440x860")
    root.minsize(1100, 700)
    root.configure(fg_color=BG_DARK)

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

    # Tab 1 — existing app (pass the tab frame as root)
    tab1_frame = tabview.tab("🎙  Live Transcription")
    app = CallNotesApp(tab1_frame)

    # Tab 2 — retrieval agent
    tab2_frame = tabview.tab("🔍  Notes Retrieval")
    NotesRetrieverTab(tab2_frame)

    root.protocol("WM_DELETE_WINDOW", lambda: (shutdown_agent(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
