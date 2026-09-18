"""Fixed top-level product taxonomy (dependency-free so models and normalize can share it)."""

from __future__ import annotations

from typing import Literal

GROUPS: tuple[str, ...] = (
    "dev-boards",
    "microcontrollers-ics",
    "sensors",
    "wireless-iot",
    "displays-leds",
    "motors-drivers",
    "power",
    "passive-components",
    "semiconductors",
    "connectors-cables",
    "prototyping",
    "tools-instruments",
    "3d-printing-cnc",
    "robotics-kits",
    "other",
)
Group = Literal[
    "dev-boards",
    "microcontrollers-ics",
    "sensors",
    "wireless-iot",
    "displays-leds",
    "motors-drivers",
    "power",
    "passive-components",
    "semiconductors",
    "connectors-cables",
    "prototyping",
    "tools-instruments",
    "3d-printing-cnc",
    "robotics-kits",
    "other",
]
