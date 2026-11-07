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
import random
from datetime import timedelta
from decimal import Decimal

# Django
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

# wger
from wger.core.models import Language
from wger.core.tests.base_testcase import WgerTestCase
from wger.dynamic_coach.models import (
    EquipmentProfile,
    UserGeneratorPreferences,
)
from wger.dynamic_coach.services.session_builder import (
    GeneratorError,
    build_session,
    exercise_count,
    save_routine,
)
from wger.exercises.models import (
    Equipment,
    Exercise,
    ExerciseCategory,
    Muscle,
    Translation,
)
from wger.manager.consts import (
    REP_UNIT_REPETITIONS,
    WEIGHT_UNIT_KG,
)
from wger.manager.models import (
    Routine,
    SlotEntry,
    WorkoutLog,
)


CHEST, DELT, TRICEPS, LATS, BICEPS = 4, 2, 5, 12, 1


class GeneratorBase(WgerTestCase):
    """A small controlled exercise pool: push and pull, barbell/dumbbell/bodyweight"""

    def setUp(self):
        super().setUp()
        Exercise.objects.all().delete()
        Equipment.objects.all().delete()
        for pk in (CHEST, DELT, TRICEPS, LATS, BICEPS):
            Muscle.objects.get_or_create(pk=pk, defaults={'name': f'muscle {pk}'})

        self.user = User.objects.get(username='test')
        self.language = Language.objects.first()
        self.category = ExerciseCategory.objects.first()
        self.barbell = Equipment.objects.create(name='Barbell')
        self.dumbbell = Equipment.objects.create(name='Dumbbell')
        self.none = Equipment.objects.create(name='none (bodyweight exercise)')

        self.bench = self.make('Bench press', [CHEST], [DELT, TRICEPS], [self.barbell])
        self.incline = self.make('Incline DB press', [CHEST], [DELT], [self.dumbbell])
        self.pushup = self.make('Push-up', [CHEST], [TRICEPS, DELT], [self.none])
        self.raise_ = self.make('Lateral raise', [DELT], [], [self.dumbbell])
        self.pushdown = self.make('Dip', [TRICEPS], [], [])
        self.row = self.make('Barbell row', [LATS], [BICEPS], [self.barbell])
        self.curl = self.make('Curl', [BICEPS], [], [self.dumbbell])

        self.gym = EquipmentProfile.objects.create(name='Gym', user=self.user)
        self.gym.equipment.set([self.barbell, self.dumbbell])
        self.rng = random.Random(1)

    def make(self, name, primary, secondary, equipment):
        exercise = Exercise.objects.create(category=self.category)
        exercise.muscles.set(primary)
        exercise.muscles_secondary.set(secondary)
        exercise.equipment.set(equipment)
        Translation.objects.create(exercise=exercise, language=self.language, name=name)
        return exercise

    def log_sets(self, exercise, n, weight, reps, hours_ago=0):
        routine = Routine.objects.filter(user=self.user).first()
        for _ in range(n):
            WorkoutLog.objects.create(
                user=self.user,
                routine=routine,
                exercise=exercise,
                weight=weight,
                repetitions=reps,
                weight_unit_id=WEIGHT_UNIT_KG,
                repetitions_unit_id=REP_UNIT_REPETITIONS,
                date=timezone.now() - timedelta(hours=hours_ago),
            )

    def names(self, planned):
        return [p.name for p in planned]


