# Django
from django.conf import settings
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.utils import timezone


class JiuJitsuSession(models.Model):
    """
    A jiu jitsu class / open mat. Counts as conditioning load in the recovery model.
    """

    KIND_DRILLING = 'drilling'
    KIND_ROLLING = 'rolling'
    KIND_COMPETITION = 'competition'
    KIND_CHOICES = (
        (KIND_DRILLING, 'Technique / drilling'),
        (KIND_ROLLING, 'Live rolling / open mat'),
        (KIND_COMPETITION, 'Competition'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='jiu_jitsu_sessions',
    )

    date = models.DateTimeField(default=timezone.now, db_index=True)

    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_ROLLING)

    gi = models.BooleanField(default=True)
    """Gi (True) or no-gi (False). Gi grip work loads the pulling muscles more"""

    duration_min = models.PositiveIntegerField(
        validators=[MinValueValidator(5), MaxValueValidator(300)]
    )

    rpe = models.PositiveSmallIntegerField(
        default=7, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )

    rounds = models.PositiveSmallIntegerField(default=0)
    """Number of live rounds"""

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date']
