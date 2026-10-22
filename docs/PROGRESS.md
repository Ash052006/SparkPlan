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
- `make test`: 342 passed (historical baseline at phase completion). `make lint`: ruff and black clean.

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
  in `build/firmware/<fixture>/compile.log` is self-describing: it starts
  with the FQBN, the `arduino-cli` version and the installed core version,
  and ends with the compiler's own "Sketch uses ... bytes" line.

- The library pins were taken from the Arduino library index and every
  pinned version was installed locally; the dependency graph was read from
  the installed `library.properties` files.

**Phase 5 release**

- Merged PR #1 (`phase-5-firmware-generation` → `main`) on 2026-10-21. Merge
  commit `c6f4a52c3c60ac6c79b946f0c2f27a1fe5a6c523`. Tag `phase-5` points at
  that commit and is pushed to `origin`.
- Post-merge gates on `main`: `make test` → **342 passed, 2 warnings**;
  `make lint` → ruff `All checks passed!`, black `110 files would be left
  unchanged`.
- GitHub Actions on the PR head `9ff587d`: `ci` (ruff, black, pytest) →
  success; `firmware` (all six supported fixtures compiled, artifacts
  uploaded) → success. Toolchain: arduino-cli 1.5.1, ESP32 core
  `esp32:esp32@3.3.12`, FQBN `esp32:esp32:esp32`.
- The `firmware` workflow trigger (`on: push branches [main], pull_request`)
  was validated end to end by the PR run.

**UNVERIFIED**

- The `firmware` workflow was validated end to end by PR #1: it ran on
  `pull_request`, installed all ten pinned libraries and compiled all six
  supported fixtures, uploading the `firmware-builds` artifact. The local
  compile above used the same pinned toolchain with the macOS ctags
  workaround.
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

## Trace infrastructure — per-run trace records

**Built**

- `sparkplan/trace.py`: `RunTrace`, `TraceStage`, `create_trace`, `write_trace`.
  Schema version `1.0`; statuses `passed`, `failed`, `skipped`, `unverified`;
  UTC timestamps; stage issues serialized from `Issue`; relative artifact
  paths; deterministic JSON (`sort_keys=True`).
- `run_pipeline` now accepts optional `trace_path` and writes `trace.json`
  when `out_dir` is provided. Trace paths are atomically claimed and are not
  overwritten. Concurrent default runs receive distinct trace filenames.
- Stages recorded: `plan`, `validation`, `geometry_layout`,
  `geometry_meshes`, `geometry_stl_export`, `firmware_generation`,
  `firmware_compile`, `guide`, `slicing`, `simulation`.
- Every finalized trace contains all ten stages. Early termination records
  unattempted stages as `skipped` with a stage-specific reason; existing
  stage records are never overwritten. Unexpected exceptions mark the active
  stage as `failed` and complete the remaining stages without masking the
  original exception.
- `slicing` is `skipped` when `do_slice=False`; when requested it is
  `passed` only if both base and lid produce nonempty G-code, otherwise
  `failed` or `unverified` with a reason. `simulation` is `unverified`
  (Wokwi/hardware not performed).
- Atomic writes use a temporary file, `flush`, `fsync`, and `os.replace`.
  Trace write failures do not mask pipeline exceptions or results.

**Tests**

- `tests/pipeline/test_trace.py` (27; 20 at trace-repair time), all offline.
  Completeness regressions cover successful, refused, failed-planning,
  geometry layout/mesh, firmware generation/compile, and unexpected-exception
  traces, including retained prior statuses and later skipped reasons.
- `make test` at trace repair: 377 passed, 1 skipped. `make lint`: ruff and
  black clean.

**Trace reliability repair**

- Top-level trace status is now `failed` if any attempted required stage has
  status `failed`, including a failed firmware compile.
- `compile_firmware` verifies arduino-cli success, exit code, and the required
  `sparkplan.ino` output before marking `firmware_compile` as `passed`.
- Trace paths are claimed with `O_CREAT | O_EXCL`; default and explicit paths
  no longer rely on check-then-write. Concurrent runs receive distinct trace
  files.
- The unreachable legacy block after `run_pipeline()` was removed.
- Regression tests added for compile failure, missing compile output, successful
  compile, explicit-path preservation, path claiming, deterministic collision
  handling, and exception preservation.

**Verified in this session**

- Successful, refused, exhausted, geometry-failure, and unexpected-exception
  runs all produced the expected trace statuses.
- Compile failure and missing compile output now force a failed trace.
- Explicit path collisions and deterministic default-path collisions are tested.
- Explicit trace-path collisions raised `FileExistsError` without overwriting.
- Two runs sharing one output directory received distinct trace files.

**UNVERIFIED**

- Slicing is implemented and locally verified on Ubuntu 24.04 amd64; the
  GitHub Actions slicing job has not been verified on this repair branch.
- Simulation remains unverified.
- No physical hardware or Wokwi execution was performed.

## Catalog provenance repair

**Built**

- `sparkplan/models/catalog.py`: added optional `dimensions_source` and
  validated `dimensions_basis` (`manufacturer_spec`, `measured`, `estimate`,
  `unknown`). Manufacturer/measured bases require a non-empty source;
  estimated dimensions cannot be marked verified.
