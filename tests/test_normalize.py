from egmarket.normalize import Catalog, canonical_rule, clean, match_key, numeric_signature, slugify
from egmarket.normalize.tags import derive_tags


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
