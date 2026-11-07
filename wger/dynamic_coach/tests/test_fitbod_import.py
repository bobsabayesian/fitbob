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
import csv
import tempfile
from decimal import Decimal
from pathlib import Path

# Django
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from io import StringIO

# wger
from wger.core.tests.base_testcase import WgerTestCase
from wger.dynamic_coach.fitbod.importer import (
    ImportError_,
    convert_weight,
    detect_weight_unit,
    read_export,
    read_mapping,
    run_import,
)
from wger.dynamic_coach.fitbod.tags import infer_tags
from wger.dynamic_coach.models import LibraryEntry
from wger.exercises.models import (
    Equipment,
    Exercise,
    Translation,
)
from wger.manager.consts import (
    REP_UNIT_REPETITIONS,
    REP_UNIT_SECONDS,
    WEIGHT_UNIT_LB,
)
from wger.manager.models import (
    WorkoutLog,
    WorkoutSession,
)


HEADER = 'Date,Exercise,Reps,Weight(kg),Duration(s),Distance(m),Incline,Resistance,isWarmup,Note,multiplier\n'
LB = 0.45359237


def line(date, name, reps=10, lb=0.0, dur=0, dist=0, warmup='false', mult='1.0'):
    return f'{date},{name}, {reps},{lb * LB},{dur},{dist},0.0,0.0,{warmup},, {mult}\n'


class TagsTestCase(WgerTestCase):
    def test_common_names(self):
        bench = infer_tags('Barbell Bench Press')
        self.assertEqual((bench.category, bench.primary), ('chest', ['chest']))
        self.assertIn('Barbell', bench.equipment)
        self.assertEqual(infer_tags('Lat Pulldown').primary, ['lats'])
        self.assertEqual(infer_tags('Walking Lunge').category, 'legs')
        self.assertEqual(infer_tags('Cycling').category, 'cardio')
        self.assertEqual(infer_tags('Push Up').equipment, ['none (bodyweight exercise)'])
        self.assertEqual(infer_tags('Handle Band Rear Deltoid Fly').primary, ['shoulders'])
        # isolation work is not a compound lift, dips and close-grip presses are
        self.assertEqual(infer_tags('Cable Rope Tricep Extension').secondary, [])
        self.assertEqual(infer_tags('Close-Grip Bench Press').secondary, ['shoulders', 'chest'])


class UnitsTestCase(WgerTestCase):
    def test_detects_pounds_from_kg_floats(self):
        sets = read_export_from(HEADER + ''.join(line('2026-09-26 10:00:00 +0000', 'X', lb=w) for w in (45, 135, 225, 12.5)))
        self.assertEqual(detect_weight_unit(sets), 'lb')

    def test_detects_kilograms(self):
        text = HEADER + ''.join(
            f'2026-09-26 10:00:00 +0000,X, 10,{w},0,0,0,0,false,, 1.0\n' for w in (20.0, 21.3, 33.7, 41.1)
        )
        self.assertEqual(detect_weight_unit(read_export_from(text)), 'kg')

    def test_converts_back_to_clean_pounds(self):
        self.assertEqual(convert_weight(280 * LB, 'lb'), Decimal('280.0'))
        self.assertEqual(convert_weight(12.5 * LB, 'lb'), Decimal('12.5'))
        self.assertIsNone(convert_weight(0, 'lb'))
        self.assertEqual(convert_weight(20.0, 'kg'), Decimal('20.0'))


def read_export_from(text):
    with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False) as handle:
        handle.write(text)
    return read_export(handle.name)