class SessionBuilderTestCase(GeneratorBase):
    def test_exercise_count(self):
        self.assertEqual([exercise_count(m) for m in (10, 30, 45, 60, 90, 200)], [3, 3, 4, 6, 7, 7])

    def test_push_only_chest_shoulder_triceps(self):
        split, planned = build_session(self.user, 60, 'push', rng=self.rng)
        self.assertEqual(split, 'push')
        self.assertTrue(set(self.names(planned)) <= {
            'Bench press', 'Incline DB press', 'Push-up', 'Lateral raise', 'Dip'})
        self.assertEqual([p.slot for p in planned], list(range(1, len(planned) + 1)))

    def test_compound_before_isolation(self):
        _, planned = build_session(self.user, 60, 'push', rng=self.rng)
        # the two 3-muscle compounds take the heavy slots, isolation never leads
        self.assertEqual(set(self.names(planned)[:2]), {'Bench press', 'Push-up'})

    def test_tiers(self):
        _, planned = build_session(self.user, 60, 'push', rng=self.rng)
        self.assertEqual([p.tier for p in planned], [1, 1, 2, 2, 3][: len(planned)])
        self.assertEqual(planned[0].sets, 4)

    def test_equipment_filter_and_bodyweight_always_allowed(self):
        self.gym.equipment.set([self.dumbbell])
        _, planned = build_session(self.user, 60, 'push', rng=self.rng)
        names = self.names(planned)
        self.assertNotIn('Bench press', names)
        self.assertIn('Push-up', names)

    def test_zero_equipment_profile_is_bodyweight_only(self):
        self.gym.equipment.clear()
        _, planned = build_session(self.user, 60, 'push', rng=self.rng)
        self.assertEqual(set(self.names(planned)), {'Push-up', 'Dip'})

    def test_no_match_raises(self):
        self.gym.equipment.clear()
        with self.assertRaises(GeneratorError):
            build_session(self.user, 45, 'legs', rng=self.rng)

    def test_exclusions(self):
        prefs = UserGeneratorPreferences.objects.create(user=self.user)
        prefs.excluded_exercises.set([self.bench])
        prefs.excluded_muscles.set([TRICEPS])
        _, planned = build_session(self.user, 60, 'push', rng=self.rng)
        names = self.names(planned)
        self.assertNotIn('Bench press', names)
        self.assertNotIn('Push-up', names)  # triceps is secondary
        self.assertNotIn('Dip', names)

    def test_no_profile_raises(self):
        EquipmentProfile.objects.all().delete()
        with self.assertRaises(GeneratorError):
            build_session(self.user, 45, 'push')

    def test_foreign_profile_raises(self):
        other = EquipmentProfile.objects.create(
            name='Other', user=User.objects.exclude(pk=self.user.pk).first()
        )
        with self.assertRaises(GeneratorError):
            build_session(self.user, 45, 'push', equipment_profile_id=other.pk)

    def test_auto_picks_freshest_split(self):
        # Fatigue chest heavily: pull should win over push
        self.log_sets(self.bench, 12, 60, 8)
        split, planned = build_session(self.user, 45, 'auto', rng=self.rng)
        self.assertEqual(split, 'pull')
        self.assertTrue(set(self.names(planned)) <= {'Barbell row', 'Curl'})

    def test_auto_skips_splits_without_exercises(self):
        # Legs is freshest but has no exercises, so it must fall through
        self.log_sets(self.bench, 12, 60, 8)
        self.log_sets(self.row, 12, 60, 8)
        split, _ = build_session(self.user, 45, 'auto', rng=self.rng)
        self.assertIn(split, ('push', 'pull'))

    def test_cold_start_has_no_weight(self):
        _, planned = build_session(self.user, 45, 'push', rng=self.rng)
        for p in planned:
            self.assertIsNone(p.weight)
            self.assertEqual(p.target_reps, 10)

    def test_weight_prescribed_from_history(self):
        self.log_sets(self.bench, 3, 80, 8, hours_ago=24 * 6)
        _, planned = build_session(self.user, 45, 'push', rng=self.rng)
        bench = next(p for p in planned if p.name == 'Bench press')
        self.assertEqual(bench.weight, Decimal('80.0'))
        self.assertEqual(bench.target_reps, 8)
        self.assertAlmostEqual(float(bench.e1rm), 101.3, places=1)

    def test_rounding_preference(self):
        UserGeneratorPreferences.objects.create(
            user=self.user, rounding_increment_kg=Decimal('5')
        )
        self.log_sets(self.bench, 1, 82, 8, hours_ago=24 * 6)
        _, planned = build_session(self.user, 45, 'push', rng=self.rng)
        bench = next(p for p in planned if p.name == 'Bench press')
        self.assertEqual(bench.weight % 5, 0)


class SaveRoutineTestCase(GeneratorBase):
    def test_persists_native_wger_objects(self):
        self.log_sets(self.bench, 3, 80, 8, hours_ago=24 * 6)
        split, planned = build_session(self.user, 45, 'push', rng=self.rng)
        routine, day = save_routine(self.user, planned, split, 'Dynamic Push - a very long name')

        self.assertEqual(len(routine.name), 25)
        self.assertEqual(day.routine_id, routine.pk)
        entries = SlotEntry.objects.filter(slot__day=day).order_by('slot__order')
        self.assertEqual(entries.count(), len(planned))
        self.assertEqual(entries[0].exercise_id, planned[0].exercise.pk)
        self.assertEqual(entries[0].setsconfig_set.get().value, planned[0].sets)
        self.assertEqual(entries[0].repetitionsconfig_set.get().value, planned[0].target_reps)
        bench = next(e for e in entries if e.exercise_id == self.bench.pk)
        self.assertEqual(bench.weightconfig_set.get().value, Decimal('80.0'))

    def test_cold_start_saves_no_weight_config(self):
        split, planned = build_session(self.user, 45, 'push', rng=self.rng)
        _, day = save_routine(self.user, planned, split, 'x')
        self.assertFalse(
            SlotEntry.objects.filter(slot__day=day, weightconfig__isnull=False).exists()
        )


