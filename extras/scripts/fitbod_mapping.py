#!/usr/bin/env python3
"""
Suggest a mapping from Fitbod export exercise names to wger exercises.

Writes a CSV for hand review. Nothing here touches the database.

Optionally pass --catalogue DIR (a clone of the openGym repo's catalogue folder, the one
that holds exercises/) to take muscles, equipment and category of unmatched exercises
from its plain-text entries instead of guessing from the name. Only exercises/*.json is
read; the media is never opened.

Usage:
    docker exec wger-db psql -U wger -d wger -At -F$'\\t' -c "<see NAMES_SQL below>" > wger_names.tsv
    extras/scripts/fitbod_mapping.py [--catalogue DIR] WorkoutExport.csv wger_names.tsv > mapping.csv

Columns of the output:
    action        map | create | skip   (edit this). Only exact name matches are
                  suggested as map; everything else is create, with the closest
                  existing exercises listed in alternatives
    fitbod_name   exercise name in the export
    sets          number of sets in the export (review the big ones first)
    wger_id       wger exercise id when action is map (edit this)
    wger_name     name of that exercise, for reference
    confidence    exact | likely | weak | none
    alternatives  other candidates as "id:name" separated by " | "
    category, primary, secondary, equipment
                  guessed from the name for rows with action "create" (muscles and
                  equipment separated by ";"); edit them, they decide what the
                  generator may pick
    tag_source    where those tags came from: catalogue:<id> or "guessed from name"
"""

# Standard Library
import csv
import difflib
import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

_TAGS = Path(__file__).resolve().parents[2] / 'wger' / 'dynamic_coach' / 'fitbod' / 'tags.py'
_spec = importlib.util.spec_from_file_location('fitbod_tags', _TAGS)
fitbod_tags = importlib.util.module_from_spec(_spec)
sys.modules['fitbod_tags'] = fitbod_tags
_spec.loader.exec_module(fitbod_tags)
_spec = importlib.util.spec_from_file_location('fitbod_catalogue', _TAGS.with_name('catalogue.py'))
fitbod_catalogue = importlib.util.module_from_spec(_spec)
sys.modules['fitbod_catalogue'] = fitbod_catalogue
_spec.loader.exec_module(fitbod_catalogue)

NAMES_SQL = (
    "select t.exercise_id, t.name, 'name' from exercises_translation t "
    "join core_language l on l.id=t.language_id where l.short_name='en' "
    "union all select t.exercise_id, a.alias, 'alias' from exercises_alias a "
    "join exercises_translation t on t.id=a.translation_id "
    "join core_language l on l.id=t.language_id where l.short_name='en';"
)

SYNONYMS = {
    'db': 'dumbbell', 'dumbbells': 'dumbbell', 'bb': 'barbell', 'barbells': 'barbell',
    'bicep': 'biceps', 'tricep': 'triceps', 'lats': 'lat', 'abs': 'ab',
    'pulldown': 'pulldown', 'pull-down': 'pulldown', 'pullup': 'pullup', 'pullups': 'pullup',
    'ups': 'up', 'situp': 'sit up', 'raises': 'raise', 'curls': 'curl', 'presses': 'press',
    'squats': 'squat', 'lunges': 'lunge', 'rows': 'row', 'flyes': 'fly', 'flys': 'fly',
    'flye': 'fly', 'extensions': 'extension', 'crunches': 'crunch', 'kickbacks': 'kickback',
    'shrugs': 'shrug', 'dips': 'dip', 'deadlifts': 'deadlift', 'thrusts': 'thrust',
    'ez': 'ezbar', 'ez-bar': 'ezbar', 'sz-bar': 'ezbar', 'szbar': 'ezbar', 'sz': 'ezbar',
    'kb': 'kettlebell', 'kettlebells': 'kettlebell',
}
EQUIPMENT_WORDS = {
    'barbell', 'dumbbell', 'cable', 'machine', 'kettlebell', 'band', 'trx', 'smith',
    'ezbar', 'landmine', 'sled', 'hammerstrength',
}
FILLER = {'the', 'a', 'an', 'with', 'on', 'of', 'to', 'using'}


