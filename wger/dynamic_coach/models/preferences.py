# Django
from django.conf import settings
from django.db import models

# wger
from wger.exercises.models import (
    Exercise,
    Muscle,
)


class UserGeneratorPreferences(models.Model):
    """
    User specific dials and hard exclusions for the workout generator
    """

    SPLIT_CHOICES = (
        ('PPL', 'Push / Pull / Legs'),
        ('UL', 'Upper / Lower'),
        ('FULL_BODY', 'Full body'),
        ('AUTO', 'Auto'),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='generator_preferences',
    )

    default_equipment_profile = models.ForeignKey(
        'dynamic_coach.EquipmentProfile',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    default_duration_min = models.IntegerField(default=45)

    preferred_split = models.CharField(max_length=16, choices=SPLIT_CHOICES, default='AUTO')

    excluded_exercises = models.ManyToManyField(Exercise, blank=True, related_name='+')

    excluded_muscles = models.ManyToManyField(Muscle, blank=True, related_name='+')
    """Injuries: no exercise that uses these muscles (primary or secondary) is selected"""

    rounding_increment_kg = models.DecimalField(max_digits=4, decimal_places=2, default=2.50)

    def __str__(self):
        return f'Generator preferences of {self.user_id}'
