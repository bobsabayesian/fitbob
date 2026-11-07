# Technical Specification: Dynamic Workout Generator & Conditioning Suite (wger Extension)

## 1. Overview & Objectives

This specification defines the functional and architectural requirements for extending an active fork of `wger-project/wger` (Django backend) with an intelligent, autoregulated training engine inspired by Fitbod, paired with dynamic conditioning tools (Sweat Deck & Tabata).

### Core Goals
1. **Fatigue & Recovery Decay Model:** Continuously score each muscle group's freshness ($0.0$ to $1.0$) using an exponential half-life decay function over logged sets.
2. **Context-Aware Dynamic Generator:** Generate strength and hypertrophy routines matching available gym equipment profiles, user injury blacklists, target training splits, and fresh muscle priority.
3. **Autoregulated Progression:** Compute estimated one-rep maximums ($\text{e1RM}$) from historical logs and automatically prescribe working weights and target rep ranges.
4. **Interval & Conditioning Engines:** Provide structured endpoints for high-intensity Tabata protocols and randomized card-deck conditioning ("Sweat Deck"), feeding completed conditioning volume back into the fatigue model.
5. **Native Integration:** Emit standard `wger` primitives (`Routine`, `Day`, `Set`, `Setting`, `WorkoutSession`) so existing frontends, mobile apps, and data tables continue to function without schema destruction.

---

## 2. System Architecture

To minimize merge conflicts with upstream `wger` releases, implement all new models, endpoints, and logic in a dedicated Django app (`wger/dynamic_coach/`).

```text
wger/
└── dynamic_coach/
    ├── migrations/
    ├── models/
    │   ├── __init__.py
    │   ├── equipment.py          # EquipmentProfile & UserGymConfig
    │   ├── preferences.py        # UserGeneratorPreferences
    │   ├── deck.py               # DeckWorkoutSession & DeckCardLog
    │   └── interval.py           # TabataProtocol & IntervalLog
    ├── services/
    │   ├── __init__.py
    │   ├── recovery_engine.py    # Fatigue tracking & exponential decay math
    │   ├── progression.py        # 1RM calculators (Epley/Brzycki) & weight rounding
    │   ├── session_builder.py    # Selection algorithm & routine construction
    │   ├── deck_engine.py        # Deck shuffling, suit mapping, volume back-propagation
    │   └── tabata_engine.py      # Timeline generation & interval metrics
    ├── api/
    │   ├── __init__.py
    │   ├── serializers.py        # DRF serializers for inputs and payloads
    │   ├── views.py              # Generator, freshness, deck, and tabata endpoints
    │   └── urls.py               # Route definitions mounted under /api/v2/coach/
    └── tests/
        ├── test_recovery.py
        ├── test_session_builder.py
        └── test_deck.py
```

---

## 3. Data Models

### 3.1 `EquipmentProfile` (`models/equipment.py`)
Allows users to switch between distinct training spaces (e.g., Home Gym, Commercial Gym, Hotel).

| Field | Type | Details |
|---|---|---|
| `user` | `ForeignKey(settings.AUTH_USER_MODEL)` | Cascade on delete. |
| `name` | `CharField(max_length=64)` | e.g., `"Commercial Gym"`, `"Garage Setup"`. |
| `equipment` | `ManyToManyField('core.Equipment', blank=True)` | Available items from `wger` core equipment library. |
| `is_active` | `BooleanField(default=True)` | Primary flag for workout generation default. |

### 3.2 `UserGeneratorPreferences` (`models/preferences.py`)
User-specific dials and hard exclusions.

| Field | Type | Details |
|---|---|---|
| `user` | `OneToOneField(settings.AUTH_USER_MODEL)` | `related_name='generator_preferences'` |
| `default_equipment_profile` | `ForeignKey(EquipmentProfile, null=True)` | `on_delete=models.SET_NULL` |
| `default_duration_min` | `IntegerField(default=45)` | Target workout duration (30, 45, 60, 90). |
| `preferred_split` | `CharField(max_length=16)` | `Choices: ('PPL', 'UL', 'FULL_BODY', 'AUTO')` |
| `excluded_exercises` | `ManyToManyField('core.Exercise', blank=True)` | Persistent user blacklist. |
| `excluded_muscles` | `ManyToManyField('core.Muscle', blank=True)` | Temporary or permanent injury exclusions. |
| `rounding_increment_kg` | `DecimalField(max_digits=4, decimal_places=2, default=2.50)` | Minimum weight step for loads. |

### 3.3 `DeckWorkoutSession` (`models/deck.py`)
State tracker for card-deck conditioning workouts.