- `catalog/parts.json`: all 15 parts now declare
  `dimensions_basis: "estimate"` and `dimensions_source: null`.
  `dimensions_verified` remains `false` for every part.
- `tests/models/test_catalog_phase1.py`: added backward-compatibility,
  valid/invalid basis, manufacturer-source, round-trip, and unverified-estimate
  tests.

**Verified in this session**

- Catalog tests: `uv run pytest tests/models/test_catalog_phase1.py tests/catalog`
  → 31 passed.
- Provenance counts: 15 `estimate`, 0 `manufacturer_spec`, 0 `measured`,
  0 `unknown`.
- No manufacturer source references were verified; all unavailable sources are
  `null`.
- `make lint`: ruff and black clean.

**UNVERIFIED**

- No dimensions were verified against datasheets or measurements.
- Full suite after this change: `make test` → 369 passed, 2 warnings; `make lint` clean; coverage 89% (4801 statements, 505 missed).
- No manufacturer URLs or citations were added because none were verified in
  this session.

## Property-based validator tests

**Built**

- Added Hypothesis as a development dependency in `pyproject.toml`.
- `tests/validators/phase2/test_p2_property.py`: 9 property tests using
  `run_all` and the Phase 2 catalog.
  - direct HC-SR04 echo connections are rejected with `LOGIC_LEVEL_HIGH`
  - divider-routed echo connections avoid `LOGIC_LEVEL_HIGH`
  - flash GPIO assignments are rejected with `GPIO_FLASH_RESERVED`
  - non-flash GPIO assignments do not trigger `GPIO_FLASH_RESERVED`
  - duplicate OLED addresses are rejected with `I2C_ADDRESS_CONFLICT`
  - a single OLED does not report `I2C_ADDRESS_CONFLICT`
  - a servo on the 5 V rail is rejected with `RAIL_OVER_BUDGET`
  - renamed valid soil-monitor wirings remain valid
  - a soil monitor without a servo is not over budget
- Settings: bounded examples, `database=None`, `deadline=None`.

**Verified in this session**

- `uv run pytest tests/validators/phase2/test_p2_property.py` → 9 passed.
- `uv run pytest tests/validators/phase2` → 75 passed.
- `make lint`: ruff and black clean.

**UNVERIFIED**

- Properties are deterministic for the repository's current Hypothesis version,
  but broader random input spaces are still bounded by the strategies used.
- No mutation-testing tool was run to prove each property would fail if its
  validator were removed.

## Slicer verification

**Slicer choice**

- Selected **PrusaSlicer 2.7.2** from the Ubuntu 24.04 `universe` package
  `prusa-slicer=2.7.2+dfsg-1build2`.
- CuraEngine was checked first: `cura-engine` has no installation candidate on
  Ubuntu 24.04 amd64. PrusaSlicer is packaged, runs from the CLI without a
  display, and reports its version from `--help`.
- Installation is pinned in CI to the exact Ubuntu package version:
  `sudo apt-get install -y --no-install-recommends prusa-slicer=2.7.2+dfsg-1build2`
  on `runs-on: ubuntu-24.04`.
- Version observed from the installed CLI:
  `PrusaSlicer-2.7.2+UNKNOWN based on Slic3r (with GUI support)`. The
  executable reports `2.7.2+UNKNOWN`, not the exact package revision; the
  `dpkg` package version above is the authoritative pin.

**Built**

- `sparkplan/geometry/prusaslicer.ini` — controlled profile: 220 x 220 x 250 mm
  bed, 0.4 mm nozzle, PLA at 210 C / 60 C, 0.2 mm layers, 2 perimeters, 20 %
  grid infill, explicit start/end G-code. It is version-controlled and always
  passed with `--load`.
- `sparkplan/geometry/slicing.py` — additive `verify_slice(base_stl, lid_stl,
  output_dir=..., slicer=..., profile=..., timeout_s=..., enabled=...)`:
  validates both inputs before launch, resolves an executable, reads the
  version, runs PrusaSlicer with a temporary `--datadir` so local presets are
  not used, captures diagnostics with home-path and secret-value redaction,
  and checks exit code plus nonempty output for each part independently.
  Statuses are `passed`, `failed`, `skipped`, and `unverified`.
- `sparkplan/pipeline/pipeline.py` — new `do_slice=False` parameter, placed
  after the pre-existing `trace_path` parameter so positional `trace_path`
  callers keep their meaning, plus `PipelineResult.slice_result`. Slicing runs
  only when requested and an output directory exists. Trace stage `slicing`
  records requested state, slicer/version/profile, per-part status and paths,
  exit codes, diagnostics, and a reason. A requested slicing stage that is not
  `passed` forces the overall trace to `failed`; the pipeline result can still
  be `ok` because slicing is verification, not artifact generation.
- `.github/workflows/ci.yml` — new `slicing` job on `ubuntu-24.04` with the
  pinned PrusaSlicer install, a `dpkg-query` assertion for the exact package
  version, the executable-reported version recorded separately, and
  `scripts/ci_verify_slice.py`. The step logs to `build/slicing-ci.log` with
  `pipefail`, so a slicer failure still fails the job. An
  `actions/upload-artifact@v4` step runs with `if: always()`, uploads
  `build/slicing/*.gcode` and `build/slicing-ci.log`, warns rather than fails
  when files are absent, and retains them for 7 days.
