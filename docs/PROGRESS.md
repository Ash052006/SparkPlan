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

## Phase 3 stabilization — token reduction and wiring guidance

Branch `phase-3-stabilize` (off `main` after `phase-3`). No architecture change;
the legacy planner/API/pipeline/eval paths are untouched. This section records
the audit and the changes made from it.

**Audit (concrete causes)**

1. Excessive tokens.
   - The digest sent a per-part prose `description` (`desc`, truncated to 60
     chars) that duplicates the capability list and is read by no validator.
   - The system prompt (digest + compact schema) is re-sent on every one of up
     to four calls; nothing was trimmed between rounds.
2. Repair rounds burning quota.
   - `_repair_prompt` echoed the **entire previous spec** plus the full user
     message (idea re-wrapped) plus indented issues on every round.
3. Infeasible power not pre-recognized.
   - The prompt stated the rail rule abstractly but never told the model to sum
     draw per rail, to use peak current for surging parts, or to refuse rather
     than emit an over-budget build.
4. Missing wiring.
   - The prompt conflated two rules: V4 wants VCC+GND, but V7 wants **every**
     non-board pin connected. The digest listed pins but did not say all of them
     must appear on a connection endpoint.

   Confirmed **not** bugs: the drone `unsupported` and the night-light
   `RAIL_OVER_BUDGET` are the validators working correctly.

**Changes**

- `sparkplan/planner/phase3/prompt.py`
  - Dropped the per-part `desc` from the digest (pure token saving; capabilities
    remain the selection signal). Digest legend now notes `mount` is omitted when
    `base`.
  - Rule 4 rewritten: sum each rail's draw, use PEAK `i_ma` for surging parts
    (servos, LED strips) else typical, and reply `unsupported` (with the rail
    reason) if the only catalog parts that fit would exceed a rail budget.
  - Rule 5 rewritten: connect **every** listed pin, not just VCC/GND (mirrors V7).
- `sparkplan/planner/phase3/planner.py`
  - `_repair_prompt(idea, issues)` now sends the idea once plus only the
    `error`-severity issues (code/message/fix_hint/part_ids, compact JSON). The
    previous spec is no longer echoed.
- `sparkplan/planner/phase3/models.py`
  - `Attempt` gained `estimated_request_tokens` (per-round local size estimate).
- `scripts/try_plan.py`
  - Prints the last-call request estimate and a per-attempt token breakdown.

**Measured effect (local estimate, this session)**

- System prompt: 10,097 -> 9,456 chars (~5,049 -> ~4,729 est tokens).
- Catalog digest: 4,203 -> 3,236 chars (~2,102 -> ~1,619 est tokens).
- Repair prompt for a failing soil-monitor spec: ~2,861 -> 844 chars
  (~1,432 -> ~423 est tokens), roughly 70% smaller per repair round. The echoed
  spec alone was ~2,017 chars.

**Tests**

- Added: digest omits `desc`; prompt states the concrete power pre-check and the
  "every pin" rule; repair prompt omits the previous spec; repair prompt filters
  to error severity; attempts record `estimated_request_tokens`.
- `make test`: 269 passed (was 263). `make lint`: ruff and black clean.
- Coverage (new packages): 99% (`sparkplan.llm.phase3` 99%,
  `sparkplan.planner.phase3` 98-100%).

**UNVERIFIED**

- All savings above are local `estimate_tokens` figures (~2.0 chars/token,
  deliberately conservative), not provider-measured. No live Groq run was made
  in this stabilization pass (offline verification only, by request).
- The new power pre-check and every-pin rules are prompt guidance; whether the
  model obeys them on the 1,000-output-token/min tier was not re-tested live.
- The catalog's current figures and rail budgets remain approximate.

**Known gaps / deferred (unchanged from Phase 3)**

- Persistent over-budget validation still yields `failed`, not `unsupported`
  (approved: only LLM refusals are `unsupported`).
- The legacy planner/client/validators remain separate and unreconciled.

## Phase 3 stabilization — live verification attempt

Ran against the real Groq API (`qwen/qwen3.8-27b`, `max_tokens=900`,
`max_retries=3`, base `https://api.groq.com/openai/v1`). Offline gates first:
`make test` 270 passed, `make lint` clean. API key never printed.