class GeneratorApiTestCase(GeneratorBase):
    def setUp(self):
        super().setUp()
        self.client.login(username='test', password='testtest')
        self.url = reverse('generate-workout')

    def test_requires_auth(self):
        self.client.logout()
        self.assertIn(self.client.post(self.url, {}).status_code, (401, 403))

    def test_generate_without_saving(self):
        response = self.client.post(
            self.url, {'duration_min': 45, 'split': 'push'}, content_type='application/json'
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIsNone(body['routine_id'])
        self.assertEqual(body['split'], 'push')
        self.assertEqual(len(body['exercises']), 4)
        self.assertEqual(
            set(body['exercises'][0]),
            {'exercise_id', 'name', 'slot', 'sets', 'target_reps', 'prescribed_weight_kg', 'e1rm_basis_kg'},
        )

    def test_generate_and_save(self):
        before = Routine.objects.filter(user=self.user).count()
        response = self.client.post(
            self.url,
            {'split': 'push', 'save_as_routine': True, 'routine_name': 'Dynamic Push'},
            content_type='application/json',
        )
        body = response.json()
        self.assertEqual(Routine.objects.filter(user=self.user).count(), before + 1)
        self.assertEqual(Routine.objects.get(pk=body['routine_id']).name, 'Dynamic Push')
        self.assertIsNotNone(body['day_id'])

    def test_validation(self):
        for payload in ({'split': 'arms'}, {'duration_min': 3}, {'routine_name': 'x' * 30}):
            response = self.client.post(self.url, payload, content_type='application/json')
            self.assertEqual(response.status_code, 400, payload)

    def test_generator_error_is_400(self):
        EquipmentProfile.objects.all().delete()
        response = self.client.post(self.url, {'split': 'push'}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('equipment profile', str(response.json()).lower())

    def test_other_users_profile_rejected(self):
        other = EquipmentProfile.objects.create(
            name='Other', user=User.objects.exclude(pk=self.user.pk).first()
        )
        response = self.client.post(
            self.url, {'equipment_profile_id': other.pk}, content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)


class ProfileAndPreferencesApiTestCase(GeneratorBase):
    def setUp(self):
        super().setUp()
        self.client.login(username='test', password='testtest')

    def test_profile_crud_is_scoped_to_user(self):
        other = EquipmentProfile.objects.create(
            name='Other', user=User.objects.exclude(pk=self.user.pk).first()
        )
        listing = self.client.get(reverse('equipment-profile-list')).json()
        results = listing['results'] if isinstance(listing, dict) else listing
        self.assertEqual([p['name'] for p in results], ['Gym'])
        self.assertEqual(
            self.client.get(reverse('equipment-profile-detail', args=[other.pk])).status_code, 404
        )

        created = self.client.post(
            reverse('equipment-profile-list'),
            {'name': 'Hotel', 'equipment': [self.dumbbell.pk]},
            content_type='application/json',
        )
        self.assertEqual(created.status_code, 201)
        duplicate = self.client.post(
            reverse('equipment-profile-list'), {'name': 'Hotel'}, content_type='application/json'
        )
        self.assertEqual(duplicate.status_code, 400)

    def test_preferences_defaults_and_update(self):
        url = reverse('generator-preferences')
        body = self.client.get(url).json()
        self.assertEqual(body['default_duration_min'], 45)
        self.assertEqual(body['preferred_split'], 'AUTO')

        response = self.client.patch(
            url,
            {'default_duration_min': 60, 'default_equipment_profile': self.gym.pk,
             'excluded_muscles': [BICEPS]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        prefs = UserGeneratorPreferences.objects.get(user=self.user)
        self.assertEqual(prefs.default_duration_min, 60)
        self.assertEqual(list(prefs.excluded_muscles.values_list('pk', flat=True)), [BICEPS])

    def test_preferences_reject_foreign_profile_and_bad_duration(self):
        other = EquipmentProfile.objects.create(
            name='Other', user=User.objects.exclude(pk=self.user.pk).first()
        )
        url = reverse('generator-preferences')
        for payload in ({'default_equipment_profile': other.pk}, {'default_duration_min': 1000}):
            response = self.client.patch(url, payload, content_type='application/json')
            self.assertEqual(response.status_code, 400, payload)
