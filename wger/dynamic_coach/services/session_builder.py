"""Strength session generation"""

# Standard Library
import datetime
import secrets
from decimal import Decimal

# Django
from django.db import transaction

# wger
from wger.core.models import RepetitionUnit
from wger.dynamic_coach.models import (
    EquipmentProfile,
    UserGeneratorPreferences,
)
from wger.dynamic_coach.services import (
    progression,
    recovery_engine,
)
from wger.dynamic_coach.services.common import (
    exercise_name,
    muscle_key,
)
from wger.exercises.models import Exercise
from wger.manager.models import (
    Day,
    Routine,
    Slot,
    SlotEntry,
)
from wger.manager.models.repetitions_config import RepetitionsConfig
from wger.manager.models.sets_config import SetsConfig
from wger.manager.models.weight_config import WeightConfig


PUSH = {'pectoralis major', 'anterior deltoid', 'triceps brachii'}
PULL = {'latissimus dorsi', 'trapezius', 'biceps brachii', 'brachialis', 'serratus anterior'}
LEGS = {
    'quadriceps femoris',
    'biceps femoris',
    'gluteus maximus',
    'gastrocnemius',
    'soleus',
}
CORE = {'rectus abdominis', 'obliquus externus abdominis'}

SPLITS = {
    'push': PUSH,
    'pull': PULL,
    'legs': LEGS,
    'upper': PUSH | PULL,
    'lower': LEGS | CORE,
    'full_body': PUSH | PULL | LEGS | CORE,
}
# Groups AUTO picks from, depending on the user's preferred split
AUTO_CANDIDATES = {
    'PPL': ['push', 'pull', 'legs'],
    'UL': ['upper', 'lower'],
    'FULL_BODY': ['full_body'],
    'AUTO': ['push', 'pull', 'legs'],
}

# tier -> (sets, rep range, target reps)
TIERS = {
    1: (4, (6, 8), 8),
    2: (3, (8, 10), 10),
    3: (3, (10, 15), 12),
}
ROUTINE_LENGTH_DAYS = 7

_rng = secrets.SystemRandom()


class GenerationError(Exception):
    pass


