# -*- coding: utf-8 -*-

# This file is part of wger Workout Manager.
#
# wger Workout Manager is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# wger Workout Manager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""
Strength session generator.

Picks a training split (or lets muscle freshness choose one), filters the
exercise pool by equipment and exclusions, slots exercises into three tiers
(heavy compound, secondary compound, isolation) and prescribes weights from the
user's history. Optionally persists the result as a native wger ``Routine``.
"""

# Standard Library
import random
from dataclasses import dataclass
from datetime import (
    date,
    timedelta,
)
from decimal import Decimal
from typing import Optional

# Django
from django.db import transaction

# wger
from wger.dynamic_coach.models import (
    EquipmentProfile,
    SplitChoices,
    UserGeneratorPreferences,
)
from wger.dynamic_coach.services.recovery_engine import muscle_freshness
from wger.exercises.models import (
    Equipment,
    Exercise,
)
from wger.manager.consts import (
    WEIGHT_UNIT_KG,
    WEIGHT_UNIT_LB,
)
from wger.manager.models import (
    Day,
    RepetitionsConfig,
    Routine,
    SetsConfig,
    Slot,
    SlotEntry,
    WeightConfig,
)
from wger.progression.services import (
    COLD_START_REPS,
    KG_PER_LB,
    prescribe,
)


# Muscle ids of the standard wger muscle fixtures
CHEST, ANTERIOR_DELTOID, TRICEPS = 4, 2, 5
LATS, TRAPEZIUS, BICEPS, BRACHIALIS = 12, 9, 1, 13
QUADS, HAMSTRINGS, GLUTES, CALVES, SOLEUS = 10, 11, 8, 7, 15

SPLITS: dict[str, frozenset] = {
    'push': frozenset({CHEST, ANTERIOR_DELTOID, TRICEPS}),
    'pull': frozenset({LATS, TRAPEZIUS, BICEPS, BRACHIALIS}),
    'legs': frozenset({QUADS, HAMSTRINGS, GLUTES, CALVES, SOLEUS}),
}
SPLITS['upper'] = SPLITS['push'] | SPLITS['pull']
SPLITS['lower'] = SPLITS['legs']
SPLITS['full_body'] = SPLITS['push'] | SPLITS['pull'] | SPLITS['legs']

AUTO_CANDIDATES = {
    SplitChoices.PPL: ('push', 'pull', 'legs'),
    SplitChoices.UL: ('upper', 'lower'),
    SplitChoices.FULL_BODY: ('full_body',),
    SplitChoices.AUTO: ('push', 'pull', 'legs'),
}

EXCLUDED_CATEGORIES = ('Cardio', 'Stretching')
MIN_EXERCISES, MAX_EXERCISES = 3, 7
ROUTINE_NAME_MAX = 25

# (tier, sets, target reps)
TIERS = {1: (4, 8), 2: (3, 10), 3: (3, 12)}


class GeneratorError(Exception):
    """Raised when no session can be built; the message is safe to show to users"""


@dataclass
class PlannedExercise:
    exercise: Exercise
    slot: int
    tier: int
    sets: int
    target_reps: int
    weight: Optional[Decimal]
    """In the user's weight unit, see ``unit``"""
    e1rm: Optional[Decimal]
    """Always kg"""
    unit: str = 'kg'

    @property
    def name(self) -> str:
        return self.exercise.get_translation().name


def weight_unit_of(user) -> str:
    """'kg' or 'lb', from the user's profile"""
    profile = getattr(user, 'userprofile', None)
    return 'lb' if profile is not None and profile.weight_unit == 'lb' else 'kg'


def rounding_increment(preferences, unit: str) -> Decimal:
    """Smallest load step in the user's unit: their setting, else 2.5 kg or 5 lb"""
    if preferences is not None and preferences.rounding_increment:
        return Decimal(preferences.rounding_increment)
    return Decimal('5') if unit == 'lb' else Decimal('2.5')


