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
from wger.exercises.models import Equipment


class EquipmentProfile(models.Model):
    """A training space (home gym, commercial gym, hotel) and what it contains"""

    class Meta:
        ordering = ['name']
        unique_together = ['user', 'name']

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='equipment_profiles',
    )
    name = models.CharField(max_length=64)
    equipment = models.ManyToManyField(Equipment, blank=True)
    is_active = models.BooleanField(default=True)
    """Profiles flagged active are the default for workout generation"""

    def __str__(self):
        return self.name
