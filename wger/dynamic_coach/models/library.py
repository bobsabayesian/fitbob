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
from wger.exercises.models import Exercise


class LibraryEntry(models.Model):
    """
    An exercise in the user's own library.

    A user with a non-empty library only gets exercises from it. A user without
    one gets the whole wger catalogue, so the generator works out of the box.
    """

    class Meta:
        unique_together = ['user', 'exercise']

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='library_entries',
    )
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name='+')

    def __str__(self):
        return f'{self.user}: {self.exercise_id}'
