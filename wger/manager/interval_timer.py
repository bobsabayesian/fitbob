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
#
# You should have received a copy of the GNU Affero General Public License

"""
Interval timer (tabata, HIIT, EMOM, ...) configuration and plan generation.

The configuration is stored in ``Day.config`` (see ``INTERVAL_DAY_TYPES``) and is
versioned so the format can evolve. Clients never need to implement the interval
arithmetic themselves: ``build_plan`` turns a config into a flat, ordered list of
timed phases that can simply be played back.

Config format (version 1), all durations in seconds::

    {
        "version": 1,
        "warmup_s": 0,
        "work_s": 20,
        "rest_s": 10,
        "rounds": 8,       # work/rest intervals per set
        "sets": 1,         # repeat the whole block this many times
        "set_rest_s": 60,  # rest between sets (not after the last one)
        "cooldown_s": 0
    }
"""

# Standard Library
from typing import Any

# Django
from django.core.exceptions import ValidationError

# wger
from wger.manager.models.day import DayType


INTERVAL_DAY_TYPES = (DayType.TABATA, DayType.HIIT, DayType.ENOM)
"""Day types whose ``config`` holds an interval timer configuration"""

CONFIG_VERSION = 1

PHASE_WARMUP = 'warmup'
PHASE_WORK = 'work'
PHASE_REST = 'rest'
PHASE_SET_REST = 'set_rest'
PHASE_COOLDOWN = 'cooldown'

MAX_ROUNDS = 100
MAX_SETS = 50
MAX_SECONDS = 3600
MAX_PHASES = 5000

DEFAULTS: dict[str, int] = {
    'warmup_s': 0,
    'work_s': 20,
    'rest_s': 10,
    'rounds': 8,
    'sets': 1,
    'set_rest_s': 60,
    'cooldown_s': 0,
}

# (min, max) per field. work_s and rounds and sets must be at least 1
_LIMITS: dict[str, tuple[int, int]] = {
    'warmup_s': (0, MAX_SECONDS),
    'work_s': (1, MAX_SECONDS),
    'rest_s': (0, MAX_SECONDS),
    'rounds': (1, MAX_ROUNDS),
    'sets': (1, MAX_SETS),
    'set_rest_s': (0, MAX_SECONDS),
    'cooldown_s': (0, MAX_SECONDS),
}

PRESETS: dict[str, dict[str, Any]] = {
    'tabata': {
        'name': 'Classic Tabata',
        'type': DayType.TABATA,
        'config': {**DEFAULTS, 'version': CONFIG_VERSION},
    },
    'tabata_4min_x2': {
        'name': 'Tabata, 2 blocks',
        'type': DayType.TABATA,
        'config': {**DEFAULTS, 'version': CONFIG_VERSION, 'sets': 2, 'set_rest_s': 60},
    },
    'hiit_30_30': {
        'name': 'HIIT 30/30',
        'type': DayType.HIIT,
        'config': {**DEFAULTS, 'version': CONFIG_VERSION, 'work_s': 30, 'rest_s': 30, 'rounds': 10},
    },
    'hiit_40_20': {
        'name': 'HIIT 40/20',
        'type': DayType.HIIT,
        'config': {**DEFAULTS, 'version': CONFIG_VERSION, 'work_s': 40, 'rest_s': 20, 'rounds': 10},
    },
    'emom_10': {
        'name': 'EMOM 10 minutes',
        'type': DayType.ENOM,
        'config': {
            **DEFAULTS,
            'version': CONFIG_VERSION,
            'work_s': 60,
            'rest_s': 0,
            'rounds': 10,
        },
    },
}


def normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """
    Validate an interval config and return it with defaults filled in.

    :raises ValidationError: if the config is malformed or out of range
    """
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValidationError('The interval configuration must be an object')

    version = config.get('version', CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ValidationError(f'Unsupported interval configuration version: {version!r}')

    unknown = set(config) - set(DEFAULTS) - {'version'}
    if unknown:
        raise ValidationError(f'Unknown interval configuration fields: {sorted(unknown)}')

    result: dict[str, Any] = {'version': CONFIG_VERSION}
    for field, default in DEFAULTS.items():
        value = config.get(field, default)
        # bool is a subclass of int, but is never a sensible duration
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f'"{field}" must be a whole number')
        low, high = _LIMITS[field]
        if not low <= value <= high:
            raise ValidationError(f'"{field}" must be between {low} and {high}')
        result[field] = value

    phases = result['sets'] * result['rounds'] * 2 + 3
    if phases > MAX_PHASES:
        raise ValidationError('The interval configuration produces too many phases')

    return result


def validate_interval_config(value: Any) -> None:
    """Django/DRF compatible validator"""
    normalize_config(value)


def build_plan(config: dict[str, Any] | None) -> dict[str, Any]:
    """
    Expand an interval config into an ordered list of timed phases.

    Zero-length phases (e.g. no warmup, no rest after the last round) are omitted.
    Each phase has ``type``, ``duration_s``, ``set`` and ``round`` (1-based, or
    ``None`` for warmup/cooldown) and ``start_s``, the offset from the start.
    """
    cfg = normalize_config(config)
    phases: list[dict[str, Any]] = []
    clock = 0

    def add(kind: str, duration: int, set_nr: int | None = None, round_nr: int | None = None):
        nonlocal clock
        if duration <= 0:
            return
        phases.append(
            {
                'type': kind,
                'duration_s': duration,
                'start_s': clock,
                'set': set_nr,
                'round': round_nr,
            }
        )
        clock += duration

    add(PHASE_WARMUP, cfg['warmup_s'])
    for set_nr in range(1, cfg['sets'] + 1):
        for round_nr in range(1, cfg['rounds'] + 1):
            add(PHASE_WORK, cfg['work_s'], set_nr, round_nr)
            is_last_round = round_nr == cfg['rounds']
            if not is_last_round:
                add(PHASE_REST, cfg['rest_s'], set_nr, round_nr)
        if set_nr != cfg['sets']:
            add(PHASE_SET_REST, cfg['set_rest_s'], set_nr)
    add(PHASE_COOLDOWN, cfg['cooldown_s'])

    return {
        'config': cfg,
        'total_duration_s': clock,
        'total_work_s': cfg['work_s'] * cfg['rounds'] * cfg['sets'],
        'phases': phases,
    }
