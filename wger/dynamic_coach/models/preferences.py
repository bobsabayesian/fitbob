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

# Django
from django.conf import settings
from django.db import models

# wger
from wger.exercises.models import (
    Exercise,
    Muscle,
)
from wger.dynamic_coach.models.equipment import EquipmentProfile


class SplitChoices(models.TextChoices):
    PPL = 'PPL'
    UL = 'UL'
    FULL_BODY = 'FULL_BODY'
    AUTO = 'AUTO'


class UserGeneratorPreferences(models.Model):
    """Per-user dials and hard exclusions for the workout generator"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='generator_preferences',
    )
    default_equipment_profile = models.ForeignKey(
        EquipmentProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    default_duration_min = models.IntegerField(default=45)
    preferred_split = models.CharField(
        max_length=16,
        choices=SplitChoices.choices,
        default=SplitChoices.AUTO,
    )
    excluded_exercises = models.ManyToManyField(Exercise, blank=True, related_name='+')
    excluded_muscles = models.ManyToManyField(Muscle, blank=True, related_name='+')
    rounding_increment = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, default=None
    )
    """Smallest load step in the user's weight unit. None means 2.5 kg or 5 lb."""

    def __str__(self):
        return f'Generator preferences of {self.user}'
