"""Small helpers shared by the services"""

# wger
from wger.exercises.models import (
    Exercise,
    Muscle,
)


def muscle_key(muscle: Muscle) -> str:
    """Lowercase English name, used to map muscles to splits and sport profiles"""
    return (muscle.name_en or muscle.name).strip().lower()


def exercise_name(exercise: Exercise) -> str:
    translation = exercise.get_translation()
    return translation.name if translation else f'Exercise {exercise.pk}'


def is_bodyweight(exercise: Exercise) -> bool:
    """True if the exercise needs no equipment other than 'none (bodyweight)'"""
    return all(
        'none' in (e.name or '').lower() or 'bodyweight' in (e.name or '').lower()
        for e in exercise.equipment.all()
    )


def exercise_muscle_loads(exercise: Exercise, sets: float, secondary_factor: float = 0.4) -> dict:
    """Muscle id -> effective sets (primary 1.0, secondary 0.4) for a number of sets"""
    out: dict[int, float] = {}
    for muscle in exercise.muscles_secondary.all():
        out[muscle.pk] = out.get(muscle.pk, 0.0) + sets * secondary_factor
    for muscle in exercise.muscles.all():
        out[muscle.pk] = out.get(muscle.pk, 0.0) + sets
    return out
