# Django
from django.utils import timezone

# wger
from wger.dynamic_coach.services import recovery_engine as re
from wger.dynamic_coach.tests.base import CoachTestCase


class RecoveryTestCase(CoachTestCase):
    def setUp(self):
        super().setUp()
        self.bench = self.make_exercise(
            'Bench', ['Pectoralis major'], ['Triceps brachii'], [self.barbell]
        )
        self.chest = self.muscles['Pectoralis major'].pk
        self.tri = self.muscles['Triceps brachii'].pk

    def test_fresh_user_is_fully_fresh(self):
        scores = re.freshness_scores(self.user)
        self.assertTrue(all(v == 1.0 for v in scores.values()))

    def test_half_life(self):
        self.assertAlmostEqual(re.decay_factor(36), 0.5)
        self.assertAlmostEqual(re.decay_factor(72), 0.25)
        self.assertAlmostEqual(re.DECAY_LAMBDA, 0.019254, places=6)

    def test_twelve_sets_exhaust_then_recover(self):
        now = timezone.now()
        for _ in range(12):
            self.log_set(self.bench, 80, 8, now)
        self.assertAlmostEqual(re.freshness_scores(self.user, now)[self.chest], 0.0)
        self.assertAlmostEqual(
            re.freshness_scores(self.user, now + self.hours(36))[self.chest], 0.5, places=3
        )
        self.assertAlmostEqual(
            re.freshness_scores(self.user, now + self.hours(72))[self.chest], 0.75, places=3
        )

    def test_secondary_muscle_counts_less(self):
        now = timezone.now()
        for _ in range(10):
            self.log_set(self.bench, 80, 8, now)
        scores = re.freshness_scores(self.user, now)
        self.assertAlmostEqual(scores[self.chest], 1 - 10 / 12)
        self.assertAlmostEqual(scores[self.tri], 1 - 4 / 12)

    def test_floor_at_zero(self):
        for _ in range(30):
            self.log_set(self.bench, 80, 8)
        self.assertEqual(re.freshness_scores(self.user)[self.chest], 0.0)

    def test_outside_window_and_other_users_ignored(self):
        for _ in range(12):
            self.log_set(self.bench, 80, 8, self.hours_ago(24 * 15))
            self.log_set(self.bench, 80, 8, user=self.other)
        self.assertEqual(re.freshness_scores(self.user)[self.chest], 1.0)

    def test_conditioning_load_counts(self):
        re.log_conditioning_load(self.user, 'deck', {self.chest: 6})
        self.assertAlmostEqual(re.freshness_scores(self.user)[self.chest], 0.5, places=2)

    def test_conditioning_load_idempotent_with_ref(self):
        for _ in range(2):
            re.log_conditioning_load(self.user, 'deck', {self.chest: 6}, source_ref='x')
        self.assertAlmostEqual(re.freshness_scores(self.user)[self.chest], 0.5, places=2)

    def test_status_thresholds(self):
        self.assertEqual(re.status_for(0.9), 'ready')
        self.assertEqual(re.status_for(0.5), 'recovering')
        self.assertEqual(re.status_for(0.1), 'fatigued')

    def test_endpoint(self):
        for _ in range(9):
            self.log_set(self.bench, 80, 8)
        resp = self.client.get('/api/v2/coach/muscle-freshness/')
        self.assertEqual(resp.status_code, 200)
        by_id = {m['muscle_id']: m for m in resp.json()['muscles']}
        self.assertEqual(by_id[self.chest]['freshness_pct'], 25.0)
        self.assertEqual(by_id[self.chest]['status'], 'fatigued')

    def test_endpoint_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/v2/coach/muscle-freshness/').status_code, 403)

    @staticmethod
    def hours(h):
        # Standard Library
        import datetime

        return datetime.timedelta(hours=h)
