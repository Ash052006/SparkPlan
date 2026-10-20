# Sparkplan progress

Live log of what is built, verified, and still open. Updated at each phase
completion. Anything not demonstrated by a command in this repo is marked
UNVERIFIED.

## Phase 1 — scaffold, catalog, data models

**Built**

- Repo scaffold: `Makefile` (test, lint, eval, run-api), `.env.example`,
  `.gitignore` (`.venv`, `node_modules`, `__pycache__`, `runs/`,
  `eval/results/`, `.env`), `.github/workflows/ci.yml` (ruff + black + pytest).
- `sparkplan/models/catalog.py`: Pydantic v2 catalog models and a loader that
  validates the whole catalog at import and fails loudly (`CatalogError`) on
  unknown fields, duplicate ids, or missing pins.
- `catalog/parts.json`: 15 parts (ESP32 DevKit 30-pin, DHT22, capacitive soil
  moisture, SSD1306 0x3C, BME280 0x76, BH1750 0x23, HC-SR04, voltage divider,
  SG90, passive piezo, 5 mm LED, 220 ohm resistor, tactile button, PIR
  HC-SR501, WS2812B 8-LED).
- ESP32 board model: rails (3V3/5V/GND, 500 mA each, approximate) and a GPIO
  table with per-pin capabilities (input-only 34/35/36/39, forbidden 6-11,
  strapping 0/2/12/15, default I2C SDA 21 / SCL 22, ADC/PWM flags), every fact
  carrying a source note and `verify_against_datasheet`.
- `sparkplan/models/buildspec.py`: BuildSpec (ok/unsupported) with parts,
  connections, layout hints and behaviors.
- Tests: loader accepts the real catalog, rejects 5 broken fixtures, and
  BuildSpec round-trips through JSON. `make test`: 109 passed.
  `make lint`: ruff and black both clean.

**Catalog discrepancy and resolution**

- The Phase 1 spec says "15 parts" but its explicit list has only 14 entries,
  because the LED and its 220 ohm resistor are described as one assembly
  ("5 mm LED + 220 ohm resistor"). To land on 15 catalog entries without
  inventing a component, the LED and resistor are modelled as two separate
  entries: `led_5mm` (category actuator) and `resistor_220r` (category
  passive). The `led_5mm` entry records the required series resistor in its
  description and notes, and the resistor's notes cross-reference it. This
  keeps 13 other required parts + LED + resistor = 15.
- An earlier draft of `catalog/parts.json` contained an unrequested `bmp280`
  part. It was removed: it is not in the required list, and it would have
  collided with the BME280 on I2C address 0x76. No fifteenth component was
  invented to satisfy the count.

**UNVERIFIED**

- All part dimensions are approximate and marked `dimensions_verified: false`
  in the catalog. None have been checked against a datasheet or calipers.
- ESP32 GPIO and rail facts carry `verify_against_datasheet: true`; they come
  from common ESP32-WROOM-32 knowledge, not a datasheet read in this session.
- The ESP32 DevKit 30-pin vs 38-pin pinout: this phase models the 30-pin board
  as requested; the pin list has not been bench-verified.

**Known gaps / deferred**

- Deterministic validators are explicitly out of scope for Phase 1.
- A prior implementation (different catalog/BuildSpec shape) still exists under
  `sparkplan/{validators,planner,geometry,firmware,guide,pipeline}`, `api/`,
  `web/`, and their tests. Reconciliation to the new models is deferred to a
  later phase. The Phase 1 catalog/BuildSpec were added alongside it.
- `docs/PROGRESS.md` did not exist before this phase despite the brief saying
  it did; it is created here.
- The GitHub remote was empty (no commits). Phase 1 is the first content on
  `main`; the usual pull/branch/merge flow was not applicable at bootstrap.