def num_exercises(duration_min: int) -> int:
    return max(3, min(7, duration_min // 10))


def tier_for_slot(slot: int) -> int:
    return 1 if slot <= 2 else 2 if slot <= 4 else 3


def resolve_profile(user, profile_id=None, prefs=None):
    """
    Explicit id > preferences default > active profile. Returns None if the user has no
    profile at all, which means equipment is not restricted.
    """
    if profile_id is not None:
        try:
            return EquipmentProfile.objects.get(pk=profile_id, user=user)
        except EquipmentProfile.DoesNotExist:
            raise GenerationError(f'Unknown equipment profile {profile_id}')
    if prefs and prefs.default_equipment_profile_id:
        return prefs.default_equipment_profile
    return EquipmentProfile.objects.filter(user=user, is_active=True).first()


def _group_freshness(group: set[str], muscles, freshness) -> float:
    ids = [m.pk for m in muscles if muscle_key(m) in group]
    return sum(freshness[i] for i in ids) / len(ids) if ids else 0.0


def choose_split(split: str, preferred: str, muscles, freshness) -> str:
    split = (split or 'auto').lower()
    if split in SPLITS:
        return split
    if split != 'auto':
        raise GenerationError(f'Unknown split {split}')
    candidates = AUTO_CANDIDATES.get(preferred, AUTO_CANDIDATES['AUTO'])
    return max(candidates, key=lambda c: _group_freshness(SPLITS[c], muscles, freshness))


def _is_compound(e: Exercise) -> bool:
    return len(e.muscles.all()) + len(e.muscles_secondary.all()) >= 3


def select_exercises(user, split: str, count: int, profile, prefs, freshness) -> list[Exercise]:
    # wger
    from wger.exercises.models import Muscle

    muscles = list(Muscle.objects.all())
    targets = {m.pk for m in muscles if muscle_key(m) in SPLITS[split]}
    excluded_muscles = set(prefs.excluded_muscles.values_list('pk', flat=True)) if prefs else set()
    excluded_exercises = (
        set(prefs.excluded_exercises.values_list('pk', flat=True)) if prefs else set()
    )
    allowed_equipment = set(profile.equipment.values_list('pk', flat=True)) if profile else None

    pool = []
    for e in (
        Exercise.objects.filter(muscles__in=targets)
        .exclude(pk__in=excluded_exercises)
        .distinct()
        .prefetch_related('muscles', 'muscles_secondary', 'equipment', 'translations')
    ):
        used = {m.pk for m in e.muscles.all()} | {m.pk for m in e.muscles_secondary.all()}
        if used & excluded_muscles:
            continue
        # Required equipment must be a subset of what the profile has
        if (
            allowed_equipment is not None
            and not {q.pk for q in e.equipment.all()} <= allowed_equipment
        ):
            continue
        if not e.translations.all():
            continue
        pool.append(e)

    def primary_freshness(e):
        ids = [m.pk for m in e.muscles.all() if m.pk in targets]
        return sum(freshness[i] for i in ids) / len(ids)

    # Fresh muscles first, random tiebreak so repeated generations vary
    ranked = sorted(pool, key=lambda e: (-round(primary_freshness(e), 2), _rng.random()))
    compounds = [e for e in ranked if _is_compound(e)]
    isolations = [e for e in ranked if not _is_compound(e)]

    chosen: list[Exercise] = []
    seen_groups = set()

    def take(candidates):
        for e in candidates:
            if e in chosen or (e.variation_group and e.variation_group in seen_groups):
                continue
            chosen.append(e)
            if e.variation_group:
                seen_groups.add(e.variation_group)
            return True
        return False

    for slot in range(1, count + 1):
        tier = tier_for_slot(slot)
        order = [compounds, isolations, ranked] if tier < 3 else [isolations, compounds, ranked]
        for candidates in order:
            if take(candidates):
                break
    return chosen


def build_session(
    user,
    duration_min=None,
    equipment_profile_id=None,
    split='auto',
    save_as_routine=True,
    routine_name=None,
    today=None,
) -> dict:
    # wger
    from wger.exercises.models import Muscle

    prefs = UserGeneratorPreferences.objects.filter(user=user).first()
    duration_min = duration_min or (prefs.default_duration_min if prefs else 45)
    increment = prefs.rounding_increment_kg if prefs else Decimal('2.5')
    profile = resolve_profile(user, equipment_profile_id, prefs)

    freshness = recovery_engine.freshness_scores(user)
    muscles = list(Muscle.objects.all())
    split = choose_split(split, prefs.preferred_split if prefs else 'AUTO', muscles, freshness)

    exercises = select_exercises(
        user, split, num_exercises(duration_min), profile, prefs, freshness
    )
    if not exercises:
        raise GenerationError('No exercises match your equipment, exclusions and split')

    plan = []
    for slot, exercise in enumerate(exercises, start=1):
        sets, rep_range, target_reps = TIERS[tier_for_slot(slot)]
        rx = progression.prescribe(user, exercise, target_reps, increment)
        plan.append(
            {
                'exercise_id': exercise.pk,
                'name': exercise_name(exercise),
                'slot': slot,
                'sets': sets,
                'target_reps': rx['reps'],
                'rep_range': list(rep_range),
                'prescribed_weight_kg': rx['weight'],
                'e1rm_basis_kg': round(rx['e1rm'], 1) if rx['e1rm'] is not None else None,
            }
        )

    result = {'routine_id': None, 'day_id': None, 'split': split, 'exercises': plan}
    if save_as_routine:
        today = today or datetime.date.today()
        routine, day = _persist(
            user,
            plan,
            routine_name or f'Dynamic {split.title()} - {today:%b %d}',
            split,
            today,
            increment,
        )
        result.update(routine_id=routine.pk, day_id=day.pk)
    return result


@transaction.atomic
def _persist(user, plan, name, split, today, increment):
    routine = Routine.objects.create(
        user=user,
        name=name,
        description=f'Generated by Dynamic Coach ({split})',
        start=today,
        end=today + datetime.timedelta(days=ROUTINE_LENGTH_DAYS),
    )
    day = Day.objects.create(routine=routine, order=1, name=split.replace('_', ' ').title())
    reps_unit = RepetitionUnit.objects.filter(
        unit_type=RepetitionUnit.UNIT_TYPE_REPETITIONS
    ).first()

    for item in plan:
        slot = Slot.objects.create(day=day, order=item['slot'])
        entry_kwargs = {'weight_rounding': increment}
        if reps_unit:
            entry_kwargs['repetition_unit'] = reps_unit
        entry = SlotEntry.objects.create(
            slot=slot,
            exercise_id=item['exercise_id'],
            order=1,
            **entry_kwargs,
        )
        SetsConfig.objects.create(slot_entry=entry, iteration=1, value=item['sets'])
        RepetitionsConfig.objects.create(
            slot_entry=entry, iteration=1, value=Decimal(item['target_reps'])
        )
        if item['prescribed_weight_kg'] is not None:
            WeightConfig.objects.create(
                slot_entry=entry, iteration=1, value=item['prescribed_weight_kg']
            )
    return routine, day
