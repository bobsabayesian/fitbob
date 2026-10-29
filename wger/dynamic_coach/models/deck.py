# Standard Library
import uuid

# Django
from django.conf import settings
from django.db import models


class DeckWorkoutSession(models.Model):
    """
    State of a card-deck ("Sweat Deck") conditioning workout
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='deck_sessions',
    )

    deck_size = models.IntegerField(default=52)

    include_jokers = models.BooleanField(default=False)

    suit_mapping = models.JSONField()
    """suit -> exercise id"""

    card_stack = models.JSONField(default=list)
    """Remaining cards, the top of the deck is the last element"""

    cards_drawn = models.JSONField(default=list)
    """Audit trail: [{"face": "K", "suit": "hearts", "reps": 13, "exercise_id": 1, "ts": ...}]"""

    is_completed = models.BooleanField(default=False)

    elapsed_seconds = models.IntegerField(null=True, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)

    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']
