import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
import threading
from transcription.transcriber import LiveTranscriber
from transcription.summarizer import generate_notes, generate_followup_email
from transcription.storage import save_notes, _md_to_docx
from transcription.history import save_session, list_sessions, get_all_customers
from transcription.question_detector import is_aws_aiml_question, extract_question
from transcription.agent_client import ask_agent, warmup as warmup_agent, shutdown as shutdown_agent
from md_render import configure_tags, MarkdownStreamer
from retrieval.notes_retriever import scan_notes, ask_notes_agent, ask_research_agent, NOTE_SOURCES, dedupe_customers
from retrieval.chat_history import save_chat_session, list_chat_sessions, load_chat_session, delete_chat_session, _ensure_table

# --- Color Palette (monochrome) ---
BG_DARK = "#0a0a0a"
BG_PANEL = "#141414"
BG_INPUT = "#1e1e1e"
BG_CARD = "#1a1a1a"
FG_TEXT = "#d4d4d4"
FG_DIM = "#8a8a8a"
FG_BRIGHT = "#f0f0f0"
ACCENT = "#ffffff"
ACCENT_HOVER = "#e0e0e0"
GREEN = "#a0a0a0"
GREEN_HOVER = "#b8b8b8"
RED = "#c0c0c0"
RED_HOVER = "#d8d8d8"
ORANGE = "#b0b0b0"
YELLOW = "#e8e8e8"
YELLOW_HOVER = "#f5f5f5"
BORDER = "#2e2e2e"

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
        self._current_email = ""
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
        transcript_header = ctk.CTkFrame(card, fg_color="transparent")
        transcript_header.pack(fill=tk.X, padx=16, pady=(4, 4))
        ctk.CTkLabel(transcript_header, text="Live Transcript",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        self.copy_transcript_btn = ctk.CTkButton(
            transcript_header, text="📋 Copy Transcript", width=130, height=28,
            fg_color=BG_INPUT, hover_color=BG_CARD, text_color=FG_TEXT,
            font=ctk.CTkFont("Segoe UI", 11), corner_radius=6,
            border_width=1, border_color=BORDER, state=tk.DISABLED,
            command=self._copy_transcript)
        self.copy_transcript_btn.pack(side=tk.RIGHT)
        self.transcript_text = StyledText(card, height=8, font=("Consolas", 10))
        self.transcript_text.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))

        # Notes
        ctk.CTkLabel(card, text="Generated Notes",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(anchor=tk.W, padx=16, pady=(2, 4))
        self.notes_text = StyledText(card, height=8)
        self.notes_text.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))

        # Follow-Up Email
        email_header = ctk.CTkFrame(card, fg_color="transparent")
        email_header.pack(fill=tk.X, padx=16, pady=(2, 4))
        ctk.CTkLabel(email_header, text="📧  Follow-Up Email",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        self.outlook_draft_btn = ctk.CTkButton(
            email_header, text="📨 Outlook Draft", width=120, height=28,
            fg_color=BG_INPUT, hover_color=BG_CARD, text_color=FG_TEXT,
            font=ctk.CTkFont("Segoe UI", 11), corner_radius=6,
            border_width=1, border_color=BORDER, state=tk.DISABLED,
            command=self._send_to_outlook_draft)
        self.outlook_draft_btn.pack(side=tk.RIGHT)
        self.email_text = StyledText(card, height=6, font=("Segoe UI", 10))
        self.email_text.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))


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
        self.copy_transcript_btn.configure(state=tk.NORMAL if self._current_transcript else tk.DISABLED)
        self.outlook_draft_btn.configure(state=tk.NORMAL if self._current_email else tk.DISABLED)
        self.status_var.set(f"Loaded session from {item['timestamp'][:16]}")

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

        for w in (self.transcript_text, self.notes_text, self.email_text):
            w.config(state=tk.NORMAL)
            w.delete("1.0", tk.END)
            w.config(state=tk.DISABLED)

        self._current_transcript = ""
        self._current_notes = ""
        self._current_email = ""
        self._pending_questions.clear()
        self.export_docx_btn.configure(state=tk.DISABLED)
        self.export_pdf_btn.configure(state=tk.DISABLED)
        self.outlook_draft_btn.configure(state=tk.DISABLED)
        self.copy_transcript_btn.configure(state=tk.DISABLED)

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

        self.status_var.set("Generating notes & follow-up email...")
        threading.Thread(target=self._generate_and_save, args=(transcript,),
                         daemon=True).start()

    def _generate_and_save(self, transcript):
        customer = self.customer_var.get().strip()
        self.root.after(0, self._prepare_notes_for_streaming)
        self.root.after(0, self._prepare_email_for_streaming)

        # Shared state for parallel results
        results = {"notes": None, "email": None, "filepath": None,
                   "notes_error": None, "email_error": None}

        def run_notes():
            try:
                def on_chunk(text):
                    self.root.after(0, self._append_notes_chunk, text)
                notes = generate_notes(transcript, customer, on_chunk=on_chunk)
                results["notes"] = notes
                self._current_notes = notes
                results["filepath"] = save_notes(customer, notes)
            except Exception as e:
                results["notes_error"] = e

        def run_email():
            try:
                def on_email_chunk(text):
                    self.root.after(0, self._append_email_chunk, text)
                email = generate_followup_email(transcript, customer, on_chunk=on_email_chunk)
                results["email"] = email
                self._current_email = email
            except Exception as e:
                results["email_error"] = e

        notes_thread = threading.Thread(target=run_notes, daemon=True)
        email_thread = threading.Thread(target=run_email, daemon=True)
        notes_thread.start()
        email_thread.start()
        notes_thread.join()
        email_thread.join()

        if results["notes_error"]:
            self.root.after(0, lambda: messagebox.showerror(
                "Error", f"Failed to generate notes:\n{results['notes_error']}"))
            self.root.after(0, lambda: self.status_var.set("Error generating notes."))
            return

        try:
            save_session(customer, transcript,
                         results["notes"] or "", results["filepath"] or "",
                         followup_email=results["email"] or "")
        except Exception:
            pass

        self.root.after(0, lambda: self.status_var.set(f"Notes saved: {results['filepath']}"))
        self.root.after(0, lambda: self.export_docx_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.export_pdf_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.copy_transcript_btn.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.outlook_draft_btn.configure(
            state=tk.NORMAL if results["email"] else tk.DISABLED))
        self.root.after(0, self._refresh_history)

        if results["email_error"]:
            self.root.after(0, lambda: self.status_var.set(
                f"Notes saved but email failed: {results['email_error']}"))

    def _prepare_notes_for_streaming(self):
        self.notes_text.config(state=tk.NORMAL)
        self.notes_text.delete("1.0", tk.END)

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
        self._build_ui(parent)
        threading.Thread(target=self._refresh_index, daemon=True).start()
        threading.Thread(target=self._init_history_table, daemon=True).start()

    def _build_ui(self, parent):
        # ── Top bar (spans full width) ──────────────────────────────────────
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill=tk.X, padx=16, pady=(14, 6))

        ctk.CTkLabel(top, text="📂  Historical Notes Retrieval",
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=FG_BRIGHT).pack(side=tk.LEFT)

        ctk.CTkButton(
            top, text="＋ New Chat", width=110, height=30,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color=BG_DARK,
            border_width=0, corner_radius=6,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._new_chat).pack(side=tk.RIGHT, padx=(8, 0))

        ctk.CTkButton(
            top, text="⟳ Refresh Index", width=130, height=30,
            fg_color=BG_INPUT, hover_color=BG_CARD, text_color=ACCENT,
            border_width=1, border_color=BORDER, corner_radius=6,
            font=ctk.CTkFont("Segoe UI", 11),
            command=lambda: threading.Thread(target=self._refresh_index, daemon=True).start()
        ).pack(side=tk.RIGHT, padx=(8, 0))

        # Clickable index summary
        self.index_label = ctk.CTkLabel(
            top, text="Scanning...", text_color=ACCENT,
            font=ctk.CTkFont("Segoe UI", 11), cursor="hand2")
        self.index_label.pack(side=tk.RIGHT, padx=(0, 12))
        self.index_label.bind("<Button-1>", lambda e: self._toggle_index_panel())

        # Collapsible index panel (hidden by default)
        self._index_panel_visible = False
        self._index_panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=8,
                                          border_width=1, border_color=BORDER)
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
                                border_width=1, border_color=BORDER, width=220)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(16, 6), pady=(0, 8))
        sidebar.grid_propagate(False)

        sh_header = ctk.CTkFrame(sidebar, fg_color="transparent")
        sh_header.pack(fill=tk.X, padx=10, pady=(10, 4))
        ctk.CTkLabel(sh_header, text="🕘 History",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        ctk.CTkButton(sh_header, text="⟳", width=28, height=24,
                      fg_color="transparent", hover_color=BG_INPUT,
                      text_color=ACCENT, font=ctk.CTkFont("Segoe UI", 12),
                      command=lambda: threading.Thread(
                          target=self._load_session_history, daemon=True).start()
                      ).pack(side=tk.RIGHT)

        # Filter: All / Retrieval only
        sh_filter_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        sh_filter_frame.pack(fill=tk.X, padx=10, pady=(0, 6))
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
                      fg_color=BG_INPUT, hover_color=RED, text_color=FG_DIM,
                      border_width=1, border_color=BORDER, corner_radius=6,
                      font=ctk.CTkFont("Segoe UI", 10),
                      command=self._delete_selected_session).pack(side=tk.LEFT)

        # ── Right: main content ────────────────────────────────────────────
        main = ctk.CTkFrame(body, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=0)
        main.rowconfigure(3, weight=1)
        main.columnconfigure(0, weight=1)

        # Directory info bar
        dir_bar = ctk.CTkFrame(main, fg_color=BG_CARD, corner_radius=6)
        dir_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        for _, label in NOTE_SOURCES:
            ctk.CTkLabel(dir_bar, text=f"📁  {label}",
                         text_color=FG_DIM, font=ctk.CTkFont("Consolas", 10),
                         anchor="w").pack(fill=tk.X, padx=10, pady=(4, 0))
        ctk.CTkFrame(dir_bar, fg_color="transparent", height=4).pack()

        # Source + customer filter row
        filter_row = ctk.CTkFrame(main, fg_color="transparent")
        filter_row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
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
        self._customer_values = ["(All)"]
        self.customer_btn = ctk.CTkButton(
            filter_row, textvariable=self.customer_filter_var, width=200,
            fg_color=BG_INPUT, hover_color=BG_CARD, text_color=FG_BRIGHT,
            font=ctk.CTkFont("Segoe UI", 11), corner_radius=6,
            border_width=1, border_color=BORDER, anchor="w",
            command=self._open_customer_picker)
        self.customer_btn.pack(side=tk.LEFT, padx=(8, 0))
        ctk.CTkLabel(filter_row,
                     text="  (changing filters starts a new chat)",
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10)).pack(side=tk.LEFT)

        # Suggested prompts
        prompts_frame = ctk.CTkFrame(main, fg_color="transparent")
        prompts_frame.grid(row=2, column=0, sticky="ew", pady=(0, 6))
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

        # Chat panel
        chat_outer = ctk.CTkFrame(main, fg_color=BG_PANEL, corner_radius=10,
                                   border_width=1, border_color=BORDER)
        chat_outer.grid(row=3, column=0, sticky="nsew", pady=(0, 0))
        chat_outer.rowconfigure(0, weight=1)
        chat_outer.rowconfigure(1, weight=0)
        chat_outer.columnconfigure(0, weight=1)

        chat_header = ctk.CTkFrame(chat_outer, fg_color="transparent")
        chat_header.grid(row=0, column=0, sticky="new", padx=12, pady=(10, 4))
        ctk.CTkLabel(chat_header, text="Chat",
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=YELLOW).pack(side=tk.LEFT)
        self.turn_label = ctk.CTkLabel(
            chat_header, text="New conversation",
            text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10))
        self.turn_label.pack(side=tk.LEFT, padx=(10, 0))
        self.model_label = ctk.CTkLabel(
            chat_header, text="📋 Notes Retrieval  ·  Claude Opus 4.6  ·  Bedrock",
            text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10))
        self.model_label.pack(side=tk.RIGHT)

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

        input_row = ctk.CTkFrame(chat_outer, fg_color=BG_CARD, corner_radius=0)
        input_row.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        input_row.columnconfigure(0, weight=1)

        self.input_var = tk.StringVar()
        self.input_entry = ctk.CTkEntry(
            input_row, textvariable=self.input_var,
            fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
            font=ctk.CTkFont("Segoe UI", 12), corner_radius=6,
            placeholder_text="Ask about your historical call notes...")
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=8)
        self.input_entry.bind("<Return>", lambda e: self._send())

        self.send_btn = ctk.CTkButton(
            input_row, text="Send ↵", width=90, height=36,
            fg_color=YELLOW, hover_color=YELLOW_HOVER, text_color=BG_DARK,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), corner_radius=6,
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
            font=ctk.CTkFont("Segoe UI", 11), corner_radius=6, height=32)
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
        """Create DynamoDB table on first run (no-op if it already exists)."""
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
            icon = "📋" if item.get("session_type") == "retrieval" else "🌐"
            ts = item.get("timestamp", "")[:16].replace("T", " ")
            title = item.get("title", "—")[:28]
            turns = item.get("turn_count", 0)
            self._sh_listbox.insert(tk.END, f"{icon} {ts}\n{title}  ({turns}t)")

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
                self.chat_text.insert(tk.END, f"{content}\n\n", "body")

        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

        turns = len(history) // 2
        ts_display = item.get("timestamp", "")[:16].replace("T", " ")
        self.turn_label.configure(
            text=f"{turns} turn{'s' if turns != 1 else ''}  ·  restored {ts_display}")
        self.send_btn.configure(state=tk.NORMAL, text="Send ↵")

    def _save_current_session(self):
        """Persist the current conversation to DynamoDB."""
        if not self._conversation_history:
            return
        # Derive a title from the first user message
        first_user = next(
            (m["content"] for m in self._conversation_history
             if m["role"] == "user" and isinstance(m["content"], str)),
            "Chat session"
        )
        title = first_user[:80]
        customer = self.customer_filter_var.get()
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

        self.customer_btn.after(0, lambda: self._update_index_ui(all_notes, customers))

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
        "🔍 Scanning note index...",
        "📂 Identifying relevant files...",
        "📖 Reading call notes...",
        "🧠 Synthesizing findings...",
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
            # e.g. "📂 Reading: filename" or "🔍 Querying..." — show in spinner
            if any(stripped.startswith(p) for p in ("📂", "🔍", "🌐", "⏳")):
                self._thinking_last_event = stripped[:60]
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
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=FG_BRIGHT).pack(side=tk.LEFT)

        ctk.CTkButton(
            top, text="＋ New Chat", width=110, height=30,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color=BG_DARK,
            border_width=0, corner_radius=6,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
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
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=ACCENT).pack(side=tk.LEFT)
        ctk.CTkButton(sh_header, text="⟳", width=28, height=24,
                      fg_color="transparent", hover_color=BG_INPUT,
                      text_color=ACCENT, font=ctk.CTkFont("Segoe UI", 12),
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
                      fg_color=BG_INPUT, hover_color=RED, text_color=FG_DIM,
                      border_width=1, border_color=BORDER, corner_radius=6,
                      font=ctk.CTkFont("Segoe UI", 10),
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
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10)).pack(side=tk.LEFT)
        for prompt in [
            "What does this company do?",
            "Recent news and funding",
            "Key decision makers",
            "AWS services they might need",
        ]:
            ctk.CTkButton(
                prompts_frame, text=prompt, height=24,
                fg_color=BG_CARD, hover_color=BG_INPUT, text_color=FG_DIM,
                border_width=1, border_color=BORDER, corner_radius=12,
                font=ctk.CTkFont("Segoe UI", 10),
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
                     font=ctk.CTkFont("Segoe UI", 12, "bold"),
                     text_color=YELLOW).pack(side=tk.LEFT)
        self.turn_label = ctk.CTkLabel(
            chat_header, text="New conversation",
            text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10))
        self.turn_label.pack(side=tk.LEFT, padx=(10, 0))
        ctk.CTkLabel(
            chat_header, text="🌐 Customer Research  ·  Claude Sonnet 4  ·  Web Search",
            text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10)).pack(side=tk.RIGHT)

        self.chat_text = StyledText(chat_outer, font=("Segoe UI", 10))
        self.chat_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=(32, 4))
        configure_tags(self.chat_text)
        self.chat_text.tag_configure("user_msg", foreground=ACCENT,
            font=("Segoe UI Semibold", 10), lmargin1=8, lmargin2=8, spacing1=6)
        self.chat_text.tag_configure("user_label", foreground=ACCENT,
            font=("Segoe UI", 9), spacing1=8)
        self.chat_text.tag_configure("assistant_label", foreground=YELLOW,
            font=("Segoe UI", 9), spacing1=8)

        input_row = ctk.CTkFrame(chat_outer, fg_color=BG_CARD, corner_radius=0)
        input_row.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        input_row.columnconfigure(0, weight=1)

        self.input_var = tk.StringVar()
        self.input_entry = ctk.CTkEntry(
            input_row, textvariable=self.input_var,
            fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
            font=ctk.CTkFont("Segoe UI", 12), corner_radius=6,
            placeholder_text="Research a customer (e.g. 'What's new with Acme Corp?')...")
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=8)
        self.input_entry.bind("<Return>", lambda e: self._send())

        self.send_btn = ctk.CTkButton(
            input_row, text="Send ↵", width=90, height=36,
            fg_color=YELLOW, hover_color=YELLOW_HOVER, text_color=BG_DARK,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), corner_radius=6,
            command=self._send)
        self.send_btn.grid(row=0, column=1, padx=(0, 10), pady=8)

        # ── Right: Customer Brief panel ──
        brief_panel = ctk.CTkFrame(body, fg_color=BG_PANEL, corner_radius=10,
                                    border_width=1, border_color=BORDER, width=240)
        brief_panel.grid(row=0, column=2, sticky="nsew", padx=(6, 16), pady=(0, 8))
        brief_panel.grid_propagate(False)

        ctk.CTkLabel(brief_panel, text="📄  Customer Brief",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=ACCENT).pack(anchor=tk.W, padx=12, pady=(12, 8))

        ctk.CTkLabel(brief_panel, text="Company Name:",
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 11)
                     ).pack(anchor=tk.W, padx=12, pady=(4, 0))
        self.brief_company_var = tk.StringVar()
        ctk.CTkEntry(brief_panel, textvariable=self.brief_company_var, width=210,
                     fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
                     font=ctk.CTkFont("Segoe UI", 11), corner_radius=6,
                     placeholder_text="e.g. Denali Therapeutics"
                     ).pack(padx=12, pady=(2, 6))

        ctk.CTkLabel(brief_panel, text="Domain:",
                     text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 11)
                     ).pack(anchor=tk.W, padx=12, pady=(0, 0))
        self.brief_domain_var = tk.StringVar()
        ctk.CTkEntry(brief_panel, textvariable=self.brief_domain_var, width=210,
                     fg_color=BG_INPUT, border_color=BORDER, text_color=FG_BRIGHT,
                     font=ctk.CTkFont("Segoe UI", 11), corner_radius=6,
                     placeholder_text="e.g. denalitherapeutics.com"
                     ).pack(padx=12, pady=(2, 8))

        self.brief_generate_btn = ctk.CTkButton(
            brief_panel, text="📄  Create Customer Brief", width=210, height=36,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color=BG_DARK,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), corner_radius=8,
            command=self._generate_brief)
        self.brief_generate_btn.pack(padx=12, pady=(0, 8))

        self.brief_status_var = tk.StringVar(value="")
        self.brief_status_label = ctk.CTkLabel(
            brief_panel, textvariable=self.brief_status_var,
            text_color=FG_DIM, font=ctk.CTkFont("Segoe UI", 10),
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
                self.chat_text.insert(tk.END, f"{content}\n\n", "body")
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
    tabview.add("🌐  Customer Research")

    # Tab 1 — existing app (pass the tab frame as root)
    tab1_frame = tabview.tab("🎙  Live Transcription")
    app = CallNotesApp(tab1_frame)

    # Tab 2 — retrieval agent
    tab2_frame = tabview.tab("🔍  Notes Retrieval")
    NotesRetrieverTab(tab2_frame)

    # Tab 3 — customer research agent
    tab3_frame = tabview.tab("🌐  Customer Research")
    CustomerResearchTab(tab3_frame)

    root.protocol("WM_DELETE_WINDOW", lambda: (shutdown_agent(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