| Field | Type | Details |
|---|---|---|
| `user` | `ForeignKey(settings.AUTH_USER_MODEL)` | Cascade on delete. |
| `deck_size` | `IntegerField(default=52)` | Typically `26` (half) or `52` (full). |
| `include_jokers` | `BooleanField(default=False)` | Wildcard cards. |
| `suit_mapping` | `JSONField` | Maps suits to `wger.Exercise` IDs: `{"spades": 12, "hearts": 45, ...}` |
| `card_stack` | `JSONField` | Shuffled array of remaining card objects. |
| `cards_drawn` | `JSONField(default=list)` | Audit trail: `[{"card": "K", "suit": "hearts", "reps": 13, "ts": ...}]` |
| `is_completed` | `BooleanField(default=False)` | Session completion status. |
| `started_at` | `DateTimeField(auto_now_add=True)` | Session start. |
| `completed_at` | `DateTimeField(null=True, blank=True)` | Session finish. |

### 3.4 `TabataProtocol` (`models/interval.py`)
Preset interval structures and customized timers.

| Field | Type | Details |
|---|---|---|
| `user` | `ForeignKey(settings.AUTH_USER_MODEL)` | Cascade on delete. |
| `name` | `CharField(max_length=64)` | e.g., `"Classic Tabata"`, `"Aerobic Long Intervals"`. |
| `work_sec` | `IntegerField(default=20)` | Active interval length. |
| `rest_sec` | `IntegerField(default=10)` | Rest interval length. |
| `rounds_per_cycle` | `IntegerField(default=8)` | Standard Tabata: 8 rounds = 4 minutes. |
| `total_cycles` | `IntegerField(default=1)` | Number of cycles/blocks. |
| `cycle_rest_sec` | `IntegerField(default=60)` | Break duration between distinct cycles. |
| `exercises` | `ManyToManyField('core.Exercise', blank=True)` | Optional ordered exercise rotation. |

---

## 4. Algorithmic Specifications

### 4.1 Muscle Fatigue & Recovery Decay (`services/recovery_engine.py`)

#### Concept
Each muscle starts at full freshness ($1.0$ or $100\%$). Completed sets accumulate fatigue; accumulated fatigue decays exponentially toward $0$ over time.

#### Mathematical Model
For any given muscle $m$ at calculation time $t$:

$$\text{Freshness}_m(t) = \max\left(0.0, \; 1.0 - \sum_{i \in \text{Logs}(m, t - T_{\text{window}})} \text{FatigueContribution}(i) \cdot e^{-\lambda \cdot (t - t_i)}\right)$$

Where:
* **$T_{\text{window}}$**: Rolling lookback window (default: `14 days`).
* **$\text{Logs}(m)$**: All logged sets within the window where muscle $m$ was engaged.
* **$\text{FatigueContribution}(i)$**:
  $$\text{FatigueContribution}(i) = \frac{\text{RoleFactor}}{\text{VolumeCapacity}_m}$$
  * `RoleFactor`: `1.0` if $m$ is in `exercise.muscles` (primary); `0.4` if $m$ is in `exercise.muscles_secondary`.
  * `VolumeCapacity_m`: Configurable set-capacity constant representing total direct hard sets to completely exhaust the muscle (default: `12.0`).
* **Decay Constant ($\lambda$)**:
  $$\lambda = \frac{\ln(2)}{\text{HalfLifeHours}} \approx \frac{0.693147}{36} \approx 0.019254\text{ hr}^{-1}$$
  * An exhausted muscle ($0\%$ freshness) recovers to $\approx 50\%$ after 36 hours, $\approx 75\%$ after 72 hours, and $>90\%$ after 96 hours.

---

### 4.2 Progressive Overload & Weight Prescription (`services/progression.py`)

When generating a set for an exercise:

1. **Query Historical Sets:** Fetch the highest completed set weight within the past 90 days.
2. **Estimate 1-Rep Max ($\text{e1RM}$):** Use the Epley equation:
   $$\text{e1RM} = w \cdot \left(1 + \frac{r}{30}\right)$$
   *(where $w$ is weight lifted and $r$ is reps completed).*
3. **Prescribe Target Weight ($W_{\text{target}}$):** Given target reps $R_{\text{target}}$ (e.g., 8 reps):
   $$W_{\text{target}} = \text{e1RM} \cdot \left(1 - \frac{R_{\text{target}}}{30}\right)$$
4. **Plate Rounding:** Round $W_{\text{target}}$ to the nearest `rounding_increment_kg` (e.g., $2.5\text{ kg}$).
5. **Cold-Start Fallback:** If no history exists, set `weight = null` and prescribe target reps (e.g., 10 reps) for baseline calibration.

