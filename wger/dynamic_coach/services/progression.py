"""Progressive overload: e1RM estimation and weight prescription"""

# Standard Library
from datetime import timedelta
from decimal import (
    ROUND_HALF_UP,
    Decimal,
)
from typing import Optional

# Django
from django.utils import timezone

# wger
from wger.manager.models import WorkoutLog


HISTORY_DAYS = 90
MAX_RELIABLE_REPS = 12
COLD_START_REPS = 10


def epley_e1rm(weight: float, reps: float) -> float:
    return float(weight) * (1 + float(reps) / 30)


def brzycki_e1rm(weight: float, reps: float) -> float:
    return float(weight) * 36 / (37 - float(reps))


def weight_for_reps(e1rm: float, reps: int) -> float:
    """
    Inverse Epley: the load that e1rm allows for `reps` repetitions.

    Note this is e1rm / (1 + reps/30), not e1rm * (1 - reps/30): the latter does not
    round trip (a set of 80 x 8 would prescribe ~75 for 8 reps again).
    """
    return e1rm / (1 + reps / 30)


def round_to_increment(value: float, increment) -> Decimal:
    inc = Decimal(str(increment))
    if inc <= 0:
        raise ValueError('increment must be positive')
    steps = (Decimal(str(value)) / inc).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return steps * inc


def best_e1rm(user, exercise, now=None) -> Optional[float]:
    """
    Best e1RM over the last 90 days. The set with the highest estimate is used,
    not the heaviest one (100 x 1 is a weaker signal than 90 x 8).
    """
    now = now or timezone.now()
    logs = WorkoutLog.objects.filter(
        user=user,
        exercise=exercise,
        date__gte=now - timedelta(days=HISTORY_DAYS),
        date__lte=now,
        weight__gt=0,
        repetitions__gt=0,
    ).values_list('weight', 'repetitions')

    best = None
    for weight, reps in logs:
        # Epley is unreliable for high-rep sets
        estimate = epley_e1rm(weight, min(reps, MAX_RELIABLE_REPS))
        if best is None or estimate > best:
            best = estimate
    return best


def prescribe(user, exercise, target_reps: int, increment=Decimal('2.5'), now=None) -> dict:
    """
    Returns {'weight': Decimal | None, 'reps': int, 'e1rm': float | None}.
    Without history the weight is None and the user calibrates on COLD_START_REPS.
    """
    e1rm = best_e1rm(user, exercise, now)
    if e1rm is None:
        return {'weight': None, 'reps': COLD_START_REPS, 'e1rm': None}

    weight = round_to_increment(weight_for_reps(e1rm, target_reps), increment)
    return {'weight': weight, 'reps': target_reps, 'e1rm': e1rm}
