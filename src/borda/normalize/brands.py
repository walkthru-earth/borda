"""Brand hygiene: sellers are not manufacturers, casing is not identity, and a brand that
only appears inside the listing title must still be searchable.

Store JSON is sloppy about brands: Shopify's `vendor` is usually the shop itself, Wix and
WooCommerce use placeholders such as "Other", and the same maker is spelled three ways.
This module turns those strings into one canonical brand per manufacturer, drops seller and
placeholder values, and recognises well-known maker brands (Waveshare, LILYGO, Seeed
Studio, ...) inside product names so that a brand search finds every product of that maker.
"""

from __future__ import annotations

import re
from functools import lru_cache

from . import names

# canonical brand -> aliases (matched case-insensitively on word boundaries, in names and
# in scraped brand strings). The canonical spelling is the manufacturer's own. Only include
# aliases that identify the maker unambiguously – product lines that are widely cloned
# (NodeMCU, NEO-6M, SIM800L, Ender parts) are deliberately absent.
BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    # maker boards & modules
    "Waveshare": ("waveshare",),
    "LILYGO": ("lilygo", "lily go", "ilygo", "ttgo", "t-display", "t-beam", "t-camera"),
    "Seeed Studio": ("seeed studio", "seeedstudio", "seeed", "xiao", "wio terminal", "recomputer"),
    "SparkFun": ("sparkfun", "spark fun"),
    "Adafruit": ("adafruit", "adafruit industries", "qt py", "trinket", "itsybitsy"),
    "DFRobot": ("dfrobot", "df robot", "firebeetle"),
    "M5Stack": ("m5stack", "m5 stack", "m5stick", "m5core", "atom lite", "atom matrix"),
    "WEMOS": ("wemos", "lolin"),
    "Heltec": ("heltec",),
    "Elecrow": ("elecrow", "crowpanel", "crowbits", "crowtail", "crowpi", "crowbot"),
    "Keyestudio": ("keyestudio", "keyes"),
    "SunFounder": ("sunfounder",),
    "Pimoroni": ("pimoroni",),
    "Olimex": ("olimex",),
    "Makerfabs": ("makerfabs",),
    "Cytron": ("cytron", "maker pi", "maker uno", "maker nano"),
    "WeAct Studio": ("weact", "weact studio"),
    "OSEPP": ("osepp",),
    "Pololu": ("pololu",),
    "Espressif": ("espressif", "espressif systems"),
    "Raspberry Pi": ("raspberry pi foundation", "raspberry pi ltd", "raspberry pi trading"),
    "Arduino": ("arduino srl", "arduino s.r.l", "arduino.cc", "arduino ag"),
    "BeagleBoard.org": ("beagleboard", "beaglebone", "beagleboard.org", "beagle bone"),
    "PJRC": ("pjrc", "teensy"),
    "NVIDIA": ("nvidia", "jetson"),
    "Sipeed": ("sipeed", "maixduino", "maix", "lichee"),
    "Luckfox": ("luckfox",),
    "Radxa": ("radxa", "rock pi"),
    "Orange Pi": ("orange pi", "orangepi"),
    "Banana Pi": ("banana pi", "bananapi"),
    "Pine64": ("pine64",),
    "Hardkernel": ("hardkernel", "odroid"),
    "Khadas": ("khadas",),
    "Slamtec": ("slamtec", "rplidar"),
    "YDLIDAR": ("ydlidar",),
    "Benewake": ("benewake",),
    "Ai-Thinker": ("ai-thinker", "ai thinker", "aithinker"),
    "Hi-Link": ("hi-link", "hilink", "hlk"),
    "Ebyte": ("ebyte", "cdebyte"),
    "SIMCom": ("simcom",),
    "Quectel": ("quectel",),
    "u-blox": ("u-blox", "ublox"),
    "Sonoff": ("sonoff", "itead"),
    "Shelly": ("shelly",),
    "Tuya": ("tuya",),
    # silicon vendors
    "Texas Instruments": ("texas instruments", "texas instrument"),
    "STMicroelectronics": ("stmicroelectronics", "st microelectronics", "stmicro", "st micro"),
    "Microchip": (
        "microchip",
        "atmel",
        "microchip (atmel)",
        "atmel/microchip",
        "atmel / microchip",
        "microchip technology",
    ),
    "NXP": ("nxp", "nxp semiconductors", "freescale", "philips semiconductors"),
    "Nordic Semiconductor": ("nordic semiconductor", "nordic semi", "nordic"),
    "Analog Devices": ("analog devices", "maxim integrated", "maxim", "linear technology"),
    "Infineon": ("infineon", "international rectifier", "cypress"),
    "onsemi": ("onsemi", "on semiconductor", "on semi", "fairchild"),
    "Nexperia": ("nexperia",),
    "Vishay": ("vishay",),
    "Renesas": ("renesas", "dialog semiconductor", "intersil"),
    "ROHM": ("rohm",),
    "Toshiba": ("toshiba",),
    "Bosch": ("bosch", "bosch sensortec"),
    "Sensirion": ("sensirion",),
    "ASAIR": ("asair", "aosong"),
    "Melexis": ("melexis",),
    "Allegro MicroSystems": ("allegro microsystems", "allegro"),
    "Silicon Labs": ("silicon labs", "silabs", "silicon laboratories"),
    "FTDI": ("ftdi", "future technology devices"),
    "WCH": ("wch", "qinheng", "nanjing qinheng"),
    "GigaDevice": ("gigadevice",),
    "Nuvoton": ("nuvoton",),
    "Winbond": ("winbond",),
    "Lattice": ("lattice semiconductor",),
    "AMD": ("xilinx", "amd xilinx"),
    "Altera": ("altera",),
    "Broadcom": ("broadcom",),
    "MediaTek": ("mediatek",),
    "Realtek": ("realtek",),
    "Rockchip": ("rockchip",),
    "Allwinner": ("allwinner",),
    "Intel": ("intel", "intel corporation", "movidius"),
    "Murata": ("murata",),
    "Panasonic": ("panasonic",),
    "Samsung": ("samsung",),
    "LG": ("lg electronics", "lg chem", "lg energy"),
    "Sony": ("sony",),
    "Omron": ("omron",),
    "Bourns": ("bourns",),
    "Littelfuse": ("littelfuse",),
    "Diodes Incorporated": ("diodes incorporated", "diodes inc"),
    "Alpha & Omega Semiconductor": ("alpha & omega semiconductor", "alpha and omega"),
    "National Semiconductor": ("national semiconductor",),
    "ams OSRAM": ("ams osram", "ams ag", "osram"),
    # connectors / passives / electromechanical
    "JST": ("jst",),
    "Molex": ("molex",),
    "TE Connectivity": ("te connectivity", "tyco electronics"),
    "Amphenol": ("amphenol",),
    "Phoenix Contact": ("phoenix contact",),
    "Wago": ("wago",),
    "Songle": ("songle",),
    # power / industrial / accessories
    "Mean Well": ("mean well", "meanwell"),
    "APC": ("apc", "apc by schneider"),
    "Schneider Electric": ("schneider electric", "schneider"),
    "ABB": ("abb",),
    "Siemens": ("siemens",),
    "Eaton": ("eaton",),
    "Delta Electronics": ("delta electronics",),
    "Anker": ("anker",),
    "Vention": ("vention",),
    "UGREEN": ("ugreen",),
    "Baseus": ("baseus",),
    "Xiaomi": ("xiaomi", "wowstick"),
    "Saft": ("saft",),
    "Ultracell": ("ultracell",),
    "Beston": ("beston",),
    "Da Koang": ("da koang", "dakoang"),
    "Shallin": ("shallin",),
    "GoldTool": ("goldtool", "gold tool"),
    "AVC": ("avc",),
    "ADDA": ("adda",),
    "NPP": ("npp", "npp power"),
    "AcBel": ("acbel",),
    # tools & instruments
    "Fluke": ("fluke",),
    "UNI-T": ("uni-t", "unit-t", "uni t", "unitrend", "uni-trend"),
    "Hakko": ("hakko",),
    "Weller": ("weller",),
    "Baku": ("baku",),
    "Yihua": ("yihua",),
    "Sugon": ("sugon",),
    "Quick": ("quick soldering", "quick 861", "quick 857", "quick 706"),
    "Mechanic": ("mechanic",),
    "Kaisi": ("kaisi",),
    "Jakemy": ("jakemy",),
    "Pro'sKit": ("pro'skit", "proskit", "pros kit", "pro's kit"),
    "Hantek": ("hantek",),
    "Rigol": ("rigol",),
    "Owon": ("owon",),
    "Siglent": ("siglent",),
    "FNIRSI": ("fnirsi",),
    "Zoyi": ("zoyi", "zotek"),
    "Aneng": ("aneng",),
    "Kaiweets": ("kaiweets",),
    "Mastech": ("mastech",),
    "Kyoritsu": ("kyoritsu",),
    "Hioki": ("hioki",),
    "Tenmars": ("tenmars",),
    "Lutron": ("lutron",),
    "Extech": ("extech",),
    "Sanwa": ("sanwa",),
    "Testo": ("testo",),
    "Brymen": ("brymen",),
    "ATTEN": ("atten",),
    "Saleae": ("saleae",),
    "Dremel": ("dremel",),
    "EXACTO": ("exacto",),
    # 3D printing / CNC
    "Creality": ("creality",),
    "Anycubic": ("anycubic",),
    "Elegoo": ("elegoo",),
    "Bambu Lab": ("bambu lab", "bambulab"),
    "Prusa": ("prusa", "prusament"),
    "E3D": ("e3d",),
    "BIGTREETECH": ("bigtreetech", "btt"),
    "MKS": ("makerbase", "mks gen", "mks robin", "mks sgen"),
    "ChiTu": ("chitu", "chitu systems"),
    "SUNLU": ("sunlu",),
    "eSUN": ("esun",),
    "Polymaker": ("polymaker",),
    "Trianglelab": ("trianglelab", "triangle lab"),
    "Micro Swiss": ("micro swiss", "microswiss"),
    "Bondtech": ("bondtech",),
}

