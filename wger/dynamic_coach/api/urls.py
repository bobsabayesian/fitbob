# Django
from django.urls import path

# Third Party
from rest_framework import routers

# wger
from wger.dynamic_coach.api import views


router = routers.SimpleRouter()
router.register('equipment-profiles', views.EquipmentProfileViewSet, basename='coach-equipment')
router.register('bjj', views.JiuJitsuViewSet, basename='coach-bjj')

urlpatterns = [
    path('muscle-freshness/', views.MuscleFreshnessView.as_view(), name='coach-freshness'),
    path('generate-workout/', views.GenerateWorkoutView.as_view(), name='coach-generate'),
    path('preferences/', views.PreferencesView.as_view(), name='coach-preferences'),
    path('deck/new/', views.DeckNewView.as_view(), name='coach-deck-new'),
    path('deck/<uuid:session_id>/draw/', views.DeckDrawView.as_view(), name='coach-deck-draw'),
    path(
        'deck/<uuid:session_id>/complete/',
        views.DeckCompleteView.as_view(),
        name='coach-deck-complete',
    ),
    path('tabata/generate/', views.TabataGenerateView.as_view(), name='coach-tabata'),
] + router.urls
