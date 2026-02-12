import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ops_dashboard.settings')
django.setup()

from logistics.models import VisitLog
from django.db.models import Count

print('Checking for duplicates...')
dups = VisitLog.objects.values('machine__name', 'timestamp').annotate(cnt=Count('id')).filter(cnt__gt=1).order_by('-cnt')

count = dups.count()
print(f'Duplicate groups found: {count}')

if count > 0:
    print('Top 10 duplicates:')
    for d in dups[:10]:
        print(f"{d['timestamp']} - {d['machine__name']}: {d['cnt']} copies")
else:
    print('No duplicates found.')
