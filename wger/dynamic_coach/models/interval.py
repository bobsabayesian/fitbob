# Django
from django.conf import settings
from django.db import models

# wger
from wger.exercises.models import Exercise


class TabataProtocol(models.Model):
    """
    A saved interval structure
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tabata_protocols',
    )

    name = models.CharField(max_length=64)

    work_sec = models.IntegerField(default=20)

    rest_sec = models.IntegerField(default=10)

    rounds_per_cycle = models.IntegerField(default=8)

    total_cycles = models.IntegerField(default=1)

    cycle_rest_sec = models.IntegerField(default=60)

    exercises = models.ManyToManyField(Exercise, blank=True, related_name='+')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
