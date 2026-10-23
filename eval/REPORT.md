# SparkPlan evaluation report

## Scope

| Field | Value |
| --- | --- |
| Mode | `fake` (scripted FakeLLM) |
| Cases file | `eval/cases.jsonl` |
| Cases evaluated | 4 |
| Started | 2026-10-22T11:25:42.455688+00:00 |
| Finished | 2026-10-22T11:26:15.208052+00:00 |
| Duration | 32.752 s |
| Slicing enabled | no |
| Compilation enabled | no |
| arduino-cli available | no |
| PrusaSlicer available | no |
| Raw result | `docs/phase9/eval-scripted.json` |

> **This is a scripted FakeLLM harness run, not a live-model benchmark.**
> It checks the harness, scoring and deterministic pipeline. It must not be
> quoted as a live pass rate. The live eval is a separate run.

## Overall

| Scope | Passed | Total | Pass rate |
| --- | ---: | ---: | ---: |
| All cases | 4 | 4 | 100.0% |
| Build cases | 2 | 2 | 100.0% |
| Refusal cases | 2 | 2 | 100.0% |

## Results by category

| Category | Passed | Total | Pass rate |
| --- | ---: | ---: | ---: |
| Plain build | 1 | 1 | 100.0% |
| Tricky build | 1 | 1 | 100.0% |
| Refusal | 1 | 1 | 100.0% |
| Adversarial | 1 | 1 | 100.0% |

## Build pipeline stages

Build cases only. The pass-rate denominator is passed + failed; skipped and
unverified stages are shown separately and never count as passes.

| Stage | Passed | Failed | Skipped | Unverified | Not run | Pass rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| plan | 2 | 0 | 0 | 0 | 0 | 100.0% |
| validation | 2 | 0 | 0 | 0 | 0 | 100.0% |
| geometry | 2 | 0 | 0 | 0 | 0 | 100.0% |
| slicing | 0 | 0 | 2 | 0 | 0 | n/a |
| firmware_generation | 2 | 0 | 0 | 0 | 0 | 100.0% |
| firmware_compile | 0 | 0 | 2 | 0 | 0 | n/a |
| guide | 2 | 0 | 0 | 0 | 0 | 100.0% |

## Build checks

| Check | Passed | Total | Rate |
| --- | ---: | ---: | ---: |
| First-pass validation | 2 | 2 | 100.0% |
| Final validation | 2 | 2 | 100.0% |
| Geometry | 2 | 2 | 100.0% |
| Firmware generation | 2 | 2 | 100.0% |
| Guide | 2 | 2 | 100.0% |

## Refusal checks

| Check | Passed | Total | Rate |
| --- | ---: | ---: | ---: |
| Correct refusals | 2 | 2 | 100.0% |
| Specific refusal reasons | 2 | 2 | 100.0% |

## LLM accounting

| Metric | Value |
| --- | ---: |
| LLM calls | 4 |
| Prompt tokens | 1,300 |
| Completion tokens | 1,650 |
| Total tokens | 2,950 |
| Provider-reported usage | yes |

## What this run does and does not establish

Established:

- The scripted planning and validation paths work for the two selected build
  cases.
- The two selected refusal cases are refused with reasons.
- Geometry generation and guide generation pass for both build cases.
- The scoring rules do not count skipped slicing or compilation as passes.

Not established:

- Live-model reliability. The LLM responses were scripted.
- Firmware compilation or STL slicing for these cases. Both stages were skipped
  because `do_slice` and `do_compile` were disabled in this run.
- Hardware behaviour. Nothing was built, flashed or measured.
- Simulation or serial behavior. Neither is implemented in this codebase.

## Failed cases

None in this selected run.
