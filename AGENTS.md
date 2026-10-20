# Sparkplan agent rules

PROJECT
Sparkplan turns a plain-language hardware idea into (1) a validated parts list and wiring, (2) a 3D-printable enclosure (STL), (3) firmware that compiles, (4) a build guide. Hardware family: ESP32 DevKit plus a catalog of about 15 parts. Nothing outside the catalog is ever recommended.

ARCHITECTURE RULES (never break these)
1. The LLM only produces a structured BuildSpec (Pydantic). It never writes geometry or firmware code.
2. Deterministic Python validators check every LLM output. Failures go back to the LLM as machine-readable errors, max 3 repair rounds.
3. Geometry comes from CadQuery driven by catalog dimensions. Firmware comes from per-part templates stored in the catalog.
4. Unsupported ideas are refused with reasons. Never approximate them.
5. Each stage is its own package with its own tests and runs without the others.

STACK
Python 3.11, Pydantic v2, FastAPI, pytest, ruff, CadQuery (fall back to build123d if the install fails), trimesh, numpy, arduino-cli (esp32:esp32 core), React + Vite + TypeScript + three.js, GitHub Actions.

LAYOUT
catalog/  sparkplan/{models,validators,planner,geometry,firmware,guide}  sparkplan/pipeline.py  api/  eval/  web/  tests/  docs/PROGRESS.md

COMMANDS
make test, make lint, make eval, make run-api. Keep them working at all times.

CODING RULES
- Type hints everywhere. Units: millimetres, volts, milliamps.
- Validators return Issue objects: code, severity (error or warning), message, part_ids, fix_hint.
- Unit tests never call the network. Use FakeLLM.
- LLM access goes through an LLMClient protocol with complete_json(system, user, schema), a FakeLLM, and one real OpenAI-compatible adapter chosen by env vars.

HONESTY RULES
- Never say something works without showing the command and its output in this session.
- Never weaken, skip or delete a test or validator to get a pass.
- If something cannot be verified (no arduino-cli, no printer), say so plainly and mark it UNVERIFIED in docs/PROGRESS.md.
- If a requirement is unclear or looks wrong, stop and ask me.

WORKING STYLE
Plan first and wait for my approval. Keep a todo list. Make small changes and run tests after each step. Do not touch files outside the current phase unless needed, and say why.

GIT WORKFLOW
Remote: https://github.com/Ash052006/SparkPlan.git, default branch main.
- Start of a phase: git pull origin main, then create branch phase-N-short-name.
- Commit small, with prefixes feat:, test:, fix:, docs:.
- Never commit secrets. .env stays in .gitignore and only .env.example is committed. Run git diff --staged and check for keys before every commit.
- Never force push. Never rewrite history on main.
- If git authentication fails, stop and tell me. Never ask for or store credentials.

PHASE COMPLETION (only after the phase gate has passed)
1. Run make test and make lint and show the output.
2. Append a section to docs/PROGRESS.md: what was built, test results, what is UNVERIFIED, known gaps.
3. Commit, push the branch, merge into main, push main, create tag phase-N, push tags.
4. If the gate has not passed: push the branch with a wip: commit, do not merge, and tell me why.