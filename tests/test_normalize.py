import pytest

from borda.normalize import Catalog, canonical_rule, clean, match_key, numeric_signature, slugify
from borda.normalize.tags import derive_tags


def test_clean_unifies_units_and_aliases():
    assert clean("ESP-32 Dev Board 5 V (Original) Egypt") == "esp32 dev board 5v"
    assert clean("Resistor 10 K Ohm 1/4W") == "resistor 10kohm 1 4w"
    assert clean("Node MCU  ESP 8266") == "nodemcu esp8266"


def test_match_key_is_order_insensitive_and_drops_stopwords():
    assert match_key("Module Relay 5V with 1 channel") == match_key("1 channel 5V relay module")


def test_numeric_signature_guards_against_wrong_merges():
    assert numeric_signature("Resistor 10k") != numeric_signature("Resistor 100k")
    assert numeric_signature("Raspberry Pi 4 4GB") != numeric_signature("Raspberry Pi 4 8GB")


def test_canonical_rules():
    assert canonical_rule("Arduino UNO R3 CH340 (clone)")[0] == "Arduino Uno R3"
    assert canonical_rule("Arduino Uno Proto Shield") is None
    assert canonical_rule("Raspberry Pi 5 - 8GB RAM")[0] == "Raspberry Pi 5 8GB"
    assert canonical_rule("حساس مسافة HC-SR04 ultrasonic")[0] == "HC-SR04 Ultrasonic Sensor"


def test_slugify_handles_arabic_only_names():
    assert slugify("Arduino Uno R3") == "arduino-uno-r3"
    assert slugify("مسمار حديد").startswith("item-")
    assert slugify("افوميتر رقمي") == "multimeter"  # glossary maps known Arabic terms


def test_arabic_listing_matches_official_product():
    assert canonical_rule("اردوينو اونو R3 CH340")[0] == "Arduino Uno R3"
    assert match_key("حساس مسافة HC-SR04") == match_key("HC-SR04 distance sensor")


def test_dedupe_merges_same_product_from_different_sellers(make_offer):
    cat = Catalog()
    a, new_a = cat.resolve(make_offer("s1", "Arduino Uno R3 Original", 450), fuzzy_threshold=93)
    b, new_b = cat.resolve(make_offer("s2", "ARDUINO UNO R3 (CH340)", 380), fuzzy_threshold=93)
    c, new_c = cat.resolve(make_offer("s3", "arduino uno r3 board egypt", 400), fuzzy_threshold=93)
    assert a == b == c == "arduino-uno-r3"
    assert (new_a, new_b, new_c) == (True, False, False)
    p = cat.products[a]
    assert p.sellers == ["s1", "s2", "s3"]
    assert len(p.raw_names) == 3  # Egyptian spellings preserved as searchable aliases
    assert len(p.listings) == 3
    assert "arduino" in p.tags


def test_dedupe_fuzzy_respects_numbers(make_offer):
    cat = Catalog()
    a, _ = cat.resolve(make_offer("s1", "Carbon Film Resistor 10K Ohm 1/4W", 1), fuzzy_threshold=93)
    b, _ = cat.resolve(make_offer("s2", "Resistor Carbon Film 10K Ohm 1/4W", 1), fuzzy_threshold=93)
    c, _ = cat.resolve(
        make_offer("s2", "Carbon Film Resistor 100K Ohm 1/4W", 1), fuzzy_threshold=93
    )
    assert a == b
    assert a != c


def test_dedupe_is_stable_across_runs(make_offer):
    cat = Catalog()
    first, _ = cat.resolve(
        make_offer("s1", "LM2596 DC-DC Step Down Module", 60), fuzzy_threshold=93
    )
    reloaded = Catalog.from_products(cat.sorted_products(), cat.redirects)
    again, is_new = reloaded.resolve(
        make_offer("s1", "LM2596 DC-DC Step Down Module", 65), fuzzy_threshold=93
    )
    assert again == first and not is_new