# Extra search tags per brand (company-level aliases only – never product lines, otherwise a
# search for "display" would surface every LILYGO board).
BRAND_TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "LILYGO": ("ttgo",),
    "Seeed Studio": ("seeed", "seeedstudio"),
    "WEMOS": ("lolin",),
    "Microchip": ("atmel",),
    "Analog Devices": ("maxim",),
    "onsemi": ("fairchild",),
    "BeagleBoard.org": ("beaglebone",),
    "UNI-T": ("uni-trend",),
    "Ai-Thinker": ("aithinker",),
    "Hi-Link": ("hlk",),
    "Pro'sKit": ("proskit",),
    "Bambu Lab": ("bambulab",),
    "BIGTREETECH": ("btt",),
    "MKS": ("makerbase",),
    "Da Koang": ("dakoang",),
    "STMicroelectronics": ("stmicro",),
    "Texas Instruments": ("ti",),
    "NXP": ("freescale",),
    "Infineon": ("cypress",),
    "Mean Well": ("meanwell",),
}

# Product-line patterns that identify a maker without naming it. LILYGO boards are sold as
# "T-<line>" (T-Display, T-Beam, T-SIM7000G, T-A7608SA-H, T-Deck, …); T-nuts, T-bolts and
# T-type connectors are deliberately not matched.
BRAND_PATTERNS: dict[str, re.Pattern[str]] = {
    "LILYGO": re.compile(
        r"(?<![\w-])t-(?:display|beam|call|deck|watch|dongle|qt|embed|camera|energy|higrow|"
        r"internet|koala|panel|rgb|twr|zigbee|encoder|impulse|keyboard|lora|motion|wristband|"
        r"journal|hmi|glass|circle|halow|echo|oi|pcie|eth|can485|u2t|dock|micro32|fpga|simcam|"
        r"sim\d\w*|a7\d\d\w*|01c3|[3578](?![\d-]))(?![a-z])",
        re.I,
    ),
}

