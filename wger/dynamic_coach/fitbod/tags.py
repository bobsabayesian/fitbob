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

"""
Guess muscles, category and equipment of an exercise from its name.

Fitbod exports carry names only, but the generator and the freshness model need
muscles and equipment. These are heuristics for a first pass that a person
reviews; rules are tried in order and the first match wins. Pure Python, no Django.
"""

# Standard Library
import re
from dataclasses import dataclass, field


# Muscle ids of the standard wger fixtures
MUSCLES = {
    'biceps': 1, 'shoulders': 2, 'serratus': 3, 'chest': 4, 'triceps': 5, 'abs': 6,
    'calves': 7, 'glutes': 8, 'traps': 9, 'quads': 10, 'hamstrings': 11, 'lats': 12,
    'brachialis': 13, 'obliques': 14, 'soleus': 15, 'lower_back': 16,
}
# Exercise category ids of the standard wger fixtures
CATEGORIES = {
    'arms': 8, 'legs': 9, 'abs': 10, 'chest': 11, 'back': 12, 'shoulders': 13,
    'calves': 14, 'cardio': 15,
}

# Equipment that exists in a stock wger install; the rest is created on import
STOCK_EQUIPMENT = {
    'Barbell', 'SZ-Bar', 'Dumbbell', 'Gym mat', 'Swiss Ball', 'Pull-up bar',
    'none (bodyweight exercise)', 'Bench', 'Incline bench', 'Kettlebell',
    'Resistance band', 'Cable machine',
}
BODYWEIGHT = 'none (bodyweight exercise)'


@dataclass
class Tags:
    category: str
    primary: list = field(default_factory=list)
    secondary: list = field(default_factory=list)
    equipment: list = field(default_factory=list)


# (pattern, category, primary, secondary): first match wins, most specific first
RULES = [
    (r'walking lunge', 'legs', ['quads', 'glutes'], ['hamstrings']),
    (r'hammerstrength.*row|iso row|high row|machine row', 'back', ['lats'], ['biceps', 'traps']),
    (r'neck', 'back', ['traps'], []),
    (r'pallof|toes to bar|stir the pot|turkish|bear crawl|iron cross|core rotation', 'abs', ['abs'], ['obliques']),
    (r'butt kick|slam|jumping|tuck jump|depth jump', 'cardio', [], []),
    (r'sumo high pull', 'shoulders', ['traps'], ['shoulders']),
    # cardio and conditioning
    (r'\b(run|running|jog|cycling|bike|rowing|walking|hiking|elliptical|stair|treadmill|jump rope|jumping jack|high knee|air bike|mountain climber|burpee)\b', 'cardio', [], []),
    (r'\b(farmer|sled|battle rope)', 'cardio', ['traps'], ['glutes', 'quads']),
    # calves
    (r'\bcalf\b', 'calves', ['calves'], ['soleus']),
    # lower body
    (r'hip thrust|glute bridge|hip bridge|frog pump|glute kickback|single leg kickback|kickback machine|donkey|hip extension|clam|fire hydrant', 'legs', ['glutes'], ['hamstrings']),
    (r'hip abduct|hip adduct|abductor|adductor', 'legs', ['glutes'], []),
    (r'romanian|stiff leg|good morning|pull through|rack pull|trap bar deadlift|deadlift', 'legs', ['hamstrings', 'glutes'], ['lower_back']),
    (r'glute ham|leg curl|hamstring', 'legs', ['hamstrings'], ['glutes']),
    (r'leg extension', 'legs', ['quads'], []),
    (r'leg press|hack squat|squat|lunge|step up|split squat|wall sit|sissy|pistol|box jump|jump squat|lunge jump', 'legs', ['quads', 'glutes'], ['hamstrings']),
    # abs and core
    (r'twist|woodchop|wood chop|side bend|oblique|side bridge|side plank|windmill', 'abs', ['obliques'], ['abs']),
    (r'crunch|sit up|situp|leg raise|knee raise|flutter|scissor|leg pull in|dead bug|plank|v up|toe touch|heel touch|ab wheel|rollout|stomach vacuum|hollow|mountain', 'abs', ['abs'], ['obliques']),
    (r'back extension|superman|hyperextension|bird dog', 'back', ['lower_back'], ['glutes', 'hamstrings']),
    # rear delts and upper back (before chest, they share the word fly)
    (r'face pull|rear delt|reverse fly|deltoid fly|back fly|rear deltoid|shoulder squeeze|y raise|y-raise|w raise|scaption', 'shoulders', ['shoulders'], ['traps']),
    # chest
    (r'tricep|triceps|skullcrusher|skull crusher|pushdown|push down|close.grip bench|jm press|dip\b|bench dip|kickback', 'arms', ['triceps'], ['shoulders', 'chest']),
    (r'bench press|chest press|push up|pushup|fly\b|flye|pec deck|crossover|cross over|chest|svend|floor press|hammerstrength', 'chest', ['chest'], ['triceps', 'shoulders']),
    # shoulders
    (r'shrug|upright row', 'back', ['traps'], ['shoulders']),
    (r'shoulder press|overhead press|push press|military|arnold|lateral raise|front raise|side lateral|plate raise|arm circle|pike|handstand|landmine press|thruster|halo', 'shoulders', ['shoulders'], ['triceps']),
    # arms
    (r'curl|chin up|hammer', 'arms', ['biceps'], ['brachialis']),
    # back
    (r'pulldown|pull down|pull up|pullup|lat |straight.arm|pullover', 'back', ['lats'], ['biceps']),
    (r'row|shotgun|gorilla|seal|pendlay|t-bar', 'back', ['lats'], ['biceps', 'traps']),
    (r'swing|clean|snatch|sit up and press', 'legs', ['glutes'], ['hamstrings', 'lower_back']),
]


