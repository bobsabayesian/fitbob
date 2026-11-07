# fitbob roadmap

Fork of [wger](https://github.com/wger-project/wger). This repo is the **Django/DRF backend only**. The
web UI is a separate React app and the mobile app is Flutter, so every feature below splits into
"backend work here" and "UI work elsewhere" (see Open questions).

## What wger already gives us

- `Day.type` already has `tabata`, `hiit`, `amrap`, `enom`, `edt`, `rft`, `afap` plus a `Day.config` JSONField.
- `SlotEntry.type` covers warmup/dropset/myo/partial/forced/tut/iso/jump, and it has a `config` JSONField.
- `WorkoutLog` (reps, weight, units, rir, target vs. actual) and `WorkoutSession` (start/end, notes, impression).
- `RepetitionUnit` / `WeightUnit` tables, which can be extended for time, rounds, etc.
- `Routine.calculate_log_statistics()` and a `/routine/<id>/stats` endpoint (volume/intensity grouped by muscle).
- Routine copy/templates, PDF and iCal export.

## Phase 0: Foundations (small, do first)

- Dev env running (uv, migrations, dummy data), baseline tests green.
- New Django app `wger/ai_bridge` (item 4) and `wger/progression` (item 3) rather than bloating `manager`.
- Decide UI strategy (see open questions).

## Phase 1: Tabata timer (item 1)

- Data: reuse `Day.type = tabata` and `Day.config` with `{work_s, rest_s, rounds, sets, set_rest_s, warmup_s, cooldown_s}`;
  add validation in `manager/validators.py`.
- API: expose a computed "interval plan" (list of timed phases) on the day endpoint so any client can just play it.
- Log the finished timer as a `WorkoutSession` (+ optional per-round logs).
- Client: timer screen (audio/vibration cues, wake lock). Standalone quick-start timer plus per-routine timer.
- Generalise afterwards to EMOM/AMRAP/HIIT, since the plan format is the same.

## Phase 2: Non-traditional training and editable JSON (item 2), simplified to duration + intensity

- Add rep units / exercise types for time, rounds, and "rolls"/"minutes sparring".
- New `ActivityLog` (or extend `WorkoutLog`) with free-form `data` JSONField: duration, rounds, partner, position drilled,
  techniques, RPE, notes. Keep the schema light, with validation via a JSON Schema per activity template.
- User-editable **activity templates** as JSON (e.g. a BJJ class: warmup, drilling, positional sparring, rolling),
  with import/export endpoints (`GET/PUT /api/v2/activity-template/<id>/json`).
- Ship starter templates: BJJ, boxing, yoga, running intervals, climbing.
- Stats: mat time per week, rounds, RPE trend.

## Phase 3: Progressive overload (item 3)

- Per-exercise progression rules stored on `SlotEntry.config`: linear (+x kg when all reps hit), double progression
  (rep range then weight), RIR/RPE-based, percentage of estimated 1RM. wger's `config_calculations` already handles
  per-iteration changes, so we plug in there.
- Progress analytics endpoint: est. 1RM (Epley/Brzycki), best set, weekly volume per exercise and per muscle, PR detection, stalls.
- "Next session suggestion" computed from the last logs + rule.
- Deload detection/suggestion as a stretch.

## Phase 4: LLM prompt export / import (item 4)

- `GET /api/v2/llm/prompt?days=14`: builds a prompt with recent sessions/logs, current routine, per-muscle volume and
  frequency, PRs, progression status, user goals/constraints, plus a strict **response format** spec.
- Response format: a versioned JSON schema (`wger.llm.v1`) for either (a) a proposed routine/plan or (b) logged workouts.
  Exercises referenced by wger exercise id or UUID with name fallback matching.
- `POST /api/v2/llm/import?dry_run=true` validates, shows a diff/preview, then commits. Never writes without confirmation.
- Token-budget controls (summarise old history), and prompt templates the user can edit.
- Depends on phases 2 and 3 for non-traditional activities and progression data.

## Phase 5: Dynamic Coach (original spec)

Full original spec: [docs/dynamic_coach_spec.md](docs/dynamic_coach_spec.md) (new app `wger/dynamic_coach/`, API under
`/api/v2/coach/`). Its four sprints map onto the phases above:

| Spec sprint | Content | Overlap with existing phases |
|---|---|---|
| 4: Tabata | timeline endpoint | **Done** in Phase 1 (`manager/interval_timer.py`). Skip `TabataProtocol` model unless saved presets are wanted. |
| 1: Engines | recovery decay, Epley e1RM + rounding, `muscle-freshness` | e1RM/rounding shared with Phase 3 (build once, in `progression`). Freshness is new. |
| 2: Generator | equipment profiles, preferences, `generate-workout` | New. Depends on Sprint 1. |
| 3: Sweat Deck | deck models, draw/complete, back-propagation to fatigue | New. Depends on Sprint 1. |

**Spec formula fix:** the spec's `W = e1RM * (1 - R/30)` is not the inverse of Epley and under-prescribes (its own example gives 75 kg, not 82.5). `progression` uses the true inverse `e1RM / (1 + R/30)`.

**Spec drift to fix before building:** the spec targets old wger names (`core.Exercise`, `core.Equipment`, `core.Muscle`,
`Day`/`Set`/`Setting`). Current wger has these in `exercises` (`Exercise`/`Equipment`/`Muscle`) and `manager`
(`Routine`/`Day`/`Slot`/`SlotEntry`), so the generator must emit Slots/SlotEntries. Equipment-per-exercise is
on translations/variations in newer versions; verify before writing the filter.

### Suggested priority

1. ~~Phase 0 + shared `progression` service~~ **Done**: `wger/progression/services.py` (Epley/Brzycki e1RM, inverse, rounding, `prescribe`). Feeds Phase 3 and the generator. Run tests with `DJANGO_SETTINGS_MODULE=settings.ci uv run python manage.py test wger.progression`.
2. Phase 1 UI (timer screen). Backend is done, so this is the quickest visible win.
3. ~~Spec Sprint 1: recovery engine + `muscle-freshness`~~ **Done**: `wger/dynamic_coach/services/recovery_engine.py` and `GET /api/v2/coach/muscle-freshness/`. `muscle_freshness(user, extra_events=...)` accepts synthetic `FatigueEvent`s for the deck. Equipment/preferences models from Sprint 1 are still open and belong with the generator.
4. Phase 3 progression rules and analytics (reuses step 1).
5. Spec Sprint 2: generator. Biggest value, biggest risk; do after 1-4 so it has real data and logs.
6. Phase 2 non-traditional activity logging (BJJ etc.). Independent; slot in whenever you start training it.
7. Spec Sprint 3: Sweat Deck. Fun but lowest value; needs the freshness model.
8. Phase 4 LLM export/import last, since it consumes everything above. It may also make the generator partly redundant.

## Cross-cutting

- Tests per feature (existing test layout: `wger/<app>/tests`), OpenAPI docs, CHANGELOG entries.
- Migrations kept additive so upstream wger merges stay easy; track upstream regularly.
- Keep all new JSON formats versioned.

## Decisions

- **Hosting:** self-hosted on a Raspberry Pi 5 (aarch64, Docker) behind a Cloudflare Tunnel. Needs `CSRF_TRUSTED_ORIGINS`,
  `SITE_URL`, forwarded-proto header, and access control (Cloudflare Access / registration off).
- **UI:** React web UI (served by Django, installable as PWA). Custom screens live in a fork of `wger-project/react`,
  built into a custom `react-components` tarball that our Dockerfile installs instead of the npm release.
- **Upstream:** stay mergeable: new Django apps, additive migrations, versioned JSON formats.
- **LLM:** copy/paste flow only, no API calls.
- **BJJ / non-traditional:** track duration + intensity only (qualitative). No techniques/partners. Journal stays elsewhere.

## Open questions

1. Can the React fork (`wger-project/react`) be added to the session so UI work can start?
2. Is the Pi already running wger, or are we setting that up from scratch (Phase 0)?
3. Tabata: want presets (20/10 x 8) plus fully custom intervals, and sound cues? Any need for the screen to stay awake / run on a locked phone?
