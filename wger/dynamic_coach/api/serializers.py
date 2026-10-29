# Standard Library
import datetime

# Django
from django.utils import timezone

# Third Party
from rest_framework import serializers

# wger
from wger.dynamic_coach.models import (
    EquipmentProfile,
    JiuJitsuSession,
    UserGeneratorPreferences,
)


class GenerateWorkoutSerializer(serializers.Serializer):
    duration_min = serializers.IntegerField(min_value=20, max_value=120, required=False)
    equipment_profile_id = serializers.IntegerField(required=False, allow_null=True)
    split = serializers.ChoiceField(
        choices=['auto', 'push', 'pull', 'legs', 'upper', 'lower', 'full_body'],
        default='auto',
    )
    save_as_routine = serializers.BooleanField(default=True)
    routine_name = serializers.CharField(max_length=25, required=False)


class DeckNewSerializer(serializers.Serializer):
    deck_size = serializers.IntegerField(min_value=1, max_value=52, default=52)
    include_jokers = serializers.BooleanField(default=False)
    suit_overrides = serializers.DictField(
        child=serializers.IntegerField(), required=False, default=dict
    )


class DeckCompleteSerializer(serializers.Serializer):
    elapsed_seconds = serializers.IntegerField(min_value=0, max_value=24 * 3600, required=False)


class TabataSerializer(serializers.Serializer):
    work_sec = serializers.IntegerField(min_value=1, max_value=600, default=20)
    rest_sec = serializers.IntegerField(min_value=0, max_value=600, default=10)
    rounds_per_cycle = serializers.IntegerField(min_value=1, max_value=50, default=8)
    total_cycles = serializers.IntegerField(min_value=1, max_value=20, default=1)
    cycle_rest_sec = serializers.IntegerField(min_value=0, max_value=1800, default=60)
    exercise_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list, max_length=50
    )


class EquipmentProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentProfile
        fields = ['id', 'name', 'equipment', 'is_active']


class PreferencesSerializer(serializers.ModelSerializer):
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
        if value and value.user_id != self.context['request'].user.pk:
            raise serializers.ValidationError('Not your equipment profile')
        return value


class JiuJitsuSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = JiuJitsuSession
        fields = ['id', 'date', 'kind', 'gi', 'duration_min', 'rpe', 'rounds', 'notes']
        read_only_fields = ['id']

    def validate_date(self, value):
        if value > timezone.now() + datetime.timedelta(minutes=5):
            raise serializers.ValidationError('Date is in the future')
        return value