---

### 4.3 Strength Session Generator (`services/session_builder.py`)

#### Step-by-Step Selection Logic
1. **Target Muscle Selection:**
   * If a split is selected (e.g., `Push`), constrain to Chest, Anterior Deltoids, Triceps.
   * If `split='AUTO'`, pick the muscle group with the highest aggregated freshness score.
2. **Exercise Pool Filtering:**
   * Select exercises from `core.Exercise` where:
     * Primary muscle matches target list.
     * Required `equipment` is a strict subset of user's active `EquipmentProfile`.
     * `id` is not in `UserGeneratorPreferences.excluded_exercises`.
3. **Exercise Count Calculation:**
   $$\text{NumExercises} = \text{clamp}\left(\left\lfloor \frac{\text{duration\_min}}{10} \right\rfloor, 3, 7\right)$$
4. **Hierarchy Slotting:**
   * **Tier 1 (Slots 1–2):** Heavy compound movements targeting primary fresh muscles (e.g., Barbell Bench Press, Squat). Prescribe 3–4 sets of 6–8 reps.
   * **Tier 2 (Slots 3–4):** Secondary compound or major accessory movements. Prescribe 3 sets of 8–10 reps.
   * **Tier 3 (Slots 5+):** Isolation movements targeting secondary muscles. Prescribe 3 sets of 10–15 reps.
5. **Persistence:**
   * Instantiate native `wger.core.models.Routine`.
   * Create associated `Day`, `Set`, and `Setting` rows populated with prescribed weights and reps.

---

### 4.4 Card-Deck Engine ("Sweat Deck") (`services/deck_engine.py`)

#### Deck Lifecycle
1. **Suit Initialization:**
   Assign each of the 4 suits to a movement category with priority to fresh muscle groups:
   * **♠ Spades:** Upper Body Push (e.g., Push-ups, Dips).
   * **♥ Hearts:** Lower Body (e.g., Air Squats, Alternating Lunges).
   * **♦ Diamonds:** Core / Posterior (e.g., Mountain Climbers, Sit-ups).
   * **♣ Clubs:** Full Body Conditioning (e.g., Burpees, Kettlebell Swings).
2. **Stack Shuffling:**
   Build an array of 52 cards (2–10 = face value; Jack = 11, Queen = 12, King = 13, Ace = 15; Jokers = configurable, default 25 or 1-minute static hold). Shuffle using a cryptographically secure randomizer (`secrets.SystemRandom`).
3. **Stateful Draw Loop:**
   * `draw()` pops the top card, records the action in `cards_drawn`, updates remaining stack, and returns the card payload.
4. **Volume Conversion back to Fatigue Engine:**
   On completion, aggregate total repetitions per exercise. Convert high-rep calisthenics to equivalent working sets:
   $$\text{EquivalentSets} = \frac{\text{TotalRepsCompleted}}{20.0}$$
   Feed these synthetic sets into the `recovery_engine` so conditioning volume depletes muscle freshness accurately.

---

### 4.5 Tabata & Interval Engine (`services/tabata_engine.py`)

#### Timeline Compilation
Produce an explicit time-series event queue consumed by frontend timers (sound cues, visual state transitions):

```json
[
  { "type": "prepare", "duration_sec": 10, "label": "Get Ready" },
  { "type": "work", "duration_sec": 20, "round": 1, "cycle": 1, "exercise_id": 42, "exercise_name": "High Knees" },
  { "type": "rest", "duration_sec": 10, "round": 1, "cycle": 1, "label": "Rest" },
  ...
  { "type": "cycle_rest", "duration_sec": 60, "label": "Cycle Complete - Long Rest" },
  { "type": "complete", "duration_sec": 0, "label": "Session Complete" }
]
```

---

## 5. API Interface Specification (DRF)

All endpoints must be authenticated via Token or Session authentication.

### 5.1 Recovery & Generation

#### `GET /api/v2/coach/muscle-freshness/`
Returns freshness scores for all anatomical muscle groups.

**Response (200 OK):**
```json
{
  "calculated_at": "2026-10-29T16:00:00Z",
  "muscles": [
    {
      "muscle_id": 1,
      "name": "Biceps brachii",
      "freshness_pct": 92.4,
      "status": "ready"
    },
    {
      "muscle_id": 4,
      "name": "Pectoralis major",
      "freshness_pct": 28.1,
      "status": "fatigued"
    }
  ]
}
```

---

#### `POST /api/v2/coach/generate-workout/`
Generates a structured strength session.

