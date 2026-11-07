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

# Third Party
from rest_framework import serializers

# wger
from wger.dynamic_coach.models import (
    EquipmentProfile,
    SplitChoices,
    UserGeneratorPreferences,
)


class EquipmentProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentProfile
        fields = ['id', 'name', 'equipment', 'is_active']

    def validate_name(self, value):
        user = self.context['request'].user
        clash = EquipmentProfile.objects.filter(user=user, name=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError('You already have a profile with this name.')
        return value


class GeneratorPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserGeneratorPreferences
        fields = [
            'default_equipment_profile',
            'default_duration_min',
            'preferred_split',
            'excluded_exercises',
            'excluded_muscles',
            'rounding_increment_kg',
        ]

    def validate_default_equipment_profile(self, value):
        if value is not None and value.user_id != self.context['request'].user.pk:
            raise serializers.ValidationError('Unknown equipment profile.')
        return value

    def validate_default_duration_min(self, value):
        if not 10 <= value <= 180:
            raise serializers.ValidationError('Must be between 10 and 180 minutes.')
        return value

    def validate_rounding_increment_kg(self, value):
        if value <= 0:
            raise serializers.ValidationError('Must be positive.')
        return value


class GenerateWorkoutSerializer(serializers.Serializer):
    SPLIT_CHOICES = ['auto', 'push', 'pull', 'legs', 'upper', 'lower', 'full_body']

    duration_min = serializers.IntegerField(required=False, min_value=10, max_value=180)
    equipment_profile_id = serializers.IntegerField(required=False)
    split = serializers.ChoiceField(choices=SPLIT_CHOICES, required=False, default='auto')
    save_as_routine = serializers.BooleanField(required=False, default=False)
    seed = serializers.IntegerField(required=False, min_value=0, max_value=2**31 - 1)
    """Repeats a previous result: same seed and same data give the same session"""
    routine_name = serializers.CharField(required=False, allow_blank=True, max_length=25)
