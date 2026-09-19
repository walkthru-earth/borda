"""Deterministic name cleaning + canonical matching rules.

`clean()`   -> normalized lowercase string used for alias lookup / fuzzy matching
`match_key()` -> order-insensitive token key (stopwords removed) used for blocking
`numeric_signature()` -> set of value+unit tokens that must agree before fuzzy merging
`canonical_rule()` -> optional (canonical_name, tags) for well-known products
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from ..config import settings

_NOISE = re.compile(
    r"\b(original|genuine|high quality|hot sale|brand new|new|in stock|"
    r"copy|1pcs?|\d+\s*pcs?|piece|pieces|free shipping)\b",
    re.I,
)
_ALIASES: dict[str, str] = {
    "esp-32": "esp32",
    "esp 32": "esp32",
    "esp-8266": "esp8266",
    "esp 8266": "esp8266",
    "node mcu": "nodemcu",
    "node-mcu": "nodemcu",
    "raspberry-pi": "raspberry pi",
    "raspberrypi": "raspberry pi",
    "rpi": "raspberry pi",
    "arduino-uno": "arduino uno",
    "st-link": "stlink",
    "wi-fi": "wifi",
    "blue-pill": "blue pill",
    "bluepill": "blue pill",
    "atmega 328p": "atmega328p",
    "at mega": "atmega",
    "l298 n": "l298n",
    "hc sr04": "hc-sr04",
    "hcsr04": "hc-sr04",
    "hc 05": "hc-05",
    "hc 06": "hc-06",
    "dht 11": "dht11",
    "dht 22": "dht22",
    "ssd 1306": "ssd1306",
}
# Arabic electronics terms -> English tokens, applied before matching so a listing
# written in Arabic lands on the same official product (the Arabic name itself is kept
# in Product.raw_names for search).
_ARABIC_GLOSSARY: dict[str, str] = {
    "اردوينو": "arduino",
    "أردوينو": "arduino",
    "اونو": "uno",
    "أونو": "uno",
    "ميجا": "mega",
    "نانو": "nano",
    "راسبيري باي": "raspberry pi",
    "راسبري باي": "raspberry pi",
    "بيكو": "pico",
    "حساس": "sensor",
    "سنسور": "sensor",
    "موديول": "module",
    "موديل": "module",
    "وحدة": "module",
    "بورد": "board",
    "بوردة": "board",
    "لوحة": "board",
    "ريلاي": "relay",
    "ريليه": "relay",
    "موتور": "motor",
    "محرك": "motor",
    "سيرفو": "servo",
    "ستيبر": "stepper",
    "درايفر": "driver",
    "بطارية": "battery",
    "شاحن": "charger",
    "محول": "converter",
    "منظم": "regulator",
    "شاشة": "display",
    "كابل": "cable",
    "كيبل": "cable",
    "سلك": "wire",
    "اسلاك": "wire",
    "جمبر": "jumper",
    "مقاومة": "resistor",
    "مكثف": "capacitor",
    "ترانزستور": "transistor",
    "دايود": "diode",
    "ليد": "led",
    "بلوتوث": "bluetooth",
    "واي فاي": "wifi",
    "وايفاي": "wifi",
    "الترا سونيك": "ultrasonic",
    "التراسونيك": "ultrasonic",
    "مسافة": "distance",
    "حرارة": "temperature",
    "رطوبة": "humidity",
    "ضغط": "pressure",
    "غاز": "gas",
    "افوميتر": "multimeter",
    "أفوميتر": "multimeter",
    "كاوية": "soldering iron",
    "لحام": "soldering",
    "مفتاح": "switch",
    "زرار": "button",
    "بريد بورد": "breadboard",
    "كاميرا": "camera",
    "مايك": "microphone",
    "سماعة": "speaker",
    "بازر": "buzzer",
    "طاقة": "power",
    "مصدر": "supply",
    "شريحة": "ic",
    "متحكم": "microcontroller",
    "مبرمج": "programmer",
    "قارئ": "reader",
    "كارت": "card",
    "ذاكرة": "memory",
}
_ARABIC_RE = re.compile("|".join(sorted(map(re.escape, _ARABIC_GLOSSARY), key=len, reverse=True)))
_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(k|m|u|µ|n|p)?\s*(ohm|Ω|ohms|v|volt|volts|a|amp|amps|ma|mah|w|watt|"
    r"watts|hz|khz|mhz|ghz|f|uf|nf|pf|mm|cm|m|inch|\"|bit|bits|ch|channel|channels|kb|mb|gb)\b",
    re.I,
)
_UNIT_CANON = {
    "ohms": "ohm", "Ω": "ohm", "volt": "v", "volts": "v", "amp": "a", "amps": "a",
    "watt": "w", "watts": "w", "uf": "uf", "µ": "u", "bits": "bit", "channels": "ch",
    "channel": "ch", "inch": "in", '"': "in",
}  # fmt: skip
_STOP = frozenset(
    [
        "with",
        "for",
        "and",
        "the",
        "of",
        "a",
        "an",
        "in",
        "to",
        "module",
        "board",
        "kit",
        "set",
        "type",
        "version",
        "ver",
        "v",
    ]
)
_SPLIT = re.compile(r"[^\w.+-]+")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def clean(raw: str) -> str:
    return _clean(raw, settings.country_profile.country_name)


@lru_cache(maxsize=65536)
def _clean(raw: str, country_name: str) -> str:
    s = _strip_accents(raw).lower()
    s = re.sub(r"\b" + re.escape(_strip_accents(country_name).lower()) + r"\b", " ", s)
    s = s.replace("×", "x").replace("–", "-").replace("—", "-")
    s = re.sub(r"\(.*?copy.*?\)", " ", s)
    s = _NOISE.sub(" ", s)
    s = _ARABIC_RE.sub(lambda m: f" {_ARABIC_GLOSSARY[m.group(0)]} ", s)
    for k, v in _ALIASES.items():
        s = s.replace(k, v)

    def unit(m: re.Match[str]) -> str:
        val, prefix, u = m.group(1), (m.group(2) or "").lower(), m.group(3).lower()
        prefix = _UNIT_CANON.get(prefix, prefix)
        u = _UNIT_CANON.get(u, u)
        return f"{val}{prefix}{u}"

    s = _UNIT_RE.sub(unit, s)
    s = re.sub(r"[^\w\s.+/-]", " ", s)
    s = re.sub(r"\s*-\s*", "-", s)  # "esp32 - wroom" -> "esp32-wroom"
    s = re.sub(r"(?<=\w)/(?=\w)", " ", s)
    s = re.sub(r"\.(?!\d)", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s


def tokens(cleaned: str) -> list[str]:
    return [t for t in _SPLIT.split(cleaned) if t]


def match_key(raw: str) -> str:
    return _match_key(clean(raw))


@lru_cache(maxsize=65536)
def _match_key(cleaned: str) -> str:
    toks = [t for t in tokens(cleaned) if t not in _STOP and len(t) > 1]
    return " ".join(sorted(set(toks)))


_NUM_TOKEN = re.compile(r"^\d+(?:\.\d+)?[a-z]*$|^[a-z]+\d+[a-z0-9-]*$")


def numeric_signature(raw: str) -> frozenset[str]:
    """Tokens that carry a value/part-number – e.g. {'10k','esp32','atmega328p','16x2'}."""
    return _numeric_signature(clean(raw))


@lru_cache(maxsize=65536)
def _numeric_signature(cleaned: str) -> frozenset[str]:
    out = set()
    for t in tokens(cleaned):
        if _NUM_TOKEN.match(t) or re.search(r"\d+x\d+", t):
            out.add(t)
    return frozenset(out)


def block_key(raw: str) -> str:
    """Cheap blocking key for fuzzy matching: the most specific token."""
    toks = [t for t in tokens(clean(raw)) if t not in _STOP]
    if not toks:
        return ""
    with_digit = [t for t in toks if any(c.isdigit() for c in t)]
    return max(with_digit or toks, key=len)


_ACCESSORY_NOUNS = re.compile(
    r"\b(?:pcb|(?:\w*)shield|case|enclosure|relay|expansion|breakout|holder|bracket|stand|"
    r"mount|heat ?sink|cover|sticker|carrier)\b"
)


_HEAD_ACCESSORY = re.compile(r"\b(?:holder|bracket|stand|mount(?:ing)?|heat ?sink|carrier)\b")
_MOUNT_STYLE = re.compile(
    r"\b(?:panel|pcb|wall|surface|screw|flush|din|rail|chassis|board|top|side|thru|through|"
    r"tht|smd|smt|vertical|horizontal|right angle|angle|stud|bolt|clip|snap|lug|flange|base|"
    r"foot|magnetic|adhesive|rack|ceiling|pole|pipe|desk|table)[- ]?mount(?:ed|ing|able)?\b"
)


_INCLUDED = re.compile(
    r"(?:\b(?:with|w|and|incl|including|includes|plus)\b|\+)(?:\s+[\w.-]+){0,2}\s*$"
)


def accessory_nouns(raw: str, *, strict: bool = True) -> frozenset[str]:
    """Nouns that make a listing an accessory *for* a part (holder, bracket, heatsink, …).

    Strict (seller wording): only the head noun ("HC-SR04 Sensor Bracket Holder") or a noun
    in a "… for X" name counts; "panel mount socket" describes how the part is fitted and
    "motor with mounting bracket" is the motor plus an included accessory. Non-strict
    (proposed official name): any mention keeps the accessory identity."""
    cleaned = _MOUNT_STYLE.sub(" ", clean(raw))
    if not cleaned:
        return frozenset()
    for_sale = " for " in f" {cleaned} "
    found = set()
    for m in _HEAD_ACCESSORY.finditer(cleaned):
        if strict and _INCLUDED.search(cleaned[: m.start()]):
            continue
        noun = "mount" if m.group(0).startswith("mount") else m.group(0).replace(" ", "")
        if not strict or for_sale or cleaned.endswith(m.group(0)):
            found.add(noun)
    return frozenset(found)


def accessory_signature(raw: str) -> frozenset[str]:
    """Accessory nouns must not disappear merely because long names look similar.
    "ProtoShield" / "ScrewShield" count as shields."""
    return frozenset(
        "shield" if m.endswith("shield") else m.replace(" ", "")
        for m in _ACCESSORY_NOUNS.findall(clean(raw))
    )


# Part-like tokens: letters + digits ("mg995", "atmega328p", "24c32", "cs100a"), compared by
# their longest digit run so ESP-WROOM-32 ~ ESP32 and GY-521 MPU6050 ~ MPU-6050, while
# 10131N vs CD4013 or ATmega168P vs ATmega328P stay apart. Units, packages, version tags and
# generic board codes are not part numbers.
_UNIT_TOKEN = re.compile(
    r"^\d+(?:\.\d+)?(?:k|m|u|n|p)?(?:ohm|v|vac|vdc|a|ma|mah|ah|w|mw|kw|wh|kwh|hz|khz|mhz|ghz|f|"
    r"uf|nf|pf|mm|cm|m|km|in|ft|bit|bits|byte|bytes|ch|kb|mb|gb|tb|rpm|dbm|db|deg|kg|g|mg|ms|s|"
    r"sec|min|h|hr|nm|lm|pa|kpa|mpa|bar|psi|lux|ppm|awg|mil|oz|lb|pin|pins|way|axis|cell|cells|"
    r"wd|led|leds|pixel|pixels|pcs|pc|set|sets|pack|inch|mp|fps|dpi|kbps|mbps|gbps|baud|bps)$"
)
_PACKAGE_TOKEN = re.compile(
    r"^(?:dip|pdip|sop|sot|soic|tssop|msop|ssop|to|qfn|dfn|lqfp|tqfp|qfp|plcc|bga|sma|smb|smc|"
    r"do|multiwatt|sip|zip|fc|gy|hw|ky|rm|v|ver|r|rev|gen|mk|type|usb|cat|ip|iso|din|m|x|fr|"
    r"class|grade|series|gen|level|stage|step|phase|pole|poles|core)-?\d+[a-z0-9]*$"
)


def part_numbers(raw: str) -> frozenset[str]:
    """Digit cores of part-number-like tokens in a name (see above)."""
    return _part_numbers(clean(raw))


@lru_cache(maxsize=65536)
def _part_numbers(cleaned: str) -> frozenset[str]:
    out: set[str] = set()
    for t in tokens(cleaned):
        if not re.search(r"\d", t) or not re.search(r"[a-z]", t) or re.search(r"\d+x\d+", t):
            continue
        if _UNIT_TOKEN.match(t) or _PACKAGE_TOKEN.match(t) or re.match(r"^\d{1,3}[a-z]$", t):
            continue  # 10k, 30a, 1s, 2p are values, not part numbers
        runs = re.findall(r"\d{3,}", t)  # 24C32 / SG90 / ESP12 are too short to be decisive
        if runs:
            out.add(max(runs, key=len))
    return frozenset(out)


def foreign_part_numbers(raw: str, canonical: str) -> frozenset[str]:
    """Part numbers a listing mentions that the canonical product does not."""
    return part_numbers(raw) - part_numbers(canonical)


def _same_part_number(a: str, b: str) -> bool:
    """74138 ~ 74HC138 ~ 138: one digit core is a suffix of the other."""
    return a.endswith(b) or b.endswith(a)


def part_numbers_conflict(left: list[str], right: list[str]) -> bool:
    """Two products whose names carry only different part numbers are different things."""
    a = frozenset().union(*(part_numbers(t) for t in left)) if left else frozenset()
    b = frozenset().union(*(part_numbers(t) for t in right)) if right else frozenset()
    return bool(a) and bool(b) and not any(_same_part_number(x, y) for x in a for y in b)


_CHANNELS = re.compile(r"\b(\d+)\s*-?\s*(?:ch|channels?)\b")
_MCU = re.compile(
    r"\b(atmega\d+\w*|attiny\d+\w*|stm32\w+|rp2040|rp2350|esp32(?:-?[sch]\d|-?p4)?|esp8266|"
    r"esp8285|pic\d+\w*)\b"
)
_USB_BRIDGE = re.compile(r"\b(ch340|ch341|ch9102|cp2102|cp2104|ft232|ftdi|pl2303)\w*")


def channel_counts(raw: str) -> frozenset[str]:
    return frozenset(_CHANNELS.findall(clean(raw)))


def mcu_parts(raw: str) -> frozenset[str]:
    """MCU families named in a title, normalised to family+number (ATmega328P-U -> atmega328,
    ESP32S3 -> esp32-s3) so package/speed-grade suffixes do not look like different chips."""
    found = set()
    for m in _MCU.findall(clean(raw)):
        if m.startswith("esp32") and len(m) > 5:
            m = re.sub(r"^esp32-?", "esp32-", m)
        elif not m.startswith(("esp", "rp", "stm32")):
            m = re.sub(r"^([a-z]+\d+).*$", r"\1", m)
        found.add(m)
    if len(found) > 1:
        found.discard("esp32")  # a generic ESP32 mention must not erase a known S3/C3 variant
    return frozenset(found)


def usb_bridges(raw: str) -> frozenset[str]:
    return frozenset("ft232" if m == "ftdi" else m for m in _USB_BRIDGE.findall(clean(raw)))


# --------------------------------------------------------------------------- canonical rules
# (pattern, canonical name, tags[, unless]). First match wins; `unless` is tested against
# the whole cleaned name so accessories / chips / variants never collapse into the board.
_BOARD_ACCESSORY = re.compile(
    r"\b(\w*shield|case|cable|kit|proto|bootloader|atmega|chip|programmed|sticker|holder|enclosure|"
    r"box|cover|mount|acrylic|adapter|connector|screw|jumper|sensor|display|module|expansion|"
    r"terminal|breakout|header|pcb|relay|lcd|tft|work area|diy|power supply|clone kit)\b"
)
_BOARD_VARIANT = re.compile(
    r"\b(compatible|weact|cytron|lilygo|ttgo|wemos|maker-pi|bare|qfn\d*|smd)\b"
)
_ESP32_VARIANT = re.compile(
    r"\b(?:s[23]|c[236]|h2|p4)(?:\b|[- ]?\d)|\besp32[- ]?(?:s[23]|c[236]|h2|p4)|"
    r"\b(?:wrover|devkitc|mini|ethernet|lora|camera|uno|lan\d+|n\d+r\d+)|"
    r"\bwroom-?32[deu]\b|\b\d+[- ]*(?:pin|mb|gb)\b|\b(?:cp2102|ch340g?|attendance|access control)\b"
)
_RELAY_VARIANT = re.compile(
    r"\b(?:wireless|wifi|bluetooth|nrf24l01|esp\w*|ssr|solid state|12v|24v|3v|3\.3v|high power|30a)\b"
)
CANONICAL_RULES: list[tuple] = [
    (
        re.compile(r"\barduino\s*uno\s*(r3|rev3)?\b"),
        "Arduino Uno R3",
        ("arduino", "avr", "dev-board"),
        re.compile(_BOARD_ACCESSORY.pattern + r"|\b(r4|wifi|minima|smd|q)\b|\batmega(?!328)\d"),
    ),
    (
        re.compile(r"\barduino\s*mega\s*2560\b"),
        "Arduino Mega 2560",
        ("arduino", "avr", "dev-board"),
        re.compile(
            _BOARD_ACCESSORY.pattern
            + r"|\b(pro|wifi|esp8266|esp32|bluetooth|adk|mini)\b|\batmega(?!2560)\d"
        ),
    ),
    (
        re.compile(r"\barduino\s*nano\b"),
        "Arduino Nano V3",
        ("arduino", "avr", "dev-board"),
        re.compile(
            _BOARD_ACCESSORY.pattern
            + r"|\b(every|33|esp32|rp2040|ble|iot|nrf24l01|rf-nano)\b|\batmega(?!328)\d"
        ),
    ),
    (re.compile(r"\barduino\s*leonardo\b"), "Arduino Leonardo", ("arduino", "avr", "dev-board")),
    (
        re.compile(r"\barduino\s*pro\s*mini\b"),
        "Arduino Pro Mini",
        ("arduino", "avr", "dev-board"),
        re.compile(r"\b\d+(?:\.\d+)?(?:v|mhz)\b"),
    ),
    (
        re.compile(r"\bnodemcu\b.*\besp8266\b|\besp8266\b.*\bnodemcu\b"),
        "NodeMCU ESP8266 (ESP-12E)",
        ("esp8266", "wifi", "dev-board"),
        re.compile(r"\b(?:d1|mini|oled|base|motor|relay|esp-?01|esp-?07|esp-?12f)\b"),
    ),
    (
        re.compile(r"\besp32[- ]?cam\b"),
        "ESP32-CAM",
        ("esp32", "wifi", "camera", "dev-board"),
        re.compile(r"\b(?:mb|programmer|downloader)\b"),
    ),
    (
        re.compile(r"\besp32\b.*\b(devkit|dev kit|development board)\b"),
        "ESP32 DevKit V1 (WROOM-32)",
        ("esp32", "wifi", "bluetooth", "dev-board"),
        _ESP32_VARIANT,
    ),
    (
        re.compile(r"^esp32[- ]?s3 devkitc$"),
        "ESP32-S3 DevKitC",
        ("esp32", "wifi", "bluetooth", "dev-board"),
    ),
    (
        re.compile(r"^esp32[- ]?c3 devkit$"),
        "ESP32-C3 DevKit",
        ("esp32", "wifi", "bluetooth", "dev-board"),
    ),
    (
        re.compile(r"\bstm32f103c8t6\b.*\bboard\b|\bblue pill\b"),
        "STM32F103C8T6 Blue Pill",
        ("stm32", "arm", "dev-board"),
    ),
    (
        re.compile(r"\bstm32f411\b.*black pill|\bblack pill\b"),
        "STM32F411 Black Pill",
        ("stm32", "arm", "dev-board"),
        re.compile(r"\bstm32f401\w*\b"),
    ),
    (
        re.compile(r"\braspberry pi\s*pico\s*w\b"),
        "Raspberry Pi Pico W",
        ("raspberry-pi", "rp2040", "wifi", "dev-board"),
    ),
    (
        re.compile(r"\braspberry pi\s*pico\s*2\b(?!.*\bw\b)"),
        "Raspberry Pi Pico 2",
        ("raspberry-pi", "rp2350", "dev-board"),
    ),
    (
        re.compile(r"\braspberry pi\s*pico\b(?!.*(w\b|2\b|kit|header))"),
        "Raspberry Pi Pico",
        ("raspberry-pi", "rp2040", "dev-board"),
    ),
    (
        re.compile(r"\braspberry pi\s*5\b.*\b(\d+)\s*gb"),
        "Raspberry Pi 5 {0}GB",
        ("raspberry-pi", "sbc", "linux"),
    ),
    (
        re.compile(r"\braspberry pi\s*4\b.*\b(\d+)\s*gb"),
        "Raspberry Pi 4 Model B {0}GB",
        ("raspberry-pi", "sbc", "linux"),
    ),
    (
        re.compile(r"\braspberry pi\s*zero\s*2\s*w"),
        "Raspberry Pi Zero 2 W",
        ("raspberry-pi", "sbc", "wifi"),
    ),
    (
        re.compile(r"\batmega328p?\b.*\b(dip|pu)\b|\batmega328p-pu\b"),
        "ATmega328P-PU (DIP-28)",
        ("avr", "microcontroller", "ic"),
    ),
    (re.compile(r"\bhc-05\b"), "HC-05 Bluetooth Module", ("bluetooth", "uart", "wireless")),
    (re.compile(r"\bhc-06\b"), "HC-06 Bluetooth Module", ("bluetooth", "uart", "wireless")),
    (re.compile(r"\bhc-sr04\b"), "HC-SR04 Ultrasonic Sensor", ("sensor", "ultrasonic", "distance")),
    (re.compile(r"\bhc-sr501\b"), "HC-SR501 PIR Motion Sensor", ("sensor", "pir", "motion")),
    (
        re.compile(r"\bdht11\b"),
        "DHT11 Temperature & Humidity Sensor",
        ("sensor", "temperature", "humidity"),
    ),
    (
        re.compile(r"\bdht22\b|\bam2302\b"),
        "DHT22 (AM2302) Temperature & Humidity Sensor",
        ("sensor", "temperature", "humidity"),
    ),
    (
        re.compile(r"\bds18b20\b(?!.*(waterproof|probe|cable))"),
        "DS18B20 Digital Temperature Sensor",
        ("sensor", "temperature", "1-wire"),
    ),
    (
        re.compile(r"\bds18b20\b.*(waterproof|probe|cable)"),
        "DS18B20 Waterproof Probe",
        ("sensor", "temperature", "1-wire"),
    ),
    (re.compile(r"\bbmp180\b"), "BMP180 Barometric Pressure Sensor", ("sensor", "pressure", "i2c")),
    (
        re.compile(r"\bbme280\b"),
        "BME280 Environmental Sensor",
        ("sensor", "pressure", "humidity", "i2c"),
    ),
    (re.compile(r"\bmpu-?6050\b"), "MPU-6050 6-Axis IMU", ("sensor", "imu", "i2c")),
    (re.compile(r"\bl298n\b"), "L298N Dual H-Bridge Motor Driver", ("motor-driver", "h-bridge")),
    (re.compile(r"\bl293d\b(?!.*shield)"), "L293D Motor Driver IC", ("motor-driver", "ic")),
    (re.compile(r"\ba4988\b"), "A4988 Stepper Motor Driver", ("motor-driver", "stepper")),
    (re.compile(r"\bdrv8825\b"), "DRV8825 Stepper Motor Driver", ("motor-driver", "stepper")),
    (re.compile(r"\btb6600\b"), "TB6600 Stepper Motor Driver", ("motor-driver", "stepper")),
    (
        re.compile(r"\buln2003\b.*\b28byj-?48\b|\b28byj-?48\b"),
        "28BYJ-48 Stepper Motor + ULN2003 Driver",
        ("motor", "stepper", "motor-driver"),
    ),
    (re.compile(r"\bsg90\b"), "SG90 Micro Servo 9g", ("servo", "motor")),
    (re.compile(r"\bmg996r\b"), "MG996R Metal Gear Servo", ("servo", "motor")),
    (re.compile(r"\bmg90s\b"), "MG90S Micro Servo", ("servo", "motor")),
    (
        re.compile(r"\bssd1306\b.*0\.96|0\.96.*\boled\b"),
        '0.96" OLED Display SSD1306 128x64',
        ("display", "oled", "i2c"),
    ),
    (
        re.compile(r"\b(lcd|display)\s*16x2\b|\b16x2\b.*\blcd\b(?!.*i2c)"),
        "LCD 16x2 Character Display",
        ("display", "lcd"),
    ),
    (
        re.compile(r"\b(lcd|display)\s*20x4\b|\b20x4\b.*\blcd\b"),
        "LCD 20x4 Character Display",
        ("display", "lcd"),
    ),
    (
        re.compile(r"\bnrf24l01\b(?!.*(pa|lna|antenna))"),
        "nRF24L01+ 2.4GHz Transceiver",
        ("wireless", "rf", "spi"),
    ),
    (
        re.compile(r"\bnrf24l01\b.*(pa|lna|antenna)"),
        "nRF24L01+ PA/LNA 2.4GHz Transceiver",
        ("wireless", "rf", "spi"),
    ),
    (
        re.compile(r"\bmax7219\b.*(8x8|matrix)"),
        "MAX7219 8x8 LED Matrix Module",
        ("display", "led", "spi"),
    ),
    (re.compile(r"\bpca9685\b"), "PCA9685 16-Channel PWM Servo Driver", ("servo", "pwm", "i2c")),
    (re.compile(r"\bft232rl\b"), "FT232RL USB to TTL Converter", ("usb", "uart", "programmer")),
    (re.compile(r"\bcp2102\b"), "CP2102 USB to TTL Converter", ("usb", "uart", "programmer")),
    (
        re.compile(r"(?=.*\bch340[gc]?\b)(?=.*\b(?:usb|ttl|uart)\b)"),
        "CH340 USB to TTL Converter",
        ("usb", "uart", "programmer"),
    ),
    (
        re.compile(r"\bstlink\b.*v2|\bst-link v2\b"),
        "ST-Link V2 Programmer",
        ("stm32", "programmer", "debugger"),
    ),
    (re.compile(r"\busbasp\b"), "USBasp AVR Programmer", ("avr", "programmer")),
    (re.compile(r"\blm2596\b"), "LM2596 Buck Converter Module", ("power", "buck", "dc-dc")),
    (re.compile(r"\bxl6009\b"), "XL6009 Boost Converter Module", ("power", "boost", "dc-dc")),
    (re.compile(r"\bmt3608\b"), "MT3608 Boost Converter Module", ("power", "boost", "dc-dc")),
    (
        re.compile(r"\btp4056\b"),
        "TP4056 Li-ion Charger Module",
        ("power", "charger", "li-ion"),
        re.compile(r"\b(?:step[- ]?up|step[- ]?down|boost|dc-dc|bms|2s|3s|4s)\b"),
    ),
    (
        re.compile(r"\bams1117\b.*3\.3"),
        "AMS1117-3.3V Regulator Module",
        ("power", "ldo", "regulator"),
    ),
    (
        re.compile(r"(?=.*\brelay\b)(?=.*\b1[\s-]*ch(?:annel)?\b)(?=.*\b5v\b)"),
        "5V 1-Channel Relay Module",
        ("relay", "switch"),
        _RELAY_VARIANT,
    ),
    (
        re.compile(r"(?=.*\brelay\b)(?=.*\b2[\s-]*ch(?:annel)?\b)(?=.*\b5v\b)"),
        "5V 2-Channel Relay Module",
        ("relay", "switch"),
        _RELAY_VARIANT,
    ),
    (
        re.compile(r"(?=.*\brelay\b)(?=.*\b4[\s-]*ch(?:annel)?\b)(?=.*\b5v\b)"),
        "5V 4-Channel Relay Module",
        ("relay", "switch"),
        _RELAY_VARIANT,
    ),
    (re.compile(r"\bmq-?2\b"), "MQ-2 Gas Sensor", ("sensor", "gas")),
    (re.compile(r"\bmq-?135\b"), "MQ-135 Air Quality Sensor", ("sensor", "gas")),
    (re.compile(r"\bacs712\b.*\b(\d+)a\b"), "ACS712 Current Sensor {0}A", ("sensor", "current")),
    (re.compile(r"\bpzem-?004t\b"), "PZEM-004T Energy Meter Module", ("sensor", "energy", "uart")),
    (re.compile(r"\bw5500\b"), "W5500 Ethernet Module", ("ethernet", "spi", "network")),
    (re.compile(r"\benc28j60\b"), "ENC28J60 Ethernet Module", ("ethernet", "spi", "network")),
    (re.compile(r"\bsim800l\b"), "SIM800L GSM/GPRS Module", ("gsm", "wireless", "uart")),
    (re.compile(r"\bneo-?6m\b"), "NEO-6M GPS Module", ("gps", "uart")),
    (re.compile(r"\brc522\b|\bmfrc522\b"), "RC522 RFID Reader Module", ("rfid", "spi", "13.56mhz")),
    (
        re.compile(r"\bsd card module\b|\bmicro ?sd (card )?module\b"),
        "Micro SD Card Module",
        ("storage", "spi"),
    ),
    (re.compile(r"\bds3231\b"), "DS3231 RTC Module", ("rtc", "i2c", "clock")),
    (re.compile(r"\bds1307\b"), "DS1307 RTC Module", ("rtc", "i2c", "clock")),
    (re.compile(r"\bhx711\b"), "HX711 Load Cell Amplifier", ("sensor", "weight", "adc")),
    (
        re.compile(r"\bmax6675\b"),
        "MAX6675 Thermocouple Amplifier",
        ("sensor", "temperature", "spi"),
    ),
    (
        re.compile(r"\bws2812b?\b.*\b(\d+)\s*(led|pixel)s?\b"),
        "WS2812B Addressable LED x{0}",
        ("led", "rgb", "neopixel"),
    ),
]

_ARABIC = re.compile(r"[\u0600-\u06FF]")
# Listings that only *mention* a well-known part: accessories for it, boards built around it,
# multi-packs, bare chips sold instead of the module (or the other way round).
_PART_ACCESSORY = re.compile(
    r"\b(?:\w*shield|holder|bracket|stand|mount(?:ing)?|heat ?sink|case|enclosure|cover|acrylic|"
    r"sticker|carrier|expansion|breakout|adapter board|base board|base adapter|controller board|"
    r"programmer|downloader|without|not included|compatible|replacement|spare|cascaded|"
    r"\d+[- ]?in[- ]?1|kit|display|lcd|oled|tft|screen|relay)\b"
)
_CHIP_PACKAGE = re.compile(
    r"\b(?:ics?|smd|sop-?\d*|soic|tssop|msop|dip-?\d*|pdip|to-\d+|sot-?\d+|qfn\d*|"
    r"plcc\d*|multiwatt\d*|bare)\b"
)
_ASSEMBLED = re.compile(r"\b(?:module|board|shield|breakout|expansion|kit|without)\b")


def has_arabic(s: str) -> bool:
    return bool(_ARABIC.search(s))


def canonical_rule(raw: str) -> tuple[str, tuple[str, ...]] | None:
    """Return (canonical_name, tags) when a hand-written rule recognises the product."""
    c = clean(raw)
    # Bare PCBs and boards integrating named peripherals are not those peripherals.
    if re.search(r"\bpcb\b", c):
        return None
    board_family = re.search(r"\b(?:esp32|esp8266|arduino|raspberry pi|stm32)\b", c)
    for pat, name, tags, *rest in CANONICAL_RULES:
        if ("dev-board" in tags or "sbc" in tags) and (
            _BOARD_ACCESSORY.search(c) or _BOARD_VARIANT.search(c)
        ):
            continue
        unless = rest[0] if rest else None
        if unless is not None and unless.search(c):
            continue
        if m := pat.search(c):
            if (
                board_family
                and not {"dev-board", "sbc"}.intersection(tags)
                and m.start() > board_family.start()
            ):
                continue
            if "{0}" in name:
                groups = [g for g in m.groups() if g and g.isdigit()]
                if not groups:
                    continue
                name = name.format(groups[0])
            if not {"dev-board", "sbc"}.intersection(tags) and not _same_part(c, name):
                continue
            return name, tags
    return None


def _same_part(cleaned: str, canonical: str) -> bool:
    """A part-number rule only applies when the listing *is* that part: not an accessory
    for it, not a board that merely integrates it, not the bare chip when the rule names the
    module (or vice versa), and not a different channel count."""
    canon = clean(canonical)
    canon_tokens = set(tokens(canon))
    if any(
        ("shield" if hit.endswith("shield") else hit) not in canon_tokens
        and hit.replace(" ", "") not in canon_tokens
        for hit in _PART_ACCESSORY.findall(cleaned)
    ):
        return False
    if _foreign_parts(cleaned, canon):
        return False
    chip_rule = bool(_CHIP_PACKAGE.search(canon))
    if chip_rule and _ASSEMBLED.search(cleaned):
        return False
    if not chip_rule and _CHIP_PACKAGE.search(cleaned):
        return False
    listing_ch, canon_ch = (
        frozenset(_CHANNELS.findall(cleaned)),
        frozenset(_CHANNELS.findall(canon)),
    )
    return not (listing_ch and canon_ch and listing_ch.isdisjoint(canon_ch))


def _foreign_parts(cleaned: str, canon: str) -> frozenset[str]:
    return _part_numbers(cleaned) - _part_numbers(canon)
