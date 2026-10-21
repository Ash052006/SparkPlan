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
- `docs/.md` did not exist before this phase despite the brief saying
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


## Phase 3 — LLM planner with repair loop and refusal

**Built**

- `sparkplan/llm/phase3/` (new subpackage, does not touch the legacy
  `sparkplan/llm/`):
  - `client.py`: `LLMResponse` (data + `LLMUsage` + `finish_reason`/`truncated`),
    `LLMUsage` (`present=False` when the provider omits usage - counts are never
    invented), `LLMError`/`LLMRetryableError`, `FakeLLM` (scripted, no network),
    `OpenAICompatibleClient`, and `make_llm_from_env()`.
  - `OpenAICompatibleClient.complete_json`: primary `json_schema` mode with a
    one-shot `json_object` fallback when the provider rejects the schema; one
    bounded transport retry per call (separate from planner repair rounds) with
    exponential backoff honouring `Retry-After`.
- `sparkplan/planner/phase3/` (new subpackage):
  - `prompt.py`: compact catalog digest + compact schema + hard rules; the
    untrusted idea is wrapped in a delimited, JSON-encoded block and defused.
    Also `provider_response_schema()`/`unwrap_provider_response()` (the union is
    wrapped under `{"build": ...}` so strict providers accept it, then unwrapped
    before parsing), `compact_schema`, `estimate_tokens`, `log_request_size`.
  - `models.py`: `PlanStatus`, `Attempt`, `PlanResult`.
  - `planner.py`: at most FOUR LLM calls (`MAX_REPAIR_ROUNDS = 3`,
    `MAX_LLM_CALLS = 4`). `ok` is returned ONLY when parsing yields a
    `SupportedBuild` AND the Phase 2 `run_all` report has no errors; warnings
    pass through. Truncated responses (`finish_reason=length`) are treated as a
    repairable `LLM_TRUNCATED`. Empty/whitespace idea -> `refused` without an
    LLM call. An LLM `UnsupportedBuild` -> `unsupported` with its exact fields.
    Otherwise `failed` with the final validation report and attempt history.
- `scripts/try_plan.py`: live runner. Loads `.env` via a minimal stdlib dotenv
  reader (never prints secrets). Exit 0 (ok/unsupported), 1 (failed), 2 (config).

**Prompt sizing and provider calibration**

- Initial attempt hit HTTP 413 (prompt too large for Groq). Fixed by compacting
  the digest (GPIO table as category lists, trimmed parts, no global capability
  list, `desc` truncated) and the schema (strip `title`/`description`, no
  indent). Provider-measured system prompt is ~3.1-3.2k prompt tokens.
- Groq rejects `json_schema` for the BuildSpec union: a top-level `oneOf` is
  rejected outright, and wrapping it under `build` still fails because Groq
  requires the property itself to be `type: object`. `json_object` mode is
  therefore the effective mode on Groq, and the model must be guided by prompt.
- `qwen/qwen3.8-27b` has a 1,000 output-token/minute ceiling; requests asking
  for more are rejected. `openai/gpt-oss-20b` was unusable (it spends the whole
  completion budget on hidden reasoning and returns empty content). Model is set
  via `SPARKPLAN_LLM_MODEL` in `.env` (git-ignored).
- Prompts now require the board as a part, the required sections, and key order
  (`parts, layout, behaviors, connections`) so a cut-off response still contains
  `layout`.

**Tests**

- `tests/planner/phase3/`: planner loop, token accounting, prompt safety, client
  (offline via `httpx.MockTransport`), provider-schema compatibility, and prompt
  compactness/size-budget tests. Fixtures:
  `tests/fixtures/phase3/{supported,unsupported}/`.
- `make test`: 263 passed. `make lint`: ruff and black clean.
- Coverage of the new packages: 99 % (`sparkplan.llm.phase3` 99 %,
  `sparkplan.planner.phase3` 98-100 %).

