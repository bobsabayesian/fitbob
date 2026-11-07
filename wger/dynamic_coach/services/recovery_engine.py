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
Muscle fatigue and recovery model.

Every logged set adds fatigue to the muscles it works. Fatigue decays
exponentially with a half-life, and freshness is ``1 - sum(fatigue)``, clamped
to [0, 1]::

    freshness(m, t) = max(0, 1 - sum(role * exp(-lambda * (t - t_i)) / capacity))

``role`` is 1.0 for primary and 0.4 for secondary muscles, ``capacity`` is the
number of hard sets that fully exhaust a muscle. The maths are pure functions
over ``FatigueEvent`` so other sources (e.g. conditioning decks) can feed the
same model with synthetic sets.
"""

# Standard Library
import math
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
)
from typing import Iterable

# Django
from django.utils import timezone

# wger
from wger.exercises.models import Muscle
from wger.manager.models import WorkoutLog


HALF_LIFE_HOURS = 36.0
WINDOW_DAYS = 14
VOLUME_CAPACITY = 12.0
PRIMARY_FACTOR = 1.0
SECONDARY_FACTOR = 0.4

STATUS_FATIGUED_BELOW = 40.0
STATUS_READY_FROM = 75.0

DECAY_LAMBDA = math.log(2) / HALF_LIFE_HOURS


@dataclass(frozen=True)
class FatigueEvent:
    muscle_id: int
    when: datetime
    factor: float = PRIMARY_FACTOR
    sets: float = 1.0
    """Number of (equivalent) hard sets, fractional for synthetic volume"""


def fatigue_contribution(event: FatigueEvent, now: datetime, capacity=VOLUME_CAPACITY) -> float:
    hours = max(0.0, (now - event.when).total_seconds() / 3600)
    return event.factor * event.sets * math.exp(-DECAY_LAMBDA * hours) / capacity


def freshness(events: Iterable[FatigueEvent], now: datetime, capacity=VOLUME_CAPACITY) -> float:
    """Freshness 0.0-1.0 of one muscle given its fatigue events"""
    window_start = now - timedelta(days=WINDOW_DAYS)
    total = sum(
        fatigue_contribution(e, now, capacity)
        for e in events
        if window_start <= e.when <= now
    )
    return max(0.0, 1.0 - total)


def status_for(freshness_pct: float) -> str:
    if freshness_pct < STATUS_FATIGUED_BELOW:
        return 'fatigued'
    if freshness_pct < STATUS_READY_FROM:
        return 'recovering'
    return 'ready'


def events_from_logs(user, now: datetime) -> list[FatigueEvent]:
    """One event per logged set and muscle the exercise works"""
    logs = (
        WorkoutLog.objects.filter(user=user, date__gte=now - timedelta(days=WINDOW_DAYS), date__lte=now)
        .select_related('exercise')
        .prefetch_related('exercise__muscles', 'exercise__muscles_secondary')
    )
    events = []
    for log in logs:
        primary = {m.pk for m in log.exercise.muscles.all()}
        for muscle_id in primary:
            events.append(FatigueEvent(muscle_id, log.date, PRIMARY_FACTOR))
        for muscle in log.exercise.muscles_secondary.all():
            if muscle.pk not in primary:
                events.append(FatigueEvent(muscle.pk, log.date, SECONDARY_FACTOR))
    return events


def muscle_freshness(user, now: datetime | None = None, extra_events=()) -> list[dict]:
    """Freshness for every muscle, in the shape of the API response"""
    now = now or timezone.now()
    by_muscle: dict[int, list[FatigueEvent]] = {}
    for event in [*events_from_logs(user, now), *extra_events]:
        by_muscle.setdefault(event.muscle_id, []).append(event)

    result = []
    for muscle in Muscle.objects.order_by('pk'):
        pct = round(freshness(by_muscle.get(muscle.pk, ()), now) * 100, 1)
        result.append(
            {
                'muscle_id': muscle.pk,
                'name': muscle.name,
                'freshness_pct': pct,
                'status': status_for(pct),
            }
        )
    return result
