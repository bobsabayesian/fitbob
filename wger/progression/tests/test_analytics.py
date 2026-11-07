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
from datetime import timedelta
from decimal import Decimal

# Django
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

# wger
from wger.core.tests.base_testcase import WgerTestCase
from wger.manager.consts import (
    REP_UNIT_REPETITIONS,
    WEIGHT_UNIT_KG,
)
from wger.manager.models import (
    Routine,
    WorkoutLog,
)
from wger.progression.analytics import (
    SetData,
    analyze,
    suggest_next,
)


NOW = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)


def sets(*sessions):
    """sessions: [(days_ago, [(weight, reps), ...]), ...]"""
    return [
        SetData(NOW - timedelta(days=days), Decimal(w), r)
        for days, rows in sessions
        for w, r in rows
    ]


class SuggestionTestCase(WgerTestCase):
    def test_cold_start(self):
        s = suggest_next([])
        self.assertIsNone(s.weight)
        self.assertEqual(s.reps, 8)

    def test_add_a_rep(self):
        s = suggest_next(sets((3, [(60, 10), (60, 9), (60, 9)])))
        self.assertEqual((s.weight, s.reps), (Decimal(60), 10))

    def test_add_weight_when_range_topped(self):
        s = suggest_next(sets((3, [(60, 12), (60, 12), (60, 12)])))
        self.assertEqual((s.weight, s.reps), (Decimal('62.5'), 8))

    def test_missing_sets_do_not_qualify(self):
        s = suggest_next(sets((3, [(60, 12), (60, 12)])))
        self.assertEqual(s.weight, Decimal(60))

    def test_never_exceeds_max_reps(self):
        s = suggest_next(sets((3, [(60, 12), (60, 12), (60, 11)])))
        self.assertEqual(s.reps, 12)

    def test_uses_heaviest_weight_of_last_session(self):
        s = suggest_next(sets((3, [(50, 12), (60, 8), (60, 8), (60, 8)])))
        self.assertEqual((s.weight, s.reps), (Decimal(60), 9))

    def test_only_last_session_counts(self):
        s = suggest_next(sets((10, [(60, 12)] * 3), (3, [(60, 8)] * 3)))
        self.assertEqual(s.weight, Decimal(60))


class AnalyzeTestCase(WgerTestCase):
    def test_empty(self):
        result = analyze([])
        self.assertEqual(result['sessions'], [])
        self.assertIsNone(result['best_e1rm'])
        self.assertFalse(result['stalled'])

    def test_sessions_and_prs(self):
        result = analyze(sets((20, [(60, 8)]), (13, [(65, 8), (50, 10)]), (6, [(60, 8)])))
        self.assertEqual(len(result['sessions']), 3)
        self.assertEqual(result['sessions'][1]['sets'], 2)
        self.assertEqual(result['sessions'][1]['top_weight'], Decimal(65))
        self.assertEqual(len(result['personal_records']), 2)
        self.assertEqual(result['best_e1rm'], round(Decimal(65) * (1 + Decimal(8) / 30), 1))

    def test_stall_detection(self):
        history = sets((30, [(70, 8)]), (24, [(60, 8)]), (18, [(60, 8)]), (12, [(60, 8)]), (6, [(60, 8)]))
        self.assertTrue(analyze(history)['stalled'])
        self.assertFalse(analyze(history, stall_sessions=5)['stalled'])

    def test_new_pr_clears_stall(self):
        history = sets((30, [(60, 8)]), (24, [(60, 8)]), (6, [(70, 8)]))
        self.assertFalse(analyze(history)['stalled'])


class ProgressApiTestCase(WgerTestCase):
    def setUp(self):
        super().setUp()
        WorkoutLog.objects.all().delete()
        mine = User.objects.get(username='test').pk
        other = User.objects.exclude(pk=mine).first().pk
        for user_id, weight in ((mine, 60), (other, 200)):
            routine = Routine.objects.filter(user_id=user_id).first()
            for _ in range(3):
                WorkoutLog.objects.create(
                    user_id=user_id,
                    routine=routine,
                    exercise_id=1,
                    weight=weight,
                    repetitions=12,
                    weight_unit_id=WEIGHT_UNIT_KG,
                    repetitions_unit_id=REP_UNIT_REPETITIONS,
                    date=timezone.now() - timedelta(days=2),
                )

    def test_requires_auth(self):
        response = self.client.get(reverse('progress', kwargs={'exercise_id': 1}))
        self.assertIn(response.status_code, (401, 403))

    def test_progress_only_own_logs(self):
        self.client.login(username='test', password='testtest')
        body = self.client.get(reverse('progress', kwargs={'exercise_id': 1})).json()
        self.assertEqual(WorkoutLog.objects.count(), 6)
        self.assertEqual(len(body['sessions']), 1)
        self.assertEqual(body['sessions'][0]['sets'], 3)
        self.assertEqual(float(body['sessions'][0]['top_weight']), 60)

    def test_suggestion(self):
        self.client.login(username='test', password='testtest')
        url = reverse('progress-suggestion', kwargs={'exercise_id': 1})
        body = self.client.get(url).json()
        self.assertEqual(float(body['weight_kg']), 62.5)
        self.assertEqual(body['reps'], 8)

    def test_validation_and_404(self):
        self.client.login(username='test', password='testtest')
        url = reverse('progress-suggestion', kwargs={'exercise_id': 1})
        self.assertEqual(self.client.get(url, {'min_reps': 'x'}).status_code, 400)
        self.assertEqual(self.client.get(url, {'min_reps': 10, 'max_reps': 5}).status_code, 400)
        self.assertEqual(self.client.get(url, {'increment': '-1'}).status_code, 400)
        missing = reverse('progress', kwargs={'exercise_id': 999999})
        self.assertEqual(self.client.get(missing).status_code, 404)
