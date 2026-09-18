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

_NOISE = re.compile(
    r"\b(original|genuine|high quality|hot sale|brand new|new|egypt|in stock|"
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
# Egyptian-market Arabic terms -> English tokens, applied before matching so a listing
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


@lru_cache(maxsize=65536)
def clean(raw: str) -> str:
    s = _strip_accents(raw).lower()
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


@lru_cache(maxsize=65536)
def match_key(raw: str) -> str:
    toks = [t for t in tokens(clean(raw)) if t not in _STOP and len(t) > 1]
    return " ".join(sorted(set(toks)))


_NUM_TOKEN = re.compile(r"^\d+(?:\.\d+)?[a-z]*$|^[a-z]+\d+[a-z0-9-]*$")


@lru_cache(maxsize=65536)
def numeric_signature(raw: str) -> frozenset[str]:
    """Tokens that carry a value/part-number – e.g. {'10k','esp32','atmega328p','16x2'}."""
    out = set()
    for t in tokens(clean(raw)):
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


def accessory_signature(raw: str) -> frozenset[str]:
    """Accessory nouns must not disappear merely because long names look similar."""
    return frozenset(
        re.findall(r"\b(?:pcb|shield|case|enclosure|relay|expansion|breakout)\b", clean(raw))
    )


# --------------------------------------------------------------------------- canonical rules
# (pattern, canonical name, tags[, unless]). First match wins; `unless` is tested against
# the whole cleaned name so accessories / chips / variants never collapse into the board.
_BOARD_ACCESSORY = re.compile(
    r"\b(shield|case|cable|kit|proto|bootloader|atmega|chip|programmed|sticker|holder|enclosure|"
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
CANONICAL_RULES: list[tuple] = [
    (
        re.compile(r"\barduino\s*uno\s*(r3|rev3)?\b"),
        "Arduino Uno R3",
        ("arduino", "avr", "dev-board"),
        re.compile(_BOARD_ACCESSORY.pattern + r"|\b(r4|wifi|minima|smd|q)\b"),
    ),
    (
        re.compile(r"\barduino\s*mega\s*2560\b"),
        "Arduino Mega 2560",
        ("arduino", "avr", "dev-board"),
        re.compile(_BOARD_ACCESSORY.pattern + r"|\bpro\b"),
    ),
    (
        re.compile(r"\barduino\s*nano\b"),
        "Arduino Nano V3",
        ("arduino", "avr", "dev-board"),
        re.compile(_BOARD_ACCESSORY.pattern + r"|\b(every|33|esp32|rp2040|ble|iot)\b"),
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
        re.compile(r"\bch340g?\b.*\b(usb|ttl)\b"),
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
    (re.compile(r"\btp4056\b"), "TP4056 Li-ion Charger Module", ("power", "charger", "li-ion")),
    (
        re.compile(r"\bams1117\b.*3\.3"),
        "AMS1117-3.3V Regulator Module",
        ("power", "ldo", "regulator"),
    ),
    (
        re.compile(r"\brelay\b.*\b1\s*ch(annel)?\b.*\b5v\b|\b5v\b.*\b1\s*ch(annel)?\b.*\brelay\b"),
        "5V 1-Channel Relay Module",
        ("relay", "switch"),
    ),
    (
        re.compile(r"\brelay\b.*\b2\s*ch(annel)?\b.*\b5v\b|\b5v\b.*\b2\s*ch(annel)?\b.*\brelay\b"),
        "5V 2-Channel Relay Module",
        ("relay", "switch"),
    ),
    (
        re.compile(r"\brelay\b.*\b4\s*ch(annel)?\b.*\b5v\b|\b5v\b.*\b4\s*ch(annel)?\b.*\brelay\b"),
        "5V 4-Channel Relay Module",
        ("relay", "switch"),
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
            if "{0}" not in name:
                return name, tags
            groups = [g for g in m.groups() if g and g.isdigit()]
            if groups:
                return name.format(groups[0]), tags
    return None
