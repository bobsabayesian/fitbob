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
Progressive overload maths shared by the progression rules, analytics and the
(planned) dynamic workout generator.

The pure functions work on plain numbers and never touch the database, so they
are trivial to test. ``prescribe`` is the one function that reads logs.
"""

# Standard Library
from dataclasses import dataclass
from datetime import timedelta
from decimal import (
    ROUND_HALF_UP,
    Decimal,
)
from typing import Optional

# Django
from django.utils import timezone

# wger
from wger.manager.consts import (
    REP_UNIT_REPETITIONS,
    WEIGHT_UNIT_KG,
    WEIGHT_UNIT_LB,
)
from wger.manager.models import WorkoutLog


DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_INCREMENT_KG = Decimal('2.5')
COLD_START_REPS = 10
KG_PER_LB = Decimal('0.45359237')
MAX_RELIABLE_REPS = 12
"""Above this many reps the 1RM formulas become unreliable, so such sets are ignored"""


def to_kg(weight, weight_unit_id) -> Decimal:
    """Converts a logged weight to kg. Only kg and lb logs are understood."""
    weight = Decimal(weight)
    if weight_unit_id == WEIGHT_UNIT_LB:
        return weight * KG_PER_LB
    if weight_unit_id == WEIGHT_UNIT_KG:
        return weight
    raise ValueError(f'Unsupported weight unit {weight_unit_id}')


def to_unit(weight_kg, unit: str) -> Decimal:
    """Converts kg to the user's unit ('kg' or 'lb')"""
    weight_kg = Decimal(weight_kg)
    return weight_kg / KG_PER_LB if unit == 'lb' else weight_kg


def epley_e1rm(weight, reps) -> Decimal:
    """Epley: w * (1 + r / 30). A single rep is the weight itself."""
    weight, reps = Decimal(weight), Decimal(reps)
    if weight <= 0 or reps <= 0:
        raise ValueError('weight and reps must be positive')
    if reps == 1:
        return weight
    return weight * (1 + reps / 30)


def brzycki_e1rm(weight, reps) -> Decimal:
    """Brzycki: w * 36 / (37 - r). Only defined for fewer than 37 reps."""
    weight, reps = Decimal(weight), Decimal(reps)
    if weight <= 0 or reps <= 0:
        raise ValueError('weight and reps must be positive')
    if reps >= 37:
        raise ValueError('Brzycki is undefined for 37 or more reps')
    return weight * 36 / (37 - reps)


def weight_for_reps(e1rm, reps) -> Decimal:
    """Inverse Epley: the load that should be doable for ``reps`` repetitions"""
    e1rm, reps = Decimal(e1rm), Decimal(reps)
    if e1rm <= 0 or reps <= 0:
        raise ValueError('e1rm and reps must be positive')
    if reps == 1:
        return e1rm
    return e1rm / (1 + reps / 30)


def round_to_increment(weight, increment=DEFAULT_INCREMENT_KG) -> Decimal:
    """Round to the nearest multiple of ``increment`` (half rounds up)"""
    increment = Decimal(increment)
    if increment <= 0:
        raise ValueError('increment must be positive')
    steps = (Decimal(weight) / increment).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return steps * increment


@dataclass
class Prescription:
    exercise_id: int
    target_reps: int
    weight: Optional[Decimal]
    """In ``unit``; None on a cold start (no usable history)"""
    e1rm: Optional[Decimal]
    """Always kg"""
    unit: str = 'kg'


def best_e1rm(user, exercise_id, days=DEFAULT_LOOKBACK_DAYS, now=None) -> Optional[Decimal]:
    """
    Best estimated 1RM (kg) from the user's logged sets of this exercise in the
    lookback window, or None. Only kg/lb repetition sets with a sane rep count count.
    """
    since = (now or timezone.now()) - timedelta(days=days)
    logs = WorkoutLog.objects.filter(
        user=user,
        exercise_id=exercise_id,
        date__gte=since,
        weight_unit_id__in=(WEIGHT_UNIT_KG, WEIGHT_UNIT_LB),
        repetitions_unit_id=REP_UNIT_REPETITIONS,
        weight__gt=0,
        repetitions__gt=0,
        repetitions__lte=MAX_RELIABLE_REPS,
    ).values_list('weight', 'repetitions', 'weight_unit_id')

    best = None
    for weight, reps, unit_id in logs:
        value = epley_e1rm(to_kg(weight, unit_id), reps)
        if best is None or value > best:
            best = value
    return best


def prescribe(
    user,
    exercise_id,
    target_reps,
    increment=DEFAULT_INCREMENT_KG,
    days=DEFAULT_LOOKBACK_DAYS,
    now=None,
    unit='kg',
) -> Prescription:
    """
    Working weight for ``target_reps`` based on recent history. With no history the
    weight is None and the caller should use ``COLD_START_REPS`` to calibrate.
    """
    e1rm = best_e1rm(user, exercise_id, days=days, now=now)
    if e1rm is None:
        return Prescription(exercise_id, target_reps, None, None, unit)
    raw = to_unit(weight_for_reps(e1rm, target_reps), unit)
    return Prescription(
        exercise_id, target_reps, round_to_increment(raw, increment), e1rm, unit
    )
