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
Import a Fitbod workout export (CSV) as wger workout sessions and logs.

Every row of the export is one set. A reviewed mapping file decides which wger
exercise each Fitbod name becomes (see ``extras/scripts/fitbod_mapping.py``).

What is imported, and what is not:

* Strength sets (reps, optionally weight) and timed holds such as planks (the
  duration becomes the repetitions, in seconds).
* Weights are stored per hand for dumbbells, like wger does. The export's
  ``multiplier`` column is ignored.
* Not imported: rows with a distance (cycling, running, hiking, rowing, ...)
  until cardio has its own log type, and warm-up sets. Both are counted in the report.
* Each Fitbod workout (one timestamp) becomes one session. The export has no
  end times, so a session ends at its last set.
"""

# Standard Library
import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

# Django
from django.db import transaction

# wger
from wger.core.models import Language
from wger.dynamic_coach.fitbod.tags import (
    CATEGORIES,
    MUSCLES,
    STOCK_EQUIPMENT,
    infer_tags,
)
from wger.dynamic_coach.models import LibraryEntry
from wger.exercises.models import (
    Equipment,
    Exercise,
    ExerciseCategory,
    Muscle,
    Translation,
)
from wger.manager.consts import (
    REP_UNIT_REPETITIONS,
    REP_UNIT_SECONDS,
    WEIGHT_UNIT_KG,
    WEIGHT_UNIT_LB,
)
from wger.manager.models import (
    WorkoutLog,
    WorkoutSession,
)
from wger.progression.services import KG_PER_LB


IMPORT_MARKER = 'Imported from Fitbod'
ACTIONS = ('map', 'create', 'skip')
LB_SHARE_FOR_AUTO = 0.8
"""Share of weights that must be multiples of 2.5 lb for the data to count as pounds"""


class ImportError_(Exception):
    """Bad input; the message says what to fix"""


@dataclass
class ExportSet:
    started: datetime
    exercise: str
    reps: int
    weight_kg: float
    duration_s: float
    distance_m: float
    warmup: bool


@dataclass
class MappingRow:
    action: str
    name: str
    wger_id: int | None = None
    category: str = ''
    primary: list = field(default_factory=list)
    secondary: list = field(default_factory=list)
    equipment: list = field(default_factory=list)


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _list(value):
    return [part.strip() for part in (value or '').split(';') if part.strip()]


def read_export(path) -> list[ExportSet]:
    sets = []
    with open(path, newline='') as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            try:
                started = datetime.strptime(row['Date'].strip(), '%Y-%m-%d %H:%M:%S %z')
            except (KeyError, ValueError):
                raise ImportError_(f'Line {line}: unreadable date {row.get("Date")!r}')
            sets.append(
                ExportSet(
                    started=started,
                    exercise=row['Exercise'].strip(),
                    reps=int(_float(row['Reps'])),
                    weight_kg=_float(row['Weight(kg)']),
                    duration_s=_float(row['Duration(s)']),
                    distance_m=_float(row['Distance(m)']),
                    warmup=row['isWarmup'].strip().lower() == 'true',
                )
            )
    return sets


def read_mapping(path) -> dict[str, MappingRow]:
    rows = {}
    with open(path, newline='') as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            action = (row.get('action') or '').strip().lower()
            name = (row.get('fitbod_name') or '').strip()
            if action not in ACTIONS:
                raise ImportError_(f'Mapping line {line} ({name}): action must be one of {ACTIONS}')
            wger_id = None
            if action == 'map':
                try:
                    wger_id = int(row.get('wger_id') or '')
                except ValueError:
                    raise ImportError_(f'Mapping line {line} ({name}): "map" needs a wger_id')
            rows[name] = MappingRow(
                action=action,
                name=name,
                wger_id=wger_id,
                category=(row.get('category') or '').strip().lower(),
                primary=_list(row.get('primary')),
                secondary=_list(row.get('secondary')),
                equipment=_list(row.get('equipment')),
            )
    return rows


def detect_weight_unit(sets: list[ExportSet]) -> str:
    """'lb' when nearly all weights are multiples of 2.5 lb (the export always says kg)"""
    weights = [s.weight_kg for s in sets if s.weight_kg > 0]
    if not weights:
        return 'kg'
    lb_like = 0
    for kg in weights:
        pounds = kg / float(KG_PER_LB)
        if abs(pounds - round(pounds / 2.5) * 2.5) < 0.05:
            lb_like += 1
    return 'lb' if lb_like / len(weights) >= LB_SHARE_FOR_AUTO else 'kg'


def convert_weight(kg: float, unit: str) -> Decimal | None:
    if kg <= 0:
        return None
    if unit == 'kg':
        return Decimal(str(round(kg, 2)))
    pounds = kg / float(KG_PER_LB)
    quarter = round(pounds * 4) / 4
    return Decimal(str(quarter if abs(pounds - quarter) < 0.05 else round(pounds, 2)))


def check_mapping(sets, mapping):
    """Every exercise in the export needs a row, every mapped id must exist"""
    names = {s.exercise for s in sets}
    missing = sorted(names - set(mapping))
    if missing:
        raise ImportError_(f'{len(missing)} exercises have no mapping row, e.g. {missing[:5]}')
    ids = {row.wger_id for row in mapping.values() if row.action == 'map'}
    found = set(Exercise.objects.filter(pk__in=ids).values_list('pk', flat=True))
    if ids - found:
        raise ImportError_(f'wger exercise ids that do not exist: {sorted(ids - found)}')


def _muscles(names):
    unknown = [n for n in names if n not in MUSCLES]
    if unknown:
        raise ImportError_(f'Unknown muscles {unknown}, use {sorted(MUSCLES)}')
    return list(Muscle.objects.filter(pk__in=[MUSCLES[n] for n in names]))


def create_exercise(row: MappingRow, english: Language) -> Exercise:
    """Reuses an exercise with exactly this English name, otherwise makes a new one"""
    existing = Translation.objects.filter(language=english, name__iexact=row.name).first()
    if existing:
        return existing.exercise

    tags = infer_tags(row.name)
    category = row.category or tags.category
    if category not in CATEGORIES:
        raise ImportError_(f'{row.name}: unknown category {category!r}, use {sorted(CATEGORIES)}')

    category_row, _ = ExerciseCategory.objects.get_or_create(name=category.capitalize())
    exercise = Exercise.objects.create(category=category_row)
    exercise.muscles.set(_muscles(row.primary or tags.primary))
    exercise.muscles_secondary.set(_muscles(row.secondary or tags.secondary))
    equipment = []
    for name in row.equipment or tags.equipment:
        item, _ = Equipment.objects.get_or_create(name=name)
        equipment.append(item)
    exercise.equipment.set(equipment)
    Translation.objects.create(exercise=exercise, language=english, name=row.name)
    return exercise


@transaction.atomic
def run_import(user, sets, mapping, weight_unit='auto', replace=False, dry_run=False) -> dict:
    check_mapping(sets, mapping)
    if weight_unit == 'auto':
        weight_unit = detect_weight_unit(sets)
    unit_id = WEIGHT_UNIT_LB if weight_unit == 'lb' else WEIGHT_UNIT_KG

    previous = WorkoutSession.objects.filter(user=user, notes=IMPORT_MARKER)
    if previous.exists():
        if not replace:
            raise ImportError_(
                f'{previous.count()} sessions from an earlier import exist, use --replace to redo it.'
            )
        previous.delete()

    english = Language.objects.get(short_name='en')
    exercises = {}
    created = 0
    for row in mapping.values():
        if row.action == 'skip':
            continue
        if row.action == 'map':
            exercises[row.name] = Exercise.objects.get(pk=row.wger_id)
        else:
            before = Exercise.objects.count()
            exercises[row.name] = create_exercise(row, english)
            created += Exercise.objects.count() - before

    skipped = Counter()
    by_workout: dict[datetime, list[ExportSet]] = {}
    for s in sets:
        if mapping[s.exercise].action == 'skip':
            skipped['skipped by mapping'] += 1
        elif s.warmup:
            skipped['warm-up'] += 1
        elif s.distance_m > 0:
            skipped['distance (cardio)'] += 1
        elif s.reps <= 0 and s.duration_s <= 0:
            skipped['no reps or time'] += 1
        else:
            by_workout.setdefault(s.started, []).append(s)

    sessions, logs = [], []
    for started, workout in sorted(by_workout.items()):
        session = WorkoutSession(
            user=user,
            datetime_start=started,
            datetime_end=started + timedelta(seconds=len(workout) - 1),
            notes=IMPORT_MARKER,
        )
        sessions.append(session)
        for i, s in enumerate(workout):
            timed = s.reps <= 0
            logs.append(
                WorkoutLog(
                    user=user,
                    session=session,
                    exercise=exercises[s.exercise],
                    date=started + timedelta(seconds=i),
                    repetitions=Decimal(str(round(s.duration_s))) if timed else Decimal(s.reps),
                    repetitions_unit_id=REP_UNIT_SECONDS if timed else REP_UNIT_REPETITIONS,
                    weight=convert_weight(s.weight_kg, weight_unit),
                    weight_unit_id=unit_id if s.weight_kg > 0 else None,
                )
            )
    WorkoutSession.objects.bulk_create(sessions, batch_size=500)
    WorkoutLog.objects.bulk_create(logs, batch_size=1000)

    used = {log.exercise_id for log in logs}
    for exercise_id in used:
        LibraryEntry.objects.get_or_create(user=user, exercise_id=exercise_id)

    if dry_run:
        transaction.set_rollback(True)

    return {
        'weight_unit': weight_unit,
        'sessions': len(sessions),
        'sets_imported': len(logs),
        'sets_skipped': dict(skipped),
        'exercises_created': created,
        'library_size': len(used),
        'dry_run': dry_run,
    }
