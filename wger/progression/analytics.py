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
Per-exercise progress analytics and next-session suggestion.

wger already applies progression rules to routines (gated ``SlotEntry`` configs
with requirements, double progression, ...). This module is the read-only
counterpart: it looks at what was actually lifted and answers "how is it going"
(history, PRs, stalls) and "what should I do next" (double progression).
"""

# Standard Library
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Optional

# Django
from django.utils import timezone

# wger
from wger.manager.consts import (
    REP_UNIT_REPETITIONS,
    WEIGHT_UNIT_KG,
)
from wger.manager.models import WorkoutLog
from wger.progression.services import (
    DEFAULT_INCREMENT_KG,
    MAX_RELIABLE_REPS,
    epley_e1rm,
    round_to_increment,
)


STALL_SESSIONS = 4
"""Sessions without a new best e1RM before an exercise counts as stalled"""


@dataclass
class SetData:
    date: object  # datetime
    weight: Decimal
    reps: int


def load_sets(user, exercise_id, days=180, now=None) -> list[SetData]:
    """Usable kg/repetition sets of the exercise, oldest first"""
    since = (now or timezone.now()) - timedelta(days=days)
    rows = (
        WorkoutLog.objects.filter(
            user=user,
            exercise_id=exercise_id,
            date__gte=since,
            weight_unit_id=WEIGHT_UNIT_KG,
            repetitions_unit_id=REP_UNIT_REPETITIONS,
            weight__gt=0,
            repetitions__gt=0,
        )
        .order_by('date')
        .values_list('date', 'weight', 'repetitions')
    )
    return [SetData(d, Decimal(w), int(r)) for d, w, r in rows]


def group_sessions(sets: list[SetData]) -> list[list[SetData]]:
    """Sets grouped by calendar day (local time), oldest session first"""
    days: dict = {}
    for s in sets:
        days.setdefault(timezone.localtime(s.date).date(), []).append(s)
    return [days[d] for d in sorted(days)]


def session_summary(session: list[SetData]) -> dict:
    top = max(session, key=lambda s: (s.weight, s.reps))
    e1rms = [epley_e1rm(s.weight, s.reps) for s in session if s.reps <= MAX_RELIABLE_REPS]
    return {
        'date': timezone.localtime(session[0].date).date().isoformat(),
        'sets': len(session),
        'top_weight': top.weight,
        'top_weight_reps': top.reps,
        'best_e1rm': round(max(e1rms), 1) if e1rms else None,
        'volume': sum(s.weight * s.reps for s in session),
    }


def analyze(sets: list[SetData], stall_sessions=STALL_SESSIONS) -> dict:
    """History, PRs and stall detection for one exercise"""
    sessions = [session_summary(s) for s in group_sessions(sets)]

    prs = []
    best = None
    best_idx = None
    for i, s in enumerate(sessions):
        if s['best_e1rm'] is not None and (best is None or s['best_e1rm'] > best):
            best, best_idx = s['best_e1rm'], i
            prs.append({'date': s['date'], 'e1rm': s['best_e1rm']})

    stalled = False
    if best_idx is not None:
        stalled = len(sessions) - 1 - best_idx >= stall_sessions

    return {
        'sessions': sessions,
        'personal_records': prs,
        'best_e1rm': best,
        'stalled': stalled,
    }


@dataclass
class Suggestion:
    weight: Optional[Decimal]
    reps: int
    sets: int
    reason: str


def suggest_next(
    sets: list[SetData],
    min_reps=8,
    max_reps=12,
    target_sets=3,
    increment=DEFAULT_INCREMENT_KG,
) -> Suggestion:
    """
    Double progression from the last session: add reps at the same weight until
    every set reaches ``max_reps``, then add weight and drop back to ``min_reps``.
    Working weight is the heaviest weight of the last session.
    """
    sessions = group_sessions(sets)
    if not sessions:
        return Suggestion(None, min_reps, target_sets, 'No history: pick a weight you can '
                          f'move for {min_reps} reps and log it to calibrate.')

    last = sessions[-1]
    weight = max(s.weight for s in last)
    at_weight = [s for s in last if s.weight == weight]
    lowest = min(s.reps for s in at_weight)

    if len(at_weight) >= target_sets and lowest >= max_reps:
        new_weight = round_to_increment(weight + Decimal(increment), increment)
        return Suggestion(
            new_weight, min_reps, target_sets,
            f'All {len(at_weight)} sets hit {max_reps} reps at {weight} kg: add weight.',
        )

    reps = min(max_reps, max(min_reps, lowest + 1))
    return Suggestion(
        weight, reps, target_sets,
        f'Stay at {weight} kg and aim for {reps} reps on every set.',
    )