**Live runs (this session, real Groq `qwen/qwen3.8-27b`, via `scripts/try_plan.py`)**

- "a desk plant monitor ... OLED" -> `ok` in 3 calls / 2 repair rounds, full
  validated BuildSpec, report passed (0 errors, 0 warnings).
- "a drone that flies ... streams video" -> `unsupported` in 1 call, with
  reasons, `missing_capabilities`, and a closest supported alternative.
- "a motion-triggered night light ... PIR ... LED strip" -> `failed` after 4
  calls; the final spec tripped `RAIL_OVER_BUDGET` (5V: PIR + WS2812B peak
  530 mA > 500 mA budget) plus missing mount zones. The catalogue itself notes
  the strip's full-white current exceeds the USB budget, so this is a genuine
  over-budget build the model did not declare unsupported.
- "a door alarm ... ultrasonic ... buzzer" -> `failed` after 4 calls on
  `NO_GROUND`/`NO_POWER` wiring omissions the validator caught.

**UNVERIFIED**

- Live runs depend on the operator's Groq account and model; the 1,000
  output-token/minute tier makes multi-call plans sensitive to pacing, so the
  two `failed` live results above may partly reflect provider-tier throttling
  rather than model capability. This was not isolated.
- The catalog's current figures and rail budget remain approximate; nothing was
  measured electrically.

**Known gaps / deferred**

- The model does not always declare genuinely over-budget ideas as
  `unsupported`; the deterministic validator catches them and the result is
  `failed`, not `unsupported`. Converting a persistent validator error into an
  `unsupported` status was explicitly out of scope for this phase.
- Streamed/constrained decoding that would force every required key is not
  available on the current provider tier; prompt guidance is used instead.
- The legacy planner/client/validators are untouched and still serve the API,
  eval and pipeline; reconciliation remains deferred.

## Phase 4 — enclosure geometry

**Built**

- `sparkplan/geometry/enclosure.py` — Phase 4 data model: `EnclosureConfig`
  (defaults honour the 220x220x250 build volume with 3 mm walls and 3 mm
  floor/lid), `PackedPart` (including `protrudes_mm`), `Opening`, `Post`,
  `EnclosureLayout`, and `Mesh` (vertices/faces + `to_trimesh`).
- `sparkplan/geometry/layout.py` — deterministic packing driven only by catalog
  dimensions and the BuildSpec `layout` hints. Wall-mounted parts that must be
  exposed to the outside (soil probe, USB/power) become *pass-through* items:
  the shorter plan dimension runs along the wall, the long axis runs
  vertically, no wall opening is generated for them and instead the floor/lid
  gets a slot at their plan position. Parts taller than the cavity record how
  far they protrude (`protrudes_mm`) but are not silently approved.
- Sizing bug found by tests: cavity dimensions were compared against
  `2 * keep`, which over-counted wall runs. Wall-mounted parts are now measured
  against the two opposing wall *strips* (max of the -y/+y and -x/+x runs),
  which removed spurious `GEOMETRY_WALL_FULL` errors while keeping the
  clearance guarantees.
- `sparkplan/geometry/shell.py` — CadQuery shell construction: base with wall
  cutouts and floor slots, screw posts and standoff bosses unioned after the
  cuts (so a boss inside a cutout survives), lid built in its own frame with
  lip and bosses, and `build_assembled` translating the lid by
  `floor_mm + inner_z_mm`. Lid-frame holes offset by the wall thickness.
  Wall bosses on the +-x walls use `outer_x_mm - wall_mm` as their centre.
- `sparkplan/geometry/checks.py` — seven checks as `Issue`s:
  watertight/non-positive volume, build volume, part collisions (signed gap
  with configurable clearance), cutouts inside their face, cutouts clashing
  with screw posts, and minimum wall thickness.
