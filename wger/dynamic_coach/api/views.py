# Django
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

# Third Party
from rest_framework import (
    status,
    viewsets,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# wger
from wger.dynamic_coach.api.serializers import (
    DeckCompleteSerializer,
    DeckNewSerializer,
    EquipmentProfileSerializer,
    GenerateWorkoutSerializer,
    JiuJitsuSessionSerializer,
    PreferencesSerializer,
    TabataSerializer,
)
from wger.dynamic_coach.models import (
    DeckWorkoutSession,
    EquipmentProfile,
    JiuJitsuSession,
    UserGeneratorPreferences,
)
from wger.dynamic_coach.services import (
    bjj,
    deck_engine,
    recovery_engine,
    session_builder,
    tabata_engine,
)
from wger.dynamic_coach.services.common import (
    exercise_name,
)
from wger.exercises.models import (
    Exercise,
    Muscle,
)


class CoachView(APIView):
    permission_classes = [IsAuthenticated]


class MuscleFreshnessView(CoachView):
    def get(self, request):
        now = timezone.now()
        scores = recovery_engine.freshness_scores(request.user, now)
        muscles = [
            {
                'muscle_id': m.pk,
                'name': m.name_en or m.name,
                'freshness_pct': round(scores[m.pk] * 100, 1),
                'status': recovery_engine.status_for(scores[m.pk]),
            }
            for m in Muscle.objects.order_by('pk')
        ]
        return Response({'calculated_at': now.isoformat(), 'muscles': muscles})


class GenerateWorkoutView(CoachView):
    def post(self, request):
        ser = GenerateWorkoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            result = session_builder.build_session(request.user, **ser.validated_data)
        except session_builder.GenerationError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        for item in result['exercises']:
            if item['prescribed_weight_kg'] is not None:
                item['prescribed_weight_kg'] = float(item['prescribed_weight_kg'])
        code = status.HTTP_201_CREATED if result['routine_id'] else status.HTTP_200_OK
        return Response(result, status=code)


def _suit_payload(session):
    exercises = Exercise.objects.in_bulk(list(session.suit_mapping.values()))
    return {
        suit: {'exercise_id': ex_id, 'name': exercise_name(exercises[ex_id])}
        for suit, ex_id in session.suit_mapping.items()
    }


class DeckNewView(CoachView):
    def post(self, request):
        ser = DeckNewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            session = deck_engine.new_session(
                request.user,
                ser.validated_data['deck_size'],
                ser.validated_data['include_jokers'],
                ser.validated_data['suit_overrides'],
            )
        except deck_engine.DeckError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                'session_id': str(session.pk),
                'total_cards': len(session.card_stack),
                'cards_remaining': len(session.card_stack),
                'suit_mapping': _suit_payload(session),
            },
            status=status.HTTP_201_CREATED,
        )


class DeckDrawView(CoachView):
    def post(self, request, session_id):
        get_object_or_404(DeckWorkoutSession, pk=session_id, user=request.user)
        try:
            session, card = deck_engine.draw(session_id, request.user)
        except deck_engine.DeckError as e:
            return Response({'detail': str(e)}, status=status.HTTP_409_CONFLICT)
        exercise = Exercise.objects.get(pk=card['exercise_id'])
        return Response(
            {
                'session_id': str(session.pk),
                'card': {
                    'suit': card['suit'],
                    'face': card['face'],
                    'reps': card['reps'],
                    'exercise_id': card['exercise_id'],
                    'exercise_name': exercise_name(exercise),
                },
                'cards_remaining': len(session.card_stack),
                'cards_drawn_count': len(session.cards_drawn),
            }
        )


class DeckCompleteView(CoachView):
    def post(self, request, session_id):
        get_object_or_404(DeckWorkoutSession, pk=session_id, user=request.user)
        ser = DeckCompleteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        summary = deck_engine.complete(
            session_id, request.user, ser.validated_data.get('elapsed_seconds')
        )
        return Response(summary)


class TabataGenerateView(CoachView):
    def post(self, request):
        ser = TabataSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            return Response(tabata_engine.build_timeline(**ser.validated_data))
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class EquipmentProfileViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EquipmentProfileSerializer

    def get_queryset(self):
        return EquipmentProfile.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class PreferencesView(CoachView):
    def get(self, request):
        prefs, _ = UserGeneratorPreferences.objects.get_or_create(user=request.user)
        return Response(PreferencesSerializer(prefs, context={'request': request}).data)

    def put(self, request):
        prefs, _ = UserGeneratorPreferences.objects.get_or_create(user=request.user)
        ser = PreferencesSerializer(
            prefs, data=request.data, partial=True, context={'request': request}
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class JiuJitsuViewSet(viewsets.ModelViewSet):
    """Logging a session also logs its muscle load for the recovery model"""

    permission_classes = [IsAuthenticated]
    serializer_class = JiuJitsuSessionSerializer
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_queryset(self):
        return JiuJitsuSession.objects.filter(user=self.request.user)

    @transaction.atomic
    def perform_create(self, serializer):
        session = serializer.save(user=self.request.user)
        bjj.log_session(session)

    @transaction.atomic
    def perform_destroy(self, instance):
        # wger
        from wger.dynamic_coach.models import ConditioningLoad

        ConditioningLoad.objects.filter(
            user=instance.user, source=ConditioningLoad.SOURCE_BJJ, source_ref=str(instance.pk)
        ).delete()
        instance.delete()
