"""Top-level product taxonomy used for browsing (`Product.group`).

Assigned from tags, the official name and the store category with ordered keyword rules.
The LLM enrichment may also propose a group (constrained to `GROUPS`), which wins when
present."""

from __future__ import annotations

import re

from ..taxonomy import GROUPS, Group
from . import names

__all__ = ["GROUPS", "Group", "assign_group"]

# (group, regex over "tags + cleaned name + store category"); first match wins, so put the
# most specific / most frequently mis-assigned groups first.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "3d-printing-cnc",
        re.compile(
            r"\b(3d[- ]?print|filament|nozzle|hotend|extruder|cnc|spindle|stepper.*(3d|cnc)|ramps|ender|creality|pla\b|petg)\b"
        ),
    ),
    (
        "tools-instruments",
        re.compile(
            r"\b(multimeter|oscilloscope|soldering|solder|desolder|tweezer|screwdriver|pliers|cutter|crimp|tester|logic analy[sz]er|power supply unit|lab supply|bench|thermometer|caliper|hot air|heat gun|glue gun|wire stripper|tool)\b"
        ),
    ),
    (
        "dev-boards",
        re.compile(
            r"\b(dev[- ]?board|devkit|development board|arduino|nodemcu|esp32[- ]?(devkit|cam|s3|c3|c6|wroom)|esp8266.*(board|nodemcu)|raspberry pi(?! camera)|pico|blue pill|black pill|stm32.*board|nucleo|discovery|teensy|micro:bit|microbit|beaglebone|orange pi|banana pi|jetson|rp2040|rp2350|sbc|single board)\b"
        ),
    ),
    (
        "robotics-kits",
        re.compile(
            r"\b(robot|chassis|kit|starter|learning|education|car kit|smart car|drone|quadcopter|track|wheel|caster|gripper|arm kit)\b"
        ),
    ),
    (
        "wireless-iot",
        re.compile(
            r"\b(wifi|wi-fi|bluetooth|ble|lora|lorawan|zigbee|nrf24|rf|433|315mhz|gsm|gprs|sim800|sim900|sim7|lte|4g|nb-iot|gps|gnss|neo-6m|rfid|nfc|esp8266|esp-01|esp32(?!.*(devkit|cam))|xbee|hc-05|hc-06|hc-12|transceiver|antenna|ethernet|w5500|enc28j60|iot)\b"
        ),
    ),
    (
        "sensors",
        re.compile(
            r"\b(sensor|dht|ds18b20|thermocouple|bmp|bme|mpu|imu|gyro|accelerometer|ultrasonic|hc-sr04|pir|lidar|tof|vl53|encoder|hall|current sensor|acs712|ina219|ina226|load cell|hx711|ldr|photoresistor|flame|gas|mq-?\d|smoke|humidity|temperature|pressure|soil|moisture|rain|water level|flow|ph|tds|turbidity|color sensor|tcs3200|heart rate|pulse|max30100|fingerprint|camera|ov7670|ov2640|microphone|sound|vibration|tilt|reed|proximity|infrared|ir receiver|line follow|obstacle|detector|probe)\b"
        ),
    ),
    (
        "displays-leds",
        re.compile(
            r"\b(display|lcd|oled|tft|e-?paper|e-?ink|led|rgb|neopixel|ws2812|7[- ]?segment|seven segment|matrix|max7219|nokia 5110|backlight|hd44780|ssd1306|st7789|ili9341|touch screen|hdmi)\b"
        ),
    ),
    (
        "motors-drivers",
        re.compile(
            r"\b(motor|servo|stepper|brushless|bldc|esc|dc gear|gearbox|driver|h-bridge|l298|l293|a4988|drv8825|tb6600|tb6612|uln2003|pump|solenoid|actuator|relay|pca9685|fan)\b"
        ),
    ),
    (
        "power",
        re.compile(
            r"\b(power|battery|batteries|lipo|li-ion|18650|lithium|charger|tp4056|bms|buck|boost|step[- ]?(up|down)|converter|regulator|lm2596|xl6009|mt3608|ams1117|lm7805|7805|ups|inverter|solar|panel|adapter|psu|supply|voltage|ac-dc|dc-dc|transformer|mppt)\b"
        ),
    ),
    (
        "microcontrollers-ics",
        re.compile(
            r"\b(atmega|attiny|atmel|stm32(?!.*board)|pic\d|pic1[0-9]|esp32-wroom-32[ed]?|esp32-s3-wroom|ch340|cp2102|ft232|max232|ic\b|integrated circuit|microcontroller|mcu|eeprom|flash memory|sram|fpga|cpld|logic gate|74hc|74ls|cd40|ne555|555 timer|lm358|lm324|op-?amp|comparator|adc|dac|pcf8574|mcp23017|shift register|74hc595|programmer|st-link|usbasp|debugger|jtag|swd|dip-?\d+|soic|qfp|tqfp)\b"
        ),
    ),
    (
        "semiconductors",
        re.compile(
            r"\b(transistor|mosfet|igbt|diode|zener|schottky|rectifier|bridge|triac|thyristor|scr|bjt|npn|pnp|irf|irfz|tip1|bc547|bc557|2n2222|2n3904|1n4007|1n4148|optocoupler|opto|thermistor|ntc|ptc|varistor|fuse|crystal|oscillator|resonator|tvs)\b"
        ),
    ),
    (
        "passive-components",
        re.compile(
            r"\b(resistor|capacitor|inductor|potentiometer|trimmer|trimpot|ohm|kohm|mohm|uf\b|nf\b|pf\b|farad|coil|ferrite|electrolytic|ceramic|tantalum|film capacitor|smd (resistor|capacitor)|passive)\b"
        ),
    ),
    (
        "connectors-cables",
        re.compile(
            r"\b(connector|header|pin header|jumper|wire|cable|usb cable|dupont|jst|xh|ph2|molex|terminal|screw terminal|socket|plug|jack|dc jack|barrel|ribbon|fpc|ffc|idc|db9|rj45|heat shrink|sleeve|crimp terminal|banana|alligator|crocodile|cord)\b"
        ),
    ),
    (
        "prototyping",
        re.compile(
            r"\b(breadboard|proto|prototype|pcb|perfboard|veroboard|stripboard|copper clad|enclosure|box|case|standoff|spacer|screw|nut|bolt|mount|bracket|shield|hat|expansion board|breakout|adapter board|module)\b"
        ),
    ),
]


def assign_group(*, name: str, tags: list[str], store_category: str | None = None) -> str:
    text = " ".join([names.clean(name), *tags, names.clean(store_category or "")])
    for group, pat in _RULES:
        if pat.search(text):
            return group
    return "other"