# Brands whose names appear in countless third-party accessories and clones; they are kept
# when a store or the model states them, but never inferred from a product title alone.
_NO_INFER = frozenset({"Arduino", "Raspberry Pi"})

# Words that may legitimately precede a brand without the product being made by it.
_NOT_MADE_BY = re.compile(
    r"\b(?:for|fits?|with|compatible|supports?|replaces?|replacement|like|vs|and|&|\+|to|on)"
    r"(?:\s+(?:the|a|an|all|your|use|most|any))?\s*$"
)
_PLACEHOLDERS = frozenset(
    {
        "",
        "other",
        "others",
        "generic",
        "general",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "-",
        "--",
        "various",
        "no-name",
        "no name",
        "noname",
        "no brand",
        "unbranded",
        "oem",
        "odm",
        "china",
        "chinese",
        "misc",
        "default",
        "brand",
        "vendor",
        "local",
        "imported",
        "original",
        "compatible",
        "clone",
    }
)
_TRADEMARKS = re.compile(r"[®™©℠]|\(tm\)|\(r\)", re.I)


def _lower_alias(alias: str) -> str:
    return re.sub(r"\s+", " ", alias.lower().strip())


@lru_cache(maxsize=1)
def _alias_index() -> tuple[dict[str, str], re.Pattern[str]]:
    """(lowercase alias -> canonical, regex matching any alias on word boundaries)."""
    index: dict[str, str] = {}
    for canonical, aliases in BRAND_ALIASES.items():
        index[_lower_alias(canonical)] = canonical
        for alias in aliases:
            index[_lower_alias(alias)] = canonical
    # longest alias first so "seeed studio" wins over "seeed"; spaces/hyphens are optional so
    # "seeedstudio", "seeed-studio" and "seeed studio" all match.
    parts = []
    for alias in sorted(index, key=len, reverse=True):
        pat = r"[\s-]*".join(re.escape(w) for w in re.split(r"[\s-]+", alias))
        parts.append(f"(?<![\\w]){pat}(?![\\w])")
    return index, re.compile("|".join(parts), re.I)


