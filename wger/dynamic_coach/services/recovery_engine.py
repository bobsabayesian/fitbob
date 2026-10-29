"""
Muscle fatigue and recovery.

Every muscle starts at freshness 1.0. Each logged set adds fatigue
(role factor / volume capacity) which decays exponentially with a half-life:

    freshness_m(t) = max(0, 1 - sum_i contribution_i * exp(-lambda * (t - t_i)))
"""

# Standard Library
import math
from datetime import timedelta

# Django
from django.utils import timezone

# wger
from wger.dynamic_coach.models import ConditioningLoad
from wger.exercises.models import Muscle
from wger.manager.models import WorkoutLog


HALF_LIFE_HOURS = 36.0
WINDOW_DAYS = 14
VOLUME_CAPACITY = 12.0
PRIMARY_FACTOR = 1.0
SECONDARY_FACTOR = 0.4
DECAY_LAMBDA = math.log(2) / HALF_LIFE_HOURS

READY_THRESHOLD = 0.70
RECOVERING_THRESHOLD = 0.40


def decay_factor(hours: float) -> float:
    return math.exp(-DECAY_LAMBDA * max(0.0, hours))


def status_for(freshness: float) -> str:
    if freshness >= READY_THRESHOLD:
        return 'ready'
    if freshness >= RECOVERING_THRESHOLD:
        return 'recovering'
    return 'fatigued'


def fatigue_events(user, now=None):
    """
    Yields (timestamp, {muscle_id: effective_sets}) for everything the user did
    inside the window: logged sets plus synthetic conditioning load.
    """
    now = now or timezone.now()
    start = now - timedelta(days=WINDOW_DAYS)

    logs = (
        WorkoutLog.objects.filter(user=user, date__gte=start, date__lte=now)
        .exclude(repetitions__isnull=True)
        .select_related('exercise')
        .prefetch_related('exercise__muscles', 'exercise__muscles_secondary')
    )
    for log in logs:
        if not log.repetitions or log.repetitions <= 0:
            continue
        loads: dict[int, float] = {}
        for muscle in log.exercise.muscles.all():
            loads[muscle.pk] = loads.get(muscle.pk, 0.0) + PRIMARY_FACTOR
        for muscle in log.exercise.muscles_secondary.all():
            loads[muscle.pk] = loads.get(muscle.pk, 0.0) + SECONDARY_FACTOR
        yield log.date, loads

    for load in ConditioningLoad.objects.filter(
        user=user, logged_at__gte=start, logged_at__lte=now
    ):
        yield load.logged_at, {int(k): float(v) for k, v in load.loads.items()}


def freshness_scores(user, now=None) -> dict[int, float]:
    """Muscle id -> freshness (0.0 - 1.0) for every muscle in the database"""
    now = now or timezone.now()
    fatigue: dict[int, float] = {m: 0.0 for m in Muscle.objects.values_list('pk', flat=True)}

    for ts, loads in fatigue_events(user, now):
        hours = (now - ts).total_seconds() / 3600
        weight = decay_factor(hours)
        for muscle_id, sets in loads.items():
            if muscle_id in fatigue:
                fatigue[muscle_id] += sets / VOLUME_CAPACITY * weight

    return {m: max(0.0, 1.0 - f) for m, f in fatigue.items()}


def log_conditioning_load(user, source: str, loads: dict, source_ref: str = '', logged_at=None):
    """
    Records synthetic sets. With a source_ref the call is idempotent, a second
    call for the same source object returns the existing row.
    """
    payload = {str(k): round(float(v), 4) for k, v in loads.items() if v > 0}
    if source_ref:
        obj, _ = ConditioningLoad.objects.get_or_create(
            user=user,
            source=source,
            source_ref=source_ref,
            defaults={'logged_at': logged_at or timezone.now(), 'loads': payload},
        )
        return obj
    return ConditioningLoad.objects.create(
        user=user, source=source, logged_at=logged_at or timezone.now(), loads=payload
    )
