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
from django.utils import timezone

# Third Party
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# wger
from wger.dynamic_coach.services.recovery_engine import muscle_freshness


class MuscleFreshnessView(APIView):
    """Freshness (0-100%) of every muscle, derived from the user's recent logs"""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        now = timezone.now()
        return Response({'calculated_at': now, 'muscles': muscle_freshness(request.user, now)})
