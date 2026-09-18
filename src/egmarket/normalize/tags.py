"""Heuristic tags derived from canonical names, store categories and store tags.
LLM tags (enrichment) are merged on top."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import names

if TYPE_CHECKING:
    from ..models import Product, RawOffer

# token (in cleaned name) -> tag
_TOKEN_TAGS: dict[str, str] = {
    "arduino": "arduino",
    "esp32": "esp32",
    "esp8266": "esp8266",
    "nodemcu": "esp8266",
    "stm32": "stm32",
    "raspberry": "raspberry-pi",
    "pico": "raspberry-pi",
    "rp2040": "rp2040",
    "attiny": "avr",
    "atmega": "avr",
    "atmega328p": "avr",
    "pic": "pic",
    "avr": "avr",
    "sensor": "sensor",
    "module": "module",
    "shield": "shield",
    "display": "display",
    "lcd": "display",
    "oled": "display",
    "tft": "display",
    "led": "led",
    "rgb": "led",
    "relay": "relay",
    "motor": "motor",
    "servo": "servo",
    "stepper": "stepper",
    "driver": "driver",
    "battery": "battery",
    "lipo": "battery",
    "18650": "battery",
    "charger": "power",
    "regulator": "power",
    "converter": "power",
    "buck": "power",
    "boost": "power",
    "power": "power",
    "supply": "power",
    "adapter": "power",
    "resistor": "resistor",
    "capacitor": "capacitor",
    "diode": "diode",
    "transistor": "transistor",
    "mosfet": "mosfet",
    "igbt": "igbt",
    "ic": "ic",
    "opamp": "ic",
    "connector": "connector",
    "header": "connector",
    "jumper": "wire",
    "wire": "wire",
    "cable": "cable",
    "usb": "usb",
    "bluetooth": "bluetooth",
    "wifi": "wifi",
    "lora": "lora",
    "gsm": "gsm",
    "gps": "gps",
    "rfid": "rfid",
    "nfc": "nfc",
    "camera": "camera",
    "ultrasonic": "sensor",
    "ir": "infrared",
    "pir": "sensor",
    "temperature": "sensor",
    "humidity": "sensor",
    "pressure": "sensor",
    "gas": "sensor",
    "encoder": "encoder",
    "potentiometer": "potentiometer",
    "switch": "switch",
    "button": "switch",
    "buzzer": "audio",
    "speaker": "audio",
    "microphone": "audio",
    "breadboard": "prototyping",
    "pcb": "pcb",
    "soldering": "tools",
    "multimeter": "tools",
    "oscilloscope": "tools",
    "tool": "tools",
    "kit": "kit",
    "3d": "3d-printing",
    "printer": "3d-printing",
    "filament": "3d-printing",
    "programmer": "programmer",
    "debugger": "programmer",
    "fan": "cooling",
    "heatsink": "cooling",
    "i2c": "i2c",
    "spi": "spi",
    "uart": "uart",
    "can": "can-bus",
    "ethernet": "ethernet",
    "rtc": "rtc",
    "sd": "storage",
    "eeprom": "storage",
    "fpga": "fpga",
    "solar": "solar",
    "inverter": "power",
    "meter": "instrument",
}
_MAX_TAGS = 12
_BAD_TAG = re.compile(r"^\d+$|^.{0,1}$|^.{31,}$")
# store-side marketing/navigation tags that say nothing about the product
_STORE_NOISE = re.compile(
    r"latest|new-?arrival|best-?sell|featured|offer|sale|discount|deal|hot|top|trending|"
    r"recommend|home|all-products|uncategori[sz]ed|electronics$|egypt|shop|store"
)


def _seller_words() -> set[str]:
    from ..scrapers.stores import STORES

    words: set[str] = set()
    for st in STORES:
        words.add(st.slug)
        words.update(_norm(w) for w in st.name.split())
        words.add(_norm(st.name))
    return {w for w in words if len(w) > 2}


def _norm(tag: str) -> str:
    return re.sub(r"[^a-z0-9+.]+", "-", names.clean(tag)).strip("-")


def derive_tags(product: Product, offer: RawOffer | None = None) -> list[str]:
    tags: set[str] = set(product.tags)
    for tok in names.tokens(names.clean(product.canonical_name)):
        if t := _TOKEN_TAGS.get(tok):
            tags.add(t)
    cat = product.category or (offer.category if offer else None)
    if cat and not names.has_arabic(cat):
        tags.add(_norm(cat))
    if product.brand and not names.has_arabic(product.brand):
        tags.add(_norm(product.brand))
    if offer:
        # store tags are noisy; keep only short, ascii, dictionary-like ones
        for st in offer.store_tags[:10]:
            t = _norm(st)
            if t and " " not in st.strip() and len(t) <= 20 and t not in tags:
                tags.add(t)
    sellers = _seller_words()
    clean = sorted(
        t
        for t in tags
        if t
        and not _BAD_TAG.match(t)
        and not names.has_arabic(t)
        and t not in sellers
        and not _STORE_NOISE.search(t)
    )
    # prefer family/function tags (from the token map) if we have to cut
    priority = set(_TOKEN_TAGS.values())
    clean.sort(key=lambda t: (t not in priority, t))
    return sorted(clean[:_MAX_TAGS])
