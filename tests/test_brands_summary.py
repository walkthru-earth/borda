"""Brand hygiene, seller-text summaries and the identity guards that keep accessories,
bare chips and different part numbers from collapsing into a well-known product."""

import pytest

from borda.models import Product
from borda.normalize import Catalog, canonical_rule
from borda.normalize.brands import brand_tags, infer_brand, normalize_brand
from borda.normalize.names import (
    accessory_signature,
    channel_counts,
    mcu_parts,
    part_numbers,
    part_numbers_conflict,
    usb_bridges,
)
from borda.normalize.summary import summarize
from borda.normalize.tags import derive_tags

# --------------------------------------------------------------------------- brands


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Circuits Electronics", None),  # Shopify `vendor` is the shop itself
        ("Circuits electronics", None),
        ("Future Electronics Egypt", None),  # regional shop label
        ("Future Electronics Egypt (Arduino Egypt)", None),
        ("Makers Electronics", None),
        ("Other", None),
        ("Generic", None),
        ("NXP/TI/ST", None),  # substitute list = generic part
        ("R305", None),  # part number echoed as brand
        ("BESTON", "Beston"),
        ("TEXAS INSTRUMENTS", "Texas Instruments"),
        ("Microchip (Atmel)", "Microchip"),
        ("Atmel/Microchip", "Microchip"),
        ("Adafruit Industries", "Adafruit"),
        ("SeeedStudio", "Seeed Studio"),
        ("ILYGO®", "LILYGO"),
        ("Waveshare®", "Waveshare"),
        ("Uni-Trend", "UNI-T"),
        ("APC", "APC"),
        ("NXP", "NXP"),
        ("Raspberry Pi", "Raspberry Pi"),
        ("Da Koang", "Da Koang"),
    ],
)
def test_normalize_brand(raw, expected):
    assert normalize_brand(raw) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Waveshare® FTDI Original Chip Basic Breakout 5V/3.3V", "Waveshare"),
        ("ILYGO® TTGO T-Beam ESP32 LoRa Development Board 433MHz", "LILYGO"),
        ('ESP32 OLED 0.96" WiFi TTGO + Bluetooth', "LILYGO"),
        ("seeed reComputer Classic Jetson J4012 16GB ORIN NX", "Seeed Studio"),
        ("Arduino Shield - CAN BUS (OBD-II) V2.0 Shield - SeeedStudio", "Seeed Studio"),
        ("Voice Recognition Module (DFRobot)", "DFRobot"),
        ("Jetson Nano Developer Kit", "NVIDIA"),
        ("UNI-T UT61E Multimeter", "UNI-T"),
        ("HLK-PM01 AC-DC 5V", "Hi-Link"),
        # LILYGO's T-<line> boards are sold without the maker's name
        ("T-A7608SA-H ESP32 GPS 4G", "LILYGO"),
        ("T-SIM7000G ESP32 LoRa GPS", "LILYGO"),
        ("T-Nut for 2020 V-Slot Aluminum Extrusion", None),
        ("T-Type Battery Connector - Male", None),
        ("SMA T-Shape Adapter Female to Dual Female", None),
        # accessories and clones are not made by the brand they fit
        ("Case for Raspberry Pi 5", None),
        ("OLED Display Shield for WEMOS D1 Mini", None),
        ("Tip for Hakko FX-888D", None),
        ("ESP32 board compatible with Adafruit Feather", None),
        # widely cloned names are never inferred from the title alone
        ("Raspberry Pi 5 8GB", None),
        ("Arduino Uno R3 CH340 clone", None),
    ],
)
def test_infer_brand(name, expected):
    assert infer_brand(name) == expected


def test_brand_from_title_beats_store_vendor_and_is_searchable(make_offer):
    cat = Catalog()
    pid, _ = cat.resolve(
        make_offer(
            "circuits-elec",
            "TTGO ESP32 Display Development Board (1.14 inch)",
            brand="Circuits Electronics",
        ),
        fuzzy_threshold=93,
    )
    p = cat.products[pid]
    assert p.brand == "LILYGO"
    assert {"lilygo", "ttgo"} <= set(p.tags)  # either spelling finds the board
    assert "circuits-electronics" not in p.tags
    assert brand_tags("Seeed Studio") == ["seeed", "seeed-studio", "seeedstudio"]


def test_legacy_snapshot_brands_are_cleaned_on_load():
    cat = Catalog.from_products(
        [
            Product(
                id="a", canonical_name="Waveshare 7inch HDMI LCD", brand="Circuits Electronics"
            ),
            Product(id="b", canonical_name="9V Battery Clip", brand="Other"),
            Product(id="c", canonical_name="Decade Counter", brand="TEXAS INSTRUMENTS"),
        ]
    )
    assert cat.products["a"].brand == "Waveshare"
    assert cat.products["b"].brand is None
    assert cat.products["c"].brand == "Texas Instruments"