**Fix found and made from live evidence**

The first live desk-plant run returned `failed` with `LLM_TRUNCATED` on all four
calls. Inspecting the raw response showed the completion was 2,582 chars and
**ended in complete, parseable JSON** (`...}]}}`) yet `finish_reason="length"`
because the provider counts its own tokens to the 900 cap. The planner was
discarding an already-valid response and re-asking, burning the whole call
budget. Fix: `planner.py` no longer rejects a truncated response up-front; it
always attempts `_parse`, and truncation only changes how an unparseable
response is labelled (`truncated` + `LLM_TRUNCATED` hint vs `parse_error`). A
parseable-but-truncated response now flows into validation like any other.
Regression tests updated/added in
`tests/planner/phase3/test_p3_planner.py`:
`test_truncated_but_parseable_valid_spec_is_accepted`,
`test_truncated_unparseable_triggers_repair`,
`test_always_truncated_unparseable_is_failed_not_ok`.

**Live results (this session)**

| Prompt | Status | Calls | Repair rounds | Result |
|---|---|---|---|---|
| Build a desk plant monitor | `failed` | 4 | 3 | Real validator errors (12), see below |
| Build a motion-triggered night light | `failed` | 1 | 0 | BLOCKED: HTTP 429 (TPD) |
| Build a door distance alarm | — | — | — | BLOCKED: HTTP 429 (TPD), not run |

- **Desk plant monitor** (post-fix): after the truncation fix the run produced
  genuine `NO_GROUND`, `NO_POWER`, `PIN_UNCONNECTED`, `NO_MOUNT_ZONE` and
  `BEHAVIOR_UNIT_MISMATCH` errors and fed them back as repair feedback across
  rounds. The model kept emitting a spec missing `layout` entries and several
  connections (it also added an unrequested `piezo_buzzer_passive`), so repairs
  did not converge and the final verdict was `failed` with 12 errors. `status`
  was correctly never `ok` while errors remained. Provider usage per call:
  prompt ~2,947 then ~3,453/~3,667 (grows with repair feedback),
  completion 900 (capped) each.
- **Motion-triggered night light** and **door distance alarm**: not completed.
  Groq returned HTTP 429 on every real (multi-thousand-token) request.

**Blocker: Groq daily token quota**

The binding limit is **tokens per day (TPD) = 200,000** on the on-demand tier
for `qwen/qwen3.8-27b`, not just the per-minute cap. At the end of this session
`Used=198,484` and any 2,950-token request was rejected (reset ~10 min later).
A single plan costs roughly 12-15k prompt tokens (system prompt ~2,950 real
tokens x up to 4 calls) plus up to 3.6k completion, so only about a dozen real
model calls per day are available. This cannot reliably cover three multi-call
plans. Tiny one-token probes sometimes returned HTTP 200, which is why quota
looked intermittently available.

**Local estimate vs provider-reported usage**

- Local `estimate_tokens` (~2.0 chars/token) for the last request of the desk
  run: ~5,091 then ~6,071 tokens; provider-reported prompt for the same first
  call was 2,947. The local estimate is ~1.7x conservative, as designed.
- Provider-reported completion was pinned at 900 (the configured cap) on every
  desk call; the local estimate cannot see the cap.

**Merged / tagged?**

No. Per the phase-completion gate and the user's instruction, the live gate is
**blocked**, so `phase-3-stabilize` is NOT merged and no tag was moved. The
existing `phase-3` tag (at `b195ae7`) was left untouched — moving it is
forbidden without explicit authorization.

**Remaining risks / manual verification needed**

- Live end-to-end success (a real `ok` spec passing Phase 2) was not observed
  this session; the desk monitor converges poorly because the model omits
  `layout`/connections. Whether the sharpened rules help is UNVERIFIED.
- The `RAIL_OVER_BUDGET` night-light question could not be re-inspected live.
- Catalog current figures and rail budgets remain approximate.
- Re-running needs either a higher Groq tier, a different model, a much smaller
  prompt, or spreading calls across days. All three prompts should then be
  re-run and recorded here.
