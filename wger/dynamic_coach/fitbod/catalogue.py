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
Read exercise facts (muscles, equipment, category) from a local clone of the
openGym exercise catalogue, to tag exercises that wger does not have.

Only the plain-text fields below are read, from ``exercises/*.json``. The
catalogue's media (pictures and animations) is licensed to openGym alone and is
never opened, and neither are the descriptions or instructions. The catalogue is
read from where the user cloned it and is not copied into this repository.

Pure Python, no Django.
"""

# Standard Library
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    # wger
    from wger.dynamic_coach.fitbod.tags import (
        BODYWEIGHT,
        MUSCLES,
        Tags,
    )
except ImportError:  # run as a standalone script, without wger's dependencies installed
    # Standard Library
    import importlib.util

    _spec = importlib.util.spec_from_file_location('fitbod_tags', Path(__file__).with_name('tags.py'))
    _tags = importlib.util.module_from_spec(_spec)
    # the dataclass decorator looks the module up by name
    import sys

    sys.modules['fitbod_tags'] = _tags
    _spec.loader.exec_module(_tags)
    BODYWEIGHT, MUSCLES, Tags = _tags.BODYWEIGHT, _tags.MUSCLES, _tags.Tags


# The only fields that are read from an entry
FIELDS = ('id', 'name', 'bodyPart', 'equipment', 'target', 'secondaryMuscles', 'category', 'hold', 'needs', 'variantOf')

TARGET_TO_MUSCLES = {
    'glutes': ['glutes'], 'delts': ['shoulders'], 'abs': ['abs'], 'quads': ['quads'],
    'pectorals': ['chest'], 'upper back': ['lats'], 'biceps': ['biceps'], 'triceps': ['triceps'],
    'lats': ['lats'], 'obliques': ['obliques'], 'hamstrings': ['hamstrings'], 'calves': ['calves'],
    'spine': ['lower_back'], 'forearms': ['brachialis'], 'abductors': ['glutes'],
    'adductors': ['glutes'], 'levator scapulae': ['traps'], 'traps': ['traps'],
    'hip flexors': ['quads'], 'serratus anterior': ['serratus'],
}
SECONDARY_TO_MUSCLES = {
    'core': ['abs'], 'shoulders': ['shoulders'], 'glutes': ['glutes'], 'hamstrings': ['hamstrings'],
    'triceps': ['triceps'], 'hip flexors': ['quads'], 'quadriceps': ['quads'],
    'forearms': ['brachialis'], 'calves': ['calves'], 'lower back': ['lower_back'],
    'upper back': ['traps'], 'biceps': ['biceps'], 'obliques': ['obliques'], 'rhomboids': ['traps'],
    'trapezius': ['traps'], 'rear deltoids': ['shoulders'], 'lats': ['lats'], 'upper chest': ['chest'],
    'adductors': ['glutes'], 'chest': ['chest'], 'lower abs': ['abs'], 'rotator cuff': ['shoulders'],
    'brachialis': ['brachialis'], 'abdominals': ['abs'], 'soleus': ['soleus'], 'groin': ['glutes'],
}
EQUIPMENT = {
    'body weight': BODYWEIGHT, 'dumbbell': 'Dumbbell', 'cable': 'Cable machine', 'barbell': 'Barbell',
    'olympic barbell': 'Barbell', 'ez barbell': 'SZ-Bar', 'leverage machine': 'Machine',
    'sled machine': 'Machine', 'assisted': 'Machine', 'smith machine': 'Smith machine',
    'band': 'Resistance band', 'resistance band': 'Resistance band', 'kettlebell': 'Kettlebell',
    'suspension trainer': 'TRX', 'stability ball': 'Swiss Ball', 'medicine ball': 'Medicine ball',
    'weight plate': 'Weight plate', 'landmine': 'Landmine', 'bosu ball': 'Balance trainer',
    'trap bar': 'Trap bar', 'rope': 'Rope', 'sandbag': 'Sandbag', 'weighted': BODYWEIGHT,
}
NEEDS = {'bench': 'Bench', 'pull-up bar': 'Pull-up bar'}
BODY_PART_TO_CATEGORY = {
    'chest': 'chest', 'back': 'back', 'shoulders': 'shoulders', 'upper arms': 'arms',
    'lower arms': 'arms', 'upper legs': 'legs', 'lower legs': 'calves', 'waist': 'abs',
    'neck': 'back', 'cardio': 'cardio', 'full body': 'legs',
}


@dataclass
class CatalogueEntry:
    id: str
    name: str
    body_part: str = ''
    equipment: str = ''
    target: str = ''
    secondary: list = field(default_factory=list)
    category: str = ''
    hold: bool = False
    needs: list = field(default_factory=list)


def key(name: str) -> str:
    """Name with word order, case, punctuation and plurals removed"""
    text = re.sub(r'[^a-z0-9 ]', '', name.lower().replace('-', ' ').replace('/', ' '))
    return ' '.join(sorted(_singular(w) for w in text.split()))


def _singular(word: str) -> str:
    if word.endswith(('sses', 'ches', 'shes', 'xes')):
        return word[:-2]
    if word.endswith('s') and len(word) > 3 and not word.endswith('ss'):
        return word[:-1]
    return word


def load(folder) -> dict[str, CatalogueEntry]:
    """
    Entries of a catalogue clone (the folder that holds ``exercises/``), by name key.

    Duplicates (the same exercise drawn on a female figure) collapse to the original.
    """
    entries: dict[str, CatalogueEntry] = {}
    for path in sorted(Path(folder, 'exercises').glob('*.json')):
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not raw.get('name') or raw.get('variantOf'):
            continue
        entry = CatalogueEntry(
            id=str(raw.get('id', path.stem)),
            name=raw['name'],
            body_part=raw.get('bodyPart') or '',
            equipment=raw.get('equipment') or '',
            target=raw.get('target') or '',
            secondary=list(raw.get('secondaryMuscles') or []),
            category=raw.get('category') or '',
            hold=bool(raw.get('hold')),
            needs=list(raw.get('needs') or []),
        )
        entries.setdefault(key(entry.name), entry)
    return entries


def _unique(items):
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def to_tags(entry: CatalogueEntry) -> Tags:
    """Translate a catalogue entry to wger's muscles, category and equipment"""
    primary = TARGET_TO_MUSCLES.get(entry.target, [])
    secondary = [m for s in entry.secondary for m in SECONDARY_TO_MUSCLES.get(s, [])]
    # upper-back pulls also work the traps
    if entry.target == 'upper back':
        secondary.append('traps')
    secondary = [m for m in _unique(secondary) if m not in primary]

    category = BODY_PART_TO_CATEGORY.get(entry.body_part, 'legs')
    if entry.category == 'cardio' or entry.target == 'cardiovascular system':
        category = 'cardio'

    equipment = []
    if entry.equipment:
        equipment.append(EQUIPMENT.get(entry.equipment, entry.equipment.title()))
    for need in entry.needs:
        if need in NEEDS and NEEDS[need] not in equipment:
            equipment.append(NEEDS[need])
    # the catalogue leaves out kit that the name already says
    lowered = entry.name.lower()
    if re.search(r'bench|incline|decline', lowered) and 'Bench' not in equipment:
        equipment.append('Bench')
    if re.search(r'pull.?up|chin.?up|hanging', lowered) and 'Pull-up bar' not in equipment:
        equipment.append('Pull-up bar')
    if not equipment:
        equipment.append(BODYWEIGHT)
    # a bench is implied by these words; a bodyweight entry that needs a bench has both
    if len(equipment) > 1 and BODYWEIGHT in equipment:
        equipment.remove(BODYWEIGHT)

    unknown = [m for m in primary + secondary if m not in MUSCLES]
    assert not unknown, unknown
    return Tags(category, list(primary), secondary, equipment)


def find(entries: dict[str, CatalogueEntry], name: str) -> CatalogueEntry | None:
    """Exact match only (word order ignored); near matches are not trusted"""
    return entries.get(key(name))
