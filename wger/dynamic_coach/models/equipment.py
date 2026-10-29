# Django
from django.conf import settings
from django.db import (
    models,
    transaction,
)

# wger
from wger.exercises.models import Equipment


class EquipmentProfile(models.Model):
    """
    A training space (home gym, commercial gym, hotel...) and the equipment in it
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='equipment_profiles',
    )

    name = models.CharField(max_length=64)

    equipment = models.ManyToManyField(Equipment, blank=True)

    is_active = models.BooleanField(default=True)
    """Used for generation when no profile is requested explicitly"""

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.user_id})'

    def save(self, *args, **kwargs):
        # Only one profile per user can be the active one
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_active:
                EquipmentProfile.objects.filter(user_id=self.user_id, is_active=True).exclude(
                    pk=self.pk
                ).update(is_active=False)
