"""System registry and ROM console auto-detection."""

from __future__ import annotations

from ggrompatcher.models import ConsoleSystem, RomFormatError
from ggrompatcher.systems.base import SystemHandler
from ggrompatcher.systems.gameboy import GameBoyHandler
from ggrompatcher.systems.gamegear import GameGearHandler, MasterSystemHandler
from ggrompatcher.systems.gba import GbaHandler
from ggrompatcher.systems.genesis import GenesisHandler
from ggrompatcher.systems.nes import NesHandler
from ggrompatcher.systems.raw import RawHandler
from ggrompatcher.systems.snes import SnesHandler

_HANDLERS: dict[ConsoleSystem, SystemHandler] = {
    ConsoleSystem.GAMEBOY: GameBoyHandler(),
    ConsoleSystem.GBA: GbaHandler(),
    ConsoleSystem.GAMEGEAR: GameGearHandler(),
    ConsoleSystem.MASTERSYSTEM: MasterSystemHandler(),
    ConsoleSystem.NES: NesHandler(),
    ConsoleSystem.SNES: SnesHandler(),
    ConsoleSystem.GENESIS: GenesisHandler(),
    ConsoleSystem.RAW: RawHandler(),
}


def get_handler(system: ConsoleSystem | str) -> SystemHandler:
    """Return the `SystemHandler` instance for a given console system."""
    resolved = system if isinstance(system, ConsoleSystem) else ConsoleSystem.from_str(system)
    return _HANDLERS[resolved]


def detect_system(rom: bytes, filename: str | None = None) -> tuple[ConsoleSystem, int]:
    """Auto-detect the most likely console system for `rom`, returning `(system, confidence)`."""
    if not rom:
        raise RomFormatError("ROM data is empty; cannot detect system.")

    scored: list[tuple[int, ConsoleSystem]] = []
    for sys_type, handler in _HANDLERS.items():
        if sys_type == ConsoleSystem.RAW:
            continue
        score = handler.detect_confidence(rom, filename=filename)
        if score > 0:
            scored.append((score, sys_type))

    if not scored:
        return ConsoleSystem.RAW, 1

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_system = scored[0]
    return best_system, best_score


def list_handlers() -> list[SystemHandler]:
    """Return all registered console handlers in display order."""
    return list(_HANDLERS.values())
