"""Card-deck conditioning ("Sweat Deck")"""

# Standard Library
import secrets
from collections import defaultdict

# Django
from django.db import transaction
from django.utils import timezone

# wger
from wger.dynamic_coach.models import (
    ConditioningLoad,
    DeckWorkoutSession,
)
from wger.dynamic_coach.services import recovery_engine
from wger.dynamic_coach.services.common import (
    exercise_muscle_loads,
    exercise_name,
    is_bodyweight,
    muscle_key,
)
from wger.exercises.models import Exercise


SUITS = ['spades', 'hearts', 'diamonds', 'clubs']
FACE_REPS = {
    **{str(n): n for n in range(2, 11)},
    'J': 11,
    'Q': 12,
    'K': 13,
    'A': 15,
}
JOKER_REPS = 25
REPS_PER_EQUIVALENT_SET = 20.0

# suit -> (muscles it should train, exercise names to prefer, in order)
SUIT_CATEGORIES = {
    'spades': (
        {'pectoralis major', 'anterior deltoid', 'triceps brachii'},
        ['push-up', 'push up', 'pushup', 'dip'],
    ),
    'hearts': (
        {'quadriceps femoris', 'gluteus maximus', 'biceps femoris'},
        ['air squat', 'squat', 'lunge'],
    ),
    'diamonds': (
        {'rectus abdominis', 'obliquus externus abdominis', 'gluteus maximus'},
        ['mountain climber', 'sit-up', 'sit up', 'crunch'],
    ),
    'clubs': (
        {'quadriceps femoris', 'gluteus maximus', 'pectoralis major', 'latissimus dorsi'},
        ['burpee', 'kettlebell swing', 'jumping jack'],
    ),
}

_rng = secrets.SystemRandom()


class DeckError(Exception):
    """Raised for illegal deck state transitions"""


def _pick_exercise(suit: str, freshness: dict[int, float], taken: set[int]) -> Exercise | None:
    muscles, preferred = SUIT_CATEGORIES[suit]
    candidates = [
        e
        for e in Exercise.objects.filter(muscles__isnull=False)
        .distinct()
        .prefetch_related('muscles', 'equipment', 'translations')
        if e.pk not in taken and is_bodyweight(e)
    ]

    # 1. well known exercises for the suit
    for hint in preferred:
        named = [e for e in candidates if hint in exercise_name(e).lower()]
        if named:
            return min(named, key=lambda e: e.pk)

    # 2. otherwise the bodyweight exercise training the category's freshest muscles
    in_category = [e for e in candidates if any(muscle_key(m) in muscles for m in e.muscles.all())]

    def score(e):
        primary = [m.pk for m in e.muscles.all() if muscle_key(m) in muscles]
        return sum(freshness.get(pk, 1.0) for pk in primary) / max(len(primary), 1)

    return max(in_category, key=lambda e: (score(e), -e.pk), default=None)


def build_suit_mapping(user, overrides: dict[str, int] | None = None) -> dict[str, int]:
    overrides = overrides or {}
    unknown = set(overrides) - set(SUITS)
    if unknown:
        raise DeckError(f'Unknown suits: {sorted(unknown)}')

    found = Exercise.objects.in_bulk(list(overrides.values()))
    for ex_id in overrides.values():
        if ex_id not in found:
            raise DeckError(f'Unknown exercise id {ex_id}')

    freshness = recovery_engine.freshness_scores(user)
    mapping: dict[str, int] = {}
    taken = set(overrides.values())
    for suit in SUITS:
        if suit in overrides:
            mapping[suit] = overrides[suit]
            continue
        exercise = _pick_exercise(suit, freshness, taken)
        if exercise is None:
            raise DeckError(f'No bodyweight exercise available for {suit}; pass suit_overrides')
        mapping[suit] = exercise.pk
        taken.add(exercise.pk)
    return mapping


def build_stack(deck_size: int = 52, include_jokers: bool = False) -> list[dict]:
    full = [{'suit': s, 'face': f, 'reps': r} for s in SUITS for f, r in FACE_REPS.items()]
    _rng.shuffle(full)
    stack = full[:deck_size]
    if include_jokers:
        stack += [{'suit': 'joker', 'face': 'JOKER', 'reps': JOKER_REPS} for _ in range(2)]
        _rng.shuffle(stack)
    return stack


def new_session(user, deck_size=52, include_jokers=False, suit_overrides=None):
    if not 1 <= deck_size <= 52:
        raise DeckError('deck_size must be between 1 and 52')
    mapping = build_suit_mapping(user, suit_overrides)
    return DeckWorkoutSession.objects.create(
        user=user,
        deck_size=deck_size,
        include_jokers=include_jokers,
        suit_mapping=mapping,
        card_stack=build_stack(deck_size, include_jokers),
    )


def _exercise_for_card(session: DeckWorkoutSession, card: dict) -> int:
    # Jokers are a static hold on the core exercise
    suit = 'diamonds' if card['suit'] == 'joker' else card['suit']
    return session.suit_mapping[suit]


@transaction.atomic
def draw(session_id, user) -> tuple[DeckWorkoutSession, dict]:
    """Pops the top card. The row is locked so concurrent draws never get the same card."""
    session = DeckWorkoutSession.objects.select_for_update().get(pk=session_id, user=user)
    if session.is_completed:
        raise DeckError('Session is already completed')
    if not session.card_stack:
        raise DeckError('The deck is empty')

    card = session.card_stack.pop()
    card['exercise_id'] = _exercise_for_card(session, card)
    card['ts'] = timezone.now().isoformat()
    session.cards_drawn.append(card)
    session.save(update_fields=['card_stack', 'cards_drawn'])
    return session, card


@transaction.atomic
def complete(session_id, user, elapsed_seconds: int | None = None) -> dict:
    """Finishes the deck and converts the reps into fatigue (20 reps = 1 working set)"""
    session = DeckWorkoutSession.objects.select_for_update().get(pk=session_id, user=user)
    reps_by_exercise: dict[int, int] = defaultdict(int)
    for card in session.cards_drawn:
        reps_by_exercise[card['exercise_id']] += card['reps']
    total_reps = sum(reps_by_exercise.values())

    if session.is_completed:
        return _summary(session, total_reps, logged=False)

    loads: dict[int, float] = defaultdict(float)
    exercises = Exercise.objects.in_bulk(list(reps_by_exercise))
    for ex_id, reps in reps_by_exercise.items():
        equivalent_sets = reps / REPS_PER_EQUIVALENT_SET
        for muscle_id, sets in exercise_muscle_loads(exercises[ex_id], equivalent_sets).items():
            loads[muscle_id] += sets

    now = timezone.now()
    if loads:
        recovery_engine.log_conditioning_load(
            user, ConditioningLoad.SOURCE_DECK, loads, source_ref=str(session.pk), logged_at=now
        )

    session.is_completed = True
    session.completed_at = now
    session.elapsed_seconds = elapsed_seconds
    session.save(update_fields=['is_completed', 'completed_at', 'elapsed_seconds'])
    return _summary(session, total_reps, logged=bool(loads))


def _summary(session, total_reps, logged) -> dict:
    return {
        'status': 'completed',
        'total_cards_drawn': len(session.cards_drawn),
        'total_reps_completed': total_reps,
        'fatigue_logged': logged,
    }
