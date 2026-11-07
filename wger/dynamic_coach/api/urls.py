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
from django.urls import (
    include,
    path,
)

# Third Party
from rest_framework import routers

# wger
from wger.dynamic_coach.api import views


router = routers.DefaultRouter()
router.register(r'equipment-profile', views.EquipmentProfileViewSet, basename='equipment-profile')

urlpatterns = [
    path('muscle-freshness/', views.MuscleFreshnessView.as_view(), name='muscle-freshness'),
    path('preferences/', views.GeneratorPreferencesView.as_view(), name='generator-preferences'),
    path('generate-workout/', views.GenerateWorkoutView.as_view(), name='generate-workout'),
    path('', include(router.urls)),
]
