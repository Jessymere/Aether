"""
Aether Desktop UI — J.A.R.V.I.S.-style conversational interface.

Chat-based UI with voice output, conversation memory, build/Q&A modes,
and system integration. Both CLI (main.py) and this UI share the
orchestrator pipeline in orchestrator.py.
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk

from orchestrator import Orchestrator, CancelledError
from agents import tester
from config import (DESK_CYCLE_SECONDS, DESK_EXECUTION_MODE, DESK_MAX_POSITIONS, MEMORY_FILE,
                    RUNS, WORKSPACE, VERSION)
from update import check_for_updates
from web_research import needs_web, research
from vision import analyze_media
from link_tools import analyze_link
from mcp_client import configured_servers, list_tools
from patch_notes import restore_latest_backup
from preflight import preflight
from proposal_validation import validate_proposal
from voice import Voice

CONVERSATION_FILE = RUNS / "conversation.json"


class ChatText(tk.Text):
    """ScrolledText-like widget with user/Aether message bubbles."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(
            state="disabled",
            wrap="word",
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#38bdf8",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#263449",
            padx=16,
            pady=14,
            font=("Segoe UI", 11),
        )
        self.tag_configure("user", foreground="#7dd3fc", font=("Segoe UI", 11, "bold"))
        self.tag_configure("aether", foreground="#6ee7b7", font=("Segoe UI", 11))
        self.tag_configure("system", foreground="#fbbf24", font=("Segoe UI", 10, "italic"))
        self.tag_configure("error", foreground="#f87171", font=("Segoe UI", 10))
        self.tag_configure("meta", foreground="#94a3b8", font=("Segoe UI", 9))
        self.tag_configure("code", foreground="#fde68a", font=("Consolas", 10))
        self.tag_config("user_linesep", spacing1=8, spacing3=4)
        self.tag_config("aether_linesep", spacing1=8, spacing3=4)

    def append(self, text: str, tag: str = "", linesep_tag: str = "") -> None:
        self.configure(state="normal")
        self.insert("end", text + "\n", tag)
        if linesep_tag:
            self.insert("end", "\n", linesep_tag)
        self.configure(state="disabled")
        self.see("end")

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


#: Palette. One place, so the cards, tables and text agree.
BG, CARD, CARD_2, LINE = "#0b1120", "#111827", "#172235", "#263449"
TEXT, MUTED, ACCENT = "#e5e7eb", "#94a3b8", "#0ea5e9"
GREEN, RED, AMBER = "#22c55e", "#ef4444", "#f59e0b"


class Label(ctk.CTkLabel):
    """CTkLabel that also takes ``foreground=``, which the handlers below use."""

    def configure(self, require_redraw=False, **kwargs):
        if "foreground" in kwargs:
            kwargs["text_color"] = kwargs.pop("foreground")
        super().configure(require_redraw, **kwargs)


class Progress(ctk.CTkProgressBar):
    """Indeterminate bar whose ``start(speed)`` matches the old ttk calls."""

    def start(self, *_speed):
        self.configure(mode="indeterminate")
        super().start()

    def stop(self):
        super().stop()
        self.configure(mode="determinate")  # an idle indeterminate bar still draws
        self.set(0)


def card(parent, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, fg_color=CARD_2, corner_radius=12, **kw)


def heading(parent, text: str) -> Label:
    return Label(parent, text=text, text_color=MUTED, font=("Segoe UI", 11, "bold"), anchor="w")


class BuilderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Aether v{VERSION}")
        # Fit the screen: the desk tab is tall, and a clipped window hid the footer.
        width = min(1280, self.root.winfo_screenwidth() - 60)
        height = min(900, self.root.winfo_screenheight() - 90)
        self.root.geometry(f"{width}x{height}+20+10")
        self.root.minsize(1000, 680)
        self.project_root = WORKSPACE
        self.media_path = None
        self.fast_var = tk.BooleanVar(value=False)
        self.auto_var = tk.BooleanVar(value=False)  # Auto-approve / Claude mode
        self.rounds_var = tk.StringVar(value="1")
        self.web_var = tk.BooleanVar(value=False)
        self.auto_web_var = tk.BooleanVar(value=True)
        self.mode_var = tk.StringVar(value="chat")  # chat | build | qa
        self.voice = Voice(enabled=False)
        self.conversation = []  # list of (speaker, text, timestamp)
        self.orchestrator = None
        self.result = None

        # Desk tab state. The runner lives on a daemon thread; the UI only ever
        # reads runner.state(), so a slow cycle can never freeze the window.
        self.desk_runner = None
        self._desk_stop = threading.Event()
        self._desk_after = None
        self._desk_active = False

        self._load_conversation()
        self._setup_styles()
        self._build_ui()
        self.root.after(200, self.check_models)

    # ------------------------------------------------------------------ UI
    def _setup_styles(self) -> None:
        # CustomTkinter draws everything but tables; the ttk Treeview is themed
        # to match the cards.
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                        rowheight=30, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=CARD_2, foreground="#cbd5e1",
                        font=("Segoe UI", 9, "bold"), padding=(8, 6), relief="flat", borderwidth=0)
        style.map("Treeview", background=[("selected", "#164e63")],
                  foreground=[("selected", "#f0f9ff")])
        style.map("Treeview.Heading", background=[("active", LINE)])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        self.root.configure(fg_color=BG)

    def _table(self, parent, columns, widths, height, stretch, tags) -> ttk.Treeview:
        frame = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=8)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
        scroll = ctk.CTkScrollbar(frame, command=tree.yview, width=12)
        tree.configure(yscrollcommand=scroll.set)
        for column in columns:
            tree.heading(column, text="P&L" if column == "pnl" else column.title())
            tree.column(column, width=widths.get(column, 70), minwidth=40, anchor="w",
                        stretch=column in stretch)
        for tag, color in tags.items():
            tree.tag_configure(tag, foreground=color)
        tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        scroll.pack(side="right", fill="y", padx=(0, 4), pady=6)
        return tree

    def _build_ui(self) -> None:
        # Header: name on the left, the three facts that matter on the right.
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(16, 6))
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left")
        Label(titles, text="Aether", text_color="#38bdf8",
              font=("Segoe UI", 28, "bold")).pack(anchor="w")
        Label(titles, text="AI assistant and trading desk — chat, build, automate, trade on paper",
              text_color=MUTED, font=("Segoe UI", 12)).pack(anchor="w")
        chips = ctk.CTkFrame(header, fg_color="transparent")
        chips.pack(side="right")
        from config import DESK_BRAIN_MODEL
        from desk_view import model_name
        self.header_mode = Label(chips, corner_radius=14, font=("Segoe UI", 11, "bold"),
                                 padx=12, height=28)
        self.header_mode.pack(side="left", padx=4)
        Label(chips, text=f"⚖  Judge: {model_name(DESK_BRAIN_MODEL)}", fg_color=CARD_2,
              corner_radius=14, padx=12, height=28, text_color="#cbd5e1",
              font=("Segoe UI", 11)).pack(side="left", padx=4)

        # Footer first and anchored to the bottom, so a tall tab can never push
        # the status line and project controls off the window.
        footer = ctk.CTkFrame(self.root, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=24, pady=(0, 10))
        self.status = Label(footer, text="● Ready", text_color="#00e676", anchor="w",
                            font=("Segoe UI", 11))
        self.status.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(footer, text="Choose folder", width=110, height=28, fg_color=CARD_2,
                      hover_color=LINE, command=self.choose_project).pack(side="right")
        ctk.CTkButton(footer, text="Open", width=60, height=28, fg_color=CARD_2,
                      hover_color=LINE, command=self.open_workspace).pack(side="right", padx=6)
        self.project_label = Label(footer, text=f"Project: {self.project_root.name}",
                                   text_color=MUTED)
        self.project_label.pack(side="right", padx=10)
        self.progress = Progress(self.root, mode="determinate", height=4,
                                 progress_color=ACCENT, fg_color=CARD)
        self.progress.set(0)
        self.progress.pack(side="bottom", fill="x", padx=24, pady=(0, 6), before=footer)

        self.notebook = ctk.CTkTabview(self.root, fg_color=CARD, corner_radius=14,
                                       segmented_button_selected_color=ACCENT,
                                       segmented_button_selected_hover_color="#38bdf8",
                                       segmented_button_unselected_color=CARD_2,
                                       segmented_button_fg_color=CARD_2)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=(0, 6))
        self.notebook._segmented_button.configure(font=("Segoe UI", 13, "bold"), height=34)
        chat_tab = self.notebook.add("  Chat  ")
        desk_tab = self.notebook.add("  Desk  ")
        self._build_chat_tab(chat_tab)
        self._build_desk_tab(desk_tab)


    def _build_chat_tab(self, tab) -> None:
        chat_card = card(tab)
        chat_card.pack(fill="both", expand=True, padx=4, pady=(4, 8))
        self.chat = ChatText(chat_card)
        self.chat.configure(bg=CARD_2, highlightthickness=0)
        chat_scroll = ctk.CTkScrollbar(chat_card, command=self.chat.yview)
        self.chat.configure(yscrollcommand=chat_scroll.set)
        chat_scroll.pack(side="right", fill="y", padx=(0, 6), pady=8)
        self.chat.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)

        self.chat.append("Aether: Hello. I'm Aether — I can chat, build code, and run the paper trading desk. How can I help?", "aether", "aether_linesep")
        for speaker, text, _ts in self.conversation[-10:]:
            if speaker == "user":
                self.chat.append(f"You: {text}", "user", "user_linesep")
            else:
                self.chat.append(f"Aether: {text}", "aether", "aether_linesep")

        # Shown only while a build proposal is waiting for a decision. The old
        # window created these buttons but never placed them, so a proposal
        # could not be approved from the app at all.
        self.approval_bar = ctk.CTkFrame(tab, fg_color="#0c4a6e", corner_radius=10)
        Label(self.approval_bar, text="📝  Proposal ready — validate it, then approve to write the files",
              text_color="#e0f2fe", font=("Segoe UI", 12, "bold")).pack(side="left", padx=14, pady=8)
        self.approve_btn = ctk.CTkButton(self.approval_bar, text="Approve & write", width=140,
                                         fg_color=GREEN, hover_color="#16a34a",
                                         command=self.approve, state="disabled")
        self.approve_btn.pack(side="right", padx=(6, 12), pady=8)
        self.validate_btn = ctk.CTkButton(self.approval_bar, text="Validate", width=100,
                                          fg_color=CARD_2, hover_color=LINE,
                                          command=self.validate_current_proposal)
        self.validate_btn.pack(side="right", pady=8)

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.pack(fill="x", padx=6, pady=(0, 6))
        self._controls = controls
        for text, var in (("Fast", self.fast_var), ("Auto-approve", self.auto_var),
                          ("Web research", self.web_var), ("Auto web", self.auto_web_var)):
            ctk.CTkSwitch(controls, text=text, variable=var, progress_color=ACCENT,
                          font=("Segoe UI", 11)).pack(side="left", padx=(0, 14))
        Label(controls, text="Review rounds", text_color=MUTED).pack(side="left", padx=(6, 6))
        ctk.CTkSegmentedButton(controls, values=["0", "1", "2"], variable=self.rounds_var,
                               selected_color=ACCENT, width=110).pack(side="left")

        tools = ctk.CTkFrame(tab, fg_color="transparent")
        tools.pack(fill="x", padx=6, pady=(0, 8))
        self.voice_btn = self._ghost(tools, "🔈 Voice off", self.toggle_voice)
        for text, command in (("📂 Vault", self.open_vault), ("💾 Save to vault", self.save_to_vault),
                              ("🧹 Clear chat", self.clear_chat), ("✓ Check models", self.check_models),
                              ("⟳ Updates", self.check_updates)):
            self._ghost(tools, text, command)

        entry_row = ctk.CTkFrame(tab, fg_color="transparent")
        entry_row.pack(fill="x", padx=6, pady=(0, 6))
        # No textvariable: CTkEntry hides its placeholder when one is attached.
        self.input_entry = ctk.CTkEntry(entry_row, height=42,
                                        corner_radius=21, border_color=LINE, fg_color=CARD_2,
                                        font=("Segoe UI", 13),
                                        placeholder_text="Ask Aether, or say \"run the desk\"…")
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.input_entry.bind("<Return>", lambda _e: self.start_run())
        self.input_entry.focus_set()
        self.cancel_btn = ctk.CTkButton(entry_row, text="Cancel", width=90, height=42,
                                        corner_radius=21, fg_color=CARD_2, hover_color=LINE,
                                        command=self.cancel_run, state="disabled")
        self.cancel_btn.pack(side="left", padx=(0, 8))
        self.send_btn = ctk.CTkButton(entry_row, text="Send  ➤", width=110, height=42,
                                      corner_radius=21, fg_color=ACCENT, hover_color="#38bdf8",
                                      font=("Segoe UI", 13, "bold"), command=self.start_run)
        self.send_btn.pack(side="left")

    def _ghost(self, parent, text: str, command) -> ctk.CTkButton:
        button = ctk.CTkButton(parent, text=text, command=command, height=30, width=10,
                               fg_color=CARD_2, hover_color=LINE, text_color="#cbd5e1",
                               corner_radius=15, font=("Segoe UI", 11))
        button.pack(side="left", padx=(0, 6))
        return button

    def _show_approval(self, show: bool) -> None:
        if show and not self.approval_bar.winfo_manager():
            self.approval_bar.pack(fill="x", padx=6, pady=(0, 8), before=self._controls)
        elif not show and self.approval_bar.winfo_manager():
            self.approval_bar.pack_forget()

    # ------------------------------------------------------------------ Desk
    #
    # The tab is a *view*. Every number it shows comes from runner.state() and
    # the ledger; it never reaches into the broker itself, so the desk keeps
    # running at full speed even while the window is repainting.
    def _build_desk_tab(self, parent) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(4, 8))
        # The one thing a trading screen must never leave in doubt.
        self.desk_mode_label = Label(bar, corner_radius=10, height=36, padx=14,
                                     font=("Segoe UI", 13, "bold"))
        self._desk_badge(DESK_EXECUTION_MODE)
        self.desk_mode_label.pack(side="left", padx=(0, 12))
        self.desk_start_btn = ctk.CTkButton(bar, text="▶  Start desk", width=130, height=36,
                                            fg_color=ACCENT, hover_color="#38bdf8",
                                            font=("Segoe UI", 12, "bold"), command=self.desk_start)
        self.desk_start_btn.pack(side="left", padx=(0, 8))
        self.desk_stop_btn = ctk.CTkButton(bar, text="■  Stop", width=90, height=36,
                                           fg_color=CARD_2, hover_color=LINE,
                                           command=self.desk_stop, state="disabled")
        self.desk_stop_btn.pack(side="left", padx=(0, 8))
        self.desk_panic_btn = ctk.CTkButton(bar, text="⚠  PANIC", width=110, height=36,
                                            fg_color="#991b1b", hover_color=RED,
                                            font=("Segoe UI", 12, "bold"),
                                            command=self.desk_panic, state="disabled")
        self.desk_panic_btn.pack(side="left", padx=(0, 16))
        self.desk_venue_vars = {"stocks": tk.BooleanVar(value=True),
                                "crypto": tk.BooleanVar(value=True)}
        for venue, var in self.desk_venue_vars.items():
            ctk.CTkSwitch(bar, text=venue.title(), variable=var, progress_color=ACCENT,
                          font=("Segoe UI", 11)).pack(side="left", padx=(0, 10))
        self.desk_summary = Label(bar, text="DESK IDLE", text_color="#9aa7b2",
                                  font=("Segoe UI", 11, "bold"))
        self.desk_summary.pack(side="right")

        # Anything that makes the desk look healthier than it is. Normally
        # empty, and packed away when it is.
        self.desk_health = Label(parent, text="", text_color=AMBER, wraplength=900,
                                 justify="left", anchor="w")

        # Market clock and the next scheduled paper run: when anything can happen.
        strip = ctk.CTkFrame(parent, fg_color="transparent")
        strip.pack(fill="x", padx=4, pady=(0, 8))
        self.desk_market = Label(strip, text="", corner_radius=12, height=26, padx=12,
                                 font=("Segoe UI", 11, "bold"))
        self.desk_market.pack(side="left", padx=(0, 8))
        self.desk_next_run = Label(strip, text="⏰  Daily paper run: checking…", fg_color=CARD_2,
                                   corner_radius=12, height=26, padx=12, text_color="#cbd5e1",
                                   font=("Segoe UI", 11))
        self.desk_next_run.pack(side="left")
        self.desk_last_run = Label(strip, text="", text_color=MUTED, font=("Segoe UI", 11))
        self.desk_last_run.pack(side="right")

        metrics = ctk.CTkFrame(parent, fg_color="transparent")
        metrics.pack(fill="x", padx=4, pady=(0, 8))
        self._desk_metrics_row = metrics
        self.desk_metrics, self.desk_metric_sub = {}, {}
        for column, (key, title, initial, color, accent, sub) in enumerate((
            ("equity", "EQUITY", "—", TEXT, ACCENT, "paper book"),
            ("pnl", "P&L · ALL TIME", "—", TEXT, GREEN, "since start"),
            ("positions", "OPEN POSITIONS", "0", TEXT, "#a78bfa", f"of {DESK_MAX_POSITIONS} max"),
            ("dial", "RISK DIAL", "0.00", "#38bdf8", AMBER, "position size, 0–1"),
            ("record", "CLOSED RECORD", "No trades", TEXT, "#f472b6", "win rate · trades"),
        )):
            metrics.columnconfigure(column, weight=1, uniform="desk_metric")
            box = card(metrics)
            box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
            ctk.CTkFrame(box, width=4, height=1, fg_color=accent, corner_radius=2).pack(
                side="left", fill="y", padx=(8, 0), pady=12)
            body = ctk.CTkFrame(box, fg_color="transparent")
            body.pack(side="left", fill="both", expand=True)
            Label(body, text=title, text_color=MUTED, font=("Segoe UI", 10, "bold"),
                  anchor="w").pack(fill="x", padx=12, pady=(10, 0))
            value = Label(body, text=initial, text_color=color, font=("Segoe UI", 21, "bold"),
                          anchor="w")
            value.pack(fill="x", padx=12)
            self.desk_metric_sub[key] = Label(body, text=sub, text_color="#64748b",
                                              font=("Segoe UI", 10), anchor="w")
            self.desk_metric_sub[key].pack(fill="x", padx=12, pady=(0, 10))
            self.desk_metrics[key] = value

        chart_card = card(parent)
        chart_card.pack(fill="x", padx=4, pady=(0, 8))
        self.desk_chart = tk.Canvas(chart_card, height=92, bg=CARD_2, highlightthickness=0)
        self.desk_chart.pack(fill="x", padx=10, pady=8)
        self.desk_chart.bind("<Configure>", lambda _e: self._desk_draw_chart(self._desk_series))
        self._desk_series, self._desk_capital = [], None

        panels = ctk.CTkFrame(parent, fg_color="transparent")
        panels.pack(fill="both", expand=True, padx=4, pady=(0, 8))
        panels.rowconfigure(0, weight=1)
        panels.columnconfigure(0, weight=1, uniform="desk_panel")
        panels.columnconfigure(1, weight=1, uniform="desk_panel")
        watch_box = card(panels)
        watch_box.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        heading(watch_box, "👁  WATCHLIST").pack(fill="x", padx=14, pady=(10, 4))
        self.desk_watch = ctk.CTkScrollableFrame(watch_box, fg_color="transparent", height=112)
        self.desk_watch.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        judge_box = card(panels)
        judge_box.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        from config import DESK_BRAIN_MODEL
        from desk_view import model_name
        heading(judge_box, f"🤖  AI JUDGE · {model_name(DESK_BRAIN_MODEL)}").pack(fill="x", padx=14, pady=(10, 4))
        self.desk_judge = ctk.CTkScrollableFrame(judge_box, fg_color="transparent", height=112)
        self.desk_judge.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self._desk_rows_key = None

        lower = ctk.CTkTabview(parent, fg_color=CARD_2, corner_radius=12, height=145,
                               segmented_button_selected_color=ACCENT,
                               segmented_button_unselected_color=CARD,
                               segmented_button_fg_color=CARD)
        # Reserved before the feeds above (pack order), so they shrink and scroll
        # instead of squeezing positions and the log to nothing.
        lower.pack(side="bottom", fill="x", padx=4, pady=(0, 4), before=panels)
        lower.grid_propagate(False)  # keep its own height; contents scroll inside
        self.desk_tree = self._table(
            lower.add("Open positions"),
            ("symbol", "qty", "entry", "mark", "pnl", "stop", "take", "venue", "mode"),
            {"symbol": 90, "pnl": 130}, 3,
            {"symbol", "qty", "entry", "mark", "pnl", "stop", "take", "venue", "mode"},
            {"up": GREEN, "down": RED, "empty": "#64748b"})
        self.desk_decisions = self._table(
            lower.add("Recent decisions"), ("symbol", "action", "conf", "vetoed", "why"),
            {"symbol": 90, "action": 70, "conf": 55, "vetoed": 100, "why": 380}, 3, {"why"},
            {"buy": GREEN, "skip": "#78909c", "empty": "#64748b"})
        log_tab = lower.add("Log")
        self.desk_log = ChatText(log_tab, height=6)
        self.desk_log.configure(bg=CARD)
        self.desk_log.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.desk_log.append("Desk ready. Start runs cycles until Stop; PANIC halts and "
                             "flattens everything. The scheduled task also runs one paper "
                             "cycle every day at 4:30 pm.", "meta")
        self.root.after(300, self._desk_ledger_tick)
        self.root.after(500, self._desk_check_schedule)

    def desk_start(self) -> None:
        venues = [v for v, var in self.desk_venue_vars.items() if var.get()]
        if not venues:
            messagebox.showinfo("Desk", "Pick at least one venue.")
            return

        if self.desk_runner is not None:
            # Restart an existing desk after a stop or a panic: clear the halt
            # and the kill file rather than building a second runner.
            self.desk_runner.resume()
        else:
            try:
                from desk_brain import make_brain
                from desk_runner import DeskRunner
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Desk", f"The desk will not load: {exc}")
                return
            self.desk_runner = DeskRunner(
                venues=venues,
                brain=make_brain(),
                status_cb=lambda line: self.root.after(0, self._desk_status, line),
                log_cb=lambda line, level="info": self.root.after(0, self._desk_append, line, level),
                cancel_cb=self._desk_stop.is_set,
            )

        self._desk_stop.clear()
        self._desk_active = True
        self.desk_start_btn.configure(state="disabled")
        self.desk_stop_btn.configure(state="normal")
        self.desk_panic_btn.configure(state="normal")
        self._desk_append(f"Desk started on {', '.join(venues)} — cycling every "
                          f"{DESK_CYCLE_SECONDS}s.", "system")
        threading.Thread(target=self._desk_loop, daemon=True).start()
        if self._desk_after is not None:
            self.root.after_cancel(self._desk_after)
        self._desk_tick()

    def _desk_loop(self) -> None:
        """Run cycles until Stop. One cycle per pass so Stop is prompt."""
        runner = self.desk_runner
        try:
            while runner is not None and not self._desk_stop.is_set():
                result = runner.run(cycles=1)
                if result.kill_switch or self._desk_stop.is_set():
                    break
                self._desk_stop.wait(max(5, DESK_CYCLE_SECONDS))
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._desk_append, f"desk error: {exc}", "error")
        finally:
            self.root.after(0, self._desk_finished)

    def _desk_finished(self) -> None:
        self._desk_active = False
        self.desk_start_btn.configure(state="normal")
        self.desk_stop_btn.configure(state="disabled")
        self.desk_panic_btn.configure(state="disabled")
        halted = bool(self.desk_runner and self.desk_runner.state().get("halted"))
        self._desk_append("Desk halted — PANIC tripped or the kill file is present."
                          if halted else "Desk stopped.", "system")

    def desk_stop(self) -> None:
        self._desk_stop.set()
        self._desk_append("Stop requested — finishing this cycle.", "system")
        self.desk_stop_btn.configure(state="disabled")

    def desk_panic(self) -> None:
        if self.desk_runner is None:
            return
        self._desk_stop.set()
        self._desk_append("PANIC — halting and flattening.", "error")
        threading.Thread(target=self._desk_panic_worker, daemon=True).start()

    def _desk_panic_worker(self) -> None:
        runner = self.desk_runner
        try:
            runner.panic(flatten=True, reason="manual panic")
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._desk_append, f"panic error: {exc}", "error")

    def _desk_status(self, line: str) -> None:
        self.status.configure(text=f"● {line}", foreground="#007acc")

    def _desk_append(self, line: str, level: str = "info") -> None:
        tag = {"trade": "aether", "warn": "system", "error": "error"}.get(level, "meta")
        self.desk_log.append(line, tag)

    def _desk_tick(self) -> None:
        """Poll the snapshot. Never touches the network, so it cannot stall."""
        runner = self.desk_runner
        if runner is None:
            return
        try:
            snapshot = runner.state()
        except Exception:  # noqa: BLE001
            snapshot = {}
        self._desk_render(snapshot)
        self._desk_after = self.root.after(800, self._desk_tick)

    def _desk_ledger_tick(self) -> None:
        """Read the ledger every few seconds: the book is there before Start.

        Equity and P&L always come from here (all-time, every recorded cycle,
        including the scheduled daily run), not from the in-memory runner,
        whose P&L restarts with each run.
        """
        try:
            from desk_ledger import connect, open_positions
            from desk_runner import DEFAULT_VENUES, _row
            from desk_view import overview
            from config import DESK_DB, DESK_START_CAPITAL
            conn = connect(DESK_DB)
            try:
                view = overview(conn, DESK_EXECUTION_MODE,
                                capital=DESK_START_CAPITAL * len(DEFAULT_VENUES))
                held = [_row(p) for p in open_positions(conn, DESK_EXECUTION_MODE)]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — a display aid must not break the app
            self.desk_summary.configure(text=f"DESK — ledger unreadable: {exc}")
            view, held = None, []
        if view is not None:
            self._desk_render_ledger(view)
            if self.desk_runner is None:  # before any Start, show the stored book
                self._desk_render({"positions": held, "mode": DESK_EXECUTION_MODE})
        self.root.after(5000, self._desk_ledger_tick)

    def _desk_check_schedule(self) -> None:
        """Ask Windows when the 'Aether paper desk' task runs next. Off-thread."""
        def work() -> None:
            try:
                out = subprocess.run(
                    ["schtasks", "/Query", "/TN", "Aether paper desk", "/FO", "LIST"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                nxt = next((line.split(":", 1)[1].strip() for line in out.splitlines()
                            if line.lower().startswith("next run time")), "")
                text = (f"⏰  Daily paper run · next {nxt}" if nxt and nxt != "N/A"
                        else "⏰  Daily paper run: not scheduled")
            except Exception:  # noqa: BLE001 — a display aid
                text = "⏰  Daily paper run: unknown"
            self.root.after(0, lambda: self.desk_next_run.configure(text=text))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(600_000, self._desk_check_schedule)

    def _desk_render_ledger(self, view: dict) -> None:
        equity, start = view.get("equity"), view.get("start_equity")
        self.desk_metrics["equity"].configure(
            text=f"${equity:,.2f}" if equity is not None else "—")
        pnl = (equity - start) if equity is not None and start else None
        self.desk_metrics["pnl"].configure(
            text=f"${pnl:+,.2f} ({pnl / start:+.2%})" if pnl is not None else "—",
            foreground="#4caf50" if (pnl or 0) > 0 else "#ff5252" if (pnl or 0) < 0 else "#e0e0e0")

        from datetime import datetime
        from desk_view import market_status, tone
        is_open, market = market_status(datetime.now())
        self.desk_market.configure(text=("●  " if is_open else "○  ") + market,
                                   fg_color="#14532d" if is_open else CARD_2,
                                   text_color="#bbf7d0" if is_open else "#cbd5e1")
        run = view.get("last_run")
        if run:
            self.desk_last_run.configure(
                text=f"Last cycle {run['ts'][5:16].replace('T', ' ')} UTC · scanned "
                     f"{run['n_candidates']} · cleared gate {run['n_gated']} · filled {run['n_filled']}")
        self.desk_metric_sub["equity"].configure(
            text=f"started at ${start:,.0f}" if start else "paper book")

        # Rows are rebuilt only when what they show changes: no flicker.
        key = (tuple((w["symbol"], w["status"]) for w in view.get("watchlist", [])),
               tuple((c["ts"], c["action"]) for c in view.get("judge_calls", [])))
        if key != self._desk_rows_key:
            self._desk_rows_key = key
            self._desk_fill_watch(view.get("watchlist", []), tone)
            self._desk_fill_judge(view.get("judge_calls", []))
        self._desk_series, self._desk_capital = view.get("series", []), start
        self._desk_draw_chart(self._desk_series)

    PILL = {"ready": ("#0c4a6e", "#bae6fd"), "bought": ("#14532d", "#bbf7d0"),
            "judge": ("#422006", "#fde68a"), "benched": ("#1f2937", "#64748b"),
            "buy": ("#14532d", "#bbf7d0"), "hold": ("#422006", "#fde68a"),
            "skip": ("#1f2937", "#94a3b8")}

    def _pill(self, parent, text: str, kind: str) -> Label:
        bg, fg = self.PILL.get(kind, self.PILL["benched"])
        return Label(parent, text=text, fg_color=bg, text_color=fg, corner_radius=10,
                     height=24, padx=10, font=("Segoe UI", 10, "bold"))

    def _desk_fill_watch(self, watch: list, tone) -> None:
        for child in self.desk_watch.winfo_children():
            child.destroy()
        if not watch:
            Label(self.desk_watch, text="Run the desk to scan the universe",
                  text_color="#64748b").pack(anchor="w", padx=8, pady=6)
        for w in watch:
            kind = tone(w["status"], w["validated"])
            row = ctk.CTkFrame(self.desk_watch, fg_color=CARD, corner_radius=8)
            row.pack(fill="x", pady=2, padx=2)
            Label(row, text=w["symbol"], width=62, anchor="w", font=("Segoe UI", 12, "bold"),
                  text_color=TEXT if w["validated"] else "#64748b").pack(side="left", padx=(10, 4), pady=3)
            Label(row, text=w["rule"] or "no rule", fg_color=CARD_2, corner_radius=8, height=22,
                  padx=8, text_color="#cbd5e1" if w["validated"] else "#64748b",
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
            status = w["status"].replace("waiting for ", "waiting · ").replace(" entry signal", "")
            self._pill(row, status, kind).pack(side="right", padx=8)

    def _desk_fill_judge(self, calls: list) -> None:
        for child in self.desk_judge.winfo_children():
            child.destroy()
        if not calls:
            Label(self.desk_judge, text="The judge is asked only when a validated rule signals",
                  text_color="#64748b").pack(anchor="w", padx=8, pady=6)
        for c in calls:
            row = ctk.CTkFrame(self.desk_judge, fg_color=CARD, corner_radius=8)
            row.pack(fill="x", pady=2, padx=2)
            self._pill(row, c["action"].upper(), c["action"]).pack(side="left", padx=(8, 8), pady=3)
            Label(row, text=c["symbol"], width=52, anchor="w",
                  font=("Segoe UI", 12, "bold")).pack(side="left")
            bar = ctk.CTkProgressBar(row, width=70, height=6, progress_color=self.PILL.get(
                c["action"], self.PILL["skip"])[1], fg_color=CARD_2)
            bar.set(max(0.0, min(1.0, c["confidence"])))
            bar.pack(side="left", padx=(6, 4))
            Label(row, text=f"{c['confidence']:.0%}", width=34, text_color=MUTED,
                  font=("Segoe UI", 10)).pack(side="left")
            Label(row, text=c["ts"][5:16].replace("T", " "), text_color="#64748b",
                  font=("Segoe UI", 10)).pack(side="right", padx=10)

    def _desk_draw_chart(self, series: list) -> None:
        canvas = self.desk_chart
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 300), int(canvas["height"])
        left, right, top, bottom = 12, w - 70, 22, h - 12
        canvas.create_text(left, 4, anchor="nw", fill=MUTED, font=("Segoe UI", 9, "bold"),
                           text=f"EQUITY · {len(series)} cycles")
        if len(series) < 2:
            canvas.create_text(w // 2, h // 2, fill="#64748b", font=("Segoe UI", 10),
                               text="The chart fills in as the desk records cycles")
            return
        capital = self._desk_capital
        lo, hi = min(series + ([capital] if capital else [])), max(series + ([capital] if capital else []))
        pad = max((hi - lo) * 0.15, abs(hi) * 0.002)  # a flat book still sits mid-chart
        lo, hi = lo - pad, hi + pad
        y = lambda v: bottom - (v - lo) / (hi - lo) * (bottom - top)  # noqa: E731
        for i in range(4):  # grid and price labels
            value = lo + (hi - lo) * i / 3
            canvas.create_line(left, y(value), right, y(value), fill="#1e293b")
            canvas.create_text(right + 8, y(value), anchor="w", fill="#64748b",
                               font=("Segoe UI", 8), text=f"${value:,.0f}")
        if capital:
            canvas.create_line(left, y(capital), right, y(capital), fill="#475569", dash=(4, 4))
            canvas.create_text(left + 4, y(capital) - 2, anchor="sw", fill="#64748b",
                               font=("Segoe UI", 8), text="start capital")
        step = (right - left) / (len(series) - 1)
        points = [(left + i * step, y(v)) for i, v in enumerate(series)]
        up = series[-1] >= (capital or series[0])
        flat = max(series) - min(series) < abs(series[-1]) * 0.0005 and \
            abs(series[-1] - (capital or series[0])) < abs(series[-1]) * 0.001
        # Flat reads neutral: a -0.02% book painted red looks like a crash.
        line, fill = ("#94a3b8", "#1a2333") if flat else (GREEN, "#0f2e1f") if up else (RED, "#2e1215")
        canvas.create_polygon(left, bottom, *[c for p in points for c in p], right, bottom,
                              fill=fill, outline="")
        canvas.create_line(*[c for p in points for c in p], fill=line, width=2)
        x, last = points[-1]
        canvas.create_oval(x - 4, last - 4, x + 4, last + 4, fill=line, outline=CARD_2, width=2)
        canvas.create_text(x - 8, last - 8, anchor="se", fill=TEXT, font=("Segoe UI", 10, "bold"),
                           text=f"${series[-1]:,.2f}" + ("  · flat" if flat else ""))

    def _desk_badge(self, label: str) -> None:
        live = "live" in label
        for widget, text in ((self.desk_mode_label,
                              "● LIVE · REAL MONEY" if live else "● PAPER · simulated money"),
                             (self.header_mode, "● LIVE" if live else "● PAPER")):
            widget.configure(text=text, fg_color="#b91c1c" if live else "#b45309",
                             text_color="#fff7ed")

    def _desk_render(self, snapshot: dict) -> None:
        modes = sorted({p.get("mode", "") for p in snapshot.get("positions", [])})
        label = snapshot.get("mode", DESK_EXECUTION_MODE)
        if modes:
            label += " / " + ", ".join(m for m in modes if m)
        self._desk_badge(label)

        if snapshot.get("halted"):
            heading = "HALTED"
        elif self._desk_active:
            heading = "running"
        else:
            heading = "idle"
        self.desk_summary.configure(
            text=f"DESK {heading.upper()}  ·  {label.upper()}",
            foreground="#ff5252" if snapshot.get("halted") else
            "#00e676" if self._desk_active else "#9aa7b2")
        # Equity and P&L are drawn from the ledger in _desk_render_ledger.
        self.desk_metrics["positions"].configure(
            text=str(len(snapshot.get("positions", []))))
        self.desk_metrics["dial"].configure(
            text=f"{snapshot.get('dial', 0.0):.2f}")
        record = snapshot.get("record") or {}
        closed = sum(r.get("closed_trades", 0) for r in record.values())
        if closed:
            wins = sum((r.get("win_rate") or 0.0) * r.get("closed_trades", 0)
                       for r in record.values())
            self.desk_metrics["record"].configure(
                text=f"{wins / closed:.0%} · {closed} trades")
        else:
            self.desk_metrics["record"].configure(text="No trades")

        health = snapshot.get("health") or []
        if health:
            self.desk_health.configure(text="⚠ " + " · ".join(health))
            if not self.desk_health.winfo_manager():
                self.desk_health.pack(fill="x", before=self._desk_metrics_row,
                                      padx=6, pady=(0, 6))
        elif self.desk_health.winfo_manager():
            self.desk_health.pack_forget()

        positions = snapshot.get("positions", [])
        seen = set()
        if not positions:
            if not self.desk_tree.exists("__empty__"):
                self.desk_tree.insert(
                    "", "end", iid="__empty__",
                    values=("No open positions", "", "", "", "", "", "", "", ""),
                    tags=("empty",),
                )
            seen.add("__empty__")
        for position in positions:
            symbol = position.get("symbol", "")
            seen.add(symbol)
            pnl = position.get("pnl")
            pct = position.get("pnl_pct")
            values = (symbol, f"{position.get('qty') or 0:g}",
                      f"{position.get('avg_price') or 0:g}",
                      f"{position.get('mark') or 0:g}" if position.get("mark") else "—",
                      f"{pnl:+,.2f} ({pct:+.1f}%)" if pnl is not None else "—",
                      f"{position.get('stop') or 0:g}",
                      f"{position.get('take') or 0:g}",
                      position.get("venue", ""), position.get("mode", ""))
            tags = ("up",) if (pnl or 0) > 0 else ("down",) if (pnl or 0) < 0 else ()
            if self.desk_tree.exists(symbol):
                self.desk_tree.item(symbol, values=values, tags=tags)
            else:
                self.desk_tree.insert("", "end", iid=symbol, values=values, tags=tags)
        for symbol in set(self.desk_tree.get_children()) - seen:
            self.desk_tree.delete(symbol)

        self.desk_decisions.delete(*self.desk_decisions.get_children())
        recent = (snapshot.get("recent") or [])[:15]
        if not recent:
            self.desk_decisions.insert(
                "", "end", values=("No decisions yet", "", "", "", ""),
                tags=("empty",),
            )
        for note in recent:
            self.desk_decisions.insert("", "end", values=(
                note.get("symbol", ""), note.get("action", ""),
                f"{note.get('confidence', 0):.2f}", note.get("vetoed_by", ""),
                note.get("reason", "")), tags=(note.get("action", "skip"),))

    # ------------------------------------------------------------------ Conversation memory
    def _load_conversation(self) -> None:
        try:
            if CONVERSATION_FILE.exists():
                data = json.loads(CONVERSATION_FILE.read_text(encoding="utf-8"))
                self.conversation = data.get("conversation", [])[-50:]
        except Exception:  # noqa: BLE001
            self.conversation = []

    def _save_conversation(self) -> None:
        try:
            RUNS.mkdir(parents=True, exist_ok=True)
            CONVERSATION_FILE.write_text(
                json.dumps({"conversation": self.conversation[-100:]}, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ Voice
    def toggle_voice(self) -> None:
        on = self.voice.toggle()
        self.voice_btn.configure(text=f"Voice: {'On' if on else 'Off'}")
        self.status.configure(text=f"● Voice {'enabled' if on else 'disabled'}", foreground="#007acc")
        self.log_event(f"Voice {'enabled' if on else 'disabled'}")

    def _speak(self, text: str) -> None:
        if self.voice.enabled:
            self.voice.speak(text)

    # ------------------------------------------------------------------ Helpers
    def log_event(self, message: str) -> None:
        self.chat.append(f"[system] {message}", "system", "system")

    def clear_chat(self) -> None:
        if not messagebox.askyesno("Clear Chat", "Clear all conversation history?"):
            return
        self.chat.clear()
        self.conversation = []
        self.chat.append("Aether: Chat cleared. How can I help you?", "aether", "aether_linesep")

    def _add_user(self, text: str) -> None:
        self.chat.append(f"You: {text}", "user", "user_linesep")
        self.conversation.append(("user", text, datetime.now().isoformat()))
        self._save_conversation()
        self._speak(f"You said: {text[:80]}")

    def _add_aether(self, text: str) -> None:
        self.chat.append(f"Aether: {text}", "aether", "aether_linesep")
        self.conversation.append(("aether", text, datetime.now().isoformat()))
        self._save_conversation()
        self._speak(text[:200])

    def _add_system(self, text: str) -> None:
        self.chat.append(f"[system] {text}", "system", "system")

    def _ui_status(self, message: str) -> None:
        self.root.after(0, lambda: self.status.configure(text=message))

    # ------------------------------------------------------------------ Run pipeline
    def start_run(self) -> None:
        request = self.input_entry.get().strip()
        if not request:
            return
        self.input_entry.delete(0, "end")
        self.send_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self._add_user(request)
        self.status.configure(text="● Running agents...", foreground="#007acc")
        self.progress.start(12)
        self.log_event("Started task")
        self.orchestrator = Orchestrator(
            project=self.project_root,
            auto_approve=bool(self.auto_var.get()),
            fast=bool(self.fast_var.get()),
            quality_rounds=max(0, min(2, int(self.rounds_var.get()))),
            web=bool(self.web_var.get()),
            auto_web=bool(self.auto_web_var.get()),
            status_cb=self._ui_status,
            log_cb=self.log_event,
            cancel_cb=self._is_cancelled,
            stream_cb=self._stream_output,
        )
        threading.Thread(target=self._run_build, args=(request,), daemon=True).start()

    def _is_cancelled(self) -> bool:
        return self.orchestrator is not None and self.orchestrator._cancelled

    def _stream_output(self, text: str) -> None:
        def add() -> None:
            self.chat.configure(state="normal")
            self.chat.insert("end", text)
            self.chat.configure(state="disabled")
            self.chat.see("end")
        self.root.after(0, add)

    def cancel_run(self) -> None:
        if self.orchestrator:
            self.orchestrator.cancel()
        self.cancel_btn.configure(state="disabled")
        self.log_event("Cancel requested")

    def _run_build(self, request: str) -> None:
        try:
            result = self.orchestrator.run(request)
            self.result = result
            self.root.after(0, lambda: self.finish_build(result))
        except CancelledError:
            self.root.after(0, self.finish_cancelled)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, lambda error=str(exc): self.fail_run(error))

    def finish_build(self, result) -> None:
        self.progress.stop()
        self.cancel_btn.configure(state="disabled")
        self.send_btn.configure(state="normal")

        if result.error == "Cancelled":
            self.chat.append("\n[Cancelled by user]\n", "system", "system")
            self.status.configure(text="● Cancelled", foreground="#ff5252")
            self.log_event("Run cancelled")
            return

        # Chat / question reply (stored in result.error but is a normal reply)
        if result.mode in ("chat", "question"):
            self._add_aether(result.error)
            self.status.configure(text="● Ready", foreground="#00e676")
            self.log_event(f"{result.mode} reply")
            return

        if result.error:
            self.chat.append(f"\nERROR\n{'=' * 40}\n{result.error}\n", "error", "error")
            self.status.configure(text="● Error", foreground="#ff5252")
            self.log_event("Task stopped with an error")
            return

        # System command result
        if result.mode == "system":
            self._add_aether(result.error)
            self.status.configure(text="● Ready", foreground="#00e676")
            self.log_event("System command executed")
            return

        # Build result
        parts = []
        if result.plan:
            parts.append(f"PLAN\n{'=' * 40}\n{result.plan}")
        if result.proposal:
            files = "\n".join(f"- {item['path']}" for item in result.proposal["files"])
            diff_parts = []
            for item in result.proposal["files"]:
                target = self.project_root / item["path"]
                old = target.read_text(encoding="utf-8") if target.exists() else ""
                diff = "\n".join(difflib.unified_diff(
                    old.splitlines(), item["content"].splitlines(),
                    fromfile=str(target), tofile=str(target) + " (proposed)", lineterm=""
                )) or "(no textual changes)"
                diff_parts.append(f"\n--- {item['path']} ---\n{diff}")
            parts.append(
                f"\nPROPOSED FILES\n{'=' * 40}\n"
                f"{result.proposal.get('summary', 'Proposal ready')}\n{files}\n"
                f"\nCHANGES\n{'=' * 40}\n" + "\n".join(diff_parts)
            )
        if result.security:
            parts.append(f"\nSECURITY REVIEW\n{'=' * 40}\n{result.security}")
        if result.validation:
            ok, msg = result.validation
            parts.append(f"\nVALIDATION\n{'=' * 40}\n{'Passed' if ok else 'Failed'}: {msg}")

        self.chat.configure(state="normal")
        self.chat.insert("end", "\n".join(parts) + "\n", "aether")
        self.chat.configure(state="disabled")
        self.chat.see("end")

        summary = result.proposal.get("summary", "Proposal ready") if result.proposal else "Done"

        if result.approved:
            self._add_aether(f"Auto-approved. Applied {len(result.files_written)} file(s) to project.")
            self.status.configure(text=f"● Applied {len(result.files_written)} file(s)", foreground="#00e676")
            self.log_event(f"Auto-applied {len(result.files_written)} file(s)")
        else:
            self._add_aether(f"{summary}. Ready for your review.")
            self.log_event("Proposal ready for your approval")
            self.approve_btn.configure(state="disabled")
            self._show_approval(True)
            self.status.configure(text="● Review complete — inspect before approving", foreground="#007acc")

        if result.validation and result.validation[0]:
            self.approve_btn.configure(state="normal")

    def finish_cancelled(self) -> None:
        self.progress.stop()
        self.cancel_btn.configure(state="disabled")
        self.send_btn.configure(state="normal")
        self.status.configure(text="● Cancelled", foreground="#ff5252")

    def fail_run(self, error: str) -> None:
        self.chat.append(f"\nERROR\n{'=' * 40}\n{error}\n", "error", "error")
        self.send_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.status.configure(text="● Error", foreground="#ff5252")
        self.progress.stop()
        self.log_event("Task stopped with an error")

    # ------------------------------------------------------------------ Q&A
    def _start_qa(self, question: str) -> None:
        self.send_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.status.configure(text="● Answering...", foreground="#007acc")
        self.progress.start(12)
        self.log_event("Answering question")
        self.orchestrator = Orchestrator(
            project=self.project_root,
            status_cb=self._ui_status,
            log_cb=self.log_event,
            stream_cb=self._stream_output,
        )
        threading.Thread(target=self._run_qa, args=(question,), daemon=True).start()

    def _run_qa(self, question: str) -> None:
        try:
            answer = self.orchestrator.run_qa(question)
            self.root.after(0, lambda: self.finish_qa(answer))
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, lambda error=str(exc): self.fail_run(error))

    def finish_qa(self, answer: str) -> None:
        self.progress.stop()
        self.cancel_btn.configure(state="disabled")
        self.send_btn.configure(state="normal")
        self._add_aether(answer)
        self.status.configure(text="● Ready", foreground="#00e676")
        self.log_event("Answer complete")

    # ------------------------------------------------------------------ Approval / validation
    def validate_current_proposal(self) -> None:
        if not self.result or not self.result.proposal:
            return
        self.status.configure(text="● Validating proposal in sandbox...", foreground="#007acc")
        self.log_event("Compiling and testing proposal in a temporary sandbox")
        threading.Thread(target=self._validate_current_proposal, daemon=True).start()

    def _validate_current_proposal(self) -> None:
        ok, res = validate_proposal(self.result.proposal)
        report = tester(self.result.request, json.dumps(self.result.proposal), res)
        self.root.after(0, lambda: self.finish_proposal_validation(ok, res, report))

    def finish_proposal_validation(self, ok: bool, res: str, report: str) -> None:
        self.chat.append(f"\nVALIDATION\n{'=' * 40}\n{res}\n\nTEST REPORT\n{'=' * 40}\n{report}", "system", "system")
        self.status.configure(
            text=("● Proposal passed validation" if ok else "● Proposal needs repair"),
            foreground="#00e676" if ok else "#ff5252",
        )
        self.approve_btn.configure(state="normal" if ok else "disabled")
        self.log_event("Proposal validation " + ("passed" if ok else "found issues"))

    def approve(self) -> None:
        if not self.result or not self.result.proposal:
            return
        if self.result.approved:
            messagebox.showinfo("Already applied", "Files were already auto-applied to the project.")
            return
        if self.result.validation and not self.result.validation[0]:
            messagebox.showwarning("Validation required", "Run Validate Proposal successfully before approving files.")
            return
        try:
            self.orchestrator.apply_proposal(self.result)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Apply failed", str(exc))
            return
        self._add_aether(f"Applied {len(self.result.files_written)} file(s) to project.")
        self.approve_btn.configure(state="disabled")
        self._show_approval(False)
        self.status.configure(text=f"● Applied {len(self.result.files_written)} file(s)", foreground="#00e676")
        self.log_event("Approved files written to the project")
        messagebox.showinfo("Files applied", "The approved files were written to the workspace folder.")

    # ------------------------------------------------------------------ System / project
    def open_workspace(self) -> None:
        os.startfile(self.project_root)

    def choose_project(self) -> None:
        selected = filedialog.askdirectory(title="Choose a project folder", initialdir=str(self.project_root))
        if selected:
            self.project_root = Path(selected).resolve()
            self.project_root.mkdir(parents=True, exist_ok=True)
            self.project_label.configure(text=f"Project: {self.project_root.name}")
            if self.orchestrator:
                self.orchestrator.project = self.project_root

    # ------------------------------------------------------------------ Vault
    def open_vault(self) -> None:
        """Open the vault folder so the user can browse/edit memory files."""
        from vault import VAULT_DIR
        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(VAULT_DIR)

    def save_to_vault(self) -> None:
        """Prompt the user to save the last Aether response to a vault file."""
        if not self.result or not self.result.proposal:
            messagebox.showinfo("Save to Vault", "No build proposal to save yet.")
            return
        filename = f"project-{datetime.now():%Y%m%d-%H%M%S}.md"
        content = f"# Project: {self.result.request}\n\n"
        content += f"## Summary\n{self.result.proposal.get('summary', '')}\n\n"
        content += "## Files\n"
        for item in self.result.proposal["files"]:
            content += f"- {item['path']}\n"
        saved_path = self.orchestrator.vault_write(filename, content)
        self._add_aether(f"Saved project summary to vault: {Path(saved_path).name}")
        self.status.configure(text="● Saved to vault", foreground="#00e676")
        self.log_event(f"Saved to vault: {saved_path}")

    def check_models(self) -> None:
        self.status.configure(text="● Checking models...", foreground="#ffab40")
        threading.Thread(target=self._check_models, daemon=True).start()

    def _check_models(self) -> None:
        # Hosted models by default; preflight says which are usable, and Ollama
        # is only probed for a role that is actually local.
        from config import DESK_BRAIN_MODEL
        from desk_view import model_name
        statuses = preflight()
        bad = [s for s in statuses if s.status != "ready"]
        surfaces = ", ".join(sorted({s.surface for s in statuses}))
        message = (f"Models ready ({len(statuses)} roles on {surfaces}); "
                   f"trading judge: {model_name(DESK_BRAIN_MODEL)}" if not bad else
                   f"{len(bad)} model role(s) not ready: " +
                   "; ".join(f"{s.role} {s.status} — {s.hint}" for s in bad[:3]))
        self.root.after(0, lambda: self.finish_model_check(not bad, message))

    def finish_model_check(self, ok: bool, message: str) -> None:
        self.status.configure(text=("● " + message), foreground="#00e676" if ok else "#ff5252")
        self.log_event(message)

    def check_updates(self) -> None:
        self.status.configure(text="● Checking for updates...", foreground="#007acc")
        self.log_event("Checking for updates...")
        threading.Thread(target=self._check_updates, daemon=True).start()

    def _check_updates(self) -> None:
        info = check_for_updates()
        self.root.after(0, lambda: self.finish_updates(info))

    def finish_updates(self, info) -> None:
        if info.available:
            msg = (
                f"Update available: v{info.version} (current v{info.current_version})\n\n"
                f"{info.notes}\n\nDownload: {info.download_url}\n\n"
                "Download the new Aether.exe, close this app, replace the old exe, and restart."
            )
            self.log_event(f"Update available: v{info.version}")
            messagebox.showinfo("Update Available", msg)
        else:
            self.log_event("No updates available")
            messagebox.showinfo("No Updates", f"You're running the latest version (v{info.current_version}).")
        self.status.configure(text="● Ready", foreground="#00e676")

    # ------------------------------------------------------------------ Existing features (media, links, MCP, memory, etc.)
    def show_mcp_tools(self) -> None:
        servers = configured_servers()
        if not servers:
            messagebox.showinfo("MCP tools", "No MCP servers are configured yet.")
            return
        self.log_event("Inspecting MCP servers...")
        def inspect():
            lines = []
            for server in servers:
                try:
                    tools = list_tools(server)
                    lines.append(f"{server}:\n" + "\n".join(f"  • {tool['name']}" for tool in tools))
                except Exception as exc:
                    lines.append(f"{server}: unavailable ({exc})")
            self.root.after(0, lambda: messagebox.showinfo("Configured MCP tools", "\n\n".join(lines)))
            self.log_event("MCP inspection complete")
        threading.Thread(target=inspect, daemon=True).start()

    def rollback_last_change(self) -> None:
        if not messagebox.askyesno("Rollback", "Restore the newest Aether backup for this project?"):
            return
        restored = restore_latest_backup(self.project_root)
        if restored is None:
            messagebox.showinfo("Rollback", "No Aether backups were found for this project.")
            return
        self.status.configure(text="● Last change rolled back", foreground="#00e676")
        self.log_event(f"Rollback restored from {restored}")
        messagebox.showinfo("Rollback complete", f"Restored files from:\n{restored}")

    def edit_memory(self) -> None:
        MEMORY_FILE.touch(exist_ok=True)
        os.startfile(MEMORY_FILE)

    def choose_media(self) -> None:
        selected = filedialog.askopenfilename(title="Choose image or video", filetypes=[("Media", "*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.mkv *.avi *.webm")])
        if selected:
            self.media_path = Path(selected)
            self.log_event(f"Media selected: {self.media_path.name}")

    def analyze_selected_media(self) -> None:
        if not self.media_path:
            return
        path = self.media_path
        self.status.configure(text="● Analyzing local media...", foreground="#007acc")
        self.log_event(f"Analyzing media: {path.name}")
        threading.Thread(target=self._analyze_media, args=(path,), daemon=True).start()

    def _analyze_media(self, path: Path) -> None:
        try:
            result = analyze_media(path)
            self.root.after(0, lambda: self.finish_media(result))
        except Exception as exc:
            self.root.after(0, lambda error=str(exc): self.fail_media(error))

    def finish_media(self, result: str) -> None:
        self.chat.append(f"\nMEDIA ANALYSIS\n{'=' * 40}\n{result}", "system", "system")
        self.status.configure(text="● Media analysis complete", foreground="#00e676")

    def fail_media(self, error: str) -> None:
        self.chat.append(f"\nMEDIA ERROR\n{'=' * 40}\n{error}", "error", "error")
        self.status.configure(text="● Media analysis failed", foreground="#ff5252")

    def open_link(self) -> None:
        pass  # link features removed in chat UI; re-add if needed

    def analyze_selected_link(self) -> None:
        pass

    def improve_builder(self) -> None:
        if not messagebox.askyesno("Self-improve", "Inspect and improve this builder, then restart the app after approval?"):
            return
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("self_improve.py")), "--rounds", "1", "--restart"], cwd=str(Path(__file__).parent), creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.root.destroy()


if __name__ == "__main__":
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()  # DPI-aware: sharp text instead of Windows' bitmap stretch
    app = BuilderApp(root)
    # Bring window to front reliably on launch
    root.lift()
    root.focus_force()
    root.attributes('-topmost', True)
    root.after(100, lambda: root.attributes('-topmost', False))
    root.mainloop()
