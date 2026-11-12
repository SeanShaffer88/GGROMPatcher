"""Cross-platform Tkinter/ttk desktop GUI for GGROMPatcher."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from ggrompatcher.gui.controller import GuiController, PatchRowView
from ggrompatcher.models import ConsoleSystem, GGPatcherError, MatchStatus
from ggrompatcher.patcher import default_patched_path

_SYSTEM_OPTIONS: list[tuple[str, ConsoleSystem | None]] = [
    ("Auto-detect", None),
    ("Game Boy / GBC", ConsoleSystem.GAMEBOY),
    ("Sega Game Gear", ConsoleSystem.GAMEGEAR),
    ("Sega Master System", ConsoleSystem.MASTERSYSTEM),
    ("NES / Famicom", ConsoleSystem.NES),
    ("Super Nintendo (SNES)", ConsoleSystem.SNES),
    ("Sega Genesis / Mega Drive", ConsoleSystem.GENESIS),
    ("Raw Hex (File Offset)", ConsoleSystem.RAW),
]

_ROM_FILETYPES = [
    (
        "Supported ROMs",
        "*.gb *.gbc *.sgb *.gg *.sms *.nes *.sfc *.smc *.swc *.fig *.bin *.md *.gen *.smd",
    ),
    ("Game Boy / GBC", "*.gb *.gbc *.sgb"),
    ("Sega Game Gear / Master System", "*.gg *.sms"),
    ("NES / Famicom", "*.nes"),
    ("Super Nintendo", "*.sfc *.smc *.swc *.fig"),
    ("Sega Genesis / Mega Drive", "*.bin *.md *.gen *.smd *.68k"),
    ("All files", "*.*"),
]

_BG = "#0F172A"
_CARD = "#1E293B"
_SURFACE = "#283548"
_BORDER = "#334155"
_TEXT = "#F8FAFC"
_MUTED = "#94A3B8"
_ACCENT = "#38BDF8"
_ACCENT_HOVER = "#0EA5E9"
_SUCCESS = "#22C55E"
_WARN = "#F59E0B"
_ERR = "#EF4444"


class GGROMPatcherApp(tk.Tk):
    """Main desktop application window."""

    def __init__(self, initial_rom: str | None = None) -> None:
        super().__init__()
        self.controller = GuiController()
        self._row_views: list[PatchRowView] = []
        self._candidate_map: dict[str, int | None] = {}

        self.title("GGROMPatcher — Game Genie ROM Patcher & Studio")
        self.geometry("1120x780")
        self.minsize(920, 660)
        self.configure(bg=_BG)

        self._configure_styles()
        self._build_variables()
        self._build_ui()
        self._bind_shortcuts()
        self._log("GGROMPatcher ready. Open a ROM file (Ctrl+O) or explore the Decoder/Encoder.")

        if initial_rom:
            self.after(50, lambda: self._load_rom_path(initial_rom))

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        mono_family = (
            "Consolas" if self.tk.call("tk", "windowingsystem") == "win32" else "TkFixedFont"
        )
        self._mono_font = (mono_family, 10)
        self._ui_font = (
            ("Segoe UI", 10)
            if self.tk.call("tk", "windowingsystem") == "win32"
            else ("TkDefaultFont", 10)
        )

        style.configure(
            ".", background=_BG, foreground=_TEXT, fieldbackground=_SURFACE, bordercolor=_BORDER
        )
        style.configure("TFrame", background=_BG)
        style.configure("Card.TFrame", background=_CARD, relief="flat")
        style.configure("TLabel", background=_BG, foreground=_TEXT)
        style.configure("Card.TLabel", background=_CARD, foreground=_TEXT)
        style.configure("Muted.TLabel", background=_CARD, foreground=_MUTED)
        style.configure(
            "Header.TLabel", background=_BG, foreground=_ACCENT, font=(self._ui_font[0], 15, "bold")
        )
        style.configure(
            "SubHeader.TLabel",
            background=_CARD,
            foreground=_ACCENT,
            font=(self._ui_font[0], 10, "bold"),
        )

        style.configure(
            "TButton",
            background=_SURFACE,
            foreground=_TEXT,
            borderwidth=1,
            focusthickness=0,
            padding=(10, 5),
        )
        style.map("TButton", background=[("active", _BORDER), ("disabled", "#172033")])

        style.configure(
            "Primary.TButton",
            background=_ACCENT,
            foreground="#082F49",
            font=(self._ui_font[0], 10, "bold"),
            padding=(12, 6),
        )
        style.map(
            "Primary.TButton", background=[("active", _ACCENT_HOVER), ("disabled", "#1E3A5F")]
        )

        style.configure("TCheckbutton", background=_CARD, foreground=_TEXT)
        style.map("TCheckbutton", background=[("active", _CARD)])

        style.configure(
            "TEntry",
            fieldbackground=_SURFACE,
            foreground=_TEXT,
            insertcolor=_TEXT,
            bordercolor=_BORDER,
            padding=4,
        )
        style.configure(
            "TCombobox",
            fieldbackground=_SURFACE,
            background=_SURFACE,
            foreground=_TEXT,
            arrowcolor=_TEXT,
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", _SURFACE)],
            selectbackground=[("readonly", _SURFACE)],
        )

        style.configure("TNotebook", background=_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=_CARD, foreground=_MUTED, padding=(14, 7))
        style.map(
            "TNotebook.Tab",
            background=[("selected", _SURFACE)],
            foreground=[("selected", _TEXT)],
        )

        style.configure(
            "Treeview",
            background=_SURFACE,
            fieldbackground=_SURFACE,
            foreground=_TEXT,
            rowheight=26,
            borderwidth=0,
            font=self._mono_font,
        )
        style.configure(
            "Treeview.Heading",
            background=_CARD,
            foreground=_ACCENT,
            relief="flat",
            padding=(6, 5),
            font=(self._ui_font[0], 9, "bold"),
        )
        style.map(
            "Treeview", background=[("selected", "#1D4ED8")], foreground=[("selected", _TEXT)]
        )

    def _build_variables(self) -> None:
        self.rom_path_var = tk.StringVar(value="")
        self.system_var = tk.StringVar(value="Auto-detect")
        self.meta_title_var = tk.StringVar(value="No ROM loaded")
        self.meta_format_var = tk.StringVar(value="Format: —")
        self.meta_size_var = tk.StringVar(value="Size: —")
        self.meta_chk_var = tk.StringVar(value="Checksum: —")

        self.code_input_var = tk.StringVar()
        self.code_desc_var = tk.StringVar()
        self.live_decode_var = tk.StringVar(
            value="Load a ROM or select a system, then enter a code."
        )

        self.bank_choice_var = tk.StringVar(value="")
        self.fix_chk_var = tk.BooleanVar(value=True)
        self.backup_var = tk.BooleanVar(value=True)
        self.all_matches_var = tk.BooleanVar(value=False)
        self.force_var = tk.BooleanVar(value=False)
        self.skip_invalid_var = tk.BooleanVar(value=False)
        self.snes_map_var = tk.StringVar(value="auto")
        self.nes_bank_var = tk.StringVar(value="auto")

        # Decoder / Encoder tab variables
        self.conv_system_var = tk.StringVar(value="Game Boy / GBC")
        self.conv_decode_in_var = tk.StringVar()
        self.conv_decode_out_var = tk.StringVar(value="Enter a Game Genie code to decode.")
        self.conv_addr_var = tk.StringVar()
        self.conv_val_var = tk.StringVar()
        self.conv_cmp_var = tk.StringVar()
        self.conv_encode_out_var = tk.StringVar(value="")

        # IPS tab variables
        self.ips_apply_rom_var = tk.StringVar()
        self.ips_apply_patch_var = tk.StringVar()
        self.ips_apply_chk_var = tk.BooleanVar(value=True)
        self.ips_create_orig_var = tk.StringVar()
        self.ips_create_mod_var = tk.StringVar()

        self.code_input_var.trace_add("write", lambda *_: self._on_live_code_change())
        self.conv_decode_in_var.trace_add("write", lambda *_: self._on_conv_decode())

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Top ROM Header Bar
        top_bar = ttk.Frame(self, padding=(14, 12, 14, 6))
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.columnconfigure(1, weight=1)

        ttk.Label(top_bar, text="GGROMPatcher", style="Header.TLabel").grid(
            row=0, column=0, padx=(0, 14), sticky="w"
        )
        rom_entry = ttk.Entry(top_bar, textvariable=self.rom_path_var, font=self._mono_font)
        rom_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(top_bar, text="Open ROM...", command=self._on_browse_rom).grid(
            row=0, column=2, padx=(0, 8)
        )

        sys_combo = ttk.Combobox(
            top_bar,
            textvariable=self.system_var,
            values=[label for label, _ in _SYSTEM_OPTIONS],
            state="readonly",
            width=24,
        )
        sys_combo.grid(row=0, column=3, padx=(0, 8))
        sys_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_system_changed())

        ttk.Button(top_bar, text="Fix Checksum", command=self._on_fix_checksum_only).grid(
            row=0, column=4
        )

        # Metadata Strip
        meta_card = ttk.Frame(self, style="Card.TFrame", padding=(12, 8))
        meta_card.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        for col in range(4):
            meta_card.columnconfigure(col, weight=1)

        ttk.Label(meta_card, textvariable=self.meta_title_var, style="SubHeader.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(meta_card, textvariable=self.meta_format_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(meta_card, textvariable=self.meta_size_var, style="Muted.TLabel").grid(
            row=0, column=2, sticky="w"
        )
        self.chk_label = ttk.Label(meta_card, textvariable=self.meta_chk_var, style="Card.TLabel")
        self.chk_label.grid(row=0, column=3, sticky="e")

        # Main Notebook
        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 8))

        self.patch_tab = ttk.Frame(notebook, padding=8)
        self.conv_tab = ttk.Frame(notebook, padding=12)
        self.ips_tab = ttk.Frame(notebook, padding=12)

        notebook.add(self.patch_tab, text="  Patch ROM  ")
        notebook.add(self.conv_tab, text="  Decoder / Encoder  ")
        notebook.add(self.ips_tab, text="  IPS Tools  ")

        self._build_patch_tab()
        self._build_converter_tab()
        self._build_ips_tab()

        # Activity Log Panel
        log_frame = ttk.Frame(self, style="Card.TFrame", padding=(10, 6))
        log_frame.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))
        log_frame.columnconfigure(0, weight=1)

        ttk.Label(log_frame, text="Activity Log", style="SubHeader.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self.log_text = tk.Text(
            log_frame,
            height=5,
            bg=_SURFACE,
            fg=_TEXT,
            insertbackground=_TEXT,
            relief="flat",
            font=self._mono_font,
            state="disabled",
            wrap="word",
        )
        self.log_text.grid(row=1, column=0, sticky="ew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _build_patch_tab(self) -> None:
        tab = self.patch_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        # Code Entry Card
        entry_card = ttk.Frame(tab, style="Card.TFrame", padding=10)
        entry_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        entry_card.columnconfigure(1, weight=2)
        entry_card.columnconfigure(3, weight=1)

        ttk.Label(entry_card, text="Code(s):", style="Card.TLabel").grid(
            row=0, column=0, padx=(0, 6), sticky="w"
        )
        code_entry = ttk.Entry(entry_card, textvariable=self.code_input_var, font=self._mono_font)
        code_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        code_entry.bind("<Return>", lambda _e: self._on_add_code())

        ttk.Label(entry_card, text="Note (opt):", style="Card.TLabel").grid(
            row=0, column=2, padx=(0, 6), sticky="w"
        )
        desc_entry = ttk.Entry(entry_card, textvariable=self.code_desc_var)
        desc_entry.grid(row=0, column=3, sticky="ew", padx=(0, 8))
        desc_entry.bind("<Return>", lambda _e: self._on_add_code())

        ttk.Button(
            entry_card, text="Add Code", style="Primary.TButton", command=self._on_add_code
        ).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(
            entry_card, text="Import File (.txt/.gg/.cht)...", command=self._on_import_file
        ).grid(row=0, column=5)

        self.live_decode_label = ttk.Label(
            entry_card,
            textvariable=self.live_decode_var,
            style="Muted.TLabel",
            font=self._mono_font,
        )
        self.live_decode_label.grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

        # Patch Queue Table
        table_card = ttk.Frame(tab, style="Card.TFrame", padding=8)
        table_card.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(0, weight=1)

        cols = ("code", "desc", "addr", "val", "cmp", "offset", "cur", "status")
        self.tree = ttk.Treeview(table_card, columns=cols, show="headings", selectmode="extended")
        headings = [
            ("code", "Code", 125),
            ("desc", "Description", 170),
            ("addr", "Address", 95),
            ("val", "New", 65),
            ("cmp", "Compare", 75),
            ("offset", "Target Offset", 145),
            ("cur", "Current", 80),
            ("status", "Status", 140),
        ]
        for cid, label, width in headings:
            self.tree.heading(cid, text=label)
            self.tree.column(
                cid,
                width=width,
                anchor="w" if cid in {"code", "desc", "offset", "status"} else "center",
            )

        self.tree.tag_configure("ready", foreground=_SUCCESS)
        self.tree.tag_configure("already", foreground=_ACCENT)
        self.tree.tag_configure("ambiguous", foreground=_WARN)
        self.tree.tag_configure("error", foreground=_ERR)
        self.tree.tag_configure("disabled", foreground=_MUTED)

        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_row_selected())
        self.tree.bind("<Double-1>", lambda _e: self._on_toggle_selected())

        # Table Toolbar & Bank Resolver
        bar = ttk.Frame(table_card, style="Card.TFrame", padding=(0, 8, 0, 0))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(4, weight=1)

        ttk.Button(bar, text="Enable / Disable", command=self._on_toggle_selected).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(bar, text="Remove Selected", command=self._on_remove_selected).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(bar, text="Clear Queue", command=self._on_clear_queue).grid(
            row=0, column=2, padx=(0, 14)
        )

        ttk.Label(bar, text="Bank / Offset:", style="Card.TLabel").grid(
            row=0, column=3, padx=(0, 6)
        )
        self.bank_combo = ttk.Combobox(
            bar, textvariable=self.bank_choice_var, state="disabled", width=34, font=self._mono_font
        )
        self.bank_combo.grid(row=0, column=4, sticky="ew", padx=(0, 6))
        self.bank_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_bank_selected())

        # Options & Save Actions Card
        bottom = ttk.Frame(tab, style="Card.TFrame", padding=10)
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        opts = ttk.Frame(bottom, style="Card.TFrame")
        opts.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ttk.Checkbutton(
            opts, text="Auto-fix Checksum", variable=self.fix_chk_var, command=self._sync_options
        ).grid(row=0, column=0, padx=(0, 12), sticky="w")
        ttk.Checkbutton(
            opts,
            text="Backup (.bak) In-Place",
            variable=self.backup_var,
            command=self._sync_options,
        ).grid(row=0, column=1, padx=(0, 12), sticky="w")
        ttk.Checkbutton(
            opts,
            text="Patch All Matching Banks",
            variable=self.all_matches_var,
            command=lambda: self._sync_options(refresh_table=True),
        ).grid(row=0, column=2, padx=(0, 12), sticky="w")
        ttk.Checkbutton(
            opts,
            text="Force on Compare Mismatch",
            variable=self.force_var,
            command=lambda: self._sync_options(refresh_table=True),
        ).grid(row=0, column=3, padx=(0, 12), sticky="w")
        ttk.Checkbutton(
            opts,
            text="Skip Invalid Codes",
            variable=self.skip_invalid_var,
            command=self._sync_options,
        ).grid(row=0, column=4, padx=(0, 12), sticky="w")

        adv = ttk.Frame(bottom, style="Card.TFrame")
        adv.grid(row=1, column=0, sticky="ew")
        adv.columnconfigure(4, weight=1)

        ttk.Label(adv, text="SNES Map:", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 4))
        snes_cb = ttk.Combobox(
            adv,
            textvariable=self.snes_map_var,
            values=["auto", "lorom", "hirom", "exhirom", "exlorom"],
            state="readonly",
            width=10,
        )
        snes_cb.grid(row=0, column=1, padx=(0, 12))
        snes_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_snes_map_changed())

        ttk.Label(adv, text="NES Bank:", style="Muted.TLabel").grid(row=0, column=2, padx=(0, 4))
        nes_cb = ttk.Combobox(
            adv,
            textvariable=self.nes_bank_var,
            values=["auto", "8k", "16k", "32k"],
            state="readonly",
            width=8,
        )
        nes_cb.grid(row=0, column=3, padx=(0, 12))
        nes_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_nes_bank_changed())

        btns = ttk.Frame(adv, style="Card.TFrame")
        btns.grid(row=0, column=4, sticky="e")
        ttk.Button(btns, text="Export .IPS Patch...", command=self._on_export_ips).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(btns, text="Patch In-Place", command=self._on_patch_inplace).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(
            btns,
            text="Patch & Save ROM As...",
            style="Primary.TButton",
            command=self._on_patch_save_as,
        ).pack(side="right")

    def _build_converter_tab(self) -> None:
        tab = self.conv_tab
        tab.columnconfigure(0, weight=1)

        # System selector
        sys_row = ttk.Frame(tab, style="Card.TFrame", padding=10)
        sys_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(sys_row, text="Console Format:", style="SubHeader.TLabel").pack(
            side="left", padx=(0, 10)
        )
        conv_combo = ttk.Combobox(
            sys_row,
            textvariable=self.conv_system_var,
            values=[label for label, sys in _SYSTEM_OPTIONS if sys is not None],
            state="readonly",
            width=28,
        )
        conv_combo.pack(side="left")
        conv_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_conv_decode())

        # Decode Card
        dec_card = ttk.Frame(tab, style="Card.TFrame", padding=12)
        dec_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        dec_card.columnconfigure(1, weight=1)

        ttk.Label(dec_card, text="Decode Game Genie Code", style="SubHeader.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        ttk.Label(dec_card, text="Code:", style="Card.TLabel").grid(
            row=1, column=0, padx=(0, 8), sticky="w"
        )
        ttk.Entry(dec_card, textvariable=self.conv_decode_in_var, font=self._mono_font).grid(
            row=1, column=1, sticky="ew"
        )
        ttk.Label(
            dec_card,
            textvariable=self.conv_decode_out_var,
            style="Card.TLabel",
            font=self._mono_font,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Encode Card
        enc_card = ttk.Frame(tab, style="Card.TFrame", padding=12)
        enc_card.grid(row=2, column=0, sticky="ew")
        enc_card.columnconfigure(1, weight=1)
        enc_card.columnconfigure(3, weight=1)
        enc_card.columnconfigure(5, weight=1)

        ttk.Label(
            enc_card, text="Encode Hex Address / Bytes to Game Genie Code", style="SubHeader.TLabel"
        ).grid(row=0, column=0, columnspan=7, sticky="w", pady=(0, 8))
        ttk.Label(enc_card, text="Address (hex):", style="Card.TLabel").grid(
            row=1, column=0, padx=(0, 6)
        )
        ttk.Entry(enc_card, textvariable=self.conv_addr_var, font=self._mono_font, width=14).grid(
            row=1, column=1, sticky="ew", padx=(0, 10)
        )
        ttk.Label(enc_card, text="New Value (hex):", style="Card.TLabel").grid(
            row=1, column=2, padx=(0, 6)
        )
        ttk.Entry(enc_card, textvariable=self.conv_val_var, font=self._mono_font, width=10).grid(
            row=1, column=3, sticky="ew", padx=(0, 10)
        )
        ttk.Label(enc_card, text="Compare (opt):", style="Card.TLabel").grid(
            row=1, column=4, padx=(0, 6)
        )
        ttk.Entry(enc_card, textvariable=self.conv_cmp_var, font=self._mono_font, width=10).grid(
            row=1, column=5, sticky="ew", padx=(0, 10)
        )
        ttk.Button(
            enc_card, text="Encode", style="Primary.TButton", command=self._on_conv_encode
        ).grid(row=1, column=6)

        out_row = ttk.Frame(enc_card, style="Card.TFrame")
        out_row.grid(row=2, column=0, columnspan=7, sticky="ew", pady=(10, 0))
        ttk.Label(out_row, text="Generated Code:", style="Muted.TLabel").pack(
            side="left", padx=(0, 8)
        )
        ttk.Entry(
            out_row,
            textvariable=self.conv_encode_out_var,
            font=self._mono_font,
            state="readonly",
            width=24,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(out_row, text="Copy", command=self._on_copy_encoded).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(out_row, text="Add to Queue", command=self._on_send_encoded_to_queue).pack(
            side="left"
        )

    def _build_ips_tab(self) -> None:
        tab = self.ips_tab
        tab.columnconfigure(0, weight=1)

        apply_card = ttk.Frame(tab, style="Card.TFrame", padding=12)
        apply_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        apply_card.columnconfigure(1, weight=1)

        ttk.Label(
            apply_card, text="Apply Existing .IPS Patch to ROM", style="SubHeader.TLabel"
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(apply_card, text="Base ROM:", style="Card.TLabel").grid(
            row=1, column=0, padx=(0, 8), sticky="w"
        )
        ttk.Entry(apply_card, textvariable=self.ips_apply_rom_var, font=self._mono_font).grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=3
        )
        ttk.Button(
            apply_card,
            text="Browse...",
            command=lambda: self._browse_into(self.ips_apply_rom_var, _ROM_FILETYPES),
        ).grid(row=1, column=2, pady=3)

        ttk.Label(apply_card, text=".IPS File:", style="Card.TLabel").grid(
            row=2, column=0, padx=(0, 8), sticky="w"
        )
        ttk.Entry(apply_card, textvariable=self.ips_apply_patch_var, font=self._mono_font).grid(
            row=2, column=1, sticky="ew", padx=(0, 8), pady=3
        )
        ttk.Button(
            apply_card,
            text="Browse...",
            command=lambda: self._browse_into(
                self.ips_apply_patch_var, [("IPS Patches", "*.ips"), ("All files", "*.*")]
            ),
        ).grid(row=2, column=2, pady=3)

        ttk.Checkbutton(
            apply_card,
            text="Auto-fix console header checksum after applying IPS",
            variable=self.ips_apply_chk_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(
            apply_card,
            text="Apply & Save ROM As...",
            style="Primary.TButton",
            command=self._on_ips_apply,
        ).grid(row=3, column=2, sticky="e", pady=(8, 0))

        create_card = ttk.Frame(tab, style="Card.TFrame", padding=12)
        create_card.grid(row=1, column=0, sticky="ew")
        create_card.columnconfigure(1, weight=1)

        ttk.Label(
            create_card,
            text="Create .IPS Patch from Original & Modified ROMs",
            style="SubHeader.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(create_card, text="Original ROM:", style="Card.TLabel").grid(
            row=1, column=0, padx=(0, 8), sticky="w"
        )
        ttk.Entry(create_card, textvariable=self.ips_create_orig_var, font=self._mono_font).grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=3
        )
        ttk.Button(
            create_card,
            text="Browse...",
            command=lambda: self._browse_into(self.ips_create_orig_var, _ROM_FILETYPES),
        ).grid(row=1, column=2, pady=3)

        ttk.Label(create_card, text="Modified ROM:", style="Card.TLabel").grid(
            row=2, column=0, padx=(0, 8), sticky="w"
        )
        ttk.Entry(create_card, textvariable=self.ips_create_mod_var, font=self._mono_font).grid(
            row=2, column=1, sticky="ew", padx=(0, 8), pady=3
        )
        ttk.Button(
            create_card,
            text="Browse...",
            command=lambda: self._browse_into(self.ips_create_mod_var, _ROM_FILETYPES),
        ).grid(row=2, column=2, pady=3)

        ttk.Button(
            create_card,
            text="Create .IPS File...",
            style="Primary.TButton",
            command=self._on_ips_create,
        ).grid(row=3, column=2, sticky="e", pady=(8, 0))

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda _e: self._on_browse_rom())
        self.bind("<Control-i>", lambda _e: self._on_import_file())
        self.bind("<Control-s>", lambda _e: self._on_patch_save_as())

    def _log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _system_from_label(self, label: str) -> ConsoleSystem | None:
        for opt_label, sys_val in _SYSTEM_OPTIONS:
            if opt_label == label:
                return sys_val
        return None

    def _on_browse_rom(self) -> None:
        path = filedialog.askopenfilename(title="Select ROM File", filetypes=_ROM_FILETYPES)
        if path:
            self._load_rom_path(path)

    def _load_rom_path(self, path: str) -> None:
        try:
            info = self.controller.load_rom(path)
            self.rom_path_var.set(path)
            if not self.ips_apply_rom_var.get():
                self.ips_apply_rom_var.set(path)
            if not self.ips_create_orig_var.get():
                self.ips_create_orig_var.set(path)
            self._update_metadata_banner()
            self._sync_converter_system(info.system)
            self._refresh_table()
            self._on_live_code_change()
            self._log(
                f"Loaded '{Path(path).name}' — {info.system.display_name}, "
                f"{info.header_format}, {info.file_size:,} bytes."
            )
        except Exception as exc:
            messagebox.showerror("Error Loading ROM", str(exc))
            self._log(f"ERROR loading ROM: {exc}")

    def _sync_converter_system(self, system: ConsoleSystem) -> None:
        for label, sys_val in _SYSTEM_OPTIONS:
            if sys_val == system:
                self.conv_system_var.set(label)
                break

    def _update_metadata_banner(self) -> None:
        info = self.controller.rom_info
        if info is None:
            return
        sys_tag = info.system.display_name
        if self.controller.system_override is None:
            sys_tag = f"{sys_tag} (auto)"
        self.meta_title_var.set(f"{info.title}  —  {sys_tag}")
        self.meta_format_var.set(f"Format: {info.header_format}")
        self.meta_size_var.set(f"Size: {info.file_size:,} bytes")

        if not info.checksums:
            self.meta_chk_var.set("Checksum: N/A")
            self.chk_label.configure(foreground=_MUTED)
        else:
            all_ok = all(c.valid for c in info.checksums)
            parts = [
                f"{c.name.split()[0]}=0x{c.stored:0{c.width * 2}X}({'OK' if c.valid else 'BAD'})"
                for c in info.checksums
            ]
            self.meta_chk_var.set("Checksum: " + ", ".join(parts))
            self.chk_label.configure(foreground=_SUCCESS if all_ok else _WARN)

    def _on_system_changed(self) -> None:
        selected = self._system_from_label(self.system_var.get())
        try:
            self.controller.set_system_override(selected)
            self._update_metadata_banner()
            self._refresh_table()
            self._on_live_code_change()
            if selected:
                self._sync_converter_system(selected)
        except Exception as exc:
            messagebox.showerror("System Switch Error", str(exc))

    def _on_snes_map_changed(self) -> None:
        self.controller.set_snes_map(self.snes_map_var.get())
        self._update_metadata_banner()
        self._refresh_table()

    def _on_nes_bank_changed(self) -> None:
        self.controller.set_nes_bank_size(self.nes_bank_var.get())
        self._refresh_table()

    def _sync_options(self, refresh_table: bool = False) -> None:
        self.controller.fix_checksum = self.fix_chk_var.get()
        self.controller.create_backup = self.backup_var.get()
        self.controller.all_matches = self.all_matches_var.get()
        self.controller.force_mismatch = self.force_var.get()
        self.controller.skip_invalid = self.skip_invalid_var.get()
        if refresh_table:
            self._refresh_table()

    def _on_live_code_change(self) -> None:
        view = self.controller.live_decode(self.code_input_var.get())
        self.live_decode_var.set(view.summary)
        self.live_decode_label.configure(
            foreground=_SUCCESS if view.valid else (_ERR if view.error else _MUTED)
        )

    def _on_add_code(self) -> None:
        raw = self.code_input_var.get().strip()
        if not raw:
            return
        desc = self.code_desc_var.get().strip()
        try:
            added = self.controller.add_codes_from_text(raw)
            if desc:
                start = len(self.controller.entries) - len(added)
                for idx in range(start, len(self.controller.entries)):
                    e = self.controller.entries[idx]
                    if not e.description:
                        self.controller.entries[idx] = type(e)(
                            code=e.code, description=desc, enabled=e.enabled
                        )
            self.code_input_var.set("")
            self.code_desc_var.set("")
            self._refresh_table()
            self._log(f"Added {len(added)} code(s) to queue.")
        except Exception as exc:
            messagebox.showerror("Code Parse Error", str(exc))

    def _on_import_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Import Cheat File",
            filetypes=[
                ("Cheat files", "*.txt *.gg *.cht"),
                ("RetroArch Cheats", "*.cht"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            loaded = self.controller.import_cheat_file(path)
            self._refresh_table()
            self._log(f"Imported {len(loaded)} code(s) from '{Path(path).name}'.")
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc))

    def _refresh_table(self) -> None:
        prev_selection = self._selected_row_indices()
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)

        self._row_views = self.controller.build_patch_rows()
        for row in self._row_views:
            tag = "disabled"
            if row.enabled:
                if row.status == MatchStatus.READY:
                    tag = "ready"
                elif row.status == MatchStatus.ALREADY_PATCHED:
                    tag = "already"
                elif row.status == MatchStatus.AMBIGUOUS:
                    tag = "ambiguous"
                else:
                    tag = "error"

            code_display = row.code if row.enabled else f"[OFF] {row.code}"
            self.tree.insert(
                "",
                "end",
                iid=str(row.index),
                values=(
                    code_display,
                    row.description,
                    row.address_hex,
                    row.value_hex,
                    row.compare_hex,
                    row.target_summary,
                    row.current_hex,
                    row.status_text,
                ),
                tags=(tag,),
            )

        for idx in prev_selection:
            if self.tree.exists(str(idx)):
                self.tree.selection_add(str(idx))
        self._on_row_selected()

    def _selected_row_indices(self) -> list[int]:
        return [int(iid) for iid in self.tree.selection() if iid.isdigit()]

    def _on_row_selected(self) -> None:
        indices = self._selected_row_indices()
        self._candidate_map.clear()
        if len(indices) != 1:
            self.bank_combo.configure(state="disabled", values=[])
            self.bank_choice_var.set("")
            return

        row = self._row_views[indices[0]]
        if not row.candidate_labels:
            self.bank_combo.configure(state="disabled", values=[])
            self.bank_choice_var.set(row.message)
            return

        options = ["Auto (default)"]
        self._candidate_map["Auto (default)"] = None
        selected_label = "Auto (default)"
        for off, label in row.candidate_labels:
            options.append(label)
            self._candidate_map[label] = off
            if row.selected_offset == off and indices[0] in self.controller.offset_overrides:
                selected_label = label

        self.bank_combo.configure(state="readonly", values=options)
        self.bank_choice_var.set(selected_label)

    def _on_bank_selected(self) -> None:
        indices = self._selected_row_indices()
        if len(indices) != 1:
            return
        label = self.bank_choice_var.get()
        off = self._candidate_map.get(label)
        self.controller.set_offset_override(indices[0], off)
        self._refresh_table()

    def _on_toggle_selected(self) -> None:
        indices = self._selected_row_indices()
        if not indices:
            return
        for idx in indices:
            self.controller.toggle_entry(idx)
        self._refresh_table()

    def _on_remove_selected(self) -> None:
        indices = self._selected_row_indices()
        if not indices:
            return
        self.controller.remove_indices(indices)
        self._refresh_table()
        self._log(f"Removed {len(indices)} item(s) from queue.")

    def _on_clear_queue(self) -> None:
        self.controller.clear_codes()
        self._refresh_table()
        self._log("Cleared patch queue.")

    def _on_patch_save_as(self) -> None:
        if self.controller.rom_path is None:
            messagebox.showwarning("No ROM", "Please open a ROM file first.")
            return
        default_dest = default_patched_path(self.controller.rom_path)
        dest = filedialog.asksaveasfilename(
            title="Save Patched ROM As",
            initialdir=str(default_dest.parent),
            initialfile=default_dest.name,
            defaultextension=default_dest.suffix,
            filetypes=_ROM_FILETYPES,
        )
        if not dest:
            return
        self._execute_patch(output_path=dest, in_place=False)

    def _on_patch_inplace(self) -> None:
        if self.controller.rom_path is None:
            messagebox.showwarning("No ROM", "Please open a ROM file first.")
            return
        backup_note = (
            " (a .bak backup will be created)" if self.backup_var.get() else " (NO backup!)"
        )
        if not messagebox.askyesno(
            "Confirm In-Place Patch",
            f"Overwrite '{self.controller.rom_path.name}' in place{backup_note}?",
        ):
            return
        self._execute_patch(output_path=None, in_place=True)

    def _on_export_ips(self) -> None:
        if self.controller.rom_path is None:
            messagebox.showwarning("No ROM", "Please open a ROM file first.")
            return
        default_ips = self.controller.rom_path.with_suffix(".ips")
        dest = filedialog.asksaveasfilename(
            title="Export IPS Patch",
            initialdir=str(default_ips.parent),
            initialfile=default_ips.name,
            defaultextension=".ips",
            filetypes=[("IPS Patch", "*.ips"), ("All files", "*.*")],
        )
        if not dest:
            return
        self._execute_patch(output_path=None, in_place=False, ips_output_path=dest)

    def _execute_patch(
        self,
        *,
        output_path: str | None,
        in_place: bool,
        ips_output_path: str | None = None,
    ) -> None:
        try:
            self._sync_options()
            result, dest = self.controller.apply_and_save(
                output_path=output_path,
                in_place=in_place,
                ips_output_path=ips_output_path,
            )
            self._update_metadata_banner()
            self._refresh_table()
            for w in result.warnings:
                self._log(f"WARNING: {w}")
            summary = (
                f"Applied {len(result.applied_patches)} patch(es), "
                f"{result.bytes_modified} byte(s) changed -> {dest}"
            )
            self._log(summary)
            messagebox.showinfo("Patch Complete", summary)
        except GGPatcherError as exc:
            messagebox.showerror("Patch Failed", str(exc))
            self._log(f"ERROR: {exc}")
        except Exception as exc:
            messagebox.showerror("Unexpected Error", str(exc))
            self._log(f"ERROR: {exc}")

    def _on_fix_checksum_only(self) -> None:
        if self.controller.rom_path is None:
            messagebox.showwarning("No ROM", "Please open a ROM file first.")
            return
        try:
            self._sync_options()
            default_dest = default_patched_path(self.controller.rom_path)
            dest = filedialog.asksaveasfilename(
                title="Save Checksum-Fixed ROM As (or choose original to overwrite)",
                initialdir=str(default_dest.parent),
                initialfile=self.controller.rom_path.name,
                defaultextension=self.controller.rom_path.suffix,
                filetypes=_ROM_FILETYPES,
            )
            if not dest:
                return
            in_place = Path(dest).resolve() == self.controller.rom_path.resolve()
            _, after, out_p = self.controller.fix_checksum_to_file(
                output_path=dest, in_place=in_place
            )
            self._update_metadata_banner()
            chk_str = ", ".join(f"{c.name}=0x{c.computed:0{c.width * 2}X}" for c in after.checksums)
            msg = f"Updated checksums ({chk_str}) -> {out_p}"
            self._log(msg)
            messagebox.showinfo("Checksum Fixed", msg)
        except Exception as exc:
            messagebox.showerror("Checksum Error", str(exc))
            self._log(f"ERROR fixing checksum: {exc}")

    def _on_conv_decode(self) -> None:
        sys_val = self._system_from_label(self.conv_system_var.get())
        view = self.controller.live_decode(self.conv_decode_in_var.get(), system=sys_val)
        self.conv_decode_out_var.set(view.summary)

    def _on_conv_encode(self) -> None:
        sys_val = self._system_from_label(self.conv_system_var.get())
        if sys_val is None:
            return
        try:
            code = self.controller.live_encode(
                sys_val,
                self.conv_addr_var.get(),
                self.conv_val_var.get(),
                self.conv_cmp_var.get(),
            )
            self.conv_encode_out_var.set(code)
            self._log(f"Encoded [{sys_val.value}] -> {code}")
        except Exception as exc:
            messagebox.showerror("Encode Error", str(exc))

    def _on_copy_encoded(self) -> None:
        code = self.conv_encode_out_var.get()
        if code:
            self.clipboard_clear()
            self.clipboard_append(code)
            self._log(f"Copied '{code}' to clipboard.")

    def _on_send_encoded_to_queue(self) -> None:
        code = self.conv_encode_out_var.get()
        if code:
            self.controller.add_codes_from_text(code)
            self._refresh_table()
            self._log(f"Added '{code}' to patch queue.")

    def _browse_into(self, target_var: tk.StringVar, filetypes: list[tuple[str, str]]) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            target_var.set(path)

    def _on_ips_apply(self) -> None:
        rom_p = self.ips_apply_rom_var.get().strip()
        ips_p = self.ips_apply_patch_var.get().strip()
        if not rom_p or not ips_p:
            messagebox.showwarning("Missing Files", "Select both a Base ROM and an .IPS patch.")
            return
        default_dest = default_patched_path(rom_p)
        dest = filedialog.asksaveasfilename(
            title="Save IPS-Patched ROM As",
            initialdir=str(default_dest.parent),
            initialfile=default_dest.name,
            defaultextension=default_dest.suffix,
            filetypes=_ROM_FILETYPES,
        )
        if not dest:
            return
        try:
            out = self.controller.apply_ips_to_file(
                rom_p,
                ips_p,
                dest,
                fix_checksum=self.ips_apply_chk_var.get(),
                system=self.controller.system_override,
            )
            msg = f"Applied IPS patch -> {out}"
            self._log(msg)
            messagebox.showinfo("IPS Applied", msg)
        except Exception as exc:
            messagebox.showerror("IPS Apply Error", str(exc))
            self._log(f"ERROR applying IPS: {exc}")

    def _on_ips_create(self) -> None:
        orig_p = self.ips_create_orig_var.get().strip()
        mod_p = self.ips_create_mod_var.get().strip()
        if not orig_p or not mod_p:
            messagebox.showwarning(
                "Missing Files", "Select both an Original ROM and a Modified ROM."
            )
            return
        default_ips = Path(mod_p).with_suffix(".ips")
        dest = filedialog.asksaveasfilename(
            title="Save Created .IPS Patch As",
            initialdir=str(default_ips.parent),
            initialfile=default_ips.name,
            defaultextension=".ips",
            filetypes=[("IPS Patch", "*.ips"), ("All files", "*.*")],
        )
        if not dest:
            return
        try:
            out = self.controller.create_ips_from_files(orig_p, mod_p, dest)
            msg = f"Created IPS patch -> {out} ({out.stat().st_size:,} bytes)"
            self._log(msg)
            messagebox.showinfo("IPS Created", msg)
        except Exception as exc:
            messagebox.showerror("IPS Creation Error", str(exc))
            self._log(f"ERROR creating IPS: {exc}")


def run_app(initial_rom: str | None = None) -> int:
    app: Any = GGROMPatcherApp(initial_rom=initial_rom)
    app.mainloop()
    return 0
