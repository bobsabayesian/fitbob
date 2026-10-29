"""
Jiu jitsu as conditioning: converts a session into muscle load for the recovery model.
Mat time is roughly distributed over the pulling muscles, core and hips.
"""

# wger
from wger.dynamic_coach.models import (
    ConditioningLoad,
    JiuJitsuSession,
)
from wger.dynamic_coach.services import recovery_engine
from wger.dynamic_coach.services.common import muscle_key
from wger.exercises.models import Muscle


# Hard sets-equivalent per minute at RPE 10
KIND_RATE = {
    JiuJitsuSession.KIND_DRILLING: 0.10,
    JiuJitsuSession.KIND_ROLLING: 0.20,
    JiuJitsuSession.KIND_COMPETITION: 0.26,
}
GI_PULL_BONUS = 1.15

# Share of the session's total sets-equivalent that lands on a muscle
MUSCLE_SHARE = {
    'latissimus dorsi': 0.50,
    'biceps brachii': 0.45,
    'brachialis': 0.30,
    'trapezius': 0.30,
    'rectus abdominis': 0.35,
    'obliquus externus abdominis': 0.35,
    'gluteus maximus': 0.30,
    'quadriceps femoris': 0.25,
    'biceps femoris': 0.25,
    'anterior deltoid': 0.20,
    'pectoralis major': 0.15,
    'serratus anterior': 0.15,
    'triceps brachii': 0.10,
    'gastrocnemius': 0.05,
}
GI_PULL_MUSCLES = {'latissimus dorsi', 'biceps brachii', 'brachialis', 'trapezius'}


def session_loads(session: JiuJitsuSession) -> dict[int, float]:
    total = session.duration_min * KIND_RATE[session.kind] * (session.rpe / 10)
    loads: dict[int, float] = {}
    for muscle in Muscle.objects.all():
        key = muscle_key(muscle)
        share = MUSCLE_SHARE.get(key)
        if share is None:
            continue
        if session.gi and key in GI_PULL_MUSCLES:
            share *= GI_PULL_BONUS
        loads[muscle.pk] = total * share
    return loads


def log_session(session: JiuJitsuSession) -> ConditioningLoad:
    return recovery_engine.log_conditioning_load(
        session.user,
        ConditioningLoad.SOURCE_BJJ,
        session_loads(session),
        source_ref=str(session.pk),
        logged_at=session.date,
    )
