"""Widget-wiring tests for GGROMPatcherApp using a lightweight mocked tkinter backend."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

from ggrompatcher.patcher import encode_code
from tests.conftest import make_gb_rom


def _build_fake_tk() -> tuple[types.ModuleType, dict[str, Any]]:
    state: dict[str, Any] = {
        "askopen": "",
        "asksave": "",
        "askyesno": True,
        "info": [],
        "warn": [],
        "error": [],
        "clipboard": [],
        "mainloop_called": False,
    }

    class FakeVar:
        def __init__(self, value: Any = "") -> None:
            self._value = value
            self._traces: list[Any] = []

        def get(self) -> Any:
            return self._value

        def set(self, val: Any) -> None:
            self._value = val
            for cb in list(self._traces):
                cb("", "", "write")

        def trace_add(self, _mode: str, callback: Any) -> str:
            self._traces.append(callback)
            return f"trace-{len(self._traces)}"

    class FakeWidget:
        def __init__(self, _master: Any = None, **kwargs: Any) -> None:
            self.kwargs = dict(kwargs)
            self.bindings: dict[str, Any] = {}

        def grid(self, **_kwargs: Any) -> None:
            pass

        def pack(self, **_kwargs: Any) -> None:
            pass

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

        def bind(self, event: str, fn: Any) -> None:
            self.bindings[event] = fn

        def columnconfigure(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def rowconfigure(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def set(self, *_args: Any) -> None:
            pass

    class FakeText(FakeWidget):
        def __init__(self, master: Any = None, **kwargs: Any) -> None:
            super().__init__(master, **kwargs)
            self.lines: list[str] = []

        def insert(self, _index: str, text: str) -> None:
            self.lines.append(text)

        def see(self, _index: str) -> None:
            pass

        def yview(self, *_args: Any) -> None:
            pass

    class FakeNotebook(FakeWidget):
        def __init__(self, master: Any = None, **kwargs: Any) -> None:
            super().__init__(master, **kwargs)
            self.tabs: list[tuple[Any, str]] = []

        def add(self, child: Any, text: str = "") -> None:
            self.tabs.append((child, text))

    class FakeTreeview(FakeWidget):
        def __init__(self, master: Any = None, **kwargs: Any) -> None:
            super().__init__(master, **kwargs)
            self.items: dict[str, dict[str, Any]] = {}
            self._selection: list[str] = []

        def heading(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def column(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def tag_configure(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def get_children(self) -> list[str]:
            return list(self.items.keys())

        def delete(self, item_id: str) -> None:
            self.items.pop(item_id, None)
            if item_id in self._selection:
                self._selection.remove(item_id)

        def insert(self, _parent: str, _index: str, iid: str, **kwargs: Any) -> str:
            self.items[iid] = kwargs
            return iid

        def exists(self, item_id: str) -> bool:
            return item_id in self.items

        def selection(self) -> tuple[str, ...]:
            return tuple(self._selection)

        def selection_add(self, *items: str) -> None:
            for item_id in items:
                if item_id not in self._selection:
                    self._selection.append(item_id)

        def set_selection(self, *items: str) -> None:
            self._selection = list(items)

        def yview(self, *_args: Any) -> None:
            pass

    class FakeTkInterpreter:
        def call(self, *_args: Any) -> str:
            return "x11"

    class FakeTk(FakeWidget):
        def __init__(self) -> None:
            super().__init__(None)
            self.tk = FakeTkInterpreter()

        def title(self, _t: str) -> None:
            pass

        def geometry(self, _g: str) -> None:
            pass

        def minsize(self, _w: int, _h: int) -> None:
            pass

        def after(self, _ms: int, fn: Any) -> None:
            fn()

        def mainloop(self) -> None:
            state["mainloop_called"] = True

        def clipboard_clear(self) -> None:
            state["clipboard"].clear()

        def clipboard_append(self, val: str) -> None:
            state["clipboard"].append(val)

    class FakeStyle:
        def __init__(self, _master: Any = None) -> None:
            pass

        def theme_names(self) -> tuple[str, ...]:
            return ("clam", "default")

        def theme_use(self, _name: str) -> None:
            pass

        def configure(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def map(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    tk_mod = types.ModuleType("tkinter")
    tk_mod.Tk = FakeTk  # type: ignore[attr-defined]
    tk_mod.StringVar = FakeVar  # type: ignore[attr-defined]
    tk_mod.BooleanVar = FakeVar  # type: ignore[attr-defined]
    tk_mod.Text = FakeText  # type: ignore[attr-defined]

    ttk_mod = types.ModuleType("tkinter.ttk")
    ttk_mod.Style = FakeStyle  # type: ignore[attr-defined]
    ttk_mod.Frame = FakeWidget  # type: ignore[attr-defined]
    ttk_mod.Label = FakeWidget  # type: ignore[attr-defined]
    ttk_mod.Entry = FakeWidget  # type: ignore[attr-defined]
    ttk_mod.Button = FakeWidget  # type: ignore[attr-defined]
    ttk_mod.Checkbutton = FakeWidget  # type: ignore[attr-defined]
    ttk_mod.Combobox = FakeWidget  # type: ignore[attr-defined]
    ttk_mod.Notebook = FakeNotebook  # type: ignore[attr-defined]
    ttk_mod.Treeview = FakeTreeview  # type: ignore[attr-defined]
    ttk_mod.Scrollbar = FakeWidget  # type: ignore[attr-defined]

    fd_mod = types.ModuleType("tkinter.filedialog")

    def _askopen(**_kwargs: Any) -> str:
        val = state["askopen"]
        return str(val.pop(0) if isinstance(val, list) else val)

    def _asksave(**_kwargs: Any) -> str:
        val = state["asksave"]
        return str(val.pop(0) if isinstance(val, list) else val)

    fd_mod.askopenfilename = _askopen  # type: ignore[attr-defined]
    fd_mod.asksaveasfilename = _asksave  # type: ignore[attr-defined]

    mb_mod = types.ModuleType("tkinter.messagebox")
    mb_mod.showinfo = lambda t, m: state["info"].append((t, m))  # type: ignore[attr-defined]
    mb_mod.showwarning = lambda t, m: state["warn"].append((t, m))  # type: ignore[attr-defined]
    mb_mod.showerror = lambda t, m: state["error"].append((t, m))  # type: ignore[attr-defined]
    mb_mod.askyesno = lambda _t, _m: bool(state["askyesno"])  # type: ignore[attr-defined]

    tk_mod.ttk = ttk_mod  # type: ignore[attr-defined]
    tk_mod.filedialog = fd_mod  # type: ignore[attr-defined]
    tk_mod.messagebox = mb_mod  # type: ignore[attr-defined]
    return tk_mod, state


def test_app_widget_wiring_and_actions(tmp_path: Path, monkeypatch: Any) -> None:
    tk_mod, state = _build_fake_tk()
    monkeypatch.setitem(sys.modules, "tkinter", tk_mod)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", tk_mod.ttk)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", tk_mod.filedialog)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", tk_mod.messagebox)
    sys.modules.pop("ggrompatcher.gui.app", None)

    app_mod = importlib.import_module("ggrompatcher.gui.app")

    rom = make_gb_rom(banks=4)
    rom[0x14BC] = 0x03
    rom[2 * 0x4000 + 0x115C] = 0x02
    rom[3 * 0x4000 + 0x115C] = 0x02
    rom_path = tmp_path / "test.gb"
    rom_path.write_bytes(bytes(rom))

    app = app_mod.GGROMPatcherApp(initial_rom=str(rom_path))
    assert not state["error"]
    assert "TESTGB" in app.meta_title_var.get()

    # Live decode trace + add code with description
    app.code_input_var.set("004-BCE-E66")
    assert "0x14BC" in app.live_decode_var.get()
    app.code_desc_var.set("Invincibility")
    app._on_add_code()
    assert len(app.tree.get_children()) == 1
    assert app.tree.items["0"]["values"][1] == "Invincibility"

    # Import cheat file containing ambiguous multi-bank code
    cht = tmp_path / "codes.txt"
    cht.write_text("001-5CA-E62  Banked Hack\n", encoding="utf-8")
    state["askopen"] = str(cht)
    app._on_import_file()
    assert len(app.tree.get_children()) == 2

    # Select ambiguous row 1 -> bank dropdown populates; choose bank 3
    app.tree.set_selection("1")
    app._on_row_selected()
    bank3_label = next(lbl for lbl in app._candidate_map if "Bank 03" in lbl)
    app.bank_choice_var.set(bank3_label)
    app._on_bank_selected()
    assert app.controller.offset_overrides[1] == 3 * 0x4000 + 0x115C

    # Save patched ROM + export IPS
    patched_out = tmp_path / "test.patched.gb"
    state["asksave"] = str(patched_out)
    app._on_patch_save_as()
    assert patched_out.is_file()
    patched_bytes = patched_out.read_bytes()
    assert patched_bytes[0x14BC] == 0x00
    assert patched_bytes[3 * 0x4000 + 0x115C] == 0x00

    ips_out = tmp_path / "test.ips"
    state["asksave"] = str(ips_out)
    app._on_export_ips()
    assert ips_out.is_file()

    # Fix checksum standalone
    fixed_out = tmp_path / "test.fixed.gb"
    state["asksave"] = str(fixed_out)
    app._on_fix_checksum_only()
    assert fixed_out.is_file()

    # Converter tab encode -> copy -> send to queue -> toggle -> remove -> clear
    app.conv_system_var.set("Game Boy / GBC")
    app.conv_addr_var.set("0x14BC")
    app.conv_val_var.set("0xEA")
    app.conv_cmp_var.set("0x03")
    app._on_conv_encode()
    expected_code = encode_code("gameboy", 0x14BC, 0xEA, 0x03)
    assert app.conv_encode_out_var.get() == expected_code

    app._on_copy_encoded()
    assert state["clipboard"] == [expected_code]

    app.conv_decode_in_var.set(expected_code)
    assert "0x14BC" in app.conv_decode_out_var.get()

    app._on_send_encoded_to_queue()
    assert len(app.tree.get_children()) == 3
    app.tree.set_selection("2")
    app._on_toggle_selected()
    assert "[OFF]" in app.tree.items["2"]["values"][0]
    app._on_remove_selected()
    assert len(app.tree.get_children()) == 2
    app._on_clear_queue()
    assert len(app.tree.get_children()) == 0

    # IPS tools tab: create & apply
    created_ips = tmp_path / "diff.ips"
    app.ips_create_orig_var.set(str(rom_path))
    app.ips_create_mod_var.set(str(patched_out))
    state["asksave"] = str(created_ips)
    app._on_ips_create()
    assert created_ips.is_file()

    applied_rom = tmp_path / "applied.gb"
    app.ips_apply_rom_var.set(str(rom_path))
    app.ips_apply_patch_var.set(str(created_ips))
    state["asksave"] = str(applied_rom)
    app._on_ips_apply()
    assert applied_rom.read_bytes() == patched_bytes

    # run_app starts mainloop cleanly
    assert app_mod.run_app(str(rom_path)) == 0
    assert state["mainloop_called"] is True
    assert not state["error"]
