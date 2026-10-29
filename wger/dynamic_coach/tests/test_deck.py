# Standard Library
from collections import Counter

# wger
from wger.dynamic_coach.models import (
    ConditioningLoad,
    DeckWorkoutSession,
)
from wger.dynamic_coach.services import (
    deck_engine,
    recovery_engine,
)
from wger.dynamic_coach.tests.base import CoachTestCase


class DeckTestCase(CoachTestCase):
    def setUp(self):
        super().setUp()
        self.pushup = self.make_exercise(
            'Push-ups', ['Pectoralis major'], ['Triceps brachii'], [self.none]
        )
        self.squat = self.make_exercise('Air Squats', ['Quadriceps femoris'], ['Gluteus maximus'])
        self.climber = self.make_exercise('Mountain Climbers', ['Rectus abdominis'])
        self.burpee = self.make_exercise(
            'Burpees', ['Quadriceps femoris', 'Pectoralis major'], ['Triceps brachii']
        )
        self.barbell_curl = self.make_exercise('Curl', ['Biceps brachii'], [], [self.barbell])

    def test_stack_composition(self):
        stack = deck_engine.build_stack(52)
        self.assertEqual(len(stack), 52)
        self.assertEqual(sum(c['reps'] for c in stack), 4 * (sum(range(2, 11)) + 11 + 12 + 13 + 15))
        self.assertEqual(Counter(c['suit'] for c in stack), {s: 13 for s in deck_engine.SUITS})

    def test_half_deck_and_jokers(self):
        self.assertEqual(len(deck_engine.build_stack(26)), 26)
        stack = deck_engine.build_stack(26, include_jokers=True)
        self.assertEqual(len(stack), 28)
        self.assertEqual(sum(c['suit'] == 'joker' for c in stack), 2)

    def test_shuffles_differ(self):
        self.assertNotEqual(deck_engine.build_stack(52), deck_engine.build_stack(52))

    def test_suit_mapping_prefers_known_bodyweight_moves(self):
        mapping = deck_engine.build_suit_mapping(self.user)
        self.assertEqual(
            mapping,
            {
                'spades': self.pushup.pk,
                'hearts': self.squat.pk,
                'diamonds': self.climber.pk,
                'clubs': self.burpee.pk,
            },
        )

    def test_override_and_bad_override(self):
        plank = self.make_exercise('Plank', ['Rectus abdominis'])
        mapping = deck_engine.build_suit_mapping(self.user, {'diamonds': plank.pk})
        self.assertEqual(mapping['diamonds'], plank.pk)
        self.assertEqual(mapping['clubs'], self.burpee.pk)
        with self.assertRaises(deck_engine.DeckError):
            deck_engine.build_suit_mapping(self.user, {'diamonds': 99999})
        with self.assertRaises(deck_engine.DeckError):
            deck_engine.build_suit_mapping(self.user, {'wands': self.burpee.pk})

    def test_full_flow_via_api(self):
        resp = self.client.post(
            '/api/v2/coach/deck/new/', {'deck_size': 26, 'include_jokers': True}, format='json'
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body['total_cards'], 28)
        sid = body['session_id']

        total = 0
        for i in range(1, 29):
            r = self.client.post(f'/api/v2/coach/deck/{sid}/draw/')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()['cards_remaining'], 28 - i)
            total += r.json()['card']['reps']
        self.assertEqual(self.client.post(f'/api/v2/coach/deck/{sid}/draw/').status_code, 409)

        done = self.client.post(
            f'/api/v2/coach/deck/{sid}/complete/', {'elapsed_seconds': 1200}, format='json'
        )
        self.assertEqual(done.json()['total_reps_completed'], total)
        self.assertTrue(done.json()['fatigue_logged'])
        self.assertEqual(ConditioningLoad.objects.filter(source='deck').count(), 1)

        # completing twice must not double count
        again = self.client.post(f'/api/v2/coach/deck/{sid}/complete/', {}, format='json')
        self.assertFalse(again.json()['fatigue_logged'])
        self.assertEqual(ConditioningLoad.objects.filter(source='deck').count(), 1)
        self.assertEqual(self.client.post(f'/api/v2/coach/deck/{sid}/draw/').status_code, 409)

    def test_reps_become_equivalent_sets(self):
        session = deck_engine.new_session(self.user, 52)
        session.card_stack = [{'suit': 'spades', 'face': 'K', 'reps': 20}]
        session.save()
        deck_engine.draw(session.pk, self.user)
        summary = deck_engine.complete(session.pk, self.user)
        self.assertEqual(summary['total_reps_completed'], 20)
        chest = self.muscles['Pectoralis major'].pk
        triceps = self.muscles['Triceps brachii'].pk
        scores = recovery_engine.freshness_scores(self.user)
        self.assertAlmostEqual(scores[chest], 1 - 1 / 12, places=3)  # 20 reps = 1 set
        self.assertAlmostEqual(scores[triceps], 1 - 0.4 / 12, places=3)

    def test_empty_complete_logs_nothing(self):
        session = deck_engine.new_session(self.user, 52)
        self.assertFalse(deck_engine.complete(session.pk, self.user)['fatigue_logged'])

    def test_other_users_deck_is_404(self):
        session = deck_engine.new_session(self.user, 52)
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.post(f'/api/v2/coach/deck/{session.pk}/draw/').status_code, 404
        )
        self.assertEqual(
            self.client.post(
                f'/api/v2/coach/deck/{session.pk}/complete/', {}, format='json'
            ).status_code,
            404,
        )

    def test_concurrent_draws_never_duplicate(self):
        # sqlite has no row locks, so run the loop serially with fresh instances:
        # each draw must re-read the persisted stack rather than a stale copy.
        session = deck_engine.new_session(self.user, 52)
        s1 = DeckWorkoutSession.objects.get(pk=session.pk)
        s2 = DeckWorkoutSession.objects.get(pk=session.pk)
        _, c1 = deck_engine.draw(s1.pk, self.user)
        _, c2 = deck_engine.draw(s2.pk, self.user)
        self.assertNotEqual((c1['suit'], c1['face']), (c2['suit'], c2['face']))
        session.refresh_from_db()
        self.assertEqual(len(session.card_stack), 50)
        self.assertEqual(len(session.cards_drawn), 2)

    def test_no_bodyweight_exercises(self):
        for e in (self.pushup, self.squat, self.climber, self.burpee):
            e.delete()
        resp = self.client.post('/api/v2/coach/deck/new/', {}, format='json')
        self.assertEqual(resp.status_code, 400)
