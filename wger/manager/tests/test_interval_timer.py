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


# Django
from django.core.exceptions import ValidationError
from django.urls import reverse

# Third Party
from rest_framework import status

# wger
from wger.core.tests.base_testcase import WgerTestCase
from wger.manager.interval_timer import (
    DEFAULTS,
    PRESETS,
    build_plan,
    normalize_config,
)
from wger.manager.models import (
    Day,
    Routine,
)
from wger.manager.models.day import DayType


class IntervalPlanTestCase(WgerTestCase):
    """Pure unit tests for plan generation"""

    def test_classic_tabata(self):
        plan = build_plan({})
        phases = plan['phases']
        # 8 work + 7 rest (no rest after the final round)
        self.assertEqual(len(phases), 15)
        self.assertEqual(plan['total_duration_s'], 8 * 20 + 7 * 10)
        self.assertEqual(plan['total_work_s'], 160)
        self.assertEqual(phases[0]['type'], 'work')
        self.assertEqual(phases[-1]['type'], 'work')
        self.assertEqual(phases[1]['start_s'], 20)

    def test_warmup_cooldown_and_sets(self):
        plan = build_plan(
            {'warmup_s': 30, 'cooldown_s': 20, 'rounds': 2, 'sets': 2, 'set_rest_s': 45}
        )
        types = [p['type'] for p in plan['phases']]
        self.assertEqual(
            types,
            ['warmup', 'work', 'rest', 'work', 'set_rest', 'work', 'rest', 'work', 'cooldown'],
        )
        # start offsets are contiguous
        clock = 0
        for phase in plan['phases']:
            self.assertEqual(phase['start_s'], clock)
            clock += phase['duration_s']
        self.assertEqual(clock, plan['total_duration_s'])

    def test_zero_rest_omits_phase(self):
        plan = build_plan({'rest_s': 0, 'rounds': 3})
        self.assertEqual([p['type'] for p in plan['phases']], ['work'] * 3)

    def test_presets_are_valid(self):
        for key, preset in PRESETS.items():
            with self.subTest(preset=key):
                self.assertTrue(build_plan(preset['config'])['phases'])

    def test_invalid_configs(self):
        bad = [
            'nope',
            {'version': 2},
            {'bogus': 1},
            {'work_s': 0},
            {'work_s': 'a'},
            {'work_s': True},
            {'work_s': 1.5},
            {'rounds': 0},
            {'rounds': 101},
            {'sets': 51},
            {'rest_s': -1},
        ]
        for config in bad:
            with self.subTest(config=config), self.assertRaises(ValidationError):
                normalize_config(config)

    def test_defaults_filled(self):
        cfg = normalize_config({'work_s': 45})
        self.assertEqual(cfg['work_s'], 45)
        self.assertEqual(cfg['rest_s'], DEFAULTS['rest_s'])
        self.assertEqual(cfg['version'], 1)


class IntervalApiTestCase(WgerTestCase):
    def setUp(self):
        super().setUp()
        self.user_login()
        self.routine = Day.objects.filter(routine__user__username='admin').first().routine

    def _create_day(self, **kwargs):
        data = {
            'routine': self.routine.pk,
            'name': 'Tabata',
            'description': 'Tabata',
            'type': DayType.TABATA,
            **kwargs,
        }
        return self.client.post(reverse('day-list'), data=data, content_type='application/json')

    def test_create_tabata_day_normalizes_config(self):
        response = self._create_day(config={'work_s': 40})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.json()['config']['rest_s'], 10)

    def test_create_tabata_day_without_config_gets_defaults(self):
        response = self._create_day()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.json()['config']['rounds'], 8)

    def test_create_tabata_day_invalid_config(self):
        response = self._create_day(config={'rounds': 0})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('config', response.json())

    def test_custom_day_config_untouched(self):
        response = self._create_day(type=DayType.CUSTOM, config={'anything': 1})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.json()['config'], {'anything': 1})

    def test_day_plan(self):
        day_id = self._create_day(config={'rounds': 4}).json()['id']
        response = self.client.get(reverse('day-interval-plan', kwargs={'pk': day_id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()['phases']), 7)

    def test_day_plan_non_interval_day(self):
        day_id = self._create_day(type=DayType.CUSTOM).json()['id']
        response = self.client.get(reverse('day-interval-plan', kwargs={'pk': day_id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_preview_and_presets(self):
        response = self.client.post(
            reverse('day-interval-plan-preview'),
            data={'rounds': 2},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['total_duration_s'], 50)

        response = self.client.post(
            reverse('day-interval-plan-preview'),
            data={'rounds': 0},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.get(reverse('day-interval-presets'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('tabata', response.json())

    def test_other_users_day_plan_forbidden(self):
        other_routine = Routine.objects.exclude(user__username='admin').first()
        other = Day.objects.create(routine=other_routine, type=DayType.TABATA, name='x')
        response = self.client.get(reverse('day-interval-plan', kwargs={'pk': other.pk}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_cannot_use_helpers(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse('day-interval-presets')).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class IntervalTimerPageTestCase(WgerTestCase):
    def test_page_requires_login(self):
        response = self.client.get(reverse('manager:routine:timer'))
        self.assertEqual(response.status_code, 302)

    def test_page_renders_for_logged_in_user(self):
        self.user_login()
        response = self.client.get(reverse('manager:routine:timer'))
        self.assertEqual(response.status_code, 200)
