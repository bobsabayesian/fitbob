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

# Django
from django.contrib.auth.models import User
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

# wger
from wger.dynamic_coach.fitbod.importer import (
    ImportError_,
    read_export,
    read_mapping,
    run_import,
)


class Command(BaseCommand):
    help = 'Import a Fitbod CSV export as workout sessions and logs (see fitbod/importer.py)'

    def add_arguments(self, parser):
        parser.add_argument('export', help='WorkoutExport.csv from Fitbod')
        parser.add_argument('mapping', help='reviewed mapping CSV (extras/scripts/fitbod_mapping.py)')
        parser.add_argument('--user', required=True, help='username to import into')
        parser.add_argument('--weight-unit', choices=['auto', 'kg', 'lb'], default='auto')
        parser.add_argument('--replace', action='store_true', help='redo an earlier import')
        parser.add_argument('--dry-run', action='store_true', help='do everything, then roll back')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options['user'])
        except User.DoesNotExist:
            raise CommandError(f'No user {options["user"]!r}')
        try:
            report = run_import(
                user,
                read_export(options['export']),
                read_mapping(options['mapping']),
                weight_unit=options['weight_unit'],
                replace=options['replace'],
                dry_run=options['dry_run'],
            )
        except ImportError_ as error:
            raise CommandError(str(error))

        for key, value in report.items():
            self.stdout.write(f'{key}: {value}')
        if report['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run: nothing was saved.'))
