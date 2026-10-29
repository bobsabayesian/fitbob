# Standard Library
import json
import tempfile
from unittest import mock

# Django
from django.core.exceptions import ImproperlyConfigured
from django.test import (
    RequestFactory,
    SimpleTestCase,
    override_settings,
)

# wger
from wger.dynamic_coach import access
from wger.dynamic_coach.models import ConditioningLoad
from wger.dynamic_coach.services import (
    recovery_engine,
    tabata_engine,
)
from wger.dynamic_coach.tests.base import CoachTestCase


class TabataTestCase(CoachTestCase):
    def test_spec_example_duration(self):
        a = self.make_exercise('High Knees', ['Quadriceps femoris'])
        b = self.make_exercise('Burpee', ['Quadriceps femoris'])
        resp = self.client.post(
            '/api/v2/coach/tabata/generate/',
            {
                'work_sec': 20,
                'rest_sec': 10,
                'rounds_per_cycle': 8,
                'total_cycles': 2,
                'cycle_rest_sec': 60,
                'exercise_ids': [a.pk, b.pk],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['total_duration_sec'], 540)
        tl = body['timeline']
        self.assertEqual(tl[0]['type'], 'prepare')
        self.assertEqual(tl[-1]['type'], 'complete')
        self.assertEqual([e['exercise_id'] for e in tl[1:6:2]], [a.pk, b.pk, a.pk])
        self.assertEqual(sum(e['type'] == 'work' for e in tl), 16)
        self.assertEqual(sum(e['type'] == 'cycle_rest' for e in tl), 1)

    def test_single_cycle_no_exercises(self):
        out = tabata_engine.build_timeline(20, 10, 8, 1, 60)
        self.assertEqual(out['total_duration_sec'], 10 + 8 * 20 + 7 * 10)

    def test_unknown_exercise_and_validation(self):
        self.assertEqual(
            self.client.post(
                '/api/v2/coach/tabata/generate/', {'exercise_ids': [999]}, format='json'
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                '/api/v2/coach/tabata/generate/', {'work_sec': 0}, format='json'
            ).status_code,
            400,
        )


class JiuJitsuTestCase(CoachTestCase):
    def test_session_fatigues_pulling_muscles_and_delete_restores(self):
        resp = self.client.post(
            '/api/v2/coach/bjj/',
            {'duration_min': 60, 'kind': 'rolling', 'rpe': 8, 'gi': True},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        lats = self.muscles['Latissimus dorsi'].pk
        quads = self.muscles['Quadriceps femoris'].pk
        scores = recovery_engine.freshness_scores(self.user)
        self.assertLess(scores[lats], scores[quads])
        self.assertLess(scores[lats], 0.7)

        self.assertEqual(
            self.client.delete(f'/api/v2/coach/bjj/{resp.json()["id"]}/').status_code, 204
        )
        self.assertEqual(recovery_engine.freshness_scores(self.user)[lats], 1.0)
        self.assertFalse(ConditioningLoad.objects.exists())

    def test_hard_roll_steers_auto_generator_away_from_pull(self):
        # wger
        from wger.dynamic_coach.services import session_builder as sb

        self.make_push_catalog()
        self.make_exercise('Row', ['Latissimus dorsi'], ['Biceps brachii'], [self.barbell])
        for _ in range(2):
            self.client.post(
                '/api/v2/coach/bjj/',
                {'duration_min': 90, 'kind': 'competition', 'rpe': 9},
                format='json',
            )
        result = sb.build_session(self.user, 45, split='auto', save_as_routine=False)
        self.assertNotEqual(result['split'], 'pull')

    def test_validation_and_isolation(self):
        self.assertEqual(
            self.client.post('/api/v2/coach/bjj/', {'duration_min': 1}, format='json').status_code,
            400,
        )
        self.client.post('/api/v2/coach/bjj/', {'duration_min': 60}, format='json')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/v2/coach/bjj/').json()['count'], 0)


class PreferencesTestCase(CoachTestCase):
    def test_roundtrip_and_foreign_profile_rejected(self):
        resp = self.client.post(
            '/api/v2/coach/equipment-profiles/',
            {'name': 'Garage', 'equipment': [self.barbell.pk]},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        pid = resp.json()['id']
        resp = self.client.put(
            '/api/v2/coach/preferences/',
            {'default_equipment_profile': pid, 'preferred_split': 'PPL'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['preferred_split'], 'PPL')

        self.client.force_authenticate(self.other)
        resp = self.client.put(
            '/api/v2/coach/preferences/', {'default_equipment_profile': pid}, format='json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_only_one_active_profile(self):
        # wger
        from wger.dynamic_coach.models import EquipmentProfile

        a = EquipmentProfile.objects.create(user=self.user, name='A')
        b = EquipmentProfile.objects.create(user=self.user, name='B')
        a.refresh_from_db()
        self.assertFalse(a.is_active)
        self.assertTrue(b.is_active)


class AccessTestCase(SimpleTestCase):
    def setUp(self):
        self.file = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
        self.write({'allowed_emails': ['Me@Example.com'], 'allowed_domains': ['corp.io']})
        access._file_cache.update(path=None, mtime=None, data=None)

    def write(self, data):
        with open(self.file.name, 'w') as f:
            json.dump(data, f)

    def middleware(self, **extra):
        cfg = dict(
            CF_ACCESS_ENABLED=True,
            CF_ACCESS_TEAM_DOMAIN='team.cloudflareaccess.com',
            CF_ACCESS_AUD='aud',
            CF_ACCESS_ACCESS_FILE=self.file.name,
            CF_ACCESS_EXEMPT_PATHS=['/health'],
        )
        cfg.update(extra)
        with override_settings(**cfg):
            return access.CloudflareAccessMiddleware(lambda r: 'OK')

    def request(self, path='/x', token=None):
        headers = {'Cf-Access-Jwt-Assertion': token} if token else {}
        return RequestFactory().get(path, headers=headers)

    def test_disabled_is_inert(self):
        with override_settings(CF_ACCESS_ENABLED=False):
            self.assertEqual(
                access.CloudflareAccessMiddleware(lambda r: 'OK')(self.request()), 'OK'
            )

    def test_misconfigured_fails_at_startup(self):
        with self.assertRaises(ImproperlyConfigured):
            self.middleware(CF_ACCESS_AUD='')

    def test_missing_token_denied(self):
        self.assertEqual(self.middleware()(self.request()).status_code, 403)

    def test_exempt_path(self):
        self.assertEqual(self.middleware()(self.request('/health')), 'OK')

    def test_bad_signature_denied(self):
        with mock.patch.object(access, 'decode_token', side_effect=ValueError):
            self.assertEqual(self.middleware()(self.request(token='t')).status_code, 403)

    def test_allowed_by_email_case_insensitive_and_domain(self):
        mw = self.middleware()
        for email in ('me@example.com', 'anyone@corp.io'):
            with mock.patch.object(access, 'decode_token', return_value={'email': email}):
                self.assertEqual(mw(self.request(token='t')), 'OK')

    def test_valid_jwt_but_not_in_access_json_denied(self):
        with mock.patch.object(access, 'decode_token', return_value={'email': 'eve@evil.com'}):
            self.assertEqual(self.middleware()(self.request(token='t')).status_code, 403)

    def test_service_token(self):
        self.write({'allowed_service_tokens': ['abc.access']})
        with mock.patch.object(access, 'decode_token', return_value={'common_name': 'abc.access'}):
            self.assertEqual(self.middleware()(self.request(token='t')), 'OK')
        with mock.patch.object(access, 'decode_token', return_value={'common_name': 'zzz'}):
            self.assertEqual(self.middleware()(self.request(token='t')).status_code, 403)

    def test_missing_or_broken_file_fails_closed(self):
        mw = self.middleware(CF_ACCESS_ACCESS_FILE='/nonexistent/access.json')
        with mock.patch.object(access, 'decode_token', return_value={'email': 'me@example.com'}):
            self.assertEqual(mw(self.request(token='t')).status_code, 403)
        with open(self.file.name, 'w') as f:
            f.write('{broken')
        access._file_cache.update(path=None, mtime=None, data=None)
        with mock.patch.object(access, 'decode_token', return_value={'email': 'me@example.com'}):
            self.assertEqual(self.middleware()(self.request(token='t')).status_code, 403)

    def test_edit_to_file_applies_without_restart(self):
        mw = self.middleware()
        with mock.patch.object(access, 'decode_token', return_value={'email': 'me@example.com'}):
            self.assertEqual(mw(self.request(token='t')), 'OK')
            # Standard Library
            import os
            import time

            self.write({'allowed_emails': []})
            os.utime(self.file.name, (time.time() + 5, time.time() + 5))
            self.assertEqual(mw(self.request(token='t')).status_code, 403)
