"""Interval timeline compilation"""

# wger
from wger.exercises.models import Exercise

# Local
from .common import exercise_name


PREPARE_SEC = 10


def build_timeline(
    work_sec: int,
    rest_sec: int,
    rounds_per_cycle: int,
    total_cycles: int,
    cycle_rest_sec: int,
    exercise_ids: list[int] | None = None,
    prepare_sec: int = PREPARE_SEC,
) -> dict:
    """
    Event queue for a frontend timer.

    Every work interval is followed by a rest (a classic Tabata cycle is 8 x 30s = 4:00),
    cycles are separated by an additional cycle rest, and the last cycle has no trailing
    rest. Exercises rotate per round and restart with every cycle.
    """
    exercise_ids = exercise_ids or []
    names = {}
    if exercise_ids:
        exercises = Exercise.objects.in_bulk(exercise_ids)
        missing = [i for i in exercise_ids if i not in exercises]
        if missing:
            raise ValueError(f'Unknown exercise ids: {missing}')
        names = {pk: exercise_name(e) for pk, e in exercises.items()}

    timeline = []
    if prepare_sec:
        timeline.append({'type': 'prepare', 'duration_sec': prepare_sec, 'label': 'Get Ready'})

    for cycle in range(1, total_cycles + 1):
        for rnd in range(1, rounds_per_cycle + 1):
            event = {'type': 'work', 'duration_sec': work_sec, 'round': rnd, 'cycle': cycle}
            if exercise_ids:
                ex_id = exercise_ids[(rnd - 1) % len(exercise_ids)]
                event.update(exercise_id=ex_id, exercise_name=names[ex_id])
            else:
                event['label'] = 'Work'
            timeline.append(event)

            is_last_of_all = cycle == total_cycles and rnd == rounds_per_cycle
            if rest_sec and not is_last_of_all:
                timeline.append(
                    {
                        'type': 'rest',
                        'duration_sec': rest_sec,
                        'round': rnd,
                        'cycle': cycle,
                        'label': 'Rest',
                    }
                )

        if cycle < total_cycles and cycle_rest_sec:
            timeline.append(
                {
                    'type': 'cycle_rest',
                    'duration_sec': cycle_rest_sec,
                    'cycle': cycle,
                    'label': 'Cycle Complete - Long Rest',
                }
            )

    timeline.append({'type': 'complete', 'duration_sec': 0, 'label': 'Session Complete'})
    return {
        'total_duration_sec': sum(e['duration_sec'] for e in timeline),
        'timeline': timeline,
    }