def test_tags_drop_store_noise_and_plural_duplicates(make_offer):
    p = Product(id="r", canonical_name="Resistor 10k", category="Resistors")
    tags = derive_tags(
        p, make_offer("s", "Resistor 10k", store_tags=["Newly Arrived", "for", "Resistors", "SMD"])
    )
    assert "resistor" in tags and "resistors" not in tags
    assert not {"newly-arrived", "for"} & set(tags)


# --------------------------------------------------------------------------- identity guards


@pytest.mark.parametrize(
    "name",
    [
        "HC-SR04 Ultrasonic Sensor Bracket",
        "Arduino HC-SR04 Ultrasonic Sensor Bracket BLUE Holder Smart Car Support 2WD 4WD",
        "CS100A UltraSonic Sensor Compatible with HC-SR04 Ranging chip SOP16",
        "Display for HC-SR04 Ultrasonic Distance Measurement Control Board",
        "Acrylic Servo Bracket for SG90 Servo",
        "16 Channel PWM Servo Controller Board with USB port for SG90 MG995",
        "L293D Motor Driver Board",  # the rule names the IC
        "L293D Dual Channel Motor Driver (Version 2) without IC",
        "L298N Dual Full-Bridge Driver ICs 46V 4A Multiwatt15",  # the rule names the module
        "Heat Sink 25x23x16 for L298N and TO247 packages",
        "2-Channel Wireless Relay Module nRF24L01+ (RM2N)",
        "Adapter Board for NRF24L01 Wireless Module 3.3V",
        "NRF24L01 Arduino RC Remote Transmitter with Pre-Programmed SMD ATMEGA328",
        "DS1307 PDIP-8 Real Time Calendar and Clock",
        "ESP12 1-Channel Relay with DS1307 RTC IoT Controller Board",
        "CH340G SMD IC Transceiver USB 2.0 SOP-16",
        "USB to RS232 Serial Module (CH340) DB9",
        "SMD IC TP4056 Battery Management SOP-8",
        "TP4056 18650 Charger With DC-DC Step Up Converter Module",
        "MAX7219 Module 4-in-1 8X8 LED Matrix Module",
        "Four cascaded MAX7219 8x8 LED Matrix Display Module",
        "D1 Mini NodeMcu Lua WiFi ESP8266 Development Board",
        "5V 1-Channel Relay Module (Wireless nRF24L01)",
        "12V 1 channel relay",
    ],
)
def test_part_rules_skip_accessories_chips_and_variants(name):
    assert canonical_rule(name) is None


@pytest.mark.parametrize(
    ("name", "canonical"),
    [
        ("HC-SR04 Ultrasonic Distance Sensor Module ( 2cm to 400cm )", "HC-SR04 Ultrasonic Sensor"),
        ("OSEPP HC-SR04 Ultrasonic Sensor Module", "HC-SR04 Ultrasonic Sensor"),
        ("SG90 Micro Servo 180 Degree", "SG90 Micro Servo 9g"),
        ("L293D Motor Driver IC (Original)", "L293D Motor Driver IC"),
        ("L298N Motor Driver Board Red", "L298N Dual H-Bridge Motor Driver"),
        ("DS1307 RTC Module with 24C32 Memory", "DS1307 RTC Module"),
        ("Tiny RTC Module (On-board DS1307 chip, 24C32 EEPROM)", "DS1307 RTC Module"),
        ("USB to UART TTL Module (CH340) Type A", "CH340 USB to TTL Converter"),
        ("TP4056 USB-C 1S 1A Lithium Battery Charger Module", "TP4056 Li-ion Charger Module"),
        ("5V 1-Channel Relay Module", "5V 1-Channel Relay Module"),
        ("1 Channel 5V Relay Module (Active Low)", "5V 1-Channel Relay Module"),
        ("PCA9685 Servo Motor 16 Channel Driver", "PCA9685 16-Channel PWM Servo Driver"),
    ],
)
def test_part_rules_still_recognise_the_part(name, canonical):
    assert canonical_rule(name)[0] == canonical