def test_merge_records_redirects_and_provenance(make_offer):
    cat = Catalog()
    a, _ = cat.resolve(make_offer("s1", "ESP32 WROOM Dev Board", 300), fuzzy_threshold=93)
    b, _ = cat.resolve(make_offer("s2", "ESP-WROOM-32 Board 38 pin", 310), fuzzy_threshold=93)
    assert a != b
    cat.merge(a, b)
    assert b not in cat.products
    assert cat.resolve_id(b) == a
    assert "ESP-WROOM-32 Board 38 pin" in cat.products[a].raw_names
    assert cat.products[a].extra_metadata["merged"] == [b]
    # a dropped id is never re-issued
    assert cat.unique_id(b, "x") != b


def test_tags_from_name_category_and_brand(make_offer):
    cat = Catalog()
    pid, _ = cat.resolve(
        make_offer("s1", "SG90 Micro Servo", 90, category="Motors & Drivers", brand="TowerPro"),
        fuzzy_threshold=93,
    )
    tags = derive_tags(cat.products[pid])
    assert {"servo", "motor", "motors-drivers", "towerpro"} <= set(tags)


@pytest.mark.parametrize(
    "name",
    [
        "PCB FOR ESP32-C6 USB Development Board",
        "ESP32 Development Board For IOT Attendance Projects",
        "ESP32-C6 DevKit Development Board",
        "ESP32-S3 N16R8 Development Board with 2.4GHz WiFi and Bluetooth 5.0",
        "ESP32-S2 WEMOS S2 Mini Development Board",
        "ESP32-S3 N8R2 Development Board",
        "ESP32-S3 N16R8 UNO Development Board WiFi & Bluetooth",
        "ESP32 30Pin DEVKIT Expansion Board",
        "14 Channels Relay Module for ESP32 IOT Development Board",
        "ESP32 Development Board 38-Pin with CP2102 Mirco USB",
        "ESP32-WROVER-E Development Board with OV2640 Camera",
        "ESP32-WROOM-32 Dual-Core Wi-Fi Bluetooth IoT Module",
        "LILYGO TTGO T-Beam ESP32 LoRa Development Board GPS Module NEO-6M",
        "ESP32-CAM-MB USB Programmer",
        "Arduino Leonardo Shield",
        "PCB For Arduino UNO Compatible Robotic DIY projects Board",
        "TFT LCD Touch Panel for Arduino UNO and Mega2560",
        "UGE Alpha Rev3 Board Arduino Uno Compatible",
        "Raspberry Pi Pico Breakout Board",
        "Raspberry Pi Pico Work Area",
        "Raspberry Pi 5 8GB Case",
        "Raspberry Pi Pico 2 W",
        "RP2350A Raspberry Pi Pico2 chip microcontroller QFN60",
        "Arduino Pro Mini 3.3V 8MHz",
        "Arduino Pro Mini 5V 16MHz",
        "STM32F401 Black Pill",
    ],
)
def test_canonical_rules_preserve_board_variants_and_accessories(name):
    assert canonical_rule(name) is None


def test_esp32_chipsets_memory_and_accessories_stay_separate(make_offer):
    cat = Catalog()
    names = [
        "ESP32 DevKit V1",
        "ESP32-C6 DevKit Development Board",
        "ESP32-S3 N16R8 Development Board",
        "ESP32-S3 N8R2 Development Board",
        "ESP32-S2 WEMOS S2 Mini Development Board",
        "PCB FOR ESP32-C6 USB Development Board",
        "ESP32 30Pin DEVKIT Expansion Board",
        "14 Channels Relay Module for ESP32 IOT Development Board",
        "ESP32 Development Board 30-Pin with CP2102",
        "ESP32 Development Board 38-Pin with CP2102",
        "ESP32-S3 N16R8 WiFi Bluetooth Development Board",
        "PCB For ESP32-S3 N16R8 WiFi Bluetooth Development Board",
    ]
    ids = [cat.resolve(make_offer("s1", name), fuzzy_threshold=93)[0] for name in names]
    assert len(set(ids)) == len(names)
    assert ids[0] == "esp32-devkit-v1-wroom-32"


