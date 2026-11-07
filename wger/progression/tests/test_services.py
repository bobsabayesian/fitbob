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
from django.utils import timezone

# wger
from wger.core.tests.base_testcase import WgerTestCase
from wger.manager.consts import (
    REP_UNIT_REPETITIONS,
    WEIGHT_UNIT_KG,
    WEIGHT_UNIT_LB,
)
from wger.manager.models import WorkoutLog
from wger.progression.services import (
    best_e1rm,
    brzycki_e1rm,
    epley_e1rm,
    prescribe,
    round_to_increment,
    weight_for_reps,
)


class FormulaTestCase(WgerTestCase):
    def test_epley(self):
        self.assertEqual(epley_e1rm(100, 1), Decimal(100))
        self.assertAlmostEqual(float(epley_e1rm(80, 8)), 101.333, places=2)

    def test_brzycki(self):
        self.assertAlmostEqual(float(brzycki_e1rm(100, 5)), 112.5, places=2)
        with self.assertRaises(ValueError):
            brzycki_e1rm(100, 37)

    def test_invalid_input(self):
        for func in (epley_e1rm, brzycki_e1rm, weight_for_reps):
            with self.assertRaises(ValueError):
                func(0, 5)
            with self.assertRaises(ValueError):
                func(50, 0)

    def test_inverse_round_trip(self):
        self.assertAlmostEqual(float(weight_for_reps(epley_e1rm(80, 8), 8)), 80, places=6)

    def test_rounding(self):
        self.assertEqual(round_to_increment('81.2'), Decimal('80.0'))
        self.assertEqual(round_to_increment('81.25'), Decimal('82.5'))
        self.assertEqual(round_to_increment('81.2', '1.25'), Decimal('81.25'))
        with self.assertRaises(ValueError):
            round_to_increment(80, 0)


class PrescribeTestCase(WgerTestCase):
    def setUp(self):
        super().setUp()
        WorkoutLog.objects.all().delete()

    def log(self, weight, reps, days_ago=1, unit=WEIGHT_UNIT_KG, user_id=1):
        WorkoutLog.objects.create(
            user_id=user_id,
            routine_id=1,
            exercise_id=1,
            weight=weight,
            repetitions=reps,
            weight_unit_id=unit,
            repetitions_unit_id=REP_UNIT_REPETITIONS,
            date=timezone.now() - timedelta(days=days_ago),
        )

    def user(self):
        from django.contrib.auth.models import User

        return User.objects.get(pk=1)

    def test_cold_start(self):
        result = prescribe(self.user(), 1, 8)
        self.assertIsNone(result.weight)
        self.assertIsNone(result.e1rm)

    def test_best_set_wins(self):
        self.log(60, 10)
        self.log(80, 5)  # 93.33
        self.assertAlmostEqual(float(best_e1rm(self.user(), 1)), 93.333, places=2)

    def test_ignores_old_other_unit_other_user_and_high_reps(self):
        self.log(200, 5, days_ago=120)
        self.log(200, 5, unit=WEIGHT_UNIT_LB)
        self.log(200, 5, user_id=2)
        self.log(50, 30)
        self.assertIsNone(best_e1rm(self.user(), 1))

    def test_prescribe_rounds_to_increment(self):
        self.log(80, 8)  # e1rm 101.33 -> 8 reps: 80.0
        result = prescribe(self.user(), 1, 8)
        self.assertEqual(result.weight, Decimal('80.0'))
        lighter = prescribe(self.user(), 1, 12, increment=Decimal('5'))
        self.assertEqual(lighter.weight, Decimal('70'))