def test_part_number_helpers():
    assert part_numbers("10131N FLIP-FLOP IC DIP-16") == {"10131"}
    assert part_numbers("Resistor 100k 1/4W 250VAC 800mA") == set()  # values are not parts
    assert part_numbers("DS1307 RTC Module with 24C32 Memory") == {"1307"}
    assert part_numbers_conflict(["10131N FLIP-FLOP IC DIP-16"], ["CD4013 Dual D-Type Flip-Flop"])
    assert not part_numbers_conflict(["ESP-WROOM-32 DevKit V1"], ["ESP32 DevKit V1"])
    assert not part_numbers_conflict(["GY-521 MPU6050 module"], ["MPU-6050 6-Axis IMU"])
    assert part_numbers_conflict(["STM32F103C8T6 Blue Pill"], ["STM32F411 Black Pill"])
    assert channel_counts("24 Channel PWM Servo Driver") == {"24"}
    assert mcu_parts("Arduino Nano ATmega168P CH340C") == {"atmega168"}
    assert mcu_parts("ATmega328P-U") == mcu_parts("ATmega328PB") == {"atmega328"}
    assert mcu_parts("ESP32-S3 board (esp32)") == {"esp32-s3"}
    assert usb_bridges("Nano with FTDI FT232RL") == {"ft232"}
    assert accessory_signature("Arduino Nano ProtoShield ScrewShield") == {"shield"}
    assert accessory_signature("HC-SR04 Sensor Bracket Holder") == {"bracket", "holder"}


def test_arduino_rules_keep_unusual_mcus_apart():
    assert canonical_rule("Arduino Nano ATmega328P-U with FT232 Uploader")[0] == "Arduino Nano V3"
    assert canonical_rule("Arduino Nano ATmega168P CH340C Driver 5V 16MHz") is None
    assert canonical_rule("Arduino Mega 2560 ATmega2560-16AU")[0] == "Arduino Mega 2560"
    assert canonical_rule("Arduino Mega 2560 + WiFi ESP8266") is None
    assert canonical_rule("Arduino RF-NANO Integrated with NRF24L01") is None


def test_fuzzy_never_merges_a_shield_into_its_board(make_offer):
    cat = Catalog()
    a, _ = cat.resolve(make_offer("s1", "Arduino Nano V3 board CH340"), fuzzy_threshold=90)
    b, _ = cat.resolve(make_offer("s2", "Arduino Nano V3 ProtoShield board"), fuzzy_threshold=90)
    assert a != b


# --------------------------------------------------------------------------- seller summaries

SELLER_TEXT = """LM324 Quad Operational Amplifier
The LM324 Operational Amplifier combines four independent op-amps inside one DIP-14 package. Therefore, it can process several analog signals while reducing component count and circuit size.
The IC supports both single and dual power supplies. You will love how cheap it is!
Key Features
Contains four independent operational amplifiers
Technical Specifications
Part Number: LM324
Component Type: Quad operational amplifier
Single Supply Range: 3V–32V
Typical Gain Bandwidth: Approximately 1MHz
Package: DIP-14
Price: 15 EGP
Pin Configuration
Pin 1: Output 1
Pin 2: Inverting input 1
Package Contents
1 × LM324 Quad Operational Amplifier IC, DIP-14
"""


def test_summarize_extracts_neutral_sentences_and_specs():
    s = summarize(SELLER_TEXT, "LM324 Quad Operational Amplifier")
    assert s.description.startswith("The LM324 Operational Amplifier combines four")
    assert "love" not in s.description and "cheap" not in s.description
    assert len(s.description) <= 320
    assert "Package: DIP-14" in s.specs
    assert "Single Supply Range: 3V–32V" in s.specs
    assert not any(
        spec.startswith(("Pin", "Price", "Part Number", "Key Features")) for spec in s.specs
    )
    assert len(s.specs) <= 6


def test_summarize_reads_flattened_spec_tables_and_semicolon_rows():
    table = "Specifications :\nAttribute\nValue\nCategory\nResistors/Chip Resistor\nRoHS\nYes\nPower (Watts)\n0.125W (125mW)\nType\nThick Film Resistor\nTolerance\n±5%"
    s = summarize(table, "Chip Resistor SMD 130Ω")
    assert s.description is None
    assert "Power (Watts): 0.125W (125mW)" in s.specs and "Type: Thick Film Resistor" in s.specs
    assert not any(spec.startswith(("Category", "RoHS", "Attribute")) for spec in s.specs)
    rows = "Features:\nProdcut Name : Metal Oxide Varistor;Material : Metal, Plastic\nMaxi. Limited Voltage : 27v;Lead Length : 2.8cm"
    s2 = summarize(rows, "Metal Oxide Varistor MOV 10D270K")
    assert "Maxi. Limited Voltage: 27v" in s2.specs and "Material: Metal, Plastic" in s2.specs
    assert not any(spec.lower().startswith("prodcut name") for spec in s2.specs)


def test_summarize_ignores_marketing_only_and_arabic_only_text():
    assert not summarize("Buy now! Best price in Egypt. Contact us on WhatsApp.", "x")
    assert not summarize("منتج ممتاز بسعر رائع. اطلب الآن.", "x")
    assert not summarize(None, "x")
