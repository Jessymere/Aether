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
from tkinter import filedialog, messagebox, ttk

from orchestrator import Orchestrator, CancelledError
from agents import tester
from config import MEMORY_FILE, MODELS, RUNS, WORKSPACE, VERSION
from update import check_for_updates
from web_research import needs_web, research
from vision import analyze_media
from link_tools import analyze_link
from mcp_client import configured_servers, list_tools
from patch_notes import restore_latest_backup
from ollama_client import check_server
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
            bg="#161616",
            fg="#e0e0e0",
            insertbackground="#007acc",
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=10,
            font=("Segoe UI", 11),
        )
        self.tag_configure("user", foreground="#4fc3f7", font=("Segoe UI", 11, "bold"))
        self.tag_configure("aether", foreground="#00e676", font=("Segoe UI", 11))
        self.tag_configure("system", foreground="#ffab40", font=("Segoe UI", 10, "italic"))
        self.tag_configure("error", foreground="#ff5252", font=("Segoe UI", 10))
        self.tag_configure("meta", foreground="#78909c", font=("Segoe UI", 9))
        self.tag_configure("code", foreground="#ffd740", font=("Consolas", 10))
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


class BuilderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"Aether v{VERSION}")
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)
        self.project_root = WORKSPACE
        self.media_path = None
        self.fast_var = tk.BooleanVar(value=False)
        self.auto_var = tk.BooleanVar(value=False)  # Auto-approve / Claude mode
        self.rounds_var = tk.IntVar(value=1)
        self.web_var = tk.BooleanVar(value=False)
        self.auto_web_var = tk.BooleanVar(value=True)
        self.mode_var = tk.StringVar(value="chat")  # chat | build | qa
        self.voice = Voice(enabled=False)
        self.conversation = []  # list of (speaker, text, timestamp)
        self.orchestrator = None
        self.result = None

        self._load_conversation()
        self._setup_styles()
        self._build_ui()
        self.root.after(200, self.check_ollama)

    # ------------------------------------------------------------------ UI
    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        bg = "#161616"
        panel = "#1e1e1e"
        fg = "#e0e0e0"
        accent = "#007acc"
        success = "#00e676"
        warn = "#ffab40"
        error = "#ff5252"

        style.configure(".", background=bg, foreground=fg, fieldbackground=panel, bordercolor="#3e3e3e")
        style.configure("TFrame", background=bg)
        style.configure("TPanel.TFrame", background=panel)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TPanel.TLabel", background=panel, foreground=fg)
        style.configure("TLabelframe", background=panel, foreground=fg, bordercolor="#3e3e3e")
        style.configure("TLabelframe.Label", background=panel, foreground=fg)
        style.configure("TButton", background="#3e3e3e", foreground=fg, bordercolor="#3e3e3e")
        style.map("TButton", background=[("active", "#4e4e4e"), ("pressed", "#5e5e5e")])
        style.configure("Accent.TButton", background=accent, foreground="white")
        style.map("Accent.TButton", background=[("active", "#148ed6"), ("pressed", "#0d6aa8")])
        style.configure("TRadiobutton", background=panel, foreground=fg)
        style.configure("TCheckbutton", background=panel, foreground=fg)
        style.configure("TEntry", fieldbackground="#252525", foreground=fg, bordercolor="#3e3e3e")
        style.configure("TSpinbox", fieldbackground="#252525", foreground=fg, bordercolor="#3e3e3e")
        style.configure("TProgressbar", background=accent, troughcolor="#252525", bordercolor="#3e3e3e")
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"), foreground=accent)
        style.configure("Subtitle.TLabel", foreground="#888888", font=("Segoe UI", 10))

        root.configure(bg=bg)

    def _build_ui(self) -> None:
        # Header
        header = ttk.Frame(self.root, padding=(20, 12, 20, 8))
        header.pack(fill="x")
        ttk.Label(header, text="Aether", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Your local AI assistant — chat, build, automate", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        # Chat area
        chat_frame = ttk.Frame(self.root, style="TPanel.TFrame")
        chat_frame.pack(fill="both", expand=True, padx=20, pady=(8, 0))
        self.chat = ChatText(chat_frame)
        self.chat.pack(fill="both", expand=True)

        # Welcome message
        self.chat.append("Aether: Hello. I'm your local AI assistant. How can I help you today?", "aether", "aether_linesep")

        # Replay recent conversation from memory
        for speaker, text, _ts in self.conversation[-10:]:
            if speaker == "user":
                self.chat.append(f"You: {text}", "user", "user_linesep")
            else:
                self.chat.append(f"Aether: {text}", "aether", "aether_linesep")

        # Input area
        input_frame = ttk.Frame(self.root, style="TPanel.TFrame", padding=(20, 8, 20, 16))
        input_frame.pack(fill="x")

        # Mode + controls row
        ctrl_row = ttk.Frame(input_frame, style="TPanel.TFrame")
        ctrl_row.pack(fill="x", pady=(0, 8))

        ttk.Label(ctrl_row, text="Mode:", style="TPanel.TLabel").pack(side="left")
        ttk.Label(ctrl_row, text="AI classifies intent automatically", style="TPanel.TLabel", foreground="#78909c").pack(side="left", padx=(8, 12))

        ttk.Separator(ctrl_row, orient="vertical").pack(side="left", fill="y", padx=12)

        self.fast_btn = ttk.Checkbutton(ctrl_row, text="Fast", variable=self.fast_var, style="TCheckbutton")
        self.fast_btn.pack(side="left", padx=(0, 8))
        self.auto_btn = ttk.Checkbutton(ctrl_row, text="Auto", variable=self.auto_var, style="TCheckbutton")
        self.auto_btn.pack(side="left", padx=(0, 8))
        ttk.Checkbutton(ctrl_row, text="Web", variable=self.web_var, style="TCheckbutton").pack(side="left", padx=(0, 8))
        ttk.Checkbutton(ctrl_row, text="Auto web", variable=self.auto_web_var, style="TCheckbutton").pack(side="left", padx=(0, 8))
        ttk.Label(ctrl_row, text="Rounds:", style="TPanel.TLabel").pack(side="left", padx=(8, 2))
        ttk.Spinbox(ctrl_row, from_=0, to=2, width=3, textvariable=self.rounds_var).pack(side="left", padx=(0, 12))

        ttk.Separator(ctrl_row, orient="vertical").pack(side="left", fill="y", padx=12)

        self.voice_btn = ttk.Button(ctrl_row, text="Voice: Off", command=self.toggle_voice)
        self.voice_btn.pack(side="left", padx=(0, 8))
        ttk.Button(ctrl_row, text="Open Vault", command=self.open_vault).pack(side="left", padx=(0, 8))
        ttk.Button(ctrl_row, text="Save to Vault", command=self.save_to_vault).pack(side="left", padx=(0, 8))
        ttk.Button(ctrl_row, text="Clear Chat", command=self.clear_chat).pack(side="left", padx=(0, 8))
        ttk.Button(ctrl_row, text="Check Ollama", command=self.check_ollama).pack(side="left", padx=(0, 8))
        ttk.Button(ctrl_row, text="Check Updates", command=self.check_updates).pack(side="left", padx=(0, 8))

        # Text input
        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(input_frame, textvariable=self.input_var, font=("Segoe UI", 11))
        self.input_entry.pack(fill="x", pady=(0, 4))
        self.input_entry.bind("<Return>", lambda _e: self.start_run())
        self.input_entry.focus_set()

        # Send + cancel row
        btn_row = ttk.Frame(input_frame, style="TPanel.TFrame")
        btn_row.pack(fill="x")
        self.send_btn = ttk.Button(btn_row, text="Send", command=self.start_run, style="Accent.TButton")
        self.send_btn.pack(side="left", padx=(0, 8))
        self.cancel_btn = ttk.Button(btn_row, text="Cancel", command=self.cancel_run, state="disabled")
        self.cancel_btn.pack(side="left")

        # Status bar
        self.status = ttk.Label(self.root, text="● Ready", anchor="w", foreground="#00e676")
        self.status.pack(fill="x", padx=20, pady=(0, 8))
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=20, pady=(0, 10))

        # Project selector (small, bottom right)
        proj_frame = ttk.Frame(self.root, style="TPanel.TFrame")
        proj_frame.pack(fill="x", padx=20, pady=(0, 10))
        self.project_label = ttk.Label(proj_frame, text=f"Project: {self.project_root.name}", style="TPanel.TLabel")
        self.project_label.pack(side="left")
        ttk.Button(proj_frame, text="Choose Folder", command=self.choose_project).pack(side="right")
        ttk.Button(proj_frame, text="Open", command=self.open_workspace).pack(side="right", padx=(0, 8))

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
        request = self.input_var.get().strip()
        if not request:
            return
        self.input_var.set("")
        self.send_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self._add_user(request)
        self.status.configure(text="● Running local agents...", foreground="#007acc")
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
            self.root.after(0, lambda: self.fail_run(str(exc)))

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

            self.validate_btn = ttk.Button(
                self.root, text="Validate Proposal", command=self.validate_current_proposal
            )
            self.approve_btn = ttk.Button(
                self.root, text="Approve & Write", command=self.approve, state="disabled"
            )
            # Place buttons temporarily (simplified — they appear in status area)
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
            self.root.after(0, lambda: self.fail_run(str(exc)))

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
        if hasattr(self, "approve_btn"):
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
        if hasattr(self, "approve_btn"):
            self.approve_btn.configure(state="disabled")
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

    def check_ollama(self) -> None:
        self.status.configure(text="● Checking Ollama...", foreground="#ffab40")
        self.log_event("Checking Ollama and configured models")
        threading.Thread(target=self._check_ollama, daemon=True).start()

    def _check_ollama(self) -> None:
        ok, message = check_server("http://127.0.0.1:11434", list(MODELS.values()))
        self.root.after(0, lambda: self.finish_ollama_check(ok, message))

    def finish_ollama_check(self, ok: bool, message: str) -> None:
        self.status.configure(text=("● " + message), foreground="#00e676" if ok else "#ff5252")
        self.log_event("Ollama check passed" if ok else "Ollama check failed")

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
            self.root.after(0, lambda: self.fail_media(str(exc)))

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
    root = tk.Tk()
    app = BuilderApp(root)
    # Bring window to front reliably on launch
    root.lift()
    root.focus_force()
    root.attributes('-topmost', True)
    root.after(100, lambda: root.attributes('-topmost', False))
    root.mainloop()