def test_product_image_follows_a_listing_that_moved_domains(make_offer):
    """EasyTest moved hosts; the old image URLs died with the old domain."""
    cat = Catalog()
    old = make_offer(
        "easytest",
        "GT1201 Ultrasonic Thickness Gauge",
        900,
        url="https://www.easytest.com.eg/en/product/449/GT1201",
        image="https://www.easytest.com.eg/images/449/cover.jpg",
    )
    pid, _ = cat.resolve(old, fuzzy_threshold=93)
    assert str(cat.products[pid].image) == "https://www.easytest.com.eg/images/449/cover.jpg"
    moved = make_offer(
        "easytest",
        "GT1201 Ultrasonic Thickness Gauge",
        900,
        url="https://easytestgroup.com/en/product/449/GT1201",  # same listing key (path)
        image="https://easytestgroup.com/images/449/cover.jpg",
    )
    assert cat.resolve(moved, fuzzy_threshold=93)[0] == pid
    assert str(cat.products[pid].image) == "https://easytestgroup.com/images/449/cover.jpg"
    # Another seller's picture does not displace an image that still works.
    other = make_offer(
        "s2", "GT1201 Ultrasonic Thickness Gauge", 950, image="https://s2.example/gt1201.jpg"
    )
    assert cat.resolve(other, fuzzy_threshold=93)[0] == pid
    assert str(cat.products[pid].image) == "https://easytestgroup.com/images/449/cover.jpg"


def test_product_image_prefers_sellers_that_allow_hotlinking(make_offer):
    cat = Catalog()
    cat._hotlink_blocked = frozenset({"blocked-shop"})
    first = make_offer(
        "blocked-shop",
        "LM358 Op-Amp DIP-8",
        5,
        url="https://blocked-shop.example/p/lm358",
        image="https://blocked-shop.example/uploads/lm358.jpg",
    )
    pid, _ = cat.resolve(first, fuzzy_threshold=93)
    # Only seller so far: a blocked image beats no image (the product page has a fallback).
    assert str(cat.products[pid].image) == "https://blocked-shop.example/uploads/lm358.jpg"
    second = make_offer(
        "open-shop", "LM358 Op-Amp DIP-8", 6, image="https://cdn.example/open/lm358.png"
    )
    assert cat.resolve(second, fuzzy_threshold=93)[0] == pid
    assert str(cat.products[pid].image) == "https://cdn.example/open/lm358.png"
    # ... and the blocked seller cannot take it back on the next run.
    assert cat.resolve(first, fuzzy_threshold=93)[0] == pid
    assert str(cat.products[pid].image) == "https://cdn.example/open/lm358.png"


def test_image_proxy_rewrites_blocked_hosts_and_still_tracks_the_listing(make_offer):
    cat = Catalog()
    cat._image_proxies = {"makers": "https://i0.wp.com/{host}{path}?w=800"}
    first = make_offer(
        "makers",
        "Elecrow Arduino Starter Kit",
        1200,
        url="https://makers.example/product/elecrow-kit/",
        image="https://makers.example/wp-content/uploads/2025/08/kit.jpg?v=2",
    )
    pid, _ = cat.resolve(first, fuzzy_threshold=93)
    proxied = "https://i0.wp.com/makers.example/wp-content/uploads/2025/08/kit.jpg?w=800"
    assert str(cat.products[pid].image) == proxied
    # The same listing changing its picture still wins, seen through the proxy.
    updated = make_offer(
        "makers",
        "Elecrow Arduino Starter Kit",
        1200,
        url="https://makers.example/product/elecrow-kit/",
        image="https://makers.example/wp-content/uploads/2026/01/kit-v2.jpg",
    )
    assert cat.resolve(updated, fuzzy_threshold=93)[0] == pid
    assert str(cat.products[pid].image).endswith("/2026/01/kit-v2.jpg?w=800")
    # Another seller does not displace a proxied (loadable) image.
    other = make_offer(
        "s2", "Elecrow Arduino Starter Kit", 1300, image="https://s2.example/kit.png"
    )
    assert cat.resolve(other, fuzzy_threshold=93)[0] == pid
    assert str(cat.products[pid].image).endswith("/2026/01/kit-v2.jpg?w=800")
    # Sellers without a proxy are untouched.
    assert cat.public_image(other) == "https://s2.example/kit.png"


def test_image_rules_come_from_the_profile():
    from borda.scrapers.stores import BY_SLUG

    assert (
        BY_SLUG["makerselectronics"]
        .params["image_proxy"]
        .startswith("https://i0.wp.com/{host}{path}")
    )
    assert Catalog().image_proxies["makerselectronics"].startswith("https://i0.wp.com/")
    assert Catalog().hotlink_blocked == frozenset()