- `sparkplan/geometry/generate.py` — `generate_enclosure(spec, catalog, config,
  out_dir)`: layout -> checks -> shells -> mesh checks -> STL export
  (`base.stl`, `lid.stl`, `assembled.stl`) + `geometry_report.json`. STLs are
  only written when no error-severity issue exists; a missing engine
  (`ImportError` on `shell`) degrades to a warning and still reports the
  layout.
- Planner wiring: after phase-2 validation passes, the planner runs geometry
  and feeds error issues back as a `geometry_error` repair attempt. A single
  repair round is allowed; a second failure is terminal (`failed`) with the
  geometry issues attached. Warnings merge into a successful result.
- `scripts/render_geometry.py` — headless matplotlib preview (plan, X and Y
  elevations) from layout numbers only, so it works without CadQuery;
  `--export-dir` also writes the STLs.
- New fixture `tests/fixtures/phase4/ultrasonic_alarm.json` (HC-SR04, divider,
  buzzer, board) covering a wall window plus base and wall mounts.

**Tests**

- `tests/geometry/phase4/` — layout (packing, pass-through, openings, sizing
  errors, determinism), checks (collision/clearance, cutout placement, post
  clash, build volume, wall thickness, mesh watertightness), generator (STL
  export, report contents, failure path, engine-unavailable fallback) and the
  render script.
- `tests/planner/phase3/test_p3_geometry_feedback.py` — geometry error ->
  single repair -> ok, persistent error -> failed, warning-only -> ok
  (monkeypatched `generate_enclosure`, offline).
- `make test`: 294 passed. `make lint`: ruff and black clean.

**Verified in this session**

- `uv run pytest tests/geometry/phase4 tests/planner/phase3/test_p3_geometry_feedback.py`:
  31 passed.
- Both phase-owned fixtures build watertight meshes: soil monitor (base
  24 614 mm^3, lid 10 972, assembled 35 586, z-extent 0..22.8 mm) and divider
  (base 42 697, lid 16 550, assembled 59 247).
- `scripts/render_geometry.py` on the ultrasonic fixture writes a 55 KB PNG and,
  with `--export-dir`, three STLs plus `geometry_report.json`.

**UNVERIFIED**

- No STL was sliced, printed or test-fitted; wall clearances, screw-post
  engagement and lid fit are simulated only.
- The PNG preview was checked programmatically (dimensions, non-white content),
  not inspected visually in this session.
- Build-volume checks use the configured box only; no printer-specific
  bed or adhesion margins are modelled.

**Known gaps / deferred**

- Packing is a deterministic shelf/row heuristic, not an optimiser; certain
  combinations of large wall mounts can still fail with `LAYOUT_TOO_LARGE` and
  are reported, never approximated.
- Pass-through parts protrude from the shell by design; their cable/egress
  path (e.g. soil-probe cable) is not modelled.
- Vent patterns are not cut (`GEOMETRY_VENT_OMITTED` warning) in this phase.

## Phase 5 — Deterministic Firmware Generation and Compile Verification

**Built**

- `sparkplan/firmware/template.py` — template engine for the Phase 2+ catalog.
  It renders per-instance C++ fragments from `catalog/parts.json`: pin
  constants from validated connections only, instance-scoped objects
  (`screen_display`), per-instance readings (`<instance>_<reading>` plus
  `<var>_valid`), per-instance actions, `Wire.begin(SDA, SCL)` for I2C
  parts, the board's setup lines, and the part's minimum sampling interval.
  Unresolved placeholders, unknown parts and missing expressions are
  issues, never guesses. `canonical_payload`/`canonical_hash` give the
  deterministic spec hash (sha256/16) used in the sketch header.
- `sparkplan/firmware/behaviors.py` — behaviour based generator for
  `SupportedBuild`: readings, edge-triggered rules, display lines, the
  `millis()` tick with rollover-safe unsigned arithmetic, `isnan` guards
  that keep the last value and mark the reading invalid, rules that fire
  only on valid readings, and one buffer per display target. A rule that
  targets an actuator, an unknown reading, an ambiguous reading name or a
  unit mismatch makes the whole build `supported = False` with a
  machine-readable issue; nothing is dropped silently.
