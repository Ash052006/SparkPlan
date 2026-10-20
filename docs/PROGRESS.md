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

## Phase 2 — deterministic validators

**Built**

- `sparkplan/validators/phase2/`: one module per validator, each a pure function
  `(spec, catalog, board) -> list[Issue]`:
  - V1 `membership.py` — unknown part ids, duplicate instance ids, ESP32 exactly
    once (missing or duplicated).
  - V2 `pins_check.py` — referenced pins exist on the catalog part; the
    controller target is a real GPIO; role fits capability (ADC for analog,
    PWM for pwm, output/input direction); flash pins 6-11 are errors;
    input-only pins used as outputs are errors; strapping pins warn.
  - V3 `conflicts.py` — one GPIO drives one signal, I2C bus lines exempt.
  - V4 `power_check.py` — VCC to a rail inside the part's supply range, every
    GND connected, per-rail current vs budget (error over, warning above 80 %);
    peak current is used and named for servos and LED strips.
  - V5 `logic_levels.py` — a part output above 3.3 V into a GPIO is an error
    unless the net passes through `voltage_divider_5v_3v3`; the fix_hint names
    the divider.
  - V6 `i2c.py` — I2C parts share one SDA and one SCL line; duplicate I2C
    addresses are an error.
  - V7 `completeness.py` — every required pin of every non-board part connected.
  - V8 `behaviors.py` — every rule and display line references an existing
    reading or actuator, and declared units match.
  - V9 `layout.py` — every non-board part has a mount zone; wall/lid parts
    declare an exposure.
- `sparkplan/validators/phase2/report.py`: `run_all(spec, catalog=None)` returns
  a `ValidationReport` with `issues`, `passed`, `warnings`, `errors`,
  `by_code()`, and per-validator `timings_ms`. A validator that raises is
  reported as `VALIDATOR_CRASH` rather than crashing the pipeline.
- `sparkplan/validators/phase2/__main__.py`: CLI
  `python -m sparkplan.validators.phase2 spec.json` prints a readable report and
  exits 0 (pass) / 1 (fail) / 2 (usage or unreadable file).

**Model changes**

- `sparkplan/models/buildspec.py` was extended for validation: `Connection`
  gained `signal_role` (signal/rail/gnd/sda/scl/passive) and `rail_name`;
  `Behavior` gained `readings`, `rules` and `display_lines`. The structural
  hard-failures (duplicate instance ids, missing ESP32) were moved out of the
  Pydantic model into V1 so malformed specs parse and are reported as Issues.
  The Phase 1 tests that asserted the old hard failures were updated to assert
  the relaxed parse plus V1 reporting (not deleted).

**Tests**

- `tests/validators/phase2/`: per-validator tests (>=3 failing fixtures + 1
  passing each), required-scenario tests, report/CLI tests, and edge-case unit
  tests. `tests/fixtures/phase2/`: 40 BuildSpec fixtures, including the five
  required scenarios:
  - HC-SR04 echo straight to a GPIO -> fails V5 (LOGIC_LEVEL_HIGH)
  - two OLEDs at 0x3C -> fails V6 (I2C_ADDRESS_CONFLICT)
  - servo + LED strip + OLED on USB power -> fails V4 (RAIL_OVER_BUDGET)
  - use of GPIO 6 -> fails V2 (GPIO_FLASH_RESERVED)
  - fully valid soil moisture monitor -> passes everything
- `make test`: 176 passed (was 109). `make lint`: ruff and black clean.

**Coverage (sparkplan/validators/phase2)**

- 97 % total (438 statements, 11 missed), well above the required 90 %:
  `report.py`, `membership.py`, `conflicts.py`, `i2c.py`, `layout.py`,
  `completeness.py`, `__init__.py` at 100 %; `__main__.py` 98 %; `power_check.py`
  97 %; `behaviors.py` 96 %; `logic_levels.py` and `pins_check.py` 93 %.

**UNVERIFIED**

- The current-budget numbers (500 mA per rail) and part current figures are the
  catalogue's approximate values; none were measured in this session.
- Validator behaviour is verified against fixtures I wrote, not against real
  faulty hardware.

**Known gaps / deferred**

- The legacy validator package (`sparkplan/validators/{structure,pins,power,
  actions,base}.py`, exported via `sparkplan.validators.__init__`) and its tests
  still exist and are untouched; Phase 2 lives in the `phase2/` subpackage so
  the two do not collide. Reconciliation remains deferred.
- `run_all` sorts issues by severity; downstream machinery (planner/API) still
  targets the legacy validator API and is not yet wired to the Phase 2 report.