def canonical_brand(alias: str) -> str | None:
    """Canonical manufacturer spelling for a known alias, else None."""
    index, _ = _alias_index()
    return index.get(re.sub(r"\s*-\s*", "-", _lower_alias(alias)))


def _seller_keys() -> frozenset[str]:
    from ..config import settings

    keys: set[str] = set()
    profile = settings.country_profile
    country = _lower_alias(profile.country_name)
    for st in profile.stores:
        keys.add(st.slug)
        keys.add(st.slug.replace("-", " "))
        name = _lower_alias(st.name)
        keys.add(name)
        keys.add(re.sub(r"\s*\(.*?\)\s*", " ", name).strip())
        keys.add(name.replace(country, "").strip())
        host = (st.base_url.host or "").lower()
        host = re.sub(r"^(?:www|store|shop)\.", "", host)
        keys.add(host.split(".")[0])
    return frozenset(k for k in keys if len(k) > 2)


def is_seller_name(value: str) -> bool:
    """True when a scraped "brand" is actually the shop (or a regional shop label)."""
    from ..config import settings

    v = _lower_alias(_TRADEMARKS.sub("", value))
    country = _lower_alias(settings.country_profile.country_name)
    if re.search(rf"\b{re.escape(country)}\b", v):
        return True  # "Future Electronics Egypt", "Arduino Egypt" – a shop label, not a maker
    stripped = re.sub(r"\s*\(.*?\)\s*", " ", v).strip()
    keys = _seller_keys()
    if v in keys or stripped in keys:
        return True
    squashed = re.sub(r"[^a-z0-9]", "", stripped)
    return any(squashed == re.sub(r"[^a-z0-9]", "", k) for k in keys if len(k) > 4)


def normalize_brand(raw: str | None) -> str | None:
    """Clean a scraped or model-supplied brand string.

    Drops placeholders, seller names and multi-manufacturer lists (a part offered by
    "NXP/TI/ST" is generic), maps known aliases to the canonical spelling and fixes
    SHOUTING variants of unknown brands so "BESTON" and "Beston" are one brand."""
    if not raw or not isinstance(raw, str):
        return None
    value = _TRADEMARKS.sub("", raw)
    value = re.sub(r"\s+", " ", value).strip(" -–,.;:")
    if not value or _lower_alias(value) in _PLACEHOLDERS or names.has_arabic(value):
        return None
    if canonical := canonical_brand(value):
        return canonical
    if "/" in value:
        return None  # "Fairchild / ON Semiconductor / Vishay" is a substitute list
    if is_seller_name(value) or len(value) > 40:
        return None
    if re.fullmatch(r"[A-Za-z]{0,4}-?\d{2,}[A-Za-z0-9-]*", value):
        return None  # "R305", "KY-026": a part number echoed back as the brand
    letters = re.sub(r"[^A-Za-z]", "", value)
    if letters.isupper() and (len(letters) > 5 or (len(letters) > 3 and " " in value)):
        value = value.title()  # BESTON -> Beston, but APC / NXP / JST stay acronyms
    return value


def infer_brand(*texts: str | None) -> str | None:
    """Recognise a known maker brand inside product names / store categories.

    The first text is the most authoritative (canonical name), so a match there wins over a
    match in a seller spelling. Mentions such as "case for Raspberry Pi" or "compatible with
    Arduino" do not make the accessory a Raspberry Pi / Arduino product."""
    _, pattern = _alias_index()
    for text in texts:
        if not text:
            continue
        for m in pattern.finditer(text):
            if _NOT_MADE_BY.search(text[: m.start()].lower()):
                continue
            hit = canonical_brand(m.group(0))
            if hit and hit not in _NO_INFER:
                return hit
        for brand, line in BRAND_PATTERNS.items():
            if (m := line.search(text)) and not _NOT_MADE_BY.search(text[: m.start()].lower()):
                return brand
    return None


def brand_tags(brand: str | None) -> list[str]:
    """Search tags for a brand: its normalised spelling plus company aliases (ttgo -> LILYGO)
    so searching for either spelling finds the product."""
    if not brand:
        return []
    tags = {_norm(brand), *(_norm(a) for a in BRAND_TAG_ALIASES.get(brand, ()))}
    return sorted(t for t in tags if len(t) > 1)


def _norm(tag: str) -> str:
    return re.sub(r"[^a-z0-9+.]+", "-", names.clean(tag)).strip("-")
