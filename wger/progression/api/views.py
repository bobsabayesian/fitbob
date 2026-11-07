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
from decimal import (
    Decimal,
    InvalidOperation,
)

# Django
from django.shortcuts import get_object_or_404

# Third Party
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# wger
from wger.exercises.models import Exercise
from wger.progression.analytics import (
    analyze,
    load_sets,
    suggest_next,
)


def _int(request, name, default, low, high):
    try:
        value = int(request.query_params.get(name, default))
    except ValueError:
        raise ValidationError({name: 'must be an integer'})
    if not low <= value <= high:
        raise ValidationError({name: f'must be between {low} and {high}'})
    return value


class ExerciseProgressView(APIView):
    """Session history, personal records and stall detection for one exercise"""

    permission_classes = (IsAuthenticated,)

    def get(self, request, exercise_id):
        get_object_or_404(Exercise, pk=exercise_id)
        days = _int(request, 'days', 180, 1, 3650)
        data = analyze(load_sets(request.user, exercise_id, days=days))
        return Response({'exercise_id': exercise_id, **data})


class NextSessionSuggestionView(APIView):
    """Double-progression suggestion for the next session of one exercise"""

    permission_classes = (IsAuthenticated,)

    def get(self, request, exercise_id):
        get_object_or_404(Exercise, pk=exercise_id)
        min_reps = _int(request, 'min_reps', 8, 1, 50)
        max_reps = _int(request, 'max_reps', 12, min_reps, 50)
        sets = _int(request, 'sets', 3, 1, 20)
        try:
            increment = Decimal(request.query_params.get('increment', '2.5'))
        except InvalidOperation:
            raise ValidationError({'increment': 'must be a number'})
        if increment <= 0:
            raise ValidationError({'increment': 'must be positive'})

        s = suggest_next(
            load_sets(request.user, exercise_id),
            min_reps=min_reps,
            max_reps=max_reps,
            target_sets=sets,
            increment=increment,
        )
        return Response(
            {
                'exercise_id': exercise_id,
                'weight_kg': s.weight,
                'reps': s.reps,
                'sets': s.sets,
                'reason': s.reason,
            }
        )