def tokens(name):
    text = name.lower().replace('-', ' ').replace('/', ' ')
    text = re.sub(r'\(.*?\)', ' ', text)
    text = re.sub(r"[^a-z0-9 ]", '', text)
    out = []
    for word in text.split():
        word = SYNONYMS.get(word, word)
        if word.endswith('s') and len(word) > 3 and not word.endswith('ss'):
            word = word[:-1]
        out.extend(SYNONYMS.get(word, word).split())
    return [w for w in out if w not in FILLER]


def score(a_tokens, b_tokens):
    a, b = set(a_tokens), set(b_tokens)
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    order = difflib.SequenceMatcher(None, ' '.join(sorted(a)), ' '.join(sorted(b))).ratio()
    value = 0.6 * jaccard + 0.4 * order
    # a different piece of equipment is a different exercise
    ea, eb = a & EQUIPMENT_WORDS, b & EQUIPMENT_WORDS
    if ea and eb and not (ea & eb):
        value -= 0.4
    elif ea and not eb:
        value -= 0.1
    return value


def main(export_path, names_path, catalogue_dir=None):
    entries = fitbod_catalogue.load(catalogue_dir) if catalogue_dir else {}
    counts = Counter()
    with open(export_path, newline='') as handle:
        for row in csv.DictReader(handle):
            counts[row['Exercise'].strip()] += 1

    wger = []
    with open(names_path) as handle:
        for line in handle:
            exercise_id, name, _kind = line.rstrip('\n').split('\t')
            wger.append((int(exercise_id), name, tokens(name)))

    writer = csv.writer(sys.stdout)
    writer.writerow(
        ['action', 'fitbod_name', 'sets', 'wger_id', 'wger_name', 'confidence', 'alternatives',
         'category', 'primary', 'secondary', 'equipment', 'tag_source']
    )
    for name, sets in counts.most_common():
        mine = tokens(name)
        # best score first; on ties prefer upstream exercises over "(Custom)" ones, then lower ids
        scored = sorted(
            ((round(score(mine, t), 3), '(Custom)' not in n, -i, n) for i, n, t in wger),
            reverse=True,
        )
        scored = [(v, -neg_id, n) for v, _, neg_id, n in scored]
        # one entry per exercise
        seen, best = set(), []
        for value, exercise_id, wger_name in scored:
            if exercise_id not in seen:
                seen.add(exercise_id)
                best.append((value, exercise_id, wger_name))
            if len(best) == 4:
                break
        top = best[0]
        if sorted(mine) == sorted(tokens(top[2])):
            confidence, action = 'exact', 'map'
        elif top[0] >= 0.8:
            # close, but "Barbell Bench Press" once matched "Pin Bench Press BB": a person decides
            confidence, action = 'likely', 'create'
        elif top[0] >= 0.6:
            confidence, action = 'weak', 'create'
        else:
            confidence, action = 'none', 'create'
        tags = source = None
        if action == 'create':
            match = fitbod_catalogue.find(entries, name) if entries else None
            tags = fitbod_catalogue.to_tags(match) if match else fitbod_tags.infer_tags(name)
            source = f'catalogue:{match.id}' if match else 'guessed from name'
        writer.writerow([
            action, name, sets,
            top[1] if action == 'map' else '', top[2] if action == 'map' else '',
            confidence,
            ' | '.join(f'{i}:{n}' for _, i, n in (best if action == 'create' else best[1:])),
            tags.category if tags else '',
            ';'.join(tags.primary) if tags else '',
            ';'.join(tags.secondary) if tags else '',
            ';'.join(tags.equipment) if tags else '',
            source or '',
        ])


if __name__ == '__main__':
    args = sys.argv[1:]
    catalogue_dir = None
    if args[:1] == ['--catalogue'] and len(args) >= 2:
        catalogue_dir, args = args[1], args[2:]
    if len(args) != 2:
        sys.exit(__doc__)
    main(args[0], args[1], catalogue_dir)