# Fitbod names often leave the obvious equipment out; used when nothing else matched
FALLBACK_EQUIPMENT = [
    (r'hammer curl|curl to shoulder press|side lateral|front raise|rear delt raise|lateral raise', 'Dumbbell'),
    (r'plate raise', 'Weight plate'),
    (r'goblet|gorilla', 'Kettlebell'),
    (r'push press|upright row|good morning|bent over row|pendlay', 'Barbell'),
    (r'shotgun row', 'Cable machine'),
    (r'lying hamstring', 'Machine'),
]


def _equipment(name):
    n = name.lower()
    found = []

    def add(item):
        if item not in found:
            found.append(item)

    if re.search(r'\bez.?bar|\bsz.?bar', n):
        add('SZ-Bar')
    if re.search(r'\bbarbell\b|back squat|front squat|\bdeadlift\b|bench press|overhead press|hip thrust', n) and not re.search(
        r'dumbbell|kettlebell|machine|cable|smith|trx|band|bodyweight|\bsingle leg\b|trap bar|landmine', n
    ):
        add('Barbell')
    if 'dumbbell' in n:
        add('Dumbbell')
    if 'kettlebell' in n:
        add('Kettlebell')
    if 'cable' in n or re.search(r'pulldown|pull down|v-bar|lat pull|face pull|straight.arm|seated row', n):
        add('Cable machine')
    if 'band' in n:
        add('Resistance band')
    if 'trx' in n:
        add('TRX')
    if 'smith' in n:
        add('Smith machine')
    if re.search(r'machine|hammerstrength|leg press|leg extension|leg curl|pec deck|calf press|hack squat|assisted|glute kickback', n):
        add('Machine')
    if 'landmine' in n:
        add('Landmine')
    if 'trap bar' in n:
        add('Trap bar')
    if re.search(r'\bsled\b', n):
        add('Sled')
    if 'medicine ball' in n:
        add('Medicine ball')
    if 'balance trainer' in n:
        add('Balance trainer')
    if re.search(r'\bbox\b|step up', n):
        add('Box')
    if 'jump rope' in n:
        add('Jump rope')
    if re.search(r'pull up|pullup|chin up|hanging|scapular', n) and 'assisted' not in n:
        add('Pull-up bar')
    if re.search(r'bench press|bench dip|hip thrust|skullcrusher|skull crusher|incline|decline|bench', n) and 'push up' not in n:
        add('Incline bench' if 'incline' in n and 'dumbbell' in n else 'Bench')
    if re.search(r'plank|crunch|sit up|bridge|dead bug|flutter|scissor|clam|superman', n) and not found:
        add('Gym mat')
    if not found:
        for pattern, item in FALLBACK_EQUIPMENT:
            if re.search(pattern, n):
                add(item)
                break
    if not found:
        add(BODYWEIGHT)
    return found


def infer_tags(name: str) -> Tags:
    """Best guess for the category, muscles and equipment of an exercise name"""
    lowered = name.lower().replace('-', ' ')
    for pattern, category, primary, secondary in RULES:
        if re.search(pattern, lowered):
            return Tags(category, list(primary), list(secondary), _equipment(name))
    return Tags('legs', [], [], _equipment(name))
