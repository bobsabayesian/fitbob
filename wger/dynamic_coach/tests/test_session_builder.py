# Standard Library
from decimal import Decimal

# wger
from wger.dynamic_coach.models import (
    EquipmentProfile,
    UserGeneratorPreferences,
)
from wger.dynamic_coach.services import progression, session_builder as sb
from wger.dynamic_coach.tests.base import CoachTestCase
from wger.manager.models import (
    Day,
    Routine,
    Slot,
    SlotEntry,
)
from wger.manager.models.repetitions_config import RepetitionsConfig
from wger.manager.models.sets_config import SetsConfig
from wger.manager.models.weight_config import WeightConfig


class ProgressionTestCase(CoachTestCase):
    def test_round_trip(self):
        e1rm = progression.epley_e1rm(80, 8)
        self.assertAlmostEqual(progression.weight_for_reps(e1rm, 8), 80)

    def test_rounding(self):
        self.assertEqual(progression.round_to_increment(81.3, Decimal('2.5')), Decimal('82.5'))
        self.assertEqual(progression.round_to_increment(80.9, Decimal('2.5')), Decimal('80.0'))
        self.assertEqual(progression.round_to_increment(21.3, Decimal('1.25')), Decimal('21.25'))

    def test_cold_start(self):
        e = self.make_exercise('X', ['Pectoralis major'])
        rx = progression.prescribe(self.user, e, 8)
        self.assertIsNone(rx['weight'])
        self.assertEqual(rx['reps'], 10)

    def test_best_estimate_wins_and_old_history_ignored(self):
        e = self.make_exercise('X', ['Pectoralis major'])
        self.log_set(e, 100, 1)  # e1rm 103.3
        self.log_set(e, 90, 8)  # e1rm 114
        self.log_set(e, 200, 5, self.hours_ago(24 * 100))  # too old
        self.assertAlmostEqual(progression.best_e1rm(self.user, e), 90 * (1 + 8 / 30))
        self.assertEqual(progression.prescribe(self.user, e, 8)['weight'], Decimal('90.0'))


