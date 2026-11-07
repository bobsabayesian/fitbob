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

# Django
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

# wger
from wger.core.tests.base_testcase import WgerTestCase
from wger.dynamic_coach.services.recovery_engine import (
    FatigueEvent,
    freshness,
    muscle_freshness,
    status_for,
)
from wger.exercises.models import (
    Exercise,
    Muscle,
)
from wger.manager.models import WorkoutLog


class FreshnessMathTestCase(WgerTestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()

    def events(self, n, hours_ago=0, **kw):
        return [FatigueEvent(1, self.now - timedelta(hours=hours_ago), **kw)] * n

    def test_no_events_is_fully_fresh(self):
        self.assertEqual(freshness([], self.now), 1.0)

    def test_capacity_sets_exhaust_the_muscle(self):
        self.assertAlmostEqual(freshness(self.events(12), self.now), 0.0)

    def test_half_life(self):
        """Exhausted muscle is 50% fresh after 36h, 75% after 72h"""
        self.assertAlmostEqual(freshness(self.events(12, 36), self.now), 0.5, places=3)
        self.assertAlmostEqual(freshness(self.events(12, 72), self.now), 0.75, places=3)

    def test_clamped_at_zero(self):
        self.assertEqual(freshness(self.events(30), self.now), 0.0)

    def test_secondary_counts_less(self):
        primary = freshness(self.events(6), self.now)
        secondary = freshness(self.events(6, factor=0.4), self.now)
        self.assertLess(primary, secondary)
        self.assertAlmostEqual(secondary, 1 - 0.4 * 6 / 12)

    def test_events_outside_window_ignored(self):
        self.assertEqual(freshness(self.events(12, 15 * 24), self.now), 1.0)

    def test_synthetic_fractional_sets(self):
        self.assertAlmostEqual(freshness(self.events(1, sets=6.0), self.now), 0.5)

    def test_status(self):
        self.assertEqual(status_for(10), 'fatigued')
        self.assertEqual(status_for(50), 'recovering')
        self.assertEqual(status_for(90), 'ready')


class FreshnessFromLogsTestCase(WgerTestCase):
    def setUp(self):
        super().setUp()
        WorkoutLog.objects.all().delete()
        self.user = User.objects.get(pk=1)
        self.exercise = Exercise.objects.get(pk=1)
        self.primary, self.secondary, self.untouched = Muscle.objects.all()[:3]
        self.exercise.muscles.set([self.primary])
        self.exercise.muscles_secondary.set([self.secondary])

    def log_sets(self, n, user_id=1, hours_ago=0):
        for _ in range(n):
            WorkoutLog.objects.create(
                user_id=user_id,
                routine_id=1,
                exercise=self.exercise,
                weight=50,
                repetitions=8,
                date=timezone.now() - timedelta(hours=hours_ago),
            )

    def by_id(self, data):
        return {m['muscle_id']: m for m in data}

    def test_logs_fatigue_primary_and_secondary(self):
        self.log_sets(6)
        data = self.by_id(muscle_freshness(self.user))
        self.assertAlmostEqual(data[self.primary.pk]['freshness_pct'], 50.0, delta=0.5)
        self.assertAlmostEqual(data[self.secondary.pk]['freshness_pct'], 80.0, delta=0.5)
        self.assertEqual(data[self.untouched.pk]['freshness_pct'], 100.0)

    def test_other_users_logs_ignored(self):
        self.log_sets(12, user_id=2)
        data = self.by_id(muscle_freshness(self.user))
        self.assertEqual(data[self.primary.pk]['freshness_pct'], 100.0)

    def test_extra_events(self):
        extra = [FatigueEvent(self.untouched.pk, timezone.now(), sets=12.0)]
        data = self.by_id(muscle_freshness(self.user, extra_events=extra))
        self.assertEqual(data[self.untouched.pk]['status'], 'fatigued')


class FreshnessApiTestCase(WgerTestCase):
    def test_requires_auth(self):
        response = self.client.get(reverse('muscle-freshness'))
        self.assertIn(response.status_code, (401, 403))

    def test_returns_all_muscles(self):
        self.client.login(username='test', password='testtest')
        response = self.client.get(reverse('muscle-freshness'))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('calculated_at', body)
        self.assertEqual(len(body['muscles']), Muscle.objects.count())
        self.assertEqual(set(body['muscles'][0]), {'muscle_id', 'name', 'freshness_pct', 'status'})
