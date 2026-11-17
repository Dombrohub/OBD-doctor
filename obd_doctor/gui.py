"""Graphical interface for OBD EFT (Tkinter - included with Python).

Run with:  python -m obd_doctor        (or: python -m obd_doctor gui)

All adapter communication happens on one background worker thread so the
window never freezes; results are passed back to the UI thread via a queue.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from . import __version__, ai, config, decoder, history, update
from . import vin as vinlib
from .diagnose import code_details, diagnose
from .dtc_db import is_valid, lookup
from .elm327 import ELM327, OBDError, PROTOCOLS, find_serial_adapter, open_transport
from .modules import ModuleScanner, merge_into_dtcs
from .scanner import Scanner

# --------------------------------------------------------------------------- look & feel
BG = "#f4f6f9"
PANEL = "#ffffff"
TEXT = "#1d2433"
MUTED = "#6b7486"
BORDER = "#dfe3ea"
ACCENT = "#1f6feb"
OK = "#2e7d32"
SEV = {  # severity -> (foreground, light background)
    "critical": ("#ffffff", "#b3261e"),
    "high": ("#c62828", "#fdecea"),
    "medium": ("#b45309", "#fff4e0"),
    "low": ("#0b6aa2", "#e6f3fb"),
    "info": ("#4b5563", "#eef0f3"),
}
SEV_LABEL = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW", "info": "INFO"}

# Live tile warning ranges: pid -> (warn_low, warn_high); None = no limit
LIMITS = {0x05: (None, 105), 0x5C: (None, 130), 0x06: (-15, 15), 0x07: (-15, 15),
          0x08: (-15, 15), 0x09: (-15, 15), 0x42: (12.0, 15.0), 0x0C: (None, 6000)}
SHORT_NAMES = {0x0C: "Engine RPM", 0x0D: "Speed", 0x05: "Coolant", 0x04: "Engine load",
               0x11: "Throttle", 0x0B: "Manifold pressure", 0x10: "Air flow (MAF)",
               0x06: "Short trim B1", 0x07: "Long trim B1", 0x08: "Short trim B2",
               0x09: "Long trim B2", 0x0E: "Timing advance", 0x14: "O2 B1S1", 0x15: "O2 B1S2",
               0x42: "Battery / module"}

COMMON_PORTS = ["demo", "demo-gm", "demo-holden", "demo-bmw", "auto", "192.168.0.10:35000"]


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


# --------------------------------------------------------------------------- worker thread
class Worker:
    """Runs adapter jobs one at a time on a background thread."""

    def __init__(self, ui_queue: queue.Queue):
        self.jobs: queue.Queue = queue.Queue()
        self.ui = ui_queue
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, name, fn, *args):
        self.jobs.put((name, fn, args))

    def _loop(self):
        while True:
            name, fn, args = self.jobs.get()
            try:
                self.ui.put((name, "ok", fn(*args)))
            except Exception as exc:  # report every failure to the UI
                self.ui.put((name, "error", exc))


# --------------------------------------------------------------------------- widgets
class ScrollFrame(ttk.Frame):
    """A vertically scrollable frame (content goes in .inner)."""

    def __init__(self, parent, bg=BG):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._wheel, add="+")

    def _wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None and w is not self:
            w = w.master
        if w is None:
            return
        step = -1 if (e.num == 4 or getattr(e, "delta", 0) > 0) else 1
        self.canvas.yview_scroll(step * 3, "units")

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)


class LiveTile(tk.Frame):
    W, H = 200, 34

    def __init__(self, parent, pid, fonts):
        super().__init__(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, padx=12, pady=8)
        self.pid = pid
        self.hist: deque = deque(maxlen=60)
        name = SHORT_NAMES.get(pid, decoder.PIDS[pid][0])
        tk.Label(self, text=name.upper(), bg=PANEL, fg=MUTED, font=fonts["small_bold"]).pack(anchor="w")
        row = tk.Frame(self, bg=PANEL)
        row.pack(anchor="w", fill="x")
        self.value = tk.Label(row, text="--", bg=PANEL, fg=TEXT, font=fonts["big"])
        self.value.pack(side="left")
        tk.Label(row, text=" " + decoder.PIDS[pid][1], bg=PANEL, fg=MUTED, font=fonts["normal"]).pack(
            side="left", anchor="s", pady=(0, 6))
        self.spark = tk.Canvas(self, width=self.W, height=self.H, bg=PANEL, highlightthickness=0)
        self.spark.pack(anchor="w", pady=(4, 0), fill="x")

    def update_value(self, v):
        if v is None:
            return
        self.hist.append(float(v))
        lo, hi = LIMITS.get(self.pid, (None, None))
        bad = (lo is not None and v < lo) or (hi is not None and v > hi)
        color = SEV["high"][0] if bad else TEXT
        self.value.config(text=f"{v:g}" if isinstance(v, (int, float)) else str(v), fg=color)
        self._draw(SEV["high"][0] if bad else ACCENT)

    def _draw(self, color):
        c = self.spark
        c.delete("all")
        w = max(c.winfo_width(), self.W)
        if len(self.hist) < 2:
            return
        lo, hi = min(self.hist), max(self.hist)
        span = (hi - lo) or 1.0
        n = max(len(self.hist), 20)  # fill the tile quickly, then scroll
        pts = []
        for i, v in enumerate(self.hist):
            x = (i + n - len(self.hist)) * (w - 4) / (n - 1) + 2
            y = self.H - 3 - (v - lo) / span * (self.H - 6)
            pts += [x, y]
        c.create_line(*pts, fill=color, width=2, smooth=True)


# --------------------------------------------------------------------------- main window
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.ui_queue: queue.Queue = queue.Queue()
        self.worker = Worker(self.ui_queue)
        self.ai_worker = Worker(self.ui_queue)  # AI calls never block the adapter
        self.ai = ai.AIExplainer()
        self.ai_busy = False
        self.ai_raw = ""          # markdown text currently shown
        self.ai_dirty = False
        self.elm: ELM327 | None = None
        self.scanner: Scanner | None = None
        self.scan: dict | None = None
        self.findings = []
        self.live_running = False
        self.live_tiles: dict[int, LiveTile] = {}
        self.busy = False

        root.title(f"OBD EFT {__version__} - Car Diagnostics")
        root.geometry("1100x760")
        root.minsize(860, 600)
        root.configure(bg=BG)
        self._setup_style()
        self._build()
        self._build_menu()
        self.root.after(4000, self._auto_update_check)
        self.recording = False
        self.rec_rows: list = []
        self.rec_t0 = 0.0
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self._poll)
        self._set_connected(False)

    # ------------------------------------------------------------ style
    def _setup_style(self):
        base = tkfont.nametofont("TkDefaultFont")
        fam = base.actual("family")
        size = max(10, abs(base.actual("size")))
        self.f = {
            "normal": (fam, size), "bold": (fam, size, "bold"),
            "small": (fam, size - 1), "small_bold": (fam, size - 2, "bold"),
            "title": (fam, size + 6, "bold"), "h2": (fam, size + 2, "bold"),
            "big": (fam, size + 12, "bold"), "mono": ("Courier", size),
        }
        base.configure(size=size)
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=TEXT, font=self.f["normal"])
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("TButton", padding=(12, 6), background=PANEL, bordercolor=BORDER)
        st.map("TButton", background=[("active", "#e9eef7"), ("disabled", BG)])
        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT)
        st.map("Accent.TButton", background=[("active", "#1a5fd0"), ("disabled", "#9db8e8")],
               foreground=[("disabled", "#eef3fc")])
        st.configure("Danger.TButton", foreground=SEV["high"][0])
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
        st.configure("TNotebook.Tab", padding=(16, 7), background="#e6e9ef", foreground=MUTED)
        st.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEXT)])
        st.configure("Treeview", rowheight=int(size * 2.6), background=PANEL, fieldbackground=PANEL,
                     bordercolor=BORDER)
        st.configure("Treeview.Heading", font=self.f["bold"], background="#eceff4", padding=(6, 5))
        st.map("Treeview", background=[("selected", "#dbe7fd")], foreground=[("selected", TEXT)])
        st.configure("TCombobox", padding=4)

    # ------------------------------------------------------------ layout
    def _build(self):
        top = tk.Frame(self.root, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill="x")
        self.top_frame = top
        # Update banner (shown only when a newer version exists)
        self.update_banner = tk.Frame(self.root, bg="#e8f0fe", highlightbackground="#b6cdfb", highlightthickness=1)
        self.update_label = tk.Label(self.update_banner, text="", bg="#e8f0fe", fg=TEXT, font=self.f["bold"])
        self.update_label.pack(side="left", padx=16, pady=8)
        ttk.Button(self.update_banner, text="Later", command=lambda: self.update_banner.pack_forget()).pack(
            side="right", padx=(4, 12), pady=6)
        ttk.Button(self.update_banner, text="What's new", command=lambda: self._show_release_notes()).pack(
            side="right", padx=4, pady=6)
        ttk.Button(self.update_banner, text="Update now", style="Accent.TButton",
                   command=lambda: self.install_update()).pack(side="right", padx=4, pady=6)
        self.update_info = None
        # Demo banner (shown while connected to the simulated car)
        self.demo = False
        self.demo_banner = tk.Frame(self.root, bg="#fff4e0", highlightbackground="#f3c98b", highlightthickness=1)
        tk.Label(self.demo_banner, text="\u26a0  DEMO MODE \u2013 these results come from a simulated car with "
                                        "made-up faults, not from your vehicle. Pick your adapter's port and "
                                        "click Connect to scan your car.",
                 bg="#fff4e0", fg="#8a4b00", font=self.f["bold"], wraplength=1000, justify="left").pack(
            side="left", padx=16, pady=8)
        inner = tk.Frame(top, bg=PANEL, padx=16, pady=10)
        inner.pack(fill="x")
        tk.Label(inner, text="OBD EFT", bg=PANEL, fg=TEXT, font=self.f["title"]).pack(side="left")
        tk.Label(inner, text=f"  car diagnostics  \u00b7  version {__version__}", bg=PANEL, fg=MUTED,
                 font=self.f["normal"]).pack(side="left", pady=(6, 0))

        self.status_dot = tk.Canvas(inner, width=12, height=12, bg=PANEL, highlightthickness=0)
        self.status_text = tk.Label(inner, text="", bg=PANEL, fg=MUTED, font=self.f["normal"])
        self.status_text.pack(side="right")
        self.status_dot.pack(side="right", padx=(0, 6))

        # connection row
        conn = ttk.Frame(self.root, padding=(16, 12, 16, 4))
        conn.pack(fill="x")
        ttk.Label(conn, text="Adapter").pack(side="left")
        self.port_var = tk.StringVar(value=config.get("last_port") or "auto")  # never start on the demo car
        self.port_box = ttk.Combobox(conn, textvariable=self.port_var, width=24)
        self.port_box.pack(side="left", padx=(6, 2))
        ttk.Button(conn, text="⟳", width=3, command=self.refresh_ports).pack(side="left")
        ttk.Label(conn, text="Protocol").pack(side="left", padx=(14, 0))
        self.proto_var = tk.StringVar(value="0 - Automatic")
        ttk.Combobox(conn, textvariable=self.proto_var, state="readonly", width=34,
                     values=[f"{k} - {v}" for k, v in PROTOCOLS.items()]).pack(side="left", padx=6)
        self.connect_btn = ttk.Button(conn, text="Connect", style="Accent.TButton", command=self.toggle_connect)
        self.connect_btn.pack(side="left", padx=(8, 0))
        self.debug_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(conn, text="Log raw traffic", variable=self.debug_var,
                        command=self._apply_debug).pack(side="left", padx=12)
        self.refresh_ports()

        # actions row
        act = ttk.Frame(self.root, padding=(16, 6, 16, 8))
        act.pack(fill="x")
        self.scan_btn = ttk.Button(act, text="Run full scan", style="Accent.TButton", command=self.run_scan)
        self.scan_btn.pack(side="left")
        self.clear_btn = ttk.Button(act, text="Clear codes", style="Danger.TButton", command=self.clear_codes)
        self.clear_btn.pack(side="left", padx=8)
        self.save_btn = ttk.Button(act, text="Save report…", command=self.save_report)
        self.save_btn.pack(side="left")
        ttk.Button(act, text="Save support file…", command=self.save_support_file).pack(side="left", padx=8)
        self.progress = ttk.Progressbar(act, mode="indeterminate", length=140)
        self.progress_label = ttk.Label(act, text="", style="Muted.TLabel")
        self.progress_label.pack(side="left", padx=10)

        lookup_box = ttk.Frame(act)
        lookup_box.pack(side="right")
        ttk.Label(lookup_box, text="Look up code").pack(side="left")
        self.lookup_var = tk.StringVar()
        e = ttk.Entry(lookup_box, textvariable=self.lookup_var, width=9)
        e.pack(side="left", padx=6)
        e.bind("<Return>", lambda _e: self.lookup_code())
        ttk.Button(lookup_box, text="Explain", command=self.lookup_code).pack(side="left")

        # summary cards
        self.summary = ttk.Frame(self.root, padding=(16, 0, 16, 8))
        self.summary.pack(fill="x")
        self.cards = {}
        for key, title in (("mil", "Check engine light"), ("codes", "Trouble codes"),
                           ("worst", "Most serious issue"), ("battery", "Battery"), ("vin", "VIN")):
            card = tk.Frame(self.summary, bg=PANEL, highlightbackground=BORDER, highlightthickness=1,
                            padx=14, pady=8)
            col = len(self.cards)
            card.grid(row=0, column=col, sticky="nsew", padx=(0, 8) if key != "vin" else 0)
            self.summary.grid_columnconfigure(col, weight=2 if key == "vin" else 1, uniform="cards")
            tk.Label(card, text=title.upper(), bg=PANEL, fg=MUTED, font=self.f["small_bold"]).pack(anchor="w")
            val = tk.Label(card, text="—", bg=PANEL, fg=TEXT, font=self.f["h2"])
            val.pack(anchor="w")
            self.cards[key] = val

        # tabs
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.tab_diag = ScrollFrame(self.nb)
        self.nb.add(self.tab_diag, text="Diagnosis")
        self._build_ai_tab()
        self._build_codes_tab()
        self._build_modules_tab()
        self._build_history_tab()
        self._build_live_tab()
        self._build_freeze_tab()
        self._build_vehicle_tab()
        self._build_log_tab()
        self._show_welcome()

    def _tree(self, parent, cols, widths, height=10):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=height)
        for col, w in zip(cols, widths):
            tree.heading(col, text=col)
            tree.column(col, width=w, anchor="w", stretch=(w > 200))
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for sev, (fg, bg) in SEV.items():
            tree.tag_configure(sev, foreground=fg if sev != "critical" else bg)
        return frame, tree

    def _build_ai_tab(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.tab_ai = tab
        self.nb.add(tab, text="✦ AI explanation")
        form = ttk.Frame(tab)
        form.pack(fill="x")
        form.grid_columnconfigure(1, weight=1)
        ttk.Label(form, text="Vehicle (optional)").grid(row=0, column=0, sticky="w", pady=2)
        self.vehicle_var = tk.StringVar(value=config.get("vehicle", ""))
        ttk.Entry(form, textvariable=self.vehicle_var).grid(row=0, column=1, sticky="ew", padx=8, pady=2)
        ttk.Label(form, text="e.g. 2014 Ford Focus 2.0L, 120,000 km", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w")
        ttk.Label(form, text="Symptoms (optional)").grid(row=1, column=0, sticky="w", pady=2)
        self.symptoms_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.symptoms_var).grid(row=1, column=1, sticky="ew", padx=8, pady=2)
        ttk.Label(form, text="e.g. rough idle, hesitates when cold", style="Muted.TLabel").grid(
            row=1, column=2, sticky="w")

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(8, 8))
        self.ai_btn = ttk.Button(bar, text="✦  Explain with AI", style="Accent.TButton", command=self.ai_explain)
        self.ai_btn.pack(side="left")
        ttk.Button(bar, text="AI settings…", command=self.ai_settings).pack(side="left", padx=8)
        self.ai_status = ttk.Label(bar, text="", style="Muted.TLabel")
        self.ai_status.pack(side="left", padx=4)

        ask = ttk.Frame(tab)
        ask.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(tab, text="Sends the trouble codes and sensor readings (not your VIN) to Anthropic's Claude AI. "
                            "AI answers can be wrong - double-check before buying parts.",
                  style="Muted.TLabel", wraplength=1000).pack(side="bottom", anchor="w", pady=(6, 0))
        ttk.Label(ask, text="Ask a follow-up").pack(side="left")
        self.ask_var = tk.StringVar()
        self.ask_entry = ttk.Entry(ask, textvariable=self.ask_var)
        self.ask_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.ask_entry.bind("<Return>", lambda _e: self.ai_ask())
        self.ask_btn = ttk.Button(ask, text="Ask", command=self.ai_ask)
        self.ask_btn.pack(side="left")

        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        self.ai_text = tk.Text(frame, wrap="word", bg=PANEL, fg=TEXT, relief="flat", padx=16, pady=12,
                               highlightbackground=BORDER, highlightthickness=1, font=self.f["normal"],
                               spacing1=2, spacing3=2)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.ai_text.yview)
        self.ai_text.configure(yscrollcommand=sb.set)
        self.ai_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._text_tags(self.ai_text)
        self.ai_text.tag_configure("q", foreground=ACCENT, font=self.f["bold"], spacing1=12)
        self.ai_text.tag_configure("num", lmargin1=18, lmargin2=36)
        self._ai_set_placeholder()

    def _build_codes_tab(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="Trouble codes")
        frame, self.codes_tree = self._tree(tab, ("Code", "Description", "Severity", "Found in"),
                                            (80, 420, 90, 300), height=8)
        frame.pack(fill="both", expand=True)
        self.codes_tree.bind("<<TreeviewSelect>>", self._on_code_select)
        ttk.Label(tab, text="Details", font=self.f["bold"]).pack(anchor="w", pady=(10, 4))
        self.code_detail = tk.Text(tab, height=10, wrap="word", bg=PANEL, fg=TEXT, relief="flat",
                                   highlightbackground=BORDER, highlightthickness=1, padx=12, pady=10,
                                   font=self.f["normal"])
        self.code_detail.pack(fill="both", expand=True)
        self._text_tags(self.code_detail)

    def _build_live_tab(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="Live data")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 8))
        self.live_btn = ttk.Button(bar, text="▶  Start live data", style="Accent.TButton", command=self.toggle_live)
        self.live_btn.pack(side="left")
        self.live_info = ttk.Label(bar, text="Connect to the car, then start live data.", style="Muted.TLabel")
        self.export_btn = ttk.Button(bar, text="Export CSV…", command=self.export_recording, state="disabled")
        self.export_btn.pack(side="right")
        self.rec_btn = ttk.Button(bar, text="\u25cf  Record", command=self.toggle_record)
        self.rec_btn.pack(side="right", padx=8)
        self.live_info.pack(side="left", padx=12)
        self.live_grid = ScrollFrame(tab)
        self.live_grid.pack(fill="both", expand=True)

    def _build_freeze_tab(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="Freeze frame")
        self.freeze_title = ttk.Label(tab, text="No freeze frame recorded yet.", style="Muted.TLabel")
        self.freeze_title.pack(anchor="w", pady=(0, 8))
        frame, self.freeze_tree = self._tree(tab, ("Sensor", "Value at time of fault"), (320, 300), height=16)
        frame.pack(fill="both", expand=True)

    def _build_vehicle_tab(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="Vehicle & tests")
        frame, self.vehicle_tree = self._tree(tab, ("Item", "Value"), (320, 400), height=18)
        frame.pack(fill="both", expand=True)
        self.vehicle_tree.tag_configure("ok", foreground=OK)
        self.vehicle_tree.tag_configure("warn", foreground=SEV["medium"][0])
        self.vehicle_tree.tag_configure("section", font=self.f["bold"], background="#f0f2f6")

    def _build_log_tab(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="Log")
        self.log_text = tk.Text(tab, wrap="none", bg="#11151c", fg="#cfd6e4", insertbackground="#fff",
                                relief="flat", padx=10, pady=8, font=self.f["mono"])
        sb = ttk.Scrollbar(tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _text_tags(self, t: tk.Text):
        t.tag_configure("h", font=self.f["h2"], spacing3=4)
        t.tag_configure("b", font=self.f["bold"])
        t.tag_configure("muted", foreground=MUTED)
        t.tag_configure("bullet", lmargin1=18, lmargin2=32)
        for sev, (fg, _bg) in SEV.items():
            t.tag_configure(sev, foreground=fg if sev != "critical" else SEV["critical"][1], font=self.f["bold"])

    # ------------------------------------------------------------ helpers
    def log(self, msg: str):
        """Thread-safe log."""
        self.ui_queue.put(("log", "ok", msg))

    def _append_log(self, msg):
        self.log_text.insert("end", time.strftime("%H:%M:%S  ") + msg + "\n")
        self.log_text.see("end")

    def _apply_debug(self):
        if self.elm:
            self.elm.debug = self.debug_var.get()

    def refresh_ports(self):
        ports = list_serial_ports()
        self.port_box["values"] = COMMON_PORTS + [p for p in ports if p not in COMMON_PORTS]

    def _set_busy(self, busy: bool, text: str = ""):
        self.busy = busy
        if busy:
            self.progress.pack(side="left", padx=(12, 0), before=self.progress_label)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
        self.progress_label.config(text=text)
        self._update_buttons()

    def _set_connected(self, connected: bool, text: str = ""):
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, 11, 11, fill=OK if connected else "#9aa3b2", outline="")
        self.status_text.config(text=text or ("Connected" if connected else "Not connected"))
        self.connect_btn.config(text="Disconnect" if connected else "Connect")
        self._update_buttons()

    def _update_buttons(self):
        conn = self.scanner is not None
        state = "normal" if conn and not self.busy else "disabled"
        if hasattr(self, "mod_search_btn"):
            self.mod_search_btn.config(state=state)
            has_mod_codes = bool(self.scan and any(m["dtcs"] for m in (self.scan.get("modules") or {}).get("list", [])))
            self.mod_clear_btn.config(state=state if has_mod_codes else "disabled")
        for b in (self.scan_btn, self.clear_btn):
            b.config(state=state)
        self.live_btn.config(state="normal" if conn else "disabled")
        self.save_btn.config(state="normal" if self.scan else "disabled")
        if hasattr(self, "ai_btn"):
            self.ai_btn.config(state="normal" if self.scan and not self.ai_busy else "disabled")
            ask_ok = bool(self.ai.messages) and not self.ai_busy
            self.ask_btn.config(state="normal" if ask_ok else "disabled")
            self.ask_entry.config(state="normal" if ask_ok else "disabled")
        self.connect_btn.config(state="disabled" if self.busy and not conn else "normal")

    def _error(self, title, exc):
        self._append_log(f"ERROR: {exc}")
        messagebox.showerror(title, str(exc), parent=self.root)

    # ------------------------------------------------------------ queue pump
    def _poll(self):
        try:
            while True:
                name, status, payload = self.ui_queue.get_nowait()
                handler = getattr(self, f"_done_{name}", None)
                if name == "log":
                    self._append_log(payload)
                elif name == "progress":
                    self.progress_label.config(text=payload)
                elif name == "ai_text":
                    self.ai_raw += payload
                    self.ai_dirty = True
                elif handler:
                    handler(status, payload)
        except queue.Empty:
            pass
        if self.ai_dirty:
            self.ai_dirty = False
            self._ai_render("end" if self.ai_busy else "keep")
        self.root.after(50, self._poll)

    # ------------------------------------------------------------ connect
    def toggle_connect(self):
        if self.scanner:
            self.live_running = False
            elm, self.elm, self.scanner = self.elm, None, None
            self.worker.submit("disconnect", elm.close)
            self.demo = False
            self.demo_banner.pack_forget()
            self._set_connected(False)
            self._append_log("Disconnected")
            return
        target = self.port_var.get().strip()
        proto = self.proto_var.get().split(" ")[0]
        if not target:
            messagebox.showinfo("Adapter", "Choose or type the adapter port first.", parent=self.root)
            return
        self._connect_target = target
        self._set_busy(True, f"Connecting to {target}…")
        self.worker.submit("connect", self._do_connect, target, proto, self.debug_var.get())

    def _do_connect(self, target, proto, debug):
        if target in ("demo", "demo-gm", "demo-holden", "demo-bmw"):
            from .simulator import start_background
            target = f"127.0.0.1:{start_background(target[5:] or 'ford')}"
            self.log("Started the built-in simulated car (demo mode)")
        elif target == "auto":
            self.log("Searching serial ports for an ELM327 adapter…")
            port, baud = find_serial_adapter()
            self.log(f"Found adapter on {port} @ {baud} baud")
            elm = ELM327(open_transport(port, baud), protocol=proto, debug=debug)
            elm.log = self.log
            elm.initialize()
            return elm
        self.log(f"Connecting to {target}")
        elm = ELM327(open_transport(target), protocol=proto, debug=debug)
        elm.log = self.log
        try:
            elm.initialize()
        except Exception:
            elm.t.close()
            raise
        return elm

    def _done_connect(self, status, payload):
        self._set_busy(False)
        if status == "error":
            msg = str(payload)
            if isinstance(payload, (OSError, ConnectionError)) and not isinstance(payload, OBDError):
                msg = f"Could not open the adapter:\n{payload}\n\nCheck the port name and that no other app is using it."
            self._error("Connection failed", msg)
            return
        self.elm, self.scanner = payload, Scanner(payload)
        self.demo = self._connect_target.startswith("demo")
        if self.demo:
            self._set_connected(True, "Connected \u00b7 DEMO \u2013 simulated car")
            self.demo_banner.pack(fill="x", after=self.top_frame)
        else:
            self._set_connected(True, f"Connected · {self.elm.protocol_name}")
            self.demo_banner.pack_forget()
            config.set("last_port", self._connect_target)  # remember the real adapter for next time
        self._append_log(f"Connected: {self.elm.version} · {self.elm.protocol_name}")
        if self.elm.voltage:
            self.cards["battery"].config(text=f"{self.elm.voltage:.1f} V", fg=TEXT)
        self.run_scan()

    def _done_disconnect(self, status, payload):
        pass

    # ------------------------------------------------------------ scan
    def run_scan(self):
        if not self.scanner or self.busy:
            return
        self._set_busy(True, "Scanning…")
        sc = self.scanner
        self.worker.submit("scan", lambda: sc.full_scan(progress=lambda m: self.ui_queue.put(("progress", "ok", m + "…"))))

    def _done_scan(self, status, payload):
        self._set_busy(False)
        if status == "error":
            self._error("Scan failed", payload)
            return
        self.scan = payload
        if self.demo:
            payload["demo"] = True
            self._append_log("Demo scan (simulated car) - not saved to history")
        elif self.save_hist_var.get():
            try:
                history.save(payload)
            except OSError as exc:
                self._append_log(f"Couldn't save to history: {exc}")
        self.findings = diagnose(payload)
        if not self.ai_busy:  # a new scan starts a new AI conversation
            self.ai = ai.AIExplainer()
            self.ai_raw = ""
            self._ai_set_placeholder()
        n_codes = len(code_details(payload))
        self._append_log(f"Scan complete: {n_codes} code(s), {len(self.findings)} finding(s)")
        self._render_all()
        self.nb.select(self.tab_diag)
        self._update_buttons()

    # ------------------------------------------------------------ clear
    def clear_codes(self):
        if not messagebox.askyesno(
                "Clear trouble codes?",
                "This erases the trouble codes and freeze frame, and turns off the check engine light.\n\n"
                "• It does NOT fix the problem - the light will come back if the fault is still there.\n"
                "• The car will not pass an emissions test until it has been driven for a few days.\n\n"
                "Turn the engine OFF with the ignition ON, then click Yes.", icon="warning", parent=self.root):
            return
        self._set_busy(True, "Clearing codes…")
        self.worker.submit("clear", self.scanner.clear_dtcs)

    def _done_clear(self, status, payload):
        self._set_busy(False)
        if status == "error":
            self._error("Clear failed", payload)
        elif payload:
            self._append_log("Trouble codes cleared")
            messagebox.showinfo("Codes cleared", "The car confirmed the codes were cleared. Re-scanning…",
                                parent=self.root)
            self.run_scan()
        else:
            messagebox.showwarning("Not confirmed", "The car did not confirm the clear request.", parent=self.root)

    # ------------------------------------------------------------ history
    def _build_history_tab(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.tab_history = tab
        self.nb.add(tab, text="History")
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.save_hist_var = tk.BooleanVar(value=config.get("save_history", True))
        ttk.Checkbutton(bar, text="Save every scan to history (stored only on this computer)",
                        variable=self.save_hist_var,
                        command=lambda: config.set("save_history", bool(self.save_hist_var.get()))).pack(side="left")
        ttk.Button(bar, text="Delete history…", command=self.delete_history).pack(side="right")
        ttk.Button(bar, text="Open folder", command=self.open_history_folder).pack(side="right", padx=8)

        self.hist_changes = tk.Text(tab, height=4, wrap="word", bg=PANEL, fg=TEXT, relief="flat", padx=12, pady=8,
                                    highlightbackground=BORDER, highlightthickness=1, font=self.f["normal"])
        self.hist_changes.pack(fill="x", pady=(8, 8))
        self._text_tags(self.hist_changes)
        self.hist_changes.tag_configure("ok", foreground=OK, font=self.f["bold"])

        chart_box = tk.Frame(tab, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        chart_box.pack(fill="x")
        tk.Label(chart_box, text="LONG-TERM FUEL TRIM OVER TIME", bg=PANEL, fg=MUTED,
                 font=self.f["small_bold"]).pack(anchor="w", padx=12, pady=(6, 0))
        self.hist_chart = tk.Canvas(chart_box, height=150, bg=PANEL, highlightthickness=0)
        self.hist_chart.pack(fill="x", padx=8, pady=(0, 6))
        self.hist_chart.bind("<Configure>", lambda e: self._draw_history_chart())

        frame, self.hist_tree = self._tree(tab, ("Date", "Check engine", "Codes", "LTFT B1", "LTFT B2", "Battery"),
                                           (150, 115, 400, 85, 85, 85), height=6)
        frame.pack(fill="both", expand=True, pady=(8, 0))
        self.hist_tree.tag_configure("on", foreground=SEV["high"][0])
        self.hist_scans = []
        self._render_history()

    def _render_history(self):
        vin = (self.scan or {}).get("vin")
        try:
            self.hist_scans = history.load_all(vin) if vin else history.load_all()
        except OSError:
            self.hist_scans = []
        t = self.hist_changes
        t.config(state="normal")
        t.delete("1.0", "end")
        h = (self.scan or {}).get("history")
        if not self.scan:
            t.insert("end", "Every scan is saved here so you can see what changed since last time.", "muted")
        elif not h:
            t.insert("end", "This is the first saved scan of this vehicle. Scan again later to see what changed.",
                     "muted")
        else:
            t.insert("end", f"Changes since the last scan ({h['previous_time']}):\n", "b")
            rows = [("new", "New: ", "high"), ("returned", "Came back after going away: ", "critical"),
                    ("resolved", "Gone: ", "ok"), ("persisting", "Still there: ", "medium")]
            shown = False
            for key, label, tag in rows:
                if h[key]:
                    t.insert("end", label, tag)
                    t.insert("end", ", ".join(h[key]) + "\n")
                    shown = True
            if not shown:
                t.insert("end", "No trouble codes now or at the last scan.", "ok")
        t.config(state="disabled")

        tree = self.hist_tree
        tree.delete(*tree.get_children())
        fmt = lambda v, u: f"{v:g} {u}" if isinstance(v, (int, float)) else "—"
        for sc in reversed(self.hist_scans[-100:]):
            sm = history.summarize(sc)
            tr = sm["tracked"]
            tree.insert("", "end", values=(sm["timestamp"], "ON" if sm["mil_on"] else "off",
                                           ", ".join(sm["codes"]) or "none", fmt(tr["LTFT B1"], "%"),
                                           fmt(tr["LTFT B2"], "%"), fmt(tr["Battery"], "V")),
                        tags=("on",) if sm["mil_on"] else ())
        self._draw_history_chart()

    def _draw_history_chart(self):
        c = self.hist_chart
        c.delete("all")
        w, h = max(c.winfo_width(), 300), int(c["height"])
        pts = [history.summarize(s)["tracked"] for s in self.hist_scans[-30:]]
        series = [("LTFT B1", ACCENT), ("LTFT B2", "#d97706")]
        if sum(1 for p in pts if isinstance(p.get("LTFT B1"), (int, float))) < 2:
            c.create_text(w / 2, h / 2, text="The trend appears after two or more scans with the engine running.",
                          fill=MUTED, font=self.f["small"])
            return
        left, right, top, bottom = 44, w - 12, 10, h - 22
        lim = max(25.0, max(abs(p[k]) for p in pts for k, _ in series if isinstance(p.get(k), (int, float))))
        y = lambda v: top + (lim - v) / (2 * lim) * (bottom - top)
        x = lambda i: left + i * (right - left) / max(1, len(pts) - 1)
        c.create_rectangle(left, y(10), right, y(-10), fill="#eef7ee", outline="")
        for v in (-lim, -10, 0, 10, lim):
            c.create_line(left, y(v), right, y(v), fill=BORDER, dash=() if v == 0 else (2, 3))
            c.create_text(left - 6, y(v), text=f"{v:+.0f}%", anchor="e", fill=MUTED, font=self.f["small"])
        for key, color in series:
            coords = [(x(i), y(p[key])) for i, p in enumerate(pts) if isinstance(p.get(key), (int, float))]
            if len(coords) >= 2:
                c.create_line(*[v for xy in coords for v in xy], fill=color, width=2)
            for cx, cy in coords:
                c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=color, outline="")
        c.create_text(right, bottom + 12, anchor="e", fill=MUTED, font=self.f["small"],
                      text="Green band = normal (±10%).   Blue = bank 1, orange = bank 2.   Oldest → newest")

    def open_history_folder(self):
        d = history.history_dir()
        os.makedirs(d, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(d)  # noqa: S606 - opening a folder for the user
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", d])
        except OSError as exc:
            messagebox.showinfo("History folder", f"Saved scans are in:\n{d}\n\n({exc})", parent=self.root)

    def delete_history(self):
        if messagebox.askyesno("Delete history?", "Delete all saved scans on this computer?", icon="warning",
                               parent=self.root):
            n = history.clear()
            self._append_log(f"Deleted {n} saved scan(s)")
            if self.scan:
                self.scan["history"] = None
            self._render_history()

    # ------------------------------------------------------------ ABS & airbag modules
    def _build_modules_tab(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.tab_modules = tab
        self.nb.add(tab, text="ABS · Airbag · Trans")
        ttk.Label(tab, text="Standard OBD-II only covers the engine. OBD EFT also asks the ABS, airbag and transmission "
                            "computers directly, in each manufacturer's own diagnostic language (UDS, GM GMLAN, BMW). This works "
                            "on most CAN-bus cars, roughly 2008 and newer. The car's VIN picks the brand.",
                  wraplength=1000, justify="left").pack(anchor="w")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=8)
        self.mod_search_btn = ttk.Button(bar, text="Full module search", command=self.module_search)
        self.mod_search_btn.pack(side="left")
        self.mod_clear_btn = ttk.Button(bar, text="Clear module codes…", style="Danger.TButton",
                                        command=self.clear_module_codes)
        self.mod_clear_btn.pack(side="left", padx=8)
        self.mod_info = ttk.Label(bar, text="Run a scan to check the ABS, airbag and transmission modules.", style="Muted.TLabel")
        self.mod_info.pack(side="left", padx=6)
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=("status",), show="tree headings")
        tree.heading("#0", text="Module / code")
        tree.heading("status", text="Status")
        tree.column("#0", width=560, stretch=True)
        tree.column("status", width=380, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for sev, (fg, bg) in SEV.items():
            tree.tag_configure(sev, foreground=fg if sev != "critical" else bg)
        tree.tag_configure("ok", foreground=OK)
        tree.tag_configure("module", font=self.f["bold"])
        tree.tag_configure("muted", foreground=MUTED)
        self.mod_tree = tree
        self.mod_note = ttk.Label(tab, text="", style="Muted.TLabel", wraplength=1000, justify="left")
        self.mod_note.pack(anchor="w", pady=(8, 0))

    def _render_modules(self):
        tree = self.mod_tree
        tree.delete(*tree.get_children())
        mods = (self.scan or {}).get("modules")
        if not mods:
            self.mod_info.config(text="The module check was skipped.")
            return
        self.mod_note.config(text=mods.get("note", ""))
        if not mods["supported"]:
            self.mod_info.config(text="Not available on this car.")
            return
        n = sum(len(m["dtcs"]) for m in mods["list"])
        if not mods["list"]:
            self.mod_info.config(text="No ABS, airbag or transmission module answered" + (
                " the full search." if mods.get("searched") else " the quick check. Try the full module search."))
        else:
            self.mod_info.config(text=f"{len(mods['list'])} module(s) found, {n} code(s)"
                                      + (" (full search)" if mods.get("searched") else " (quick check)"))
        for m in mods["list"]:
            count = len(m["dtcs"])
            status = (f"{count} code(s)" if count else "No codes") + (f" - {m['error']}" if m["error"] else "")
            parent = tree.insert("", "end", text=f"  {m['label']}", values=(status,), open=True,
                                 tags=("module", "high" if count else "ok"))
            for d in m["dtcs"]:
                info = lookup(d["code"])
                tree.insert(parent, "end", text=f"{d['code']}  {info['description']}",
                            values=(", ".join(d["status"]) or "stored",), tags=(info["severity"],))

    def module_search(self):
        if not self.scanner or self.busy:
            return
        self._set_busy(True, "Searching for modules…")
        sc = self.scanner
        vin = (self.scan or {}).get("vin")
        self.worker.submit("modules", lambda: sc.scan_modules(
            discover=True, progress=lambda m: self.ui_queue.put(("progress", "ok", m)), vin=vin))

    def _done_modules(self, status, payload):
        self._set_busy(False)
        if status == "error":
            self._error("Module search failed", payload)
            return
        self.scan["modules"] = payload
        merge_into_dtcs(self.scan)
        self.findings = diagnose(self.scan)
        self._append_log(f"Module search: {len(payload.get('list', []))} module(s) found")
        self._render_all()
        self.nb.select(self.tab_modules)
        self._update_buttons()

    def clear_module_codes(self):
        mods = [m for m in ((self.scan or {}).get("modules") or {}).get("list", []) if m["dtcs"]]
        if not mods or self.busy:
            return
        names = "\n".join(f"  \u2022 {m['label']}" for m in mods)
        if not messagebox.askyesno(
                "Clear ABS / airbag / transmission codes?",
                f"This clears the trouble codes in:\n{names}\n\n"
                "\u2022 It does NOT fix anything. If the fault is still there, the warning light comes back.\n"
                "\u2022 For airbags: while the light is on, the airbags may not work. Get the fault repaired.\n"
                "\u2022 Airbag modules that stored crash data after an accident cannot be cleared this way.\n\n"
                "Turn the engine OFF with the ignition ON, then click Yes.", icon="warning", parent=self.root):
            return
        self._set_busy(True, "Clearing module codes…")
        elm = self.elm
        self.worker.submit("modclear", lambda: ModuleScanner(
            elm, lambda m: self.ui_queue.put(("progress", "ok", m))).clear_modules(mods))

    def _done_modclear(self, status, payload):
        self._set_busy(False)
        if status == "error":
            self._error("Clear failed", payload)
            return
        lines = [f"{label}: {'cleared' if ok else 'NOT confirmed'}" for label, ok in payload.items()]
        self._append_log("Module clear: " + "; ".join(lines))
        messagebox.showinfo("Module codes", "\n".join(lines) + "\n\nRe-scanning…", parent=self.root)
        self.run_scan()

    # ------------------------------------------------------------ live data
    def toggle_live(self):
        if self.live_running:
            self.live_running = False
            self.live_btn.config(text="▶  Start live data")
            self.live_info.config(text="Paused.")
            return
        if not self.scanner:
            return
        self.live_running = True
        self.live_btn.config(text="■  Stop live data")
        self.live_info.config(text="Reading sensors…")
        self.nb.select(2)
        self._live_round()

    def _live_round(self):
        if self.live_running and self.scanner:
            sc = self.scanner
            self.worker.submit("live", lambda: (time.monotonic(), sc.read_live(decoder.LIVE_PIDS)))

    def _done_live(self, status, payload):
        if status == "error":
            self.live_running = False
            self.live_btn.config(text="▶  Start live data")
            self._error("Live data stopped", payload)
            return
        t0, values = payload
        if not self.live_tiles:
            self._build_tiles(values.keys())
        for pid, v in values.items():
            if pid in self.live_tiles:
                self.live_tiles[pid].update_value(v)
        hz = 1.0 / max(time.monotonic() - t0, 0.001)
        rec = ""
        if self.recording:
            self.rec_rows.append((round(time.monotonic() - self.rec_t0, 2), values))
            rec = f"  ·  \u25cf recording {len(self.rec_rows)} samples ({self.rec_rows[-1][0]:.0f} s)"
        self.live_info.config(text=f"Updated {time.strftime('%H:%M:%S')}  ·  {len(values)} sensors  ·  {hz:.1f} updates/s{rec}"
                              if self.live_running else "Paused.")
        if self.live_running:
            self.root.after(200, self._live_round)

    def _build_tiles(self, pids):
        self.live_grid.clear()
        grid = self.live_grid.inner
        cols = 4
        for i, pid in enumerate(pids):
            tile = LiveTile(grid, pid, self.f)
            tile.grid(row=i // cols, column=i % cols, sticky="nsew", padx=(0, 8), pady=(0, 8))
            self.live_tiles[pid] = tile
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1, uniform="tiles")

    # ------------------------------------------------------------ lookup
    def lookup_code(self):
        code = self.lookup_var.get().strip().upper()
        if not is_valid(code):
            messagebox.showinfo("Look up code", "Enter a code like P0420, P0301, C0035, B1234 or U0100.",
                                parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title(f"{code} - OBD EFT")
        win.geometry("640x420")
        win.configure(bg=BG)
        t = tk.Text(win, wrap="word", bg=PANEL, fg=TEXT, relief="flat", padx=16, pady=14, font=self.f["normal"])
        t.pack(fill="both", expand=True, padx=12, pady=12)
        self._text_tags(t)
        self._write_code(t, lookup(code))
        t.config(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 12))

    # ------------------------------------------------------------ rendering
    def _show_welcome(self):
        p = self.tab_diag.inner
        box = tk.Frame(p, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, padx=24, pady=20)
        box.pack(fill="x", padx=4, pady=8)
        tk.Label(box, text="Welcome to OBD EFT", bg=PANEL, fg=TEXT, font=self.f["title"]).pack(anchor="w")
        steps = [
            "1.  Plug your ELM327 adapter into the car's OBD-II port (under the dashboard, driver's side).",
            "2.  Turn the ignition ON or start the engine.",
            "3.  Pick your adapter above:  its COM port for USB or Bluetooth (e.g. COM5),  "
            "192.168.0.10:35000 for Wi-Fi,  or 'auto' to search for it.",
            "     (Choosing 'demo' connects to a simulated car with made-up faults, for trying the app without a car.)",
            "4.  Click Connect. A full scan and diagnosis runs automatically.",
        ]
        for s in steps:
            tk.Label(box, text=s, bg=PANEL, fg=TEXT, font=self.f["normal"], justify="left",
                     wraplength=900).pack(anchor="w", pady=3)

    def _render_all(self):
        scan = self.scan
        st = scan.get("status") or {}
        details = code_details(scan)
        # summary cards
        if st:
            self.cards["mil"].config(text="ON" if st["mil_on"] else "OFF",
                                     fg=SEV["high"][0] if st["mil_on"] else OK)
        self.cards["codes"].config(text=str(len(details)), fg=SEV["high"][0] if details else OK)
        if self.findings and self.findings[0].severity != "info":
            sev = self.findings[0].severity
            self.cards["worst"].config(text=SEV_LABEL[sev], fg=SEV[sev][0] if sev != "critical" else SEV[sev][1])
        else:
            self.cards["worst"].config(text="None", fg=OK)
        volts = scan["live"].get(0x42) or scan.get("adapter_voltage")
        if volts:
            self.cards["battery"].config(text=f"{volts:.1f} V", fg=TEXT)
        self.cards["vin"].config(text=scan.get("vin") or "not reported", fg=TEXT)

        self._render_diagnosis()
        self._render_codes(details)
        self._render_freeze()
        self._render_vehicle()
        self._render_modules()
        self._render_history()
        # pre-fill live tiles with the scan snapshot
        live = {p: v for p, v in scan["live"].items() if p in decoder.LIVE_PIDS}
        if live:
            if not self.live_tiles:
                self._build_tiles(live.keys())
            for pid, v in live.items():
                if pid in self.live_tiles:
                    self.live_tiles[pid].update_value(v)

    def _render_diagnosis(self):
        self.tab_diag.clear()
        p = self.tab_diag.inner
        if not self.findings:
            box = tk.Frame(p, bg="#e8f5e9", highlightbackground="#b7dfba", highlightthickness=1, padx=20, pady=18)
            box.pack(fill="x", padx=4, pady=8)
            tk.Label(box, text="✓  No problems detected", bg="#e8f5e9", fg=OK, font=self.f["title"]).pack(anchor="w")
            tk.Label(box, text="The car's computer reports no trouble codes and the sensor readings look normal.",
                     bg="#e8f5e9", fg=TEXT, font=self.f["normal"]).pack(anchor="w", pady=(4, 0))
        for i, fnd in enumerate(self.findings, 1):
            fg, bg = SEV[fnd.severity]
            card = tk.Frame(p, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x", padx=4, pady=(8, 0))
            tk.Frame(card, bg=bg if fnd.severity == "critical" else fg, width=6).pack(side="left", fill="y")
            body = tk.Frame(card, bg=PANEL, padx=16, pady=12)
            body.pack(side="left", fill="both", expand=True)
            head = tk.Frame(body, bg=PANEL)
            head.pack(fill="x")
            badge_fg, badge_bg = (fg, bg) if fnd.severity != "critical" else (fg, bg)
            tk.Label(head, text=f" {SEV_LABEL[fnd.severity]} ", bg=badge_bg, fg=badge_fg,
                     font=self.f["small_bold"], padx=4, pady=1).pack(side="left")
            tk.Label(head, text=f"  {i}. {fnd.title}", bg=PANEL, fg=TEXT, font=self.f["h2"],
                     justify="left", wraplength=820).pack(side="left", anchor="w")
            wrap = 880
            if fnd.codes:
                tk.Label(body, text="Related codes: " + ", ".join(fnd.codes), bg=PANEL, fg=MUTED,
                         font=self.f["small"]).pack(anchor="w", pady=(4, 0))
            if fnd.detail:
                tk.Label(body, text=fnd.detail, bg=PANEL, fg=TEXT, font=self.f["normal"], justify="left",
                         wraplength=wrap).pack(anchor="w", pady=(6, 0))
            if fnd.causes:
                tk.Label(body, text="Likely causes", bg=PANEL, fg=TEXT, font=self.f["bold"]).pack(anchor="w", pady=(8, 0))
                for cause in fnd.causes:
                    tk.Label(body, text="•  " + cause, bg=PANEL, fg=TEXT, font=self.f["normal"], justify="left",
                             wraplength=wrap).pack(anchor="w", padx=(10, 0))
            if fnd.steps:
                tk.Label(body, text="What to do", bg=PANEL, fg=TEXT, font=self.f["bold"]).pack(anchor="w", pady=(8, 0))
                for step in fnd.steps:
                    tk.Label(body, text="→  " + step, bg=PANEL, fg=ACCENT, font=self.f["normal"], justify="left",
                             wraplength=wrap).pack(anchor="w", padx=(10, 0))
        tk.Label(p, text="This is guidance based on OBD-II data, not a substitute for a professional inspection. "
                         "For brakes, steering, overheating or oil pressure problems, see a mechanic.",
                 bg=BG, fg=MUTED, font=self.f["small"], justify="left", wraplength=900).pack(anchor="w", padx=6, pady=14)

    def _render_codes(self, details):
        self.codes_tree.delete(*self.codes_tree.get_children())
        self._code_info = {}
        for info in details:
            iid = self.codes_tree.insert("", "end", values=(info["code"], info["description"],
                                                            SEV_LABEL[info["severity"]], ", ".join(info["found_in"])),
                                         tags=(info["severity"],))
            self._code_info[iid] = info
        self.code_detail.config(state="normal")
        self.code_detail.delete("1.0", "end")
        if details:
            first = self.codes_tree.get_children()[0]
            self.codes_tree.selection_set(first)
        else:
            self.code_detail.insert("end", "No trouble codes stored in any module.", "muted")
            self.code_detail.config(state="disabled")

    def _on_code_select(self, _e=None):
        sel = self.codes_tree.selection()
        if not sel:
            return
        t = self.code_detail
        t.config(state="normal")
        t.delete("1.0", "end")
        self._write_code(t, self._code_info[sel[0]])
        t.config(state="disabled")

    def _write_code(self, t: tk.Text, info):
        t.insert("end", f"{info['code']}  ", "h")
        t.insert("end", f"{info['description']}\n", "h")
        t.insert("end", f"{SEV_LABEL[info['severity']]}", info["severity"])
        t.insert("end", f"   ·   {info['system']}\n", "muted")
        if info.get("found_in"):
            t.insert("end", "Found in: " + ", ".join(info["found_in"]) + "\n", "muted")
        if info["manufacturer_specific"]:
            t.insert("end", "Manufacturer-specific code - the exact meaning depends on the brand.\n", "muted")
        if info["causes"]:
            t.insert("end", "\nLikely causes (most common first)\n", "b")
            for c in info["causes"]:
                t.insert("end", f"•  {c}\n", "bullet")
        if info["tips"]:
            t.insert("end", "\nTips\n", "b")
            for tip in info["tips"]:
                t.insert("end", f"→  {tip}\n", "bullet")

    def _render_freeze(self):
        tree = self.freeze_tree
        tree.delete(*tree.get_children())
        ff = self.scan.get("freeze_frame")
        if not ff:
            self.freeze_title.config(text="No freeze frame recorded.")
            return
        info = lookup(ff["trigger_dtc"])
        self.freeze_title.config(text=f"Sensor readings at the moment {ff['trigger_dtc']} "
                                      f"({info['description']}) was set:")
        for pid, v in ff["data"].items():
            name, unit, _ = decoder.PIDS[pid]
            tree.insert("", "end", values=(name, f"{v} {unit}".strip()))

    def _render_vehicle(self):
        tree = self.vehicle_tree
        tree.delete(*tree.get_children())
        s = self.scan
        st = s.get("status") or {}
        from .diagnose import inspection_readiness
        rows = [("section", "Vehicle", ""), ("", "VIN", s.get("vin") or "not reported"),
                ("", "Make / year (from VIN)", vinlib.describe(s.get("vin")) or "unknown"),
                ("", "Protocol", s["protocol"]), ("", "Adapter", s["adapter"]),
                ("", "Battery at OBD port", f"{s['adapter_voltage']:.1f} V" if s["adapter_voltage"] else "—"),
                ("", "Scanned at", s["timestamp"])]
        if st:
            insp = inspection_readiness(s)
            rows += [("", "Engine type", st["engine_type"]),
                     ("warn" if st["mil_on"] else "ok", "Check engine light", "ON" if st["mil_on"] else "OFF")]
            if insp:
                rows.append(("ok" if insp["pass"] else "warn", "Emissions inspection (US EPA guideline)",
                             "Likely to PASS" if insp["pass"] else "Likely to FAIL: " + "; ".join(insp["reasons"])))
            rows.append(("section", "Emissions readiness monitors", ""))
            for name, state in st["monitors"].items():
                rows.append(("ok" if state == "complete" else "warn", name,
                             "Ready" if state == "complete" else "Not ready"))
        tests = s.get("tests") or []
        if tests:
            from .monitor_tests import misfire_counts
            failed = sum(1 for t in tests if not t["passed"] and not 0xA1 <= t["mid"] <= 0xAD)
            rows.append(("section", f"Self-test results (OBD Mode 6): {failed} failed", ""))
            for t in tests:
                if 0xA1 <= t["mid"] <= 0xAD:
                    continue
                val = f"{t['value']} {t['unit']}".strip()
                rows.append(("ok" if t["passed"] else "warn", f"{t['monitor']} - {t['test']}",
                             f"{'Pass' if t['passed'] else 'FAILED'}: {val} (limits {t['min']} to {t['max']})"))
            counts = misfire_counts(tests)
            if counts:
                rows.append(("section", "Misfire counts (this / last drive)", ""))
                for cyl, n in sorted(counts.items()):
                    rows.append(("warn" if n else "ok", f"Cylinder {cyl}", f"{n} misfires"))
        info = s.get("ecu_info") or {}
        if info:
            rows.append(("section", "Computers (OBD Mode 9)", ""))
            for ecu, d in info.items():
                rows.append(("", ecu, d.get("name", "")))
                if d.get("calibration_ids"):
                    rows.append(("", "    Calibration (software) ID", ", ".join(d["calibration_ids"])))
                if d.get("checksums"):
                    rows.append(("", "    Calibration checksum (CVN)", ", ".join(d["checksums"])))
        for pid in (0x21, 0x30, 0x31, 0x1F, 0x4D, 0x4E, 0xA6, 0x51, 0x1C):
            if pid in s["live"]:
                if not any(r[1] == "History" for r in rows):
                    rows.append(("section", "History", ""))
                name, unit, _ = decoder.PIDS[pid]
                rows.append(("", name, f"{s['live'][pid]} {unit}".strip()))
        for tag, k, v in rows:
            tree.insert("", "end", values=(k, v), tags=(tag,) if tag else ())

    # ------------------------------------------------------------ menu, tools
    def _build_menu(self):
        menubar = tk.Menu(self.root)
        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Test adapter…", command=self.test_adapter)
        tools.add_command(label="Drive cycle helper…", command=self.drive_cycle_helper)
        tools.add_command(label="Relearn & reset guide…", command=self.relearn_guide)
        tools.add_command(label="Create desktop & Start Menu shortcuts", command=self.create_shortcuts)
        tools.add_separator()
        tools.add_command(label="Save support file…", command=self.save_support_file)
        tools.add_command(label="Open history folder", command=self.open_history_folder)
        tools.add_command(label="AI settings…", command=self.ai_settings)
        helpm = tk.Menu(menubar, tearoff=0)
        helpm.add_command(label="Check for updates…", command=lambda: self.check_for_updates(manual=True))
        self.auto_update_var = tk.BooleanVar(value=config.get("auto_update_check", True))
        helpm.add_checkbutton(label="Check for updates automatically", variable=self.auto_update_var,
                              command=lambda: config.set("auto_update_check", bool(self.auto_update_var.get())))
        helpm.add_separator()
        helpm.add_command(label="About OBD EFT", command=self.about)
        menubar.add_cascade(label="Tools", menu=tools)
        menubar.add_cascade(label="Help", menu=helpm)
        self.root.config(menu=menubar)

    # ------------------------------------------------------------ updates
    def _auto_update_check(self):
        if update.due_for_auto_check():
            self.check_for_updates(manual=False)

    def check_for_updates(self, manual=True):
        if manual and not update.is_configured():
            messagebox.showinfo("Check for updates",
                                f"You have OBD EFT {__version__}.\n\nAutomatic updates aren't set up for this copy "
                                "yet - get new versions from whoever gave you the app.", parent=self.root)
            return
        if manual:
            self._append_log("Checking for updates…")

        def job():
            try:
                self.ui_queue.put(("update", "ok", (manual, update.check())))
            except update.UpdateError as exc:
                self.ui_queue.put(("update", "error", (manual, exc)))
        threading.Thread(target=job, daemon=True).start()

    def _done_update(self, status, payload):
        manual, info = payload
        if status == "error":
            self._append_log(f"Update check: {info}")
            if manual:
                messagebox.showwarning("Check for updates", str(info), parent=self.root)
            return
        if not info:
            self._append_log(f"Update check: {__version__} is the latest version")
            if manual:
                messagebox.showinfo("Check for updates", f"You have the latest version ({__version__}).",
                                    parent=self.root)
            return
        self.update_info = info
        self._append_log(f"Update available: {info['version']}")
        self.update_label.config(text=f"\u2b06  OBD EFT {info['version']} is available "
                                      f"(you have {__version__}).")
        self.update_banner.pack(fill="x", after=self.top_frame)

    def _show_release_notes(self):
        info = self.update_info or {}
        notes = info.get("notes") or "No release notes were published for this version."
        messagebox.showinfo(f"What's new in {info.get('version', '')}", notes[:3000], parent=self.root)

    def install_update(self):
        info = self.update_info
        if not info:
            return
        if not update.is_installed_copy() or not info.get("installer_url"):
            # Portable copy (or no installer attached): open the download page instead
            import webbrowser
            url = info.get("portable_url") or info.get("page")
            if url:
                webbrowser.open(url)
            messagebox.showinfo("Update", "Your browser will open the download. Unzip the new version over the "
                                          "old OBDEFT folder (close the app first).", parent=self.root)
            return
        if not messagebox.askyesno("Update OBD EFT",
                                   f"Download and install OBD EFT {info['version']} now?\n\nOBD EFT will close "
                                   "while the installer runs. Your settings, API key and history are kept.",
                                   parent=self.root):
            return
        self.live_running = False
        win = tk.Toplevel(self.root)
        win.title("Downloading update")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        ttk.Label(win, text=f"Downloading OBD EFT {info['version']}…", padding=(16, 14, 16, 6)).pack(anchor="w")
        bar = ttk.Progressbar(win, mode="determinate", length=380, maximum=100)
        bar.pack(padx=16, pady=(0, 6))
        lbl = ttk.Label(win, text="", style="Muted.TLabel", padding=(16, 0, 16, 14))
        lbl.pack(anchor="w")
        self._upd_win, self._upd_bar, self._upd_lbl = win, bar, lbl

        def job():
            try:
                path = update.download_installer(
                    info, lambda done, total: self.ui_queue.put(("updprogress", "ok", (done, total))))
                self.ui_queue.put(("upddone", "ok", path))
            except update.UpdateError as exc:
                self.ui_queue.put(("upddone", "error", exc))
        threading.Thread(target=job, daemon=True).start()

    def _done_updprogress(self, status, payload):
        done, total = payload
        if total:
            self._upd_bar["value"] = 100 * done / total
            self._upd_lbl.config(text=f"{done / 1e6:.1f} of {total / 1e6:.1f} MB")
        else:
            self._upd_lbl.config(text=f"{done / 1e6:.1f} MB")

    def _done_upddone(self, status, payload):
        self._upd_win.destroy()
        if status == "error":
            self._error("Update", str(payload))
            return
        self._append_log(f"Update downloaded and verified: {payload}")
        try:
            update.launch_installer(payload)
        except (update.UpdateError, OSError) as exc:
            self._error("Update", str(exc))
            return
        self.on_close()  # close so the installer can replace the program files

    # ------------------------------------------------------------ drive cycle & relearn
    DRIVE_STEPS = [
        "Start cold: let the car sit for at least 8 hours (overnight) so the engine is fully cold. Keep the fuel "
        "tank between 1/4 and 3/4 full (the EVAP test needs this).",
        "Start the engine and idle for 2-3 minutes with the headlights and rear demister on.",
        "Drive in town for about 10 minutes at 30-50 km/h, with a few gentle stops.",
        "Accelerate gently onto an open road and hold a steady 80-100 km/h for 10-15 minutes.",
        "Coast down from about 90 to 30 km/h without braking or changing gear. Repeat 2-3 times where it's safe.",
        "Stop, idle for 2 minutes, then switch off. Some monitors (often EVAP) finish only after the car sits "
        "overnight.",
    ]

    def drive_cycle_helper(self):
        if not self.scanner:
            messagebox.showinfo("Drive cycle helper", "Connect to the car first. The helper then shows each "
                                "readiness monitor completing as you drive.", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("Drive cycle helper - OBD EFT")
        win.configure(bg=BG)
        win.geometry("760x620")
        t = tk.Text(win, wrap="word", bg=PANEL, fg=TEXT, relief="flat", padx=16, pady=14, font=self.f["normal"])
        t.pack(fill="both", expand=True, padx=12, pady=12)
        self._text_tags(t)
        t.tag_configure("ok", foreground=OK, font=self.f["bold"])
        t.tag_configure("no", foreground=SEV["medium"][0], font=self.f["bold"])
        self._dc_win, self._dc_text, self._dc_status = win, t, None
        self.live_running = False

        def render():
            t.config(state="normal")
            t.delete("1.0", "end")
            t.insert("end", "Readiness monitors\n", "h")
            st = self._dc_status
            if not st:
                t.insert("end", "Reading from the car…\n", "muted")
            else:
                mons = st.get("monitors", {})
                done = sum(1 for v in mons.values() if v == "complete")
                t.insert("end", f"{done} of {len(mons)} ready", "b")
                t.insert("end", f"   (updated {time.strftime('%H:%M:%S')})\n\n", "muted")
                for name, state in mons.items():
                    t.insert("end", " \u2713 " if state == "complete" else " \u25cb ", "ok" if state == "complete" else "no")
                    t.insert("end", f"{name}\n")
            t.insert("end", "\nGeneral drive cycle\n", "h")
            t.insert("end", "Safety first: follow road rules, and have a passenger watch this screen.\n", "b")
            for i, step in enumerate(self.DRIVE_STEPS, 1):
                t.insert("end", f"{i}. {step}\n", "num")
            t.insert("end", "\nThis is a general cycle that suits most cars; exact cycles vary by manufacturer. "
                            "Many monitors also complete during a few days of normal mixed driving.", "muted")
            t.config(state="disabled")

        def poll():
            if not win.winfo_exists() or not self.scanner:
                return
            sc = self.scanner
            self.worker.submit("drivecycle", lambda: sc.read_pid(0x01))
            win.after(5000, poll)

        self._dc_render = render
        render()
        poll()
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 12))

    def _done_drivecycle(self, status, payload):
        if status == "ok" and isinstance(payload, dict) and getattr(self, "_dc_win", None) is not None:
            try:
                if self._dc_win.winfo_exists():
                    self._dc_status = payload
                    self._dc_render()
            except tk.TclError:
                pass

    RELEARN = [
        ("After disconnecting or replacing the battery",
         ["Start the engine and let it idle for about 10 minutes with the A/C, lights and other loads off, "
          "without touching the accelerator, so the idle speed relearns.",
          "Then drive normally for 15-20 minutes with a mix of speeds.",
          "Reset the clock and radio, and the one-touch windows if needed (see the owner's manual).",
          "Some cars (many BMW, Mercedes, Audi/VW and newer Ford models with a battery monitor) need the new "
          "battery registered with a brand-capable scan tool. A workshop can do this."]),
        ("After cleaning or replacing the throttle body",
         ["Turn the ignition on (engine off) for 30 seconds, then off for 10 seconds. Many cars use this to learn "
          "the closed-throttle position.",
          "Start the engine and idle for 10 minutes without touching the accelerator.",
          "If the idle is still high, low or hunting, the car needs its own throttle relearn procedure (service "
          "manual) or a scan tool with that function."]),
        ("After a repair or clearing codes",
         ["The readiness monitors reset. Use Tools \u2192 Drive cycle helper and drive until they're ready again.",
          "Re-scan after a few days to make sure the code hasn't returned."]),
        ("After rotating or replacing tyres (tyre pressure sensors)",
         ["Set all tyres to the pressure on the door-pillar sticker.",
          "Many cars relearn by themselves after 10-20 minutes of driving above about 25 km/h.",
          "Others use a button or menu sequence in the owner's manual, or need a TPMS tool."]),
        ("Jobs that need a workshop or brand-specific tool",
         ["Steering angle sensor calibration (after a wheel alignment or steering work)",
          "Electronic park brake service mode (when replacing rear brake pads)",
          "ABS brake bleeding after the ABS unit is replaced",
          "Diesel injector coding, module coding and ECU programming",
          "Key and immobiliser programming",
          "OBD EFT doesn't do these: they write to the car's computers using manufacturer-specific procedures, "
          "and a mistake can disable a module."]),
    ]

    def relearn_guide(self):
        win = tk.Toplevel(self.root)
        win.title("Relearn & reset guide - OBD EFT")
        win.configure(bg=BG)
        win.geometry("820x640")
        t = tk.Text(win, wrap="word", bg=PANEL, fg=TEXT, relief="flat", padx=16, pady=14, font=self.f["normal"])
        sb = ttk.Scrollbar(win, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=12)
        sb.pack(side="left", fill="y", pady=12, padx=(0, 12))
        self._text_tags(t)
        t.insert("end", "General guidance - your owner's manual or service manual has the exact procedure "
                        "for your car.\n\n", "muted")
        for title, steps in self.RELEARN:
            t.insert("end", title + "\n", "h")
            for step in steps:
                t.insert("end", "\u2022  " + step + "\n", "bullet")
            t.insert("end", "\n")
        t.config(state="disabled")

    def about(self):
        messagebox.showinfo(
            "About OBD EFT",
            f"OBD EFT {__version__}\n\nReads your car's engine, ABS and airbag computers through an ELM327 "
            "OBD-II adapter and explains what's wrong.\n\nAI explanations by Anthropic's Claude (optional, uses your "
            f"own API key).\n\nSettings and history: {os.path.dirname(config.config_path())}\n\n"
            "Guidance only - not a substitute for a professional inspection.", parent=self.root)

    def test_adapter(self):
        if self.busy:
            return
        from .adaptercheck import check, check_target
        prog = lambda m: self.ui_queue.put(("progress", "ok", m))
        if self.elm:  # already connected: test the open adapter
            self.live_running = False
            elm = self.elm
            job = lambda: check(elm, prog)
        else:
            target = self.port_var.get().strip()
            if target in ("demo", "demo-gm", "demo-holden", "demo-bmw"):
                from .simulator import start_background
                target = f"127.0.0.1:{start_background(target[5:] or 'ford')}"
            elif target == "auto":
                from .elm327 import find_serial_adapter
                job0 = find_serial_adapter
                job = lambda: check_target(*job0(), progress=prog)
                target = None
            if target:
                job = lambda: check_target(target, progress=prog)
        self._set_busy(True, "Testing the adapter…")
        self.worker.submit("adaptertest", job)

    def _done_adaptertest(self, status, payload):
        self._set_busy(False)
        if status == "error":
            self._error("Adapter test", f"Couldn't open the adapter: {payload}\n\nCheck the port name and that "
                                        "no other app is using the adapter.")
            return
        from .adaptercheck import format_result
        self._append_log("Adapter test:\n" + format_result(payload))
        win = tk.Toplevel(self.root)
        win.title("Adapter test - OBD EFT")
        win.configure(bg=BG)
        win.geometry("900x480")
        t = tk.Text(win, wrap="word", bg=PANEL, fg=TEXT, relief="flat", padx=16, pady=14, font=self.f["normal"])
        t.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self._text_tags(t)
        t.tag_configure("ok", foreground=OK, font=self.f["bold"])
        t.tag_configure("no", foreground=SEV["high"][0], font=self.f["bold"])
        good = payload["verdict"].startswith(("Excellent", "Good"))
        t.insert("end", payload["version"] + (f" - {payload['description']}" if payload["description"] else "") + "\n", "h")
        t.insert("end", payload["verdict"] + "\n", "ok" if good else "medium")
        for n in payload["notes"]:
            t.insert("end", n + "\n", "muted")
        if payload["voltage"]:
            t.insert("end", f"Voltage at the adapter: {payload['voltage']:.1f} V\n", "muted")
        t.insert("end", "\n")
        for f in payload["features"]:
            t.insert("end", " \u2713 " if f["supported"] else " \u2717 ", "ok" if f["supported"] else "no")
            t.insert("end", f["feature"], "b")
            t.insert("end", f"  -  needed for {f['needed_for']}\n")
        if payload["car"]:
            t.insert("end", "\n" + payload["car"]["detail"], "ok" if payload["car"]["connected"] else "muted")
        t.config(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 12))

    def create_shortcuts(self):
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            messagebox.showinfo("Shortcuts", "Shortcuts can be created by the installed Windows app (OBDEFT.exe).",
                                parent=self.root)
            return
        import subprocess
        exe = sys.executable.replace("'", "''")
        folder = os.path.dirname(sys.executable).replace("'", "''")
        script = (
            "$w = New-Object -ComObject WScript.Shell; "
            "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { "
            "$s = $w.CreateShortcut((Join-Path $d 'OBD EFT.lnk')); "
            f"$s.TargetPath = '{exe}'; $s.WorkingDirectory = '{folder}'; $s.IconLocation = '{exe},0'; "
            "$s.Description = 'OBD EFT - car diagnostics'; $s.Save() }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                               capture_output=True, text=True, timeout=30, creationflags=0x08000000)
            ok = r.returncode == 0
            detail = (r.stderr or r.stdout or "").strip()[:300]
        except (OSError, subprocess.SubprocessError) as exc:
            ok, detail = False, str(exc)
        if ok:
            self._append_log("Created desktop and Start Menu shortcuts")
            messagebox.showinfo("Shortcuts", "Created an \u201cOBD EFT\u201d shortcut on your desktop and in the "
                                             "Start Menu.", parent=self.root)
        else:
            self._error("Shortcuts", "Couldn't create the shortcuts automatically.\n\nYou can make one by hand: "
                                     "right-click OBDEFT.exe \u2192 Show more options \u2192 Send to \u2192 Desktop "
                                     f"(create shortcut).\n\nDetails: {detail}")

    # ------------------------------------------------------------ live recording
    def toggle_record(self):
        if self.recording:
            self.recording = False
            self.rec_btn.config(text="\u25cf  Record")
            self.export_btn.config(state="normal" if self.rec_rows else "disabled")
            self.live_info.config(text=f"Recorded {len(self.rec_rows)} samples. Click Export CSV to save them.")
            return
        if not self.scanner:
            messagebox.showinfo("Record", "Connect to the car first.", parent=self.root)
            return
        self.rec_rows, self.rec_t0, self.recording = [], time.monotonic(), True
        self.rec_btn.config(text="\u25a0  Stop recording")
        self.export_btn.config(state="disabled")
        if not self.live_running:
            self.toggle_live()

    def export_recording(self):
        if not self.rec_rows:
            return
        import csv
        path = filedialog.asksaveasfilename(parent=self.root, title="Export recording",
                                            initialfile=f"obd-recording-{time.strftime('%Y%m%d-%H%M')}.csv",
                                            defaultextension=".csv", filetypes=[("CSV (Excel)", "*.csv")])
        if not path:
            return
        pids = [p for p in decoder.LIVE_PIDS if any(p in v for _, v in self.rec_rows)]
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Time (s)"] + [f"{decoder.PIDS[p][0]} ({decoder.PIDS[p][1]})" if decoder.PIDS[p][1]
                                       else decoder.PIDS[p][0] for p in pids])
            for t, vals in self.rec_rows:
                w.writerow([t] + [vals.get(p, "") for p in pids])
        self._append_log(f"Recording exported to {path} ({len(self.rec_rows)} samples)")
        messagebox.showinfo("Export", f"Saved {len(self.rec_rows)} samples to:\n{path}\n\nOpen it in Excel to make "
                                      "charts.", parent=self.root)

    # ------------------------------------------------------------ AI explanation
    def _ai_set_placeholder(self):
        t = self.ai_text
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", "Get a plain-English explanation of this scan\n", "h")
        msg = ("Click \u201cExplain with AI\u201d after a scan. Claude reads the trouble codes, freeze frame and live "
               "sensor data together, and explains what is most likely wrong, how urgent it is, what to check "
               "first (with rough costs) and what to tell a mechanic.\n\n"
               "Adding the vehicle and the symptoms you notice makes the answer more accurate. "
               "You can then ask follow-up questions, such as \u201cCan I fix this myself?\u201d.\n\n")
        t.insert("end", msg)
        if not ai.has_api_key():
            t.insert("end", "First, add your Anthropic API key in AI settings.", "b")
        t.config(state="disabled")

    def _ai_render(self, scroll="keep"):
        """Render the (simple) markdown in self.ai_raw into the text box.
        scroll: 'keep' (stay put), 'end' (follow streaming), 'top', or 'lastq' (show the latest question)."""
        t = self.ai_text
        first = t.yview()[0]
        t.config(state="normal")
        t.delete("1.0", "end")
        for line in self.ai_raw.split("\n"):
            stripped = line.strip()
            if stripped.startswith("\u2753 "):
                t.insert("end", stripped[2:] + "\n", "q")
                continue
            if stripped.startswith("#"):
                t.insert("end", stripped.lstrip("#").strip().replace("**", "") + "\n", "h")
                continue
            base = ()
            if stripped[:2] in ("- ", "* "):
                t.insert("end", "\u2022  ", "bullet")
                line, base = stripped[2:], ("bullet",)
            elif len(stripped) > 2 and stripped.split(".")[0].isdigit() and stripped[len(stripped.split(".")[0])] == ".":
                line, base = stripped, ("num",)
            parts = line.split("**")
            for i, part in enumerate(parts):
                if part:
                    t.insert("end", part, base + (("b",) if i % 2 else ()))
            t.insert("end", "\n", base)
        t.config(state="disabled")
        t.update_idletasks()
        if scroll == "end":
            t.see("end")
        elif scroll == "top":
            t.yview_moveto(0)
        elif scroll == "lastq" and t.tag_ranges("q"):
            t.yview(t.tag_ranges("q")[-2])
        else:
            t.yview_moveto(first)

    def _ai_start(self, status_text):
        if not ai.has_api_key():
            self.ai_settings(first_time=True)
            if not ai.has_api_key():
                return False
        self.ai_busy = True
        self.ai_status.config(text=status_text)
        self._update_buttons()
        return True

    def ai_explain(self):
        if not self.scan or self.ai_busy:
            return
        vehicle, symptoms = self.vehicle_var.get().strip(), self.symptoms_var.get().strip()
        if not self._ai_start("Claude is analyzing the scan… (this can take a little while)"):
            return
        config.set("vehicle", vehicle)
        self.nb.select(self.tab_ai)
        self.ai_raw = ""
        self.ai_text.config(state="normal")
        self.ai_text.delete("1.0", "end")
        self.ai_text.insert("end", "Thinking…", "muted")
        self.ai_text.config(state="disabled")
        scan, findings, explainer = self.scan, self.findings, self.ai
        self.ai_worker.submit("ai", lambda: explainer.explain(
            scan, findings, vehicle, symptoms, on_text=lambda t: self.ui_queue.put(("ai_text", "ok", t))))

    def ai_ask(self):
        q = self.ask_var.get().strip()
        if not q or self.ai_busy or not self.ai.messages:
            return
        if not self._ai_start("Claude is answering…"):
            return
        self.ask_var.set("")
        self.ai_raw += f"\n\n\u2753 {q}\n\n"
        self._ai_render("end")
        explainer = self.ai
        self.ai_worker.submit("ai", lambda: explainer.ask(q, on_text=lambda t: self.ui_queue.put(("ai_text", "ok", t))))

    def _done_ai(self, status, payload):
        self.ai_busy = False
        self._update_buttons()
        if status == "error":
            self.ai_status.config(text="")
            # drop the unanswered question / partial output from the view
            self.ai_raw = self.ai.transcript_text_for_view() if self.ai.transcript else ""
            if self.ai_raw:
                self._ai_render()
            else:
                self._ai_set_placeholder()
            msg = str(payload) if isinstance(payload, ai.AIError) else f"Unexpected error: {payload}"
            self._error("AI explanation", msg)
            return
        self.ai_status.config(text="Done. Ask a follow-up question below.")
        self._append_log("AI explanation received")
        self._ai_render("top" if len(self.ai.transcript) == 1 else "lastq")

    def ai_settings(self, first_time=False):
        win = tk.Toplevel(self.root)
        win.title("AI settings")
        win.configure(bg=BG)
        win.transient(self.root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Anthropic API key", font=self.f["h2"]).pack(anchor="w")
        intro = ("AI explanations use Anthropic's Claude. You need your own API key: create one at "
                 "console.anthropic.com (API keys). Each explanation costs a few cents.")
        if first_time:
            intro = "To use AI explanations, add an Anthropic API key first.\n\n" + intro
        ttk.Label(body, text=intro, wraplength=460, justify="left").pack(anchor="w", pady=(4, 10))
        key_var = tk.StringVar(value=config.get("anthropic_api_key", ""))
        entry = ttk.Entry(body, textvariable=key_var, width=58, show="\u2022")
        entry.pack(anchor="w", fill="x")
        entry.focus_set()
        show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="Show key", variable=show_var,
                        command=lambda: entry.config(show="" if show_var.get() else "\u2022")).pack(anchor="w", pady=4)
        note = f"Saved on this computer only, in {config.config_path()}"
        if os.environ.get("ANTHROPIC_API_KEY"):
            note += "\nNote: the ANTHROPIC_API_KEY environment variable is set and takes priority."
        ttk.Label(body, text=note, style="Muted.TLabel", wraplength=460, justify="left").pack(anchor="w", pady=(4, 12))
        btns = ttk.Frame(body)
        btns.pack(fill="x")

        def save():
            key = key_var.get().strip()
            if key and not key.startswith("sk-ant-"):
                if not messagebox.askyesno("API key", "That doesn't look like an Anthropic API key "
                                           "(they start with \u201csk-ant-\u201d). Save it anyway?", parent=win):
                    return
            config.set("anthropic_api_key", key)
            self.ai = ai.AIExplainer() if not self.ai.messages else self.ai
            self.ai._client = None  # pick up the new key
            if not self.ai_raw:
                self._ai_set_placeholder()
            win.destroy()

        ttk.Button(btns, text="Save", style="Accent.TButton", command=save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=8)
        ttk.Button(btns, text="Remove key", command=lambda: (key_var.set(""), save())).pack(side="left")
        entry.bind("<Return>", lambda _e: save())
        win.grab_set()
        self.root.wait_window(win)

    # ------------------------------------------------------------ save
    def save_report(self):
        if not self.scan:
            return
        from .__main__ import build_json, text_report
        stamp = time.strftime("%Y%m%d-%H%M")
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save diagnostic report", initialfile=f"obd-report-{stamp}.txt",
            defaultextension=".txt", filetypes=[("Text report", "*.txt"), ("JSON data", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            if path.lower().endswith(".json"):
                data = build_json(self.scan, self.findings)
                if self.ai.transcript:
                    data["ai_explanation"] = [{"role": r, "text": t} for r, t in self.ai.transcript]
                json.dump(data, fh, indent=2, ensure_ascii=False)
            else:
                fh.write(text_report(self.scan, self.findings))
                if self.ai.transcript:
                    fh.write("\n== AI EXPLANATION (Claude) " + "=" * 37 + "\n\n" + self.ai.transcript_text() + "\n")
        self._append_log(f"Report saved to {path}")

    def save_support_file(self):
        from .support import default_filename, write_support_file
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save support file", initialfile=default_filename(),
            defaultextension=".zip", filetypes=[("Support file", "*.zip")])
        if not path:
            return
        try:
            write_support_file(path, self.scan, self.findings, self.log_text.get("1.0", "end"),
                               self.elm, self.port_var.get(), self.ai.transcript or None)
        except Exception as exc:
            self._error("Support file", f"Couldn't save the support file: {exc}")
            return
        self._append_log(f"Support file saved to {path}")
        messagebox.showinfo(
            "Support file saved",
            f"Saved:\n{path}\n\nIt contains the scan report, the app log, recent adapter messages and "
            "system details. Your API key is not included, and your VIN is partly masked.\n\n"
            "Send this file to whoever is helping you troubleshoot.", parent=self.root)

    # ------------------------------------------------------------ shutdown
    def on_close(self):
        self.live_running = False
        if self.elm:
            try:
                self.elm.close()
            except Exception:
                pass
        self.root.destroy()


def _set_icon(root):
    import os
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    try:
        if sys.platform == "win32":
            root.iconbitmap(default=os.path.join(assets, "icon.ico"))
        else:
            root.iconphoto(True, tk.PhotoImage(file=os.path.join(assets, "icon.png")))
    except Exception:
        pass


def _broken_install(exc):
    """Explain a damaged installation in plain words and offer the download page."""
    url = "https://github.com/Dombrohub/obd-doctor/releases/latest"
    msg = ("OBD EFT can't start because some of its files are missing - usually after an installation or update "
           "was interrupted.\n\nPlease reinstall it: download OBDEFT-Setup.exe and run it. Your settings and scan "
           "history are kept.\n\nOpen the download page now?")
    if sys.platform == "win32":
        import ctypes
        if ctypes.windll.user32.MessageBoxW(None, msg, "OBD EFT - please reinstall", 0x04 | 0x30) == 6:  # Yes
            import webbrowser
            webbrowser.open(url)
    else:
        print(f"{msg}\n{url}\n(details: {exc})", file=sys.stderr)


def run():
    try:  # sharper text on Windows high-DPI screens (must happen before Tk starts)
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # e.g. an interrupted install left the window-drawing files missing
        _broken_install(exc)
        return
    _set_icon(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    run()