- `scripts/ci_verify_slice.py` — generates the `soil_monitor` enclosure STLs,
  invokes `verify_slice`, prints exact commands/output sizes, and exits 2 when
  the slicer is unavailable.

**Tests**

- `tests/geometry/test_slicing.py` (13) covers missing/empty STLs, missing
  executable, missing profile, timeout, nonzero exit, zero exit with missing
  output, zero exit with empty output, separate base/lid outcomes, paths with
  spaces, disabled/skipped behavior, version parsing, and diagnostic
  redaction/truncation.
- `tests/pipeline/test_trace.py` (27) adds passed, failed, unverified, and
  missing-output-directory slicing cases alongside the existing trace tests.
  It also covers a positional `trace_path` argument to prevent the
  `do_slice` parameter from shifting it.
- `uv run pytest tests/geometry/test_slicing.py tests/pipeline/test_trace.py -q`:
  **40 passed**.
- `uv run pytest tests/geometry tests/pipeline/test_trace.py -q`:
  **77 passed, 1 skipped**.
- `make test`: **397 passed, 1 skipped, 2 warnings in 93.94s**.
- `make lint`: ruff `All checks passed!`; black
  `116 files would be left unchanged.`
- Coverage: full suite **89 %** (5019 statements, 541 missed);
  `sparkplan/geometry/slicing.py` **91 %** (164 statements, 14 missed);
  `sparkplan/pipeline/pipeline.py` **91 %** (278 statements, 24 missed);
  `sparkplan/trace.py` **88 %** (107 statements, 13 missed).

**Real project-native slicer invocation (Ubuntu 24.04, Python 3.12)**

- Platform: Ubuntu 24.04 amd64, Python 3.12, in a clean virtual environment
  with the real `sparkplan` package on `PYTHONPATH`. No project modules were
  stubbed.
- Installed package: `prusa-slicer=2.7.2+dfsg-1build2`.
- Executable version reported by `--help`: `2.7.2+UNKNOWN`
  (`PrusaSlicer-2.7.2+UNKNOWN based on Slic3r (with GUI support)`). The
  executable does not identify the exact package revision; the `dpkg` package
  version above is the authoritative pin.
- Profile: `sparkplan/geometry/prusaslicer.ini`.
- Generated fresh artifacts from
  `tests/fixtures/phase3/supported/soil_monitor.json`:
  `base.stl` 379,684 bytes and `lid.stl` 346,484 bytes.