def exercise_count(duration_min: int) -> int:
    return max(MIN_EXERCISES, min(MAX_EXERCISES, duration_min // 10))


def tier_for_slot(slot: int) -> int:
    return 1 if slot <= 2 else 2 if slot <= 4 else 3


def resolve_profile(user, profile_id=None, preferences=None) -> EquipmentProfile:
    """Explicit id, else the preferred profile, else the newest active one"""
    profiles = EquipmentProfile.objects.filter(user=user)
    if profile_id is not None:
        try:
            return profiles.get(pk=profile_id)
        except EquipmentProfile.DoesNotExist:
            raise GeneratorError('Equipment profile not found.')
    if preferences and preferences.default_equipment_profile_id:
        return preferences.default_equipment_profile
    profile = profiles.filter(is_active=True).order_by('-pk').first()
    if profile is None:
        raise GeneratorError('Create an equipment profile first.')
    return profile


def rank_splits(split, preferences, freshness_by_muscle: dict) -> list[str]:
    """
    Keys of ``SPLITS`` to try, best first. An explicit split is the only entry;
    ``auto`` ranks the candidate splits by mean freshness of their muscles.
    """
    split = (split or 'auto').lower()
    if split in SPLITS:
        return [split]
    if split != 'auto':
        raise GeneratorError(f'Unknown split "{split}".')

    preferred = preferences.preferred_split if preferences else SplitChoices.AUTO
    candidates = AUTO_CANDIDATES[SplitChoices(preferred)]

    def mean_freshness(key):
        muscles = SPLITS[key]
        return sum(freshness_by_muscle.get(m, 100.0) for m in muscles) / len(muscles)

    # sorted() is stable, so ties follow the listed order
    return sorted(candidates, key=mean_freshness, reverse=True)


def candidate_pool(split_key, equipment_ids, bodyweight_ids, excluded_exercises, excluded_muscles):
    targets = SPLITS[split_key]
    allowed = set(equipment_ids) | set(bodyweight_ids)
    pool = []
    queryset = (
        Exercise.objects.filter(muscles__in=targets)
        .exclude(category__name__in=EXCLUDED_CATEGORIES)
        .exclude(pk__in=excluded_exercises)
        .select_related('category')
        .prefetch_related('muscles', 'muscles_secondary', 'equipment')
        .distinct()
        .order_by('pk')
    )
    for exercise in queryset:
        if {e.pk for e in exercise.equipment.all()} - allowed:
            continue
        primary = {m.pk for m in exercise.muscles.all()}
        secondary = {m.pk for m in exercise.muscles_secondary.all()}
        if (primary | secondary) & set(excluded_muscles):
            continue
        pool.append(exercise)
    return pool


def _compound_size(exercise) -> int:
    return len(exercise.muscles.all()) + len(exercise.muscles_secondary.all())


def select_exercises(pool, split_key, freshness_by_muscle, count, rng) -> list[Exercise]:
    """
    Greedy slotting. Early slots favour compound lifts on fresh muscles, later
    slots favour isolation. Exercises that only hit already-covered muscles, or
    share a variation group with a chosen one, are penalised.
    """
    targets = SPLITS[split_key]
    chosen: list[Exercise] = []
    covered: set[int] = set()
    used_variations = set()
    remaining = list(pool)

    for slot in range(1, count + 1):
        if not remaining:
            break
        tier = tier_for_slot(slot)

        def score(ex):
            primary = {m.pk for m in ex.muscles.all()} & targets
            fresh = sum(freshness_by_muscle.get(m, 100.0) for m in primary) / len(primary) / 100
            size = min(_compound_size(ex), 4) / 4
            shape = size if tier < 3 else 1 - size
            value = 2.0 * shape + 1.0 * fresh + rng.random() * 0.5
            if primary <= covered:
                value -= 0.75
            if ex.variation_group and ex.variation_group in used_variations:
                value -= 2.0
            return value

        best = max(remaining, key=score)
        remaining.remove(best)
        chosen.append(best)
        covered |= {m.pk for m in best.muscles.all()}
        if best.variation_group:
            used_variations.add(best.variation_group)
    return chosen


def build_session(
    user,
    duration_min=None,
    split=None,
    equipment_profile_id=None,
    rng: Optional[random.Random] = None,
):
    """Returns ``(split_key, [PlannedExercise])`` without touching the database"""
    rng = rng or random.Random()
    preferences = UserGeneratorPreferences.objects.filter(user=user).first()
    duration_min = duration_min or (preferences.default_duration_min if preferences else 45)
    profile = resolve_profile(user, equipment_profile_id, preferences)

    freshness = {m['muscle_id']: m['freshness_pct'] for m in muscle_freshness(user)}
    # wger ships a "none (bodyweight exercise)" equipment entry that is always available
    bodyweight = list(
        Equipment.objects.filter(name__istartswith='none').values_list('pk', flat=True)
    )
    excluded_exercises = (
        list(preferences.excluded_exercises.values_list('pk', flat=True)) if preferences else []
    )
    excluded_muscles = (
        list(preferences.excluded_muscles.values_list('pk', flat=True)) if preferences else []
    )
    equipment_ids = list(profile.equipment.values_list('pk', flat=True))

    pool = []
    split_key = None
    for split_key in rank_splits(split, preferences, freshness):
        pool = candidate_pool(
            split_key, equipment_ids, bodyweight, excluded_exercises, excluded_muscles
        )
        if pool:
            break
    if not pool:
        raise GeneratorError('No exercises match this split, equipment profile and exclusions.')

    unit = weight_unit_of(user)
    increment = rounding_increment(preferences, unit)
    chosen = select_exercises(pool, split_key, freshness, exercise_count(duration_min), rng)

    planned = []
    for slot, exercise in enumerate(chosen, start=1):
        tier = tier_for_slot(slot)
        sets, reps = TIERS[tier]
        if duration_min < 45 and sets > 3:
            sets = 3
        result = prescribe(user, exercise.pk, reps, increment=increment, unit=unit)
        if result.weight is None:
            reps = COLD_START_REPS
        planned.append(
            PlannedExercise(exercise, slot, tier, sets, reps, result.weight, result.e1rm, unit)
        )
    return split_key, planned


@transaction.atomic
def save_routine(user, planned: list[PlannedExercise], split_key: str, name: str):
    """Persists the plan as Routine > Day > Slot > SlotEntry with set/rep/weight configs"""
    today = date.today()
    routine = Routine.objects.create(
        user=user,
        name=name[:ROUTINE_NAME_MAX],
        description=f'Generated {split_key.replace("_", " ")} session',
        start=today,
        end=today + timedelta(weeks=1),
    )
    day = Day.objects.create(routine=routine, name=split_key.replace('_', ' ').title(), order=1)
    for item in planned:
        slot = Slot.objects.create(day=day, order=item.slot)
        entry = SlotEntry.objects.create(
            slot=slot,
            exercise=item.exercise,
            order=1,
            weight_unit_id=WEIGHT_UNIT_LB if item.unit == 'lb' else WEIGHT_UNIT_KG,
        )
        SetsConfig.objects.create(slot_entry=entry, iteration=1, value=item.sets)
        RepetitionsConfig.objects.create(slot_entry=entry, iteration=1, value=item.target_reps)
        if item.weight is not None:
            WeightConfig.objects.create(slot_entry=entry, iteration=1, value=item.weight)
    return routine, day