**Request Payload:**
```json
{
  "duration_min": 45,
  "equipment_profile_id": 2,
  "split": "push",
  "save_as_routine": true,
  "routine_name": "Dynamic Push - Oct 29"
}
```

**Response (201 Created):**
```json
{
  "routine_id": 84,
  "day_id": 112,
  "split": "push",
  "exercises": [
    {
      "exercise_id": 14,
      "name": "Barbell Bench Press",
      "slot": 1,
      "sets": 4,
      "target_reps": 8,
      "prescribed_weight_kg": 82.5,
      "e1rm_basis_kg": 102.5
    },
    {
      "exercise_id": 88,
      "name": "Incline Dumbbell Flyes",
      "slot": 2,
      "sets": 3,
      "target_reps": 12,
      "prescribed_weight_kg": 18.0,
      "e1rm_basis_kg": null
    }
  ]
}
```

---

### 5.2 Conditioning & Deck Engines

#### `POST /api/v2/coach/deck/new/`
Initializes a new randomized deck session.

**Request Payload:**
```json
{
  "deck_size": 52,
  "include_jokers": false,
  "suit_overrides": {
    "diamonds": 104
  }
}
```

**Response (201 Created):**
```json
{
  "session_id": "b3e5a2c4-82a1-460b-8d59-3d1f112e45cf",
  "total_cards": 52,
  "cards_remaining": 52,
  "suit_mapping": {
    "spades": { "exercise_id": 12, "name": "Push-ups" },
    "hearts": { "exercise_id": 45, "name": "Air Squats" },
    "diamonds": { "exercise_id": 104, "name": "Mountain Climbers" },
    "clubs": { "exercise_id": 21, "name": "Burpees" }
  }
}
```

---

#### `POST /api/v2/coach/deck/<session_id>/draw/`
Draws the next card from the persistent stack.

**Response (200 OK):**
```json
{
  "session_id": "b3e5a2c4-82a1-460b-8d59-3d1f112e45cf",
  "card": {
    "suit": "hearts",
    "face": "Q",
    "reps": 12,
    "exercise_id": 45,
    "exercise_name": "Air Squats"
  },
  "cards_remaining": 47,
  "cards_drawn_count": 5
}
```

---

#### `POST /api/v2/coach/deck/<session_id>/complete/`
Finalizes deck session and logs accumulated fatigue.

**Request Payload:**
```json
{
  "elapsed_seconds": 1240
}
```

**Response (200 OK):**
```json
{
  "status": "completed",
  "total_cards_drawn": 52,
  "total_reps_completed": 380,
  "fatigue_logged": true
}
```

---

### 5.3 Tabata Endpoints

#### `POST /api/v2/coach/tabata/generate/`
Builds an interval timeline array.

**Request Payload:**
```json
{
  "work_sec": 20,
  "rest_sec": 10,
  "rounds_per_cycle": 8,
  "total_cycles": 2,
  "cycle_rest_sec": 60,
  "exercise_ids": [14, 21]
}
```

**Response (200 OK):**
```json
{
  "total_duration_sec": 540,
  "timeline": [
    { "type": "prepare", "duration_sec": 10 },
    { "type": "work", "duration_sec": 20, "round": 1, "cycle": 1, "exercise_id": 14 },
    { "type": "rest", "duration_sec": 10, "round": 1, "cycle": 1 },
    { "type": "work", "duration_sec": 20, "round": 2, "cycle": 1, "exercise_id": 21 }
  ]
}
```

---

## 6. Implementation Plan & Delivery Phasing

```text
Sprint 1: Core Mathematical Engines
  ├── models/equipment.py & models/preferences.py
  ├── services/recovery_engine.py (half-life decay math)
  ├── services/progression.py (Epley 1RM & rounding)
  └── GET /api/v2/coach/muscle-freshness/

Sprint 2: Dynamic Strength Generator
  ├── services/session_builder.py (hierarchy slotting & equipment filter)
  ├── POST /api/v2/coach/generate-workout/
  └── Integration test verifying wger Routine/Day/Set DB persistence

Sprint 3: Card-Deck Conditioning ("Sweat Deck")
  ├── models/deck.py
  ├── services/deck_engine.py
  ├── POST /api/v2/coach/deck/new/, draw/, complete/
  └── Volume back-propagation into recovery_engine

Sprint 4: Tabata Engine & Final Hardening
  ├── models/interval.py & services/tabata_engine.py
  ├── POST /api/v2/coach/tabata/generate/
  └── Unit test suite covering cold-starts, zero equipment, and concurrency
```