- `sparkplan/firmware/generate.py` — public API. `generate_firmware` takes
  either the legacy `BuildSpec` (split catalog, action pipeline) or the
  Phase 2+ `SupportedBuild` (`catalog/parts.json`, behaviour pipeline) and
  refuses a mismatched catalog. The legacy header now records components,
  GPIO assignments and the spec hash.
- `sparkplan/firmware/manifest.py` — pinned library registry. Every library
  a catalog part can need is pinned to one version with the download URL and
  the SHA-256 checksum from the official Arduino library index
  (`https://downloads.arduino.cc/libraries/library_index.json`); the
  transitive dependencies come from the `depends` field of the installed
  `library.properties` of that exact version. `resolve_libraries` returns
  the pinned closure of a sketch, `libraries.txt`/`library_manifest.json`
  are written next to the sketch, and an unpinned library is an error, never
  a floating "latest".
- `sparkplan/firmware/compile.py` — compile wrapper. It detects the CLI
  (`SPARKPLAN_ARDUINO_CLI`, `PATH`), installs the pinned libraries, compiles
  with `--fqbn esp32:esp32:esp32`, and reports `passed`, `failed`,
  `skipped` or `unsupported` with `Issue` objects
  (`ARDUINO_CLI_MISSING`, `ESP32_PLATFORM_MISSING`, `LIBRARY_UNAVAILABLE`,
  `LIB_INSTALL_ERROR`, `LIB_INSTALL_TIMEOUT`, `COMPILE_TIMEOUT`,
  `COMPILE_ERROR`). Extra build properties can be passed with
  `SPARKPLAN_ARDUINO_BUILD_PROPERTIES` (used below for the macOS ctags
  shim).
- `catalog/parts.json` — firmware templates completed for the parts the
  behaviour path needs: readings with type and expression, per-part
  `object_name`, `pin_map` entries and the protocol minimum intervals
  (dht22 2000 ms, bh1750 200 ms, hc_sr04 60 ms). `sparkplan/models/catalog.py`
  gained `Firmware.min_interval_ms`.
- `scripts/ci_compile_firmware.py` — CI driver. It renders and compiles the
  three legacy fixtures and the three behaviour fixtures, writes each sketch
  as `<folder>/<folder>.ino` (arduino-cli requires the main file to be named
  after its folder), writes the pinned manifest next to it, and fails when a
  sketch does not compile.
- `.github/workflows/firmware.yml` — CI job: pinned `arduino-cli` 1.5.1,
  pinned ESP32 core `esp32:esp32@3.3.12` (cached), the pinned libraries
  taken from the manifest (`python -m sparkplan.firmware.manifest
  --install-specs`, one library per line so a name with spaces is not
  split), the compile run, and the build artifacts.
- New fixture `tests/fixtures/phase3/supported/desk_weather_bme280.json`
  (BME280 + OLED, two display lines, one rule); it passes every Phase 2
  validator.

**Fixes found by compiling for real**

- The legacy renderer emitted the board pin label (`GPIO4`) where the ESP32
  core expects the GPIO number; the sketches never compiled
  (`error: 'GPIO4' was not declared in this scope`). The legacy path now
  emits the number the catalog pin stands for (`DHT env_dht(4, DHTTYPE);`),
  resolved from the catalog board pinout, never parsed by guesswork.
- The legacy renderer printed a float with a `%s` conversion
  (`"T=%sC"` with a float argument), which is undefined behaviour. The
  format is now converted to a conversion that matches the value type
  (`"T=%.1fC"`), and the argument is cast to match.
- A rule or a display line that could not be rendered still produced a
  sketch. The build is now `supported = False` with the issue attached.
