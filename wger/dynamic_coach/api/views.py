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

# Standard Library
import random

# Django
from django.utils import timezone

# Third Party
from rest_framework import (
    generics,
    status,
    viewsets,
)
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# wger
from wger.dynamic_coach.api.serializers import (
    EquipmentProfileSerializer,
    GenerateWorkoutSerializer,
    GeneratorPreferencesSerializer,
)
from wger.dynamic_coach.models import (
    EquipmentProfile,
    UserGeneratorPreferences,
)
from wger.progression.services import KG_PER_LB
from wger.dynamic_coach.services.session_builder import (
    GeneratorError,
    build_session,
    save_routine,
)
from wger.dynamic_coach.services.recovery_engine import muscle_freshness


class MuscleFreshnessView(APIView):
    """Freshness (0-100%) of every muscle, derived from the user's recent logs"""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        now = timezone.now()
        return Response({'calculated_at': now, 'muscles': muscle_freshness(request.user, now)})


class EquipmentProfileViewSet(viewsets.ModelViewSet):
    """The user's own equipment profiles"""

    serializer_class = EquipmentProfileSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        return EquipmentProfile.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class GeneratorPreferencesView(generics.RetrieveUpdateAPIView):
    """The user's generator preferences, created with defaults on first access"""

    serializer_class = GeneratorPreferencesSerializer
    permission_classes = (IsAuthenticated,)

    def get_object(self):
        return UserGeneratorPreferences.objects.get_or_create(user=self.request.user)[0]


class GenerateWorkoutView(APIView):
    """Generates a strength session, optionally saving it as a wger routine"""

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = GenerateWorkoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        seed = data['seed'] if 'seed' in data else random.randrange(2**31)
        try:
            split_key, planned = build_session(
                request.user,
                duration_min=data.get('duration_min'),
                split=data['split'],
                equipment_profile_id=data.get('equipment_profile_id'),
                rng=random.Random(seed),
            )
        except GeneratorError as error:
            raise ValidationError({'detail': str(error)})

        routine_id = day_id = None
        if data['save_as_routine']:
            name = data.get('routine_name') or f'Dynamic {split_key.replace("_", " ")}'
            routine, day = save_routine(request.user, planned, split_key, name)
            routine_id, day_id = routine.pk, day.pk

        return Response(
            {
                'routine_id': routine_id,
                'day_id': day_id,
                'split': split_key,
                'seed': seed,
                'exercises': [
                    {
                        'exercise_id': p.exercise.pk,
                        'name': p.name,
                        'slot': p.slot,
                        'sets': p.sets,
                        'target_reps': p.target_reps,
                        'prescribed_weight': p.weight,
                        'weight_unit': p.unit,
                        'prescribed_weight_kg': (
                            None
                            if p.weight is None
                            else round(p.weight * (KG_PER_LB if p.unit == 'lb' else 1), 2)
                        ),
                        'e1rm_basis_kg': round(p.e1rm, 1) if p.e1rm is not None else None,
                    }
                    for p in planned
                ],
            },
            status=status.HTTP_201_CREATED,
        )
