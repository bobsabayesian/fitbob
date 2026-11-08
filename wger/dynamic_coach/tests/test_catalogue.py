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

# Standard Library
import json
import tempfile
from pathlib import Path

# Django
from django.test import SimpleTestCase

# wger
from wger.dynamic_coach.fitbod.catalogue import (
    find,
    key,
    load,
    to_tags,
)


def entry(**kw):
    base = {'id': '1', 'name': 'x', 'bodyPart': 'chest', 'equipment': 'barbell', 'target': 'pectorals',
            'secondaryMuscles': [], 'category': 'strength'}
    base.update(kw)
    return base


class CatalogueTestCase(SimpleTestCase):
    def make(self, entries):
        folder = tempfile.mkdtemp()
        (Path(folder) / 'exercises').mkdir()
        for raw in entries:
            (Path(folder) / 'exercises' / f'{raw["id"]}.json').write_text(json.dumps(raw))
        return folder

    def test_key_ignores_word_order_case_and_plurals(self):
        self.assertEqual(key('Barbell Bench Press'), key('bench presses barbell'))
        self.assertNotEqual(key('Dumbbell Curl'), key('Barbell Curl'))

    def test_loads_and_finds_exact_names_only(self):
        folder = self.make([entry(id='25', name='barbell bench press'), entry(id='26', name='squat jacks')])
        entries = load(folder)
        self.assertEqual(find(entries, 'Barbell Bench Press').id, '25')
        self.assertIsNone(find(entries, 'Back Squat'))

    def test_skips_variants_and_unnamed(self):
        folder = self.make([
            entry(id='1', name='sit up'), entry(id='2', name='sit up', variantOf='1'), {'id': '3'},
        ])
        self.assertEqual([e.id for e in load(folder).values()], ['1'])

    def test_never_reads_the_media_folder_or_text_fields(self):
        folder = self.make([entry(id='1', name='sit up', description='SECRET', instructions=['SECRET'])])
        media = Path(folder, 'media')
        media.mkdir()
        (media / 'still.webp').write_bytes(b'\x00not json')
        entries = load(folder)
        self.assertNotIn('SECRET', repr(entries))

    def test_translates_to_wger_vocabulary(self):
        tags = to_tags(load(self.make([entry(
            id='1', name='barbell bench press', secondaryMuscles=['triceps', 'shoulders', 'core'],
        )]))['barbell bench press'])
        self.assertEqual(tags.category, 'chest')
        self.assertEqual(tags.primary, ['chest'])
        self.assertEqual(tags.secondary, ['triceps', 'shoulders', 'abs'])
        self.assertEqual(tags.equipment, ['Barbell', 'Bench'])  # the bench is implied by the name

    def test_needs_and_bodyweight_and_cardio(self):
        folder = self.make([
            entry(id='1', name='dumbbell fly', equipment='dumbbell', needs=['bench']),
            entry(id='2', name='push up', equipment='body weight'),
            entry(id='3', name='jump rope', equipment='rope', bodyPart='cardio', category='cardio',
                  target='cardiovascular system'),
            entry(id='4', name='pull up', equipment='body weight', bodyPart='back', target='lats'),
        ])
        entries = load(folder)
        self.assertEqual(to_tags(entries[key('dumbbell fly')]).equipment, ['Dumbbell', 'Bench'])
        self.assertEqual(to_tags(entries[key('push up')]).equipment, ['none (bodyweight exercise)'])
        self.assertEqual(to_tags(entries[key('jump rope')]).category, 'cardio')
        self.assertEqual(to_tags(entries[key('pull up')]).equipment, ['Pull-up bar'])

    def test_primary_is_not_repeated_as_secondary(self):
        folder = self.make([entry(id='1', name='row', target='upper back', secondaryMuscles=['lats', 'biceps'])])
        tags = to_tags(load(folder)['row'])
        self.assertEqual(tags.primary, ['lats'])
        self.assertEqual(tags.secondary, ['biceps', 'traps'])

    def test_every_real_value_maps_to_a_wger_muscle(self):
        # the translation tables must only name muscles wger has
        from wger.dynamic_coach.fitbod.catalogue import SECONDARY_TO_MUSCLES, TARGET_TO_MUSCLES
        from wger.dynamic_coach.fitbod.tags import MUSCLES

        for table in (SECONDARY_TO_MUSCLES, TARGET_TO_MUSCLES):
            for muscles in table.values():
                self.assertTrue(set(muscles) <= set(MUSCLES))