- Found in the pre-merge audit: `write_manifest` fell back to the whole
  pinned registry when a sketch needed no library, so the manifest of a
  library-free sketch (for example the `hc_sr04` fixture) listed all ten
  pinned libraries. `write_manifest(out_dir, names)` now writes exactly the
  requested libraries, an empty list writes an empty manifest and only
  `None` (the command line default) writes the whole registry.
- Found in the pre-merge audit: the CI step fed the install specs to
  `xargs`, which splits a library name at its spaces, so
  `Adafruit BME280 Library@2.3.0` reached `arduino-cli lib install` as three
  arguments and the step failed with `Library 'Adafruit@latest' not found`
  before anything was compiled. The specs are now installed one per line
  with a `while read` loop; all ten pins install locally with exit 0.

**Tests**

- `tests/firmware/test_behaviors.py` (14), `test_template.py` (12),
  `test_manifest.py` (10), `test_compile.py` (12) — all offline; the compile
  tests use a fake CLI script.
- `make test`: 342 passed. `make lint`: ruff and black clean.

**Verified in this session**

- `python scripts/ci_compile_firmware.py` with `arduino-cli` 1.5.1 and ESP32
  core `esp32:esp32@3.3.12`: all six supported fixtures compile.

  | fixture | family | program storage | globals |
  | --- | --- | --- | --- |
  | `weather_station` | legacy actions | 307 404 B (23 %) | 23 692 B (7 %) |
  | `ultrasonic_distance` | legacy actions | 271 880 B (20 %) | 22 116 B (6 %) |
  | `servo_test` | legacy actions | 283 431 B (21 %) | 22 480 B (6 %) |
  | `soil_monitor` | behaviours | 314 016 B (23 %) | 23 812 B (7 %) |
  | `desk_weather_bme280` | behaviours | 311 980 B (23 %) | 23 780 B (7 %) |
  | `valid` (phase2/v8) | behaviours | 314 016 B (23 %) | 23 812 B (7 %) |

  The compile run was repeated from scratch during the pre-merge audit
  (the build folder was deleted first); the script exited 0 and every log
  in `build/firmware/<fixture>/compile.log` shows the compiler's own
  "Sketch uses ... bytes" line.

- The library pins were taken from the Arduino library index and every
  pinned version was installed locally; the dependency graph was read from
  the installed `library.properties` files.

**UNVERIFIED**

- The `firmware` workflow has never run. It triggers on pushes to `main` and
  on pull requests only, so it does not run on this branch; the `ci` workflow
  did run on the pushed commits (ruff, black and pytest green). The local
  compile of the same fixtures with the same pinned toolchain is the
  firmware evidence above, and the workflow's install step was verified
  locally.
- The sketches were compiled, not flashed: no board was connected, so no
  reading, rule or display line was observed on real hardware.
- The pinned library versions are the ones this project pins and installs;
  whether a newer version exists upstream was not checked beyond the index
  entries that were read.

**Known gaps / deferred**

- Hysteresis is still not representable in the schema, so a rule fires on
  the rising edge only and no hysteresis band is generated.
- A rule cannot drive an actuator: the schema says which actuator, not
  which action or with which argument, so such a build is refused with
  `FIRMWARE_UNSUPPORTED_RULE_ACTION` instead of guessing.
- A reading name that two instances provide is ambiguous and is refused
  (`FIRMWARE_AMBIGUOUS_READING`); the schema has no per-instance reading
  reference for rules and display lines.
- On this macOS host the Arduino `ctags` binary is Apple's `xcode-select`
  shim, which the ESP32 build kills (`Error during build: signal: killed`,
  then `exit status 1` with `ctags: illegal option -- -`). The local
  compiles therefore used
  `SPARKPLAN_ARDUINO_BUILD_PROPERTIES="tools.ctags.pattern=/usr/bin/true"`.
  CI on ubuntu-latest uses the real Arduino `ctags` and needs no such flag.
- Sampling intervals come from the part protocol, not from the user: the
  build schema still has no interval or hysteresis field.
