"""
Django management command to ingest logistics data from Excel files.
Supports both On-Site Operator Logs and Car Operator Logs.
"""

import pandas as pd
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from logistics.models import Machine, Operator, VisitLog, CarLog
from logistics.utils import resolve_machine, clean_numeric_value, get_or_create_operator


class Command(BaseCommand):
    help = 'Ingest logistics data from Excel files into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            'file_path',
            type=str,
            help='Path to the Excel file to ingest'
        )
        parser.add_argument(
            '--type',
            type=str,
            choices=['onsite', 'car'],
            default='onsite',
            help='Type of log file: "onsite" for On-Site Operator Logs, "car" for Car Operator Logs'
        )
        parser.add_argument(
            '--sheet',
            type=str,
            default=None,
            help='Sheet name to read (optional, reads first sheet by default)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving to database (preview mode)'
        )
        parser.add_argument(
            '--skip-ai',
            action='store_true',
            help='Skip AI fallback for machine resolution'
        )

    def handle(self, *args, **options):
        file_path = options['file_path']
        log_type = options['type']
        sheet_name = options['sheet']
        dry_run = options['dry_run']
        skip_ai = options['skip_ai']

        self.stdout.write(f"Reading file: {file_path}")
        self.stdout.write(f"Log type: {log_type}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be saved"))

        try:
            # Read Excel file
            df = pd.read_excel(
                file_path,
                sheet_name=sheet_name if sheet_name else 0,
                engine='openpyxl'
            )
            # Normalize column names
            df.columns = df.columns.astype(str).str.strip()
            self.stdout.write(f"Loaded {len(df)} rows from Excel")
            self.stdout.write(f"Columns: {list(df.columns)}")

        except FileNotFoundError:
            raise CommandError(f"File not found: {file_path}")
        except Exception as e:
            raise CommandError(f"Error reading file: {e}")

        if log_type == 'onsite':
            self._process_onsite_logs(df, dry_run, skip_ai)
        else:
            self._process_car_logs(df, dry_run)

    def _process_onsite_logs(self, df, dry_run, skip_ai):
        """Process On-Site Operator Logs."""
        # Expected columns mapping (Arabic -> English)
        # Adjust these based on actual column names in the Excel
        column_mapping = {
            'timestamp': ['Timestamp', 'timestamp', 'التوقيت', 'التاريخ'],
            'operator': ['" الأسم " ثلاثي', 'Operator Name', 'الأسم ثلاثي', 'Name', 'Operator'],
            'date': ['Date', 'التاريخ', 'date'],
            'machine': ['ما هو أسم الماكينه؟', 'Machine Name', 'Machine', 'machine'],
            'transactions': ['عدد المعامت خلال اليوم', 'Transactions', 'No of Transactions', 'transactions', 'عدد المعاملات'],
            'voids': ['في اليوم VOIDS عدد ال', 'Voids', 'voids', 'Void', 'الفويد'],
            'comments': ['تعليقات', 'Comments', 'comments', 'Notes', 'ملاحظات']
        }

        # Find actual column names
        columns = self._find_columns(df, column_mapping)
        
        created_count = 0
        skipped_count = 0
        unresolved_machines = []

        for index, row in df.iterrows():
            try:
                # Parse timestamp
                timestamp = self._parse_timestamp(row, columns)
                if not timestamp:
                    self.stdout.write(self.style.WARNING(f"Row {index + 2}: Skipping - no valid timestamp"))
                    skipped_count += 1
                    continue

                # Get operator
                operator_name = self._get_column_value(row, columns.get('operator'))
                operator, _ = get_or_create_operator(operator_name) if operator_name else (None, False)

                # Get machine (with intelligent resolution)
                raw_machine_name = self._get_column_value(row, columns.get('machine'))
                if not raw_machine_name:
                    self.stdout.write(self.style.WARNING(f"Row {index + 2}: Skipping - no machine name"))
                    skipped_count += 1
                    continue

                machine = resolve_machine(raw_machine_name, use_ai_fallback=not skip_ai)
                if not machine:
                    # Create new machine if not resolved
                    from logistics.models import Machine
                    clean_name = str(raw_machine_name).strip()
                    machine, created = Machine.objects.get_or_create(name=clean_name)
                    if created:
                        self.stdout.write(self.style.SUCCESS(f"Row {index + 2}: Created new machine '{clean_name}'"))
                    else:
                        self.stdout.write(self.style.WARNING(f"Row {index + 2}: Using existing machine '{clean_name}' (exact match failed previously)"))

                # Get numeric values
                transactions = clean_numeric_value(self._get_column_value(row, columns.get('transactions')))
                voids = clean_numeric_value(self._get_column_value(row, columns.get('voids')))
                comments = self._get_column_value(row, columns.get('comments')) or ''

                if not dry_run:
                    # Check for duplicates
                    if not VisitLog.objects.filter(machine=machine, timestamp=timestamp).exists():
                        VisitLog.objects.create(
                            timestamp=timestamp,
                            operator=operator,
                            machine=machine,
                            transactions=transactions,
                            voids=voids,
                            comments=str(comments),
                            raw_machine_name=raw_machine_name
                        )
                        created_count += 1
                        self.stdout.write(f"Row {index + 2}: {machine.name} - T:{transactions}, V:{voids}")
                    else:
                        self.stdout.write(self.style.WARNING(f"Row {index + 2}: Duplicate log skipped"))
                        skipped_count += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Row {index + 2}: Error - {e}"))
                skipped_count += 1

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{'[DRY RUN] ' if dry_run else ''}Processed: {created_count} records"))
        self.stdout.write(self.style.WARNING(f"Skipped: {skipped_count} records"))
        
        if unresolved_machines:
            unique_unresolved = list(set(unresolved_machines))
            self.stdout.write(self.style.WARNING(f"\nUnresolved machines ({len(unique_unresolved)}):"))
            for name in unique_unresolved[:10]:
                self.stdout.write(f"  - {name}")
            if len(unique_unresolved) > 10:
                self.stdout.write(f"  ... and {len(unique_unresolved) - 10} more")

    def _process_car_logs(self, df, dry_run):
        """Process Car Operator Logs."""
        column_mapping = {
            'timestamp': ['Timestamp', 'timestamp', 'التوقيت', 'التاريخ'],
            'driver': ['" الأسم " ثلاثي', 'Driver Name', 'driver', 'السائق', 'Name', 'Operator Name'],
            'route_list': ['ما هو أسم الأماكن اللتي ستمر عليها اليوم؟', 'Route List', 'Routes', 'route_list', 'المسار'],
            'issues': [' أي مشاكل حدثت من بدايه الرحله حتي النهايه وضحها هنا بالتفاصيل', 'Issues', 'issues', 'Problems', 'المشاكل'],
            'photos': ['ضع صور لجميع الأوراق الخاصه بلرحله', 'Photos', 'photos', 'Photo Links', 'الصور']
        }

        columns = self._find_columns(df, column_mapping)
        created_count = 0
        skipped_count = 0

        for index, row in df.iterrows():
            try:
                timestamp = self._parse_timestamp(row, columns)
                if not timestamp:
                    self.stdout.write(self.style.WARNING(f"Row {index + 2}: Skipping - no valid timestamp"))
                    skipped_count += 1
                    continue

                driver_name = self._get_column_value(row, columns.get('driver'))
                driver, _ = get_or_create_operator(driver_name, is_driver=True) if driver_name else (None, False)

                route_list = self._get_column_value(row, columns.get('route_list')) or ''
                issues = self._get_column_value(row, columns.get('issues')) or ''
                photos = self._get_column_value(row, columns.get('photos')) or ''

                if not dry_run:
                    if not CarLog.objects.filter(driver=driver, timestamp=timestamp).exists():
                        CarLog.objects.create(
                            timestamp=timestamp,
                            driver=driver,
                            route_list=str(route_list),
                            issues=str(issues),
                            photos_link=str(photos) if photos else ''
                        )
                        created_count += 1
                        driver_display = driver.name if driver else 'Unknown'
                        self.stdout.write(f"Row {index + 2}: {driver_display}")
                    else:
                        skipped_count += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Row {index + 2}: Error - {e}"))
                skipped_count += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{'[DRY RUN] ' if dry_run else ''}Processed: {created_count} car log records"))
        self.stdout.write(self.style.WARNING(f"Skipped: {skipped_count} records"))

    def _find_columns(self, df, column_mapping):
        """Find actual column names from possible mappings."""
        found = {}
        for key, possible_names in column_mapping.items():
            for name in possible_names:
                if name in df.columns:
                    found[key] = name
                    break
        return found

    def _get_column_value(self, row, column_name):
        """Safely get a column value from a row."""
        if not column_name:
            return None
        value = row.get(column_name)
        if pd.isna(value):
            return None
        return value

    def _parse_timestamp(self, row, columns):
        """Parse timestamp from row, handling various formats."""
        # Try timestamp column
        timestamp_col = columns.get('timestamp')
        if timestamp_col:
            ts = row.get(timestamp_col)
            if pd.notna(ts):
                if isinstance(ts, datetime):
                    return timezone.make_aware(ts) if timezone.is_naive(ts) else ts
                try:
                    parsed = pd.to_datetime(ts)
                    return timezone.make_aware(parsed.to_pydatetime()) if timezone.is_naive(parsed) else parsed.to_pydatetime()
                except:
                    pass

        # Try date column as fallback
        date_col = columns.get('date')
        if date_col:
            dt = row.get(date_col)
            if pd.notna(dt):
                try:
                    parsed = pd.to_datetime(dt)
                    return timezone.make_aware(parsed.to_pydatetime()) if timezone.is_naive(parsed) else parsed.to_pydatetime()
                except:
                    pass

        return None
