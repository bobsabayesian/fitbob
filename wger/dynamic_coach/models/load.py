# Django
from django.conf import settings
from django.db import models


class ConditioningLoad(models.Model):
    """
    Synthetic training load that is not backed by WorkoutLog rows (card decks,
    jiu jitsu...), consumed by the recovery engine.
    """

    SOURCE_DECK = 'deck'
    SOURCE_BJJ = 'bjj'
    SOURCE_CHOICES = ((SOURCE_DECK, 'Deck'), (SOURCE_BJJ, 'Jiu jitsu'))

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conditioning_loads',
    )

    source = models.CharField(max_length=8, choices=SOURCE_CHOICES)

    source_ref = models.CharField(max_length=64, blank=True)
    """Id of the originating object, makes logging idempotent"""

    logged_at = models.DateTimeField(db_index=True)

    loads = models.JSONField(default=dict)
    """muscle id -> effective hard sets (role factor already applied)"""

    class Meta:
        ordering = ['-logged_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'source', 'source_ref'],
                condition=~models.Q(source_ref=''),
                name='unique_conditioning_load_source_ref',
            )
        ]