class SessionBuilderTestCase(CoachTestCase):
    def setUp(self):
        super().setUp()
        self.make_push_catalog()

    def test_exercise_count(self):
        self.assertEqual(
            [sb.num_exercises(m) for m in (20, 30, 45, 60, 90, 120)], [3, 3, 4, 6, 7, 7]
        )

    def test_tiers(self):
        self.assertEqual([sb.tier_for_slot(i) for i in range(1, 7)], [1, 1, 2, 2, 3, 3])

    def test_push_session_structure(self):
        result = sb.build_session(self.user, 60, split='push', save_as_routine=False)
        names = [x['name'] for x in result['exercises']]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(result['exercises'][0]['sets'], 4)
        # compounds fill the heavy slots, the isolation lifts come last
        self.assertIn(names[0], {'Bench Press', 'Overhead Press', 'Push-up'})
        self.assertIn(names[-1], {'Dumbbell Fly', 'Pushdown'})
        self.assertIsNone(result['routine_id'])

    def test_only_target_muscles(self):
        pull = self.make_exercise('Row', ['Latissimus dorsi'], ['Biceps brachii'], [self.barbell])
        result = sb.build_session(self.user, 45, split='push', save_as_routine=False)
        self.assertNotIn(pull.pk, [x['exercise_id'] for x in result['exercises']])

    def test_equipment_subset(self):
        profile = EquipmentProfile.objects.create(user=self.user, name='Hotel')
        profile.equipment.set([self.dumbbell])
        result = sb.build_session(
            self.user, 60, equipment_profile_id=profile.pk, split='push', save_as_routine=False
        )
        ids = {x['exercise_id'] for x in result['exercises']}
        self.assertEqual(ids, {self.fly.pk, self.pushdown.pk})

    def test_zero_equipment_is_bodyweight_only(self):
        profile = EquipmentProfile.objects.create(user=self.user, name='Nothing')
        result = sb.build_session(
            self.user, 60, equipment_profile_id=profile.pk, split='push', save_as_routine=False
        )
        ids = {x['exercise_id'] for x in result['exercises']}
        self.assertEqual(ids, {self.pushdown.pk})  # no equipment listed at all

    def test_nothing_matches(self):
        profile = EquipmentProfile.objects.create(user=self.user, name='Nothing')
        self.pushdown.delete()
        with self.assertRaises(sb.GenerationError):
            sb.build_session(self.user, 45, equipment_profile_id=profile.pk, split='push')

    def test_foreign_profile_rejected(self):
        profile = EquipmentProfile.objects.create(user=self.other, name='Theirs')
        with self.assertRaises(sb.GenerationError):
            sb.build_session(self.user, 45, equipment_profile_id=profile.pk, split='push')

    def test_exclusions(self):
        prefs = UserGeneratorPreferences.objects.create(user=self.user)
        prefs.excluded_exercises.add(self.bench)
        prefs.excluded_muscles.add(self.muscles['Trapezius'])  # injured: kills overhead press
        result = sb.build_session(self.user, 90, split='push', save_as_routine=False)
        ids = {x['exercise_id'] for x in result['exercises']}
        self.assertFalse(ids & {self.bench.pk, self.ohp.pk})

    def test_auto_picks_freshest_group(self):
        row = self.make_exercise('Row', ['Latissimus dorsi'], ['Biceps brachii'], [self.barbell])
        for _ in range(12):
            self.log_set(self.bench, 80, 8)
        result = sb.build_session(self.user, 45, split='auto', save_as_routine=False)
        self.assertNotEqual(result['split'], 'push')
        self.assertIn(row.pk, [x['exercise_id'] for x in result['exercises']])

    def test_weight_prescribed_from_history(self):
        self.log_set(self.bench, 80, 8, self.hours_ago(100))
        result = sb.build_session(self.user, 60, split='push', save_as_routine=False)
        bench = next(x for x in result['exercises'] if x['exercise_id'] == self.bench.pk)
        if bench['slot'] <= 2:
            self.assertEqual(bench['prescribed_weight_kg'], Decimal('80.0'))
            self.assertEqual(bench['e1rm_basis_kg'], 101.3)
        fly = next(x for x in result['exercises'] if x['exercise_id'] == self.fly.pk)
        self.assertIsNone(fly['prescribed_weight_kg'])

    def test_persists_native_wger_rows(self):
        self.log_set(self.bench, 80, 8, self.hours_ago(100))
        result = sb.build_session(
            self.user, 45, split='push', save_as_routine=True, routine_name='Dynamic Push'
        )
        routine = Routine.objects.get(pk=result['routine_id'])
        self.assertEqual((routine.user, routine.name), (self.user, 'Dynamic Push'))
        day = Day.objects.get(pk=result['day_id'])
        self.assertEqual(day.routine, routine)
        self.assertEqual(Slot.objects.filter(day=day).count(), len(result['exercises']))
        for item in result['exercises']:
            entry = SlotEntry.objects.get(slot__day=day, slot__order=item['slot'])
            self.assertEqual(entry.exercise_id, item['exercise_id'])
            self.assertEqual(SetsConfig.objects.get(slot_entry=entry).value, item['sets'])
            self.assertEqual(
                RepetitionsConfig.objects.get(slot_entry=entry).value, item['target_reps']
            )
            weights = WeightConfig.objects.filter(slot_entry=entry)
            if item['prescribed_weight_kg'] is None:
                self.assertFalse(weights.exists())
            else:
                self.assertEqual(weights.get().value, item['prescribed_weight_kg'])

    def test_endpoint(self):
        resp = self.client.post(
            '/api/v2/coach/generate-workout/',
            {'duration_min': 45, 'split': 'push', 'routine_name': 'Push'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertTrue(Routine.objects.filter(pk=body['routine_id']).exists())
        self.assertEqual(len(body['exercises']), 4)

    def test_endpoint_validation_and_no_match(self):
        resp = self.client.post(
            '/api/v2/coach/generate-workout/', {'split': 'bogus'}, format='json'
        )
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post('/api/v2/coach/generate-workout/', {'split': 'legs'}, format='json')
        self.assertEqual(resp.status_code, 400)