- Exact commands (from the module's recorded `command` fields):

  ```text
  /usr/bin/prusa-slicer --export-gcode --output /work/project-native-out/real-base.gcode --load /repo/sparkplan/geometry/prusaslicer.ini --datadir /tmp/sparkplan-slicer-a62uo15i --loglevel 2 /work/real-base.stl
  /usr/bin/prusa-slicer --export-gcode --output /work/project-native-out/real-lid.gcode --load /repo/sparkplan/geometry/prusaslicer.ini --datadir /tmp/sparkplan-slicer-a62uo15i --loglevel 2 /work/real-lid.stl
  ```

- Both commands exited **0** and produced nonempty output:
  `real-base.gcode` 2,853,302 bytes and `real-lid.gcode` 696,035 bytes.
  `verify_slice` returned `passed` for both parts with no issues.
- PrusaSlicer reported all five observed lid warnings:
  `Floating object part`, `Floating bridge anchors`, `Low bed adhesion`,
  `Loose extrusions`, and `Long bridging extrusions`. These are preserved as
  diagnostic output and are not slicing failures.
- Successful slicing only proves that PrusaSlicer emitted nonempty G-code. It
  does not prove physical printability, mechanical strength, or dimensional
  fit.
- Running `scripts/ci_verify_slice.py` on this macOS host without PrusaSlicer
  generated the STLs, reported `SLICER_UNAVAILABLE` / `unverified`, and exited
  **2**. It did not claim a pass.

**UNVERIFIED**

- The GitHub Actions `slicing` job has not been verified on this repair branch.
  No local workflow runner (`act`) is installed. The
  pinned install, package-version assertion, and real-slicer command were
  verified locally in Ubuntu 24.04 amd64, but the hosted runner and artifact
  upload action remain unverified.
- Slicing proves only that PrusaSlicer emitted nonempty G-code. No print was
  made, and physical printability, fit, strength, and material suitability
  remain unverified.
- The profile is a generic enclosure profile, not tuned to a specific printer;
  the five lid warnings may require supports, brim, or reorientation on a real
  machine.

## Phase 6 — API and pipeline orchestration

### Step 2 — typed run results and safe persistence

**Built**

- `sparkplan/pipeline/run_models.py` — Pydantic run models with separate
  overall `RunStatus` (`running`, `ok`, `refused`, `unsupported`, `failed`)
  and per-stage `StageStatus` (`passed`, `failed`, `skipped`, `unverified`).
  `RunStageResult` requires a reason for skipped/unverified stages and rejects
  error-severity issues on passed stages. `RunResult` rejects duplicate stage
  names and rejects any non-failed overall status when a stage failed.
  Serialization is deterministic, reuses the existing `Issue` schema, and
  accepts only relative POSIX artifact/evidence references.
- `sparkplan/pipeline/run_paths.py` — `RunPaths` allocates the required
  artifact names (`spec.json`, `report.json`, `base.stl`, `lid.stl`,
  `assembled.stl`, `sketch/sketch.ino`, `GUIDE.md`, `trace.json`) under one
  run directory. Run IDs are limited to 64 characters matching
  `[A-Za-z0-9_-]+`. Claims use exclusive `os.mkdir`, so an existing run is
  never reused. Artifact paths are rejected if they escape the run directory
  or traverse a symlink. JSON is written through a temporary file and
  hard-linked into place, refusing overwrite. Text and binary artifacts also
  refuse overwrite. `SPARKPLAN_RUNS_DIR` can override the default `runs/`
  root.

**Tests**

- `tests/pipeline/test_run_models.py` (35) covers status values, deterministic
  JSON, issue serialization, duplicate stages, overall/stage status
  consistency, skipped/unverified reasons, passed-stage errors, safe and
  unsafe references, and run-ID bounds.
- `tests/pipeline/test_run_paths.py` (34) covers valid/invalid IDs, environment
  root selection, exclusive/idempotent/concurrent claims, existing-run
  collisions, artifact containment, unknown artifacts, claim-required writes,
  deterministic atomic JSON, no-overwrite behavior, serialization cleanup,
  text/binary writes, and file/directory/run-directory symlink rejection.
- `uv run pytest tests/pipeline/test_run_models.py tests/pipeline/test_run_paths.py -q`:
  **69 passed**.
- `make test`: **466 passed, 1 skipped, 2 warnings in 91.53s**.
- `make lint`: ruff `All checks passed!`; black
  `120 files would be left unchanged.`

**Known gaps at the end of Step 2**

- The canonical `run(prompt, run_id)` orchestrator was not implemented in
  Step 2; Step 3 below adds it. New API endpoints and UI integration remain
  unimplemented.
- Step 2 exercised no enclosure, firmware, slicer, compiler, or print behavior.

### Step 3 — canonical run orchestrator

**Built**

- `sparkplan/pipeline/run.py` — canonical `run(idea, llm, *, run_id=None,
  runs_root=None, catalog=None, do_slice=True, do_compile=True, ...)`
  orchestrator, separate from the legacy `run_pipeline()`/`/v1` API. It emits
  all seven stages in the fixed order `plan`, `validation`, `geometry`,
  `slicing`, `firmware_generation`, `firmware_compile`, `guide`.
- The run directory is not created until the planner returns a validated
  `SupportedBuild`. Refusal, unsupported ideas, planning failures, and planner
  exceptions return a terminal `RunResult` with no runs-root or run-directory
  artifacts. Existing run directories are rejected before the LLM is called.
- The supported spec is persisted exactly once as `spec.json`. Validation is
  not rerun; the planner's `ValidationReport` is recorded as the validation
  stage. Geometry is then generated with `out_dir` set to the run directory and
  its STL files are verified to exist and be nonempty.
- Slicing is skipped when geometry did not export valid STLs. `do_slice=False`
  skips the stage without invoking the slicer. A missing PrusaSlicer is
  `unverified`; a nonzero exit, missing/empty G-code, or an internally
  inconsistent slicer result is `failed`.
- Firmware generation and compilation run even after geometry or slicing
  failures. Firmware is persisted as `sketch/sketch.ino`; compilation uses the
  same filename with `write_sketch_file=False` so the canonical pipeline does
  not overwrite the exclusive artifact.
- A missing `arduino-cli`/ESP32 platform/library is `unverified`; an unpinned
  library, real compile failure, or missing sketch is `failed`. `do_compile=False`
  skips the compile stage without invoking the compiler.
- `render_supported_guide` now renders a deterministic Phase 3 guide from the
  supported spec, optional enclosure layout, optional firmware result, and
  accumulated issues. The guide runs even when geometry, slicing, or compile
  fails, and explicitly says when no enclosure layout or firmware was produced.
- `report.json` is the `RunResult` summary. `trace.json` carries the full
  stage order, per-stage status/timing/issues, plan and validation evidence,
  per-part slicing details, compile log (paths/home/secrets redacted and
  truncated), and safe relative artifact references. Issue messages, stage
  reasons, slicer diagnostics, and the compile log are redacted before either
  file is written; absolute run paths are not emitted.
- Overall status is `failed` if any canonical stage failed. Unavailable tools
  remain `unverified`, skipped work remains `skipped`, and neither is ever
  reported as `passed`. `RunResult` now permits `refused`/`unsupported` outcomes
  to carry a failed `plan` stage, and only the plan stage.
- `sparkplan/firmware/generate.py` — `write_sketch` accepts an explicit safe
  `filename`/`sketch_name`; `compile_firmware` accepts `sketch_name` and
  `write_sketch_file`, with the legacy `sparkplan.ino` and write-now defaults
  unchanged.

**Tests**

- `tests/pipeline/test_run.py` (17) covers the full successful artifact
  inventory and trace, refusal/unsupported/planning-failure/no-artifact paths,
  planner exceptions, geometry-repair exhaustion with honest validation,
  existing-run rejection, invalid run IDs, geometry
  failure/engine-unavailable/missing-STL behavior, missing and inconsistent
  slicer handling, missing/real-failing compiler handling, disabled slicing
  and compile, firmware/guide continuation after failure, and relative-only
  report/trace references.
- `tests/pipeline/test_run_models.py` (38) adds the refused/unsupported
  failed-plan exception and rejects failed validation stages for those
  terminal outcomes.
- `tests/firmware/test_generate.py` (13) adds explicit sketch naming and
  rejection of unsafe or non-`.ino` names.
- `uv run pytest tests/pipeline/test_run.py tests/pipeline/test_run_models.py tests/pipeline/test_run_paths.py tests/firmware/test_generate.py -q`:
  **102 passed**.
- `uv run pytest tests/pipeline -q`: **120 passed**.
- `make test`: **491 passed, 1 skipped, 2 warnings in 128.17s (0:02:08)**.
- `make lint`: ruff `All checks passed!`; black
  `122 files would be left unchanged.`

**UNVERIFIED**

- This macOS host has no `arduino-cli` and no PrusaSlicer, so canonical runs
  cannot be exercised end-to-end with real external tools here. Tool-less tests
  verify the `unverified` paths; real compile/slice success remains unverified.
- No physical print or hardware run was performed.
- GitHub Actions for this branch has not been checked in this session.

**Known gaps**

- The Step 4 API endpoints and the React/three.js UI integration are not
  implemented. `sparkplan/pipeline/__init__.py` intentionally still exports only
  the legacy pipeline; the canonical entry point is
  `sparkplan.pipeline.run.run`.
- The canonical run is sequential and synchronous; it does not add background
  jobs, progress streaming, cancellation, or retry/queue behavior.
- The guide renderer reuses conservative legacy assembly wording and does not
  claim printability or physical fit.

### Step 4 — canonical API and web integration

**Built**

- `api/v2.py` — canonical `/v2` router with exactly:
  `POST /v2/runs`, `GET /v2/runs/{run_id}`, and
  `GET /v2/runs/{run_id}/artifacts/{artifact_name}`. New runs are
  synchronous, server-generate their run ID, and go through
  `sparkplan.pipeline.run.run` with the Phase 3 LLM and canonical catalog.
  `RunResult` is returned directly.
- Terminal domain outcomes (`ok`, `refused`, `unsupported`, `failed`) return
  HTTP `200`. Unknown/unavailable runs and artifacts return `404`, request
  validation returns `422`, missing or invalid canonical LLM configuration
  returns `503`, and unexpected failures return a safe
  `{"detail": {"code", "message"}}` envelope. No exception text, environment
  values, or absolute paths are returned.
- Artifact downloads are limited to `spec`, `base_stl`, `lid_stl`,
  `assembled_stl`, `sketch`, and `guide`; `report.json` and `trace.json` are
  not downloadable through the artifact route. Paths are resolved with
  `RunPaths`, and the persisted report must name the expected inventory
  reference. Traversal, symlinks, missing files, and corrupt reports are
  rejected. Downloads use fixed media types, filenames, and `nosniff`.
- `api/app.py` — preserves the legacy `create_app(llm, catalog)` signature and
  all `/v1` routes. New keyword-only canonical dependencies are injectable for
  tests: `canonical_llm`, `canonical_catalog`, `runs_root`, and
  `canonical_run`. `/healthz` is unchanged.
- `web/src/api.ts` — typed `/v2` client for starting runs and loading artifacts.
- `web/src/App.tsx` — now uses `/v2`; renders overall status, all seven stages,
  per-stage durations/reasons/issues, terminal refusal/unsupported/failure
  reasons, the spec, guide, available artifact downloads, and the base/lid STL
  viewer. Skipped and unverified stages are labelled explicitly.
- `web/src/styles.css` — status badges and stage/issue/download layout for the
  canonical states.
- `web/vite.config.ts` — proxies `/v2` while keeping `/v1` and `/healthz`.
- `README.md` — documents the canonical routes, artifact allowlist, terminal
  status behavior, error codes, synchronous execution, and legacy `/v1`.

**Tests**

- `tests/api/test_api_v2.py` (17) covers the persisted `RunResult`, server-side
  run IDs, `Location`, terminal `200` outcomes, 404/422/503/500 behavior,
  safe error bodies, artifact media types/filenames, report/trace exclusion,
  symlinks, malformed reports, and an end-to-end API run through the default
  canonical runner with mocked geometry/slicer/compiler dependencies.
- `uv run pytest tests/api -q`: **23 passed**.
- `make test`: **508 passed, 1 skipped, 2 warnings in 136.12s (0:02:16)**.
- `make lint`: ruff `All checks passed!`; black
  `124 files would be left unchanged.`
- `npx --prefix web tsc --noEmit -p web/tsconfig.json`: clean.
- `npm --prefix web run build`: succeeded; Vite emitted its existing
  >500 kB chunk-size warning for three.js.

**UNVERIFIED**

- The canonical API was not exercised against a real LLM endpoint; tests use
  `FakeLLM`.
- This macOS host still has no `arduino-cli` or PrusaSlicer, so real
  compile/slice success through the API remains unverified.
- The React UI was verified by TypeScript and a production Vite build, not by
  manual browser interaction or a real end-to-end run.
- GitHub Actions for this branch has not been checked in this session.
- No physical print or hardware run was performed.

**Known gaps**

- `/v1` remains the legacy in-memory build API; `/v2` is the canonical API.
  `sparkplan/pipeline/__init__.py` intentionally still exports only the legacy
  pipeline, so the canonical entry point is `sparkplan.pipeline.run.run`.
- The canonical run is synchronous and sequential. Step 4 adds no background
  jobs, progress streaming, cancellation, queue, retry, run history, or deep
  links.
- Step 4 work was uncommitted on `phase-6-api-pipeline` when this section was written.

### Phase 6 — guide, pipeline, background API and initial gate attempt (superseded)

**Status**

The implementation and offline gate passed, but the first real configured-LLM
curl smoke test did not produce a completed run. This attempt is superseded by
the final gate rerun below. Nothing was merged into `main`, `main` was not
pushed, and no `phase-6` tag was created or moved. The feature branch remained
uncommitted at the time of this report.

**Built**

- `sparkplan/guide/models.py` — typed `RunVerification`, with only `true`,
  `false`, or `skipped`; the default is `skipped`, never passed.
- `sparkplan/guide/render.py` — the supported guide now contains a BOM with
  quantity and role, a wiring table with wire colours, a Mermaid wiring
  diagram, board rail/power notes, print settings (0.2 mm PLA/PETG), numbered
  assembly steps, behaviours, warnings, a pre-flight checklist, and a final
  Verification table followed by the exact line `Not physically built.`
- `sparkplan/pipeline/run.py` — computes verification from actual stage
  outcomes, passes it to the guide, and writes it to `trace.json` alongside
  stage timings and planner attempt history. Refusals and unsupported ideas
  still stop before creating a run directory.
- `sparkplan/pipeline/__init__.py` — exports the canonical `run` while keeping
  `PipelineResult` and `run_pipeline` compatible. The package layout is kept;
  no shadowing `sparkplan/pipeline.py` was created.
- `api/jobs.py` — bounded, thread-safe background worker manager with a
  max-worker and max-pending bound, safe failed results, and no raw exception
  leakage.
- `api/limits.py` — thread-safe in-memory per-IP fixed-window limiter for
  `POST /plan`, defaulting to 10 requests per 60 seconds.
- `api/canonical.py` — additive root API: `POST /plan`, `GET /runs/{id}`,
  `GET /runs/{id}/files/{name}`, `GET /runs/{id}/bundle.zip`, `GET /catalog`,
  and `GET /health`. Prompts are limited to 500 characters. File names and ZIP
  entries are allowlisted, symlinks/traversal are rejected, and errors use a
  safe code/message envelope.
- `api/app.py` — preserves `/v1`, `/v2`, and `/healthz`; adds CORS for the Vite
  origins, injectable background/limiter dependencies, and the canonical root
  router.
- `web/src/api.ts`, `web/src/App.tsx`, `web/vite.config.ts` — the UI submits to
  `/plan`, polls `GET /runs/{id}`, renders the running state, loads files from
  the filename route, and offers `bundle.zip`.
- `.env.example` — documents `SPARKPLAN_RUNS_DIR`, `SPARKPLAN_SLICER`,
  `SPARKPLAN_CORS_ORIGINS`, `SPARKPLAN_RATE_LIMIT`, and
  `SPARKPLAN_RATE_WINDOW_S`.
- `README.md` — documents the asynchronous root API, limits, CORS, safe file
  access, bundle, and the retained `/v2` compatibility API.

**Offline verification**

- `make test`: **528 passed, 1 skipped, 2 warnings in 142.10s (0:02:22)**.
- `make lint`: ruff `All checks passed!`; black
  `131 files would be left unchanged.`
- `npx --prefix web tsc --noEmit -p web/tsconfig.json`: clean.
- `npm --prefix web run build`: succeeded; Vite emitted the existing
  >500 kB chunk-size warning for three.js.
- `git diff --check`: clean.
- Tests include guide content/determinism, all verification states, pipeline
  trace verification, the existing single Phase 3 geometry repair round,
  missing-tool truthfulness, root API background polling, worker failure,
  500/501 prompt boundaries, rate limiting, CORS, file traversal/symlinks,
  bundle contents, and a default canonical runner end-to-end through the root
  API with FakeLLM and mocked geometry/slicer/compiler.

**Real configured-LLM curl smoke test — FAILED**

The server was started with the configured `.env` on
`127.0.0.1:8765` using `set -a; . ./.env; set +a` because `uvicorn
--env-file` requires the unavailable optional `python-dotenv` package.
`GET /health` returned `{"status":"ok"}`. Three real `POST /plan` requests each
returned `202` immediately with a `Location` header and a run ID; polling
returned `running` and then `failed`. No run artifacts were produced.

- `b1618a339fc748aab232179d5e876340` — failed after 3 repairs with
  `NO_GROUND`, `NO_POWER`, and `NO_MOUNT_ZONE` for the OLED, soil sensor, and
  BME280.
- `2356f7fa0a294e13ac0da41fd70343da` — failed after 3 repairs with
  `NO_MOUNT_ZONE` for the OLED.
- `636facdec3b242ba8fa8cd6e692cfcc7` — failed after 3 repairs with
  `NO_GROUND` and `NO_POWER` for the capacitive soil sensor.

The configured LLM did not produce a valid `SupportedBuild` within the bounded
repair rounds. Consequently the real run did not reach geometry, firmware,
guide, or verification output. This is a real gate failure, not a fabricated
pass.

**UNVERIFIED**

- Real compile and slicing success: this macOS host has no `arduino-cli` and
  no PrusaSlicer, so tool-less tests verify only `skipped`/`unverified` paths.
- Real guide output from a completed LLM run was not produced; guide content
  and the exact `Not physically built.` line are verified with FakeLLM/mocks.
- The React UI was typechecked and production-built, not manually exercised in
  a browser.
- GitHub Actions for this branch was not run in this session.
- No physical print or hardware build was performed.

**Known gaps**

- Background job state is in-memory. Completed runs reload from `report.json`
  after restart; refused/unsupported jobs with no artifacts are lost on
  restart.
- Rate limiting is process-local, not distributed.
- There is no cancellation, progress streaming, or run-history browser.

### Phase 6 — final gate rerun (GATE PASSED)

**Status**

The offline gate and the real configured-LLM gate now pass. A real API run
completed with status `ok`, generated a guide, and marked unavailable tools as
`unverified` rather than passed. The implementation and evidence are on
`phase-6-api-pipeline`; nothing was merged into `main`, `main` was not pushed,
and no `phase-6` tag was created or moved.

**Fixes made after the initial failed gate**

- `sparkplan/planner/phase3/normalize.py` — canonicalizes unambiguous
  `LayoutHint.part_id` instance-id aliases to catalog ids, and rewrites
  board-first connections (`board -> peripheral`) as
  `peripheral -> board`, preserving signal role, rail name, and pins.
- `sparkplan/planner/phase3/planner.py` — applies both normalizations before
  `parse_build_spec`; truncation hints now tell the operator to raise
  `SPARKPLAN_LLM_MAX_TOKENS`.
- `sparkplan/planner/phase3/prompt.py` — layout ids must be catalog ids, never
  instance ids; connections must be written peripheral-first; behavior
  readings must use the exact catalog reading name and unit.
- `sparkplan/validators/phase2/behaviors.py` — behavior readings must exist in
  the catalog, be provided by the bound instance, and use the matching unit.
  New codes: `BEHAVIOR_UNBOUND_READING`, `BEHAVIOR_UNKNOWN_READING`,
  `BEHAVIOR_UNIT_MISMATCH`.
- `sparkplan/pipeline/run.py` — the geometry artifact check now allows the
  `geometry_report` entry alongside the three required STLs.
- `.env.example` and `README.md` — recommend
  `SPARKPLAN_LLM_MAX_TOKENS=1800` for multi-sensor BuildSpecs.
- Regression coverage was added in
  `tests/planner/phase3/test_p3_layout_aliases.py`,
  `tests/planner/phase3/test_p3_prompt_compact.py`,
  `tests/validators/phase2/test_p2_behaviors.py`, and
  `tests/pipeline/test_run.py`.

**Offline verification**

- `make test`: **542 passed, 1 skipped, 2 warnings in 245.86s (0:04:05)**.
- `make lint`: ruff `All checks passed!`; black
  `133 files would be left unchanged.`
- `npx --prefix web tsc --noEmit -p web/tsconfig.json`: clean.
- `npm --prefix web run build`: succeeded; Vite emitted the existing
  >500 kB chunk-size warning (621.37 kB JS, 167.72 kB gzip).
- `git diff --check`: clean.

**Real configured-LLM smoke — PASSED**

The server was started on `127.0.0.1:8765` with the configured `.env`,
`SPARKPLAN_LLM_MAX_TOKENS=1800`, and
`SPARKPLAN_RUNS_DIR=/tmp/sparkplan-real-runs`. `GET /health` returned
`{"status":"ok"}`. `POST /plan` returned `202` with run id
`ee5427698c7546dbb3d39eb0a5dd61f3`, and polling reached status `ok` with:

- `plan` passed in 16.26 s with 1 LLM call and 0 repair rounds.
- `validation` passed.
- `geometry` passed in 6.15 s using cadquery.
- `slicing` unverified: `PrusaSlicer is not installed or is not executable.`
- `firmware_generation` passed: sketch and library manifest produced.
- `firmware_compile` unverified: `arduino-cli not found`.
- `guide` passed.

Artifacts: `spec.json`, `trace.json`, `report.json`, `base.stl`, `lid.stl`,
`assembled.stl`, `geometry_report.json`, `sketch/sketch.ino`,
`sketch/libraries.txt`, `sketch/library_manifest.json`, and `GUIDE.md`.
The guide states `Validated: true`, `Compiled: skipped`, `Sliced: skipped`,
`Simulated: skipped`, and ends with `Not physically built.`

**UNVERIFIED**

- Real compilation: `arduino-cli` is not installed on this host; the run
  correctly reports `Compiled: skipped` / stage `unverified`.
- Real slicing: PrusaSlicer is not installed; the run correctly reports
  `Sliced: skipped` / stage `unverified`.
- The React UI was typechecked and production-built, not manually exercised in
  a browser.
- GitHub Actions for this branch was not run in this session.
- No physical print or hardware build was performed.

**Known gaps**

- Background job state is in-memory. Completed runs reload from `report.json`
  after restart; refused/unsupported jobs with no artifacts are lost on
  restart.
- Rate limiting is process-local, not distributed.
- There is no cancellation, progress streaming, or run-history browser.
- For this run the LLM supplied numeric controller pins (`21`, `22`, `34`);
  validators and firmware resolve them as GPIOs, and the guide displays them
  verbatim. Catalog-style `D21`/`D22` would be clearer.
- The generated behavior description rendered as `No description.` because
  the LLM omitted it; the guide stays truthful rather than inventing text.

## Phase 8 — web UI (GATE PASSED)

**Status**

The web UI is implemented on `phase-8-web-ui`, based on the Phase 6 API commit
`be1ab97`. The offline web gate passes: typecheck, production build, two mobile
Playwright smoke tests, full Python tests, and lint. The branch has not been
merged, `main` has not been changed, and no `phase-8` tag has been created or
moved. Phase 7 remains separately parked as a WIP commit and is not part of this
branch.

**Built**

- Canonical typed web client in `web/src/api.ts` for `POST /plan`,
  `GET /runs/{id}`, artifact text/JSON, `trace.json`, `GET /catalog`, and the
  ZIP bundle. Polling handles queued/running/success/refused/failed and aborts
  stale requests on reset/unmount.
- Prompt view with a live 500-character counter, disabled submit while loading,
  four example prompts, request errors, and retry.
- Seven-stage progress stepper. Running runs show queued stages because the API
  does not stream partial progress; completed stages are rendered from the run
  result only.
- Results: verification badges for validated/compiled/sliced/simulated with
  `passed`/`failed`/`skipped`/unknown, plus a visible `Not physically built.`
  statement; BOM; wiring with catalog pin facts; validation issues with severity;
  read-only firmware with copy; rendered `GUIDE.md`; report/trace/spec artifact
  links; and bundle download.
- Dedicated refusal view using planner-provided reasons, missing capabilities,
  and closest supported alternative. No artifacts or capabilities are invented.
- Failure view with stage issues and retry.
- `three.js` STL viewer with OrbitControls, base/lid/assembled modes, an
  assembled lid offset derived from loaded bounds, an exploded slider, auto-fit,
  resize handling, cleanup, theme updates, mouse/touch controls, and keyboard
  orbit/zoom. It remains explicitly illustrative, not a fit check.
- Responsive light/dark UI with persisted theme choice and a manual toggle.
- Backend `RunRefusal` metadata on `RunResult`, populated only for planner
  `refused`/`unsupported` outcomes and serialized only when present. Existing
  success/failure serialization is unchanged.
- Playwright smoke tests use mocked canonical API responses and cover the
  success and refusal flows at 390×844 in light/dark mode, no horizontal
  overflow, no browser console/page errors, no duplicate plan submission, and
  an artifact-free refusal.

**Verification**

- `make test`: **544 passed, 1 skipped, 2 warnings in 284.46s (0:04:44)**.
- `make lint`: ruff `All checks passed!`; black
  `133 files would be left unchanged.`
- `npm --prefix web run typecheck`: clean.
- `npm --prefix web run build`: succeeded; Vite emitted the existing
  >500 kB chunk-size warning (824.77 kB JS, 227.71 kB gzip).
- `npm --prefix web run test:e2e`: **2 passed (3.7s)** with Chromium.
- Mobile screenshots: `docs/phase8/success-390.png` (390×5275) and
  `docs/phase8/refusal-390.png` (390×2324).
- CI now installs Chromium and runs the Playwright smoke tests in the web job.

**UNVERIFIED**

- The Playwright tests use mocked API responses. The UI has not been driven
  against a live FastAPI server plus a real LLM in a browser in this session.
- The STL viewer was exercised in headless Chromium for mode/slider/keyboard
  behavior and console cleanliness, not visually compared against a physical
  enclosure or slicer.
- GitHub Actions for this branch was not run in this session.
- `arduino-cli` and a real slicer remain unavailable on this host; compile and
  slice truthfulness still relies on the existing `skipped`/`unverified` paths.
- No physical print, wiring, or hardware build was performed.

**Known gaps**

- Progress is polled; there is no server-sent stage streaming or cancellation.
- Completed runs reload from `report.json` after restart. Refused/unsupported
  runs with no artifacts remain in memory only, so old persisted reports have no
  `refusal` object and fall back to the plan-stage reason.
- Playwright covers smoke behavior, not pixel-level visual regression.
- The production bundle is a single large chunk because `three.js` is imported
  eagerly; the build passes with a warning.
- Run background jobs and rate limits remain process-local.
