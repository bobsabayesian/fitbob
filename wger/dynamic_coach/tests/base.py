# Standard Library
import datetime
from decimal import Decimal

# Django
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

# Third Party
from rest_framework.test import APIClient

# wger
from wger.core.models import (
    Language,
    License,
)
from wger.exercises.models import (
    Equipment,
    Exercise,
    ExerciseCategory,
    Muscle,
    Translation,
)
from wger.manager.models import WorkoutLog


MUSCLE_NAMES = [
    'Pectoralis major',
    'Anterior deltoid',
    'Triceps brachii',
    'Latissimus dorsi',
    'Trapezius',
    'Biceps brachii',
    'Quadriceps femoris',
    'Gluteus maximus',
    'Biceps femoris',
    'Rectus abdominis',
]


class CoachTestCase(TestCase):
    fixtures = (
        'setting_repetition_units',
        'setting_weight_units',
        'initial_trophies',
        'test-languages',
        'test-licenses',
    )

    def setUp(self):
        self.user = User.objects.create_user('lifter', 'lifter@example.com', 'pw')
        self.other = User.objects.create_user('other', 'other@example.com', 'pw')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.lang = Language.objects.get(short_name='en')
        self.license = License.objects.first()
        self.category = ExerciseCategory.objects.create(name='Cat')
        self.muscles = {
            n: Muscle.objects.create(name=n, name_en=n, is_front=True) for n in MUSCLE_NAMES
        }
        self.barbell = Equipment.objects.create(name='Barbell')
        self.dumbbell = Equipment.objects.create(name='Dumbbell')
        self.none = Equipment.objects.create(name='none (bodyweight exercise)')

    def make_exercise(self, name, primary, secondary=(), equipment=(), variation=None):
        e = Exercise.objects.create(
            category=self.category, license=self.license, variation_group=variation
        )
        e.muscles.set([self.muscles[m] for m in primary])
        e.muscles_secondary.set([self.muscles[m] for m in secondary])
        e.equipment.set(equipment)
        Translation.objects.create(exercise=e, language=self.lang, name=name, license=self.license)
        return e

    def log_set(self, exercise, weight, reps, when=None, user=None):
        return WorkoutLog.objects.create(
            user=user or self.user,
            exercise=exercise,
            date=when or timezone.now(),
            weight=Decimal(str(weight)),
            repetitions=Decimal(str(reps)),
        )

    def hours_ago(self, h):
        return timezone.now() - datetime.timedelta(hours=h)

    def make_push_catalog(self):
        self.bench = self.make_exercise(
            'Bench Press',
            ['Pectoralis major'],
            ['Anterior deltoid', 'Triceps brachii'],
            [self.barbell],
        )
        self.ohp = self.make_exercise(
            'Overhead Press',
            ['Anterior deltoid'],
            ['Triceps brachii', 'Trapezius'],
            [self.barbell],
        )
        self.fly = self.make_exercise('Dumbbell Fly', ['Pectoralis major'], [], [self.dumbbell])
        self.pushup = self.make_exercise(
            'Push-up', ['Pectoralis major'], ['Triceps brachii', 'Anterior deltoid'], [self.none]
        )
        self.pushdown = self.make_exercise('Pushdown', ['Triceps brachii'], [], [])