class ImportTestCase(WgerTestCase):
    def setUp(self):
        super().setUp()
        WorkoutLog.objects.all().delete()
        WorkoutSession.objects.all().delete()
        self.user = User.objects.get(username='test')
        self.existing = Exercise.objects.get(pk=1)
        self.sets = read_export_from(
            HEADER
            + line('2026-09-26 22:53:07 +0000', 'Bench', 8, 135)
            + line('2026-09-26 22:53:07 +0000', 'Bench', 8, 145)
            + line('2026-09-26 22:53:07 +0000', 'Brand New Curl', 10, 25, mult='2.0')
            + line('2026-09-26 22:53:07 +0000', 'Plank', 0, 0, dur=60)
            + line('2026-09-26 22:53:07 +0000', 'Cycling', 0, 0, dur=1800, dist=9000)
            + line('2026-09-26 22:53:07 +0000', 'Bench', 12, 95, warmup='true')
            + line('2026-09-26 22:53:07 +0000', 'Dropped', 10, 10)
            + line('2026-09-28 18:00:00 +0000', 'Bench', 8, 155)
        )
        self.mapping_text = (
            'action,fitbod_name,sets,wger_id,wger_name,confidence,alternatives,category,primary,secondary,equipment\n'
            f'map,Bench,3,{self.existing.pk},x,exact,,,,,\n'
            'create,Brand New Curl,1,,,none,,arms,biceps,,Dumbbell;Preacher pad\n'
            'create,Plank,1,,,none,,,,,\n'
            'create,Cycling,1,,,none,,,,,\n'
            'skip,Dropped,1,,,none,,,,,\n'
        )
        self.mapping = read_mapping_from(self.mapping_text)

    def test_imports_sessions_and_sets(self):
        report = run_import(self.user, self.sets, self.mapping)
        self.assertEqual(report['weight_unit'], 'lb')
        self.assertEqual(report['sessions'], 2)
        self.assertEqual(report['sets_imported'], 5)
        self.assertEqual(
            report['sets_skipped'],
            {'warm-up': 1, 'distance (cardio)': 1, 'skipped by mapping': 1},
        )
        self.assertEqual(WorkoutSession.objects.filter(user=self.user).count(), 2)
        bench = WorkoutLog.objects.filter(exercise=self.existing).order_by('date')
        self.assertEqual([(l.repetitions, l.weight) for l in bench], [(8, Decimal('135')), (8, Decimal('145')), (8, Decimal('155'))])
        self.assertTrue(all(l.weight_unit_id == WEIGHT_UNIT_LB for l in bench))
        self.assertTrue(all(l.session_id for l in bench))

    def test_timed_sets_use_seconds(self):
        run_import(self.user, self.sets, self.mapping)
        plank = WorkoutLog.objects.get(exercise=Translation.objects.get(name='Plank').exercise)
        self.assertEqual((plank.repetitions, plank.repetitions_unit_id), (60, REP_UNIT_SECONDS))
        self.assertIsNone(plank.weight)

    def test_dumbbell_weight_is_not_multiplied(self):
        run_import(self.user, self.sets, self.mapping)
        curl = WorkoutLog.objects.get(exercise=Translation.objects.get(name='Brand New Curl').exercise)
        self.assertEqual(curl.weight, Decimal('25'))
        self.assertEqual(curl.repetitions_unit_id, REP_UNIT_REPETITIONS)

    def test_creates_tagged_exercises_and_equipment(self):
        report = run_import(self.user, self.sets, self.mapping)
        self.assertEqual(report['exercises_created'], 3)  # curl, plank, cycling
        curl = Translation.objects.get(name='Brand New Curl').exercise
        self.assertEqual([m.pk for m in curl.muscles.all()], [1])
        self.assertEqual({e.name for e in curl.equipment.all()}, {'Dumbbell', 'Preacher pad'})
        self.assertTrue(Equipment.objects.filter(name='Preacher pad').exists())

    def test_builds_the_library_from_what_was_imported(self):
        run_import(self.user, self.sets, self.mapping)
        names = {e.exercise.get_translation().name for e in LibraryEntry.objects.filter(user=self.user)}
        self.assertIn('Brand New Curl', names)
        self.assertIn('Plank', names)
        self.assertNotIn('Cycling', names)  # only had distance rows, nothing imported
        self.assertEqual(LibraryEntry.objects.filter(user=self.user, exercise=self.existing).count(), 1)

    def test_dry_run_saves_nothing(self):
        exercises = Exercise.objects.count()
        report = run_import(self.user, self.sets, self.mapping, dry_run=True)
        self.assertEqual(report['sets_imported'], 5)
        self.assertEqual(WorkoutLog.objects.count(), 0)
        self.assertEqual(Exercise.objects.count(), exercises)

    def test_second_import_needs_replace(self):
        run_import(self.user, self.sets, self.mapping)
        with self.assertRaises(ImportError_):
            run_import(self.user, self.sets, self.mapping)
        run_import(self.user, self.sets, self.mapping, replace=True)
        self.assertEqual(WorkoutSession.objects.filter(user=self.user).count(), 2)
        self.assertEqual(WorkoutLog.objects.filter(user=self.user).count(), 5)
        # no duplicated exercises
        self.assertEqual(Translation.objects.filter(name='Brand New Curl').count(), 1)

    def test_rejects_unmapped_names_and_bad_ids(self):
        del self.mapping['Plank']
        with self.assertRaisesMessage(ImportError_, 'no mapping row'):
            run_import(self.user, self.sets, self.mapping)
        self.mapping = read_mapping_from(self.mapping_text.replace(f'map,Bench,3,{self.existing.pk}', 'map,Bench,3,999999'))
        with self.assertRaisesMessage(ImportError_, 'do not exist'):
            run_import(self.user, self.sets, self.mapping)

    def test_mapping_validation(self):
        with self.assertRaises(ImportError_):
            read_mapping_from('action,fitbod_name,wger_id\nmaybe,Bench,\n')
        with self.assertRaises(ImportError_):
            read_mapping_from('action,fitbod_name,wger_id\nmap,Bench,\n')

    def test_does_not_touch_other_users(self):
        other = User.objects.exclude(pk=self.user.pk).first()
        run_import(self.user, self.sets, self.mapping)
        self.assertEqual(WorkoutLog.objects.filter(user=other).count(), 0)

    def test_management_command(self):
        with tempfile.TemporaryDirectory() as folder:
            export, mapping = Path(folder, 'e.csv'), Path(folder, 'm.csv')
            export.write_text(HEADER + line('2026-09-26 22:53:07 +0000', 'Bench', 8, 135))
            mapping.write_text(self.mapping_text)
            out = StringIO()
            call_command('import_fitbod', str(export), str(mapping), '--user', 'test', '--dry-run', stdout=out)
            self.assertIn('sets_imported: 1', out.getvalue())
            self.assertIn('Dry run', out.getvalue())
            with self.assertRaises(CommandError):
                call_command('import_fitbod', str(export), str(mapping), '--user', 'nobody')


def read_mapping_from(text):
    with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False) as handle:
        handle.write(text)
    return read_mapping(handle.name)
