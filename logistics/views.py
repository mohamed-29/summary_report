"""
Views for the logistics dashboard.
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
import json
import time
from django.shortcuts import render, redirect
from django.db import models
from django.db.models import Sum, Avg, Count, F, Q
from django.db.models.functions import TruncMonth
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.utils import timezone
from django.shortcuts import get_object_or_404



from .models import Machine, VisitLog, MonthlyReport, Operator, SupervisorDailyLog, SupervisorDailyLogImage


def set_language(request):
    """Toggle language between 'en' and 'ar'. Redirects back to referrer."""
    lang = request.GET.get('lang', 'en')
    if lang not in ('en', 'ar'):
        lang = 'en'
    request.session['lang'] = lang
    referer = request.META.get('HTTP_REFERER', '/')
    return redirect(referer)


@login_required
def dashboard(request):
    """
    Main dashboard view showing monthly aggregated data.
    Replicates the "ALL Operators Summary" logic.
    """
    # Get all months with data for the dropdown (needed early for default selection)
    available_months = (
        VisitLog.objects
        .annotate(month=TruncMonth('timestamp'))
        .values('month')
        .distinct()
        .order_by('-month')
    )
    available_months_list = list(available_months)

    # Get month filter from query params
    month_str = request.GET.get('month')
    if month_str:
        try:
            selected_month = datetime.strptime(month_str, '%Y-%m').date().replace(day=1)
        except ValueError:
            selected_month = date.today().replace(day=1)
    else:
        # Default to most recent month with data, or current month if no data
        if available_months_list:
            selected_month = available_months_list[0]['month'].date() if hasattr(available_months_list[0]['month'], 'date') else available_months_list[0]['month']
        else:
            selected_month = date.today().replace(day=1)

    # Aggregate data by machine for the selected month
    month_start = selected_month
    month_end = (selected_month.replace(day=28) + timedelta(days=4)).replace(day=1)  # First day of next month
    
    aggregated_data = (
        VisitLog.objects
        .filter(timestamp__gte=month_start, timestamp__lt=month_end)
        .values('machine__id', 'machine__name')
        .annotate(
            total_transactions=Sum('transactions'),
            total_voids=Sum('voids'),
            visit_count=Count('id')
        )
        .order_by('machine__name')
    )

    # Calculate void percentage for each machine
    summary_data = []
    for item in aggregated_data:
        total_trans = item['total_transactions'] or 0
        total_voids = item['total_voids'] or 0
        void_pct = (total_voids / total_trans * 100) if total_trans > 0 else 0
        
        # Get AI summary if available
        try:
            monthly_report = MonthlyReport.objects.get(
                machine_id=item['machine__id'],
                month=selected_month
            )
            ai_summary = monthly_report.ai_summary
        except MonthlyReport.DoesNotExist:
            ai_summary = ''

        summary_data.append({
            'machine_id': item['machine__id'],
            'machine_name': item['machine__name'],
            'total_transactions': total_trans,
            'total_voids': total_voids,
            'void_percentage': round(void_pct, 2),
            'visit_count': item['visit_count'],
            'ai_summary': ai_summary,
        })

    # Calculate grand totals
    grand_totals = {
        'total_transactions': sum(d['total_transactions'] for d in summary_data),
        'total_voids': sum(d['total_voids'] for d in summary_data),
        'machine_count': len(summary_data),
    }
    if grand_totals['total_transactions'] > 0:
        grand_totals['void_percentage'] = round(
            grand_totals['total_voids'] / grand_totals['total_transactions'] * 100, 2
        )
    else:
        grand_totals['void_percentage'] = 0

    # Fleet-level AI summary (cached in session to avoid re-generating on refresh)
    fleet_summary = None
    fleet_cache_key = f'fleet_summary_{selected_month.strftime("%Y-%m")}'
    if summary_data and bool(getattr(settings, 'OPENROUTER_API_KEY', '')):
        fleet_summary = request.session.get(fleet_cache_key, '')

    # Get cached AI operator report from session
    today = date.today()
    ai_report_cache_key = f'ai_operator_report_{today.isoformat()}'
    ai_operator_report = request.session.get(ai_report_cache_key, '')
    ai_report_time = request.session.get(f'{ai_report_cache_key}_time', '')

    context = {
        'summary_data': summary_data,
        'grand_totals': grand_totals,
        'selected_month': selected_month,
        'available_months': available_months,
        'has_ai_key': bool(getattr(settings, 'OPENROUTER_API_KEY', '')),
        'fleet_summary': fleet_summary,
        'ai_operator_report': ai_operator_report,
        'ai_report_time': ai_report_time,
    }

    return render(request, 'logistics/dashboard.html', context)


# Import here to avoid issues at top
from datetime import timedelta


@require_POST
@login_required
def generate_summaries(request):
    """
    Generate AI summaries for all machines in the selected month.
    This is a synchronous version for simplicity. For production,
    consider using Celery for background processing.
    """
    month_str = request.POST.get('month')
    if not month_str:
        messages.error(request, 'No month specified.')
        return redirect('logistics:dashboard')

    try:
        selected_month = datetime.strptime(month_str, '%Y-%m').date().replace(day=1)
    except ValueError:
        messages.error(request, 'Invalid month format.')
        return redirect('logistics:dashboard')

    # Check if OpenRouter is configured
    api_key = getattr(settings, 'OPENROUTER_API_KEY', '')
    if not api_key:
        messages.error(request, 'OpenRouter API key not configured.')
        return redirect('logistics:dashboard')

    try:
        from .utils import get_openrouter_client, openrouter_generate
        client = get_openrouter_client()
        if not client:
            messages.error(request, 'Failed to initialize OpenRouter client.')
            return redirect('logistics:dashboard')
    except Exception as e:
        messages.error(request, f'Failed to initialize OpenRouter: {e}')
        return redirect('logistics:dashboard')

    # Get date range (timezone-aware)
    from django.utils import timezone as tz
    month_start = tz.make_aware(datetime.combine(selected_month, datetime.min.time()))
    month_end = tz.make_aware(datetime.combine(
        (selected_month.replace(day=28) + timedelta(days=4)).replace(day=1),
        datetime.min.time()
    ))

    # Get all machines with visits this month
    machines_with_visits = (
        VisitLog.objects
        .filter(timestamp__gte=month_start, timestamp__lt=month_end)
        .values_list('machine_id', flat=True)
        .distinct()
    )

    generated_count = 0
    machines_data = []

    # 1. Gather Data
    for machine_id in machines_with_visits:

        try:
            machine = Machine.objects.get(id=machine_id)
            visit_logs = VisitLog.objects.filter(
                machine=machine,
                timestamp__gte=month_start,
                timestamp__lt=month_end
            )
            comments = [log.comments for log in visit_logs if log.comments and log.comments.strip() and log.comments.lower() != 'nan']
            product_issues = [log.product_issue for log in visit_logs if log.product_issue and log.product_issue.strip() and log.product_issue.lower() != 'nan']
            machine_issues = [log.machine_issue for log in visit_logs if log.machine_issue and log.machine_issue.strip() and log.machine_issue.lower() != 'nan']
            cleanliness_vals = [log.cleanliness_rating for log in visit_logs if log.cleanliness_rating]
            satisfaction_vals = [log.customer_satisfaction for log in visit_logs if log.customer_satisfaction]
            avg_cleanliness = round(sum(cleanliness_vals) / max(1, len(cleanliness_vals)), 1) if cleanliness_vals else None
            avg_satisfaction = round(sum(satisfaction_vals) / max(1, len(satisfaction_vals)), 1) if satisfaction_vals else None
            total_trans = sum(log.transactions for log in visit_logs)
            total_voids = sum(log.voids for log in visit_logs)
            
            machines_data.append({
                'machine': machine,
                'comments': comments,
                'product_issues': product_issues,
                'machine_issues': machine_issues,
                'avg_cleanliness': avg_cleanliness,
                'avg_satisfaction': avg_satisfaction,
                'total_trans': total_trans,
                'total_voids': total_voids
            })
        except Machine.DoesNotExist:
            continue

    # 2. Process each machine individually (one AI call per machine, rotating models)
    all_models_exhausted = False
    for data in machines_data:
        if all_models_exhausted:
            break

        has_data = data['comments'] or data['product_issues'] or data['machine_issues']

        if not has_data:
            # No text data -> No AI needed
            MonthlyReport.objects.update_or_create(
                machine=data['machine'],
                month=selected_month,
                defaults={
                    'total_transactions': data['total_trans'],
                    'total_voids': data['total_voids'],
                    'ai_summary': "No comments recorded this month.",
                    'raw_comments': ""
                }
            )
            generated_count += 1
            continue

        # Build prompt for this single machine
        machine_issues_text = "; ".join(data['machine_issues'])[:500] if data['machine_issues'] else "None"
        product_issues_text = "; ".join(data['product_issues'])[:500] if data['product_issues'] else "None"
        comments_text = "; ".join(data['comments'])[:500] if data['comments'] else "None"
        cleanliness_str = str(data['avg_cleanliness']) if data['avg_cleanliness'] else "N/A"
        satisfaction_str = str(data['avg_satisfaction']) if data['avg_satisfaction'] else "N/A"

        void_pct = round((data['total_voids'] / data['total_trans'] * 100), 1) if data['total_trans'] > 0 else 0

        prompt = f"""You are a vending machine operations analyst. Summarize this machine's monthly report in 2-3 sentences.
Be specific and actionable. Flag high void rates (>3%), low ratings (<3/5), and recurring issues.
If nothing significant, say "Operating normally. No significant issues."

Machine: {data['machine'].name}
- Machine Issues: {machine_issues_text}
- Product Issues: {product_issues_text}
- Operator Comments: {comments_text}
- Avg Cleanliness: {cleanliness_str}/5
- Avg Satisfaction: {satisfaction_str}/5
- Transactions: {data['total_trans']}, Voids: {data['total_voids']} ({void_pct}%)

Reply with ONLY the summary text. Be concise."""

        try:
            summary = openrouter_generate(client, prompt)
            MonthlyReport.objects.update_or_create(
                machine=data['machine'],
                month=selected_month,
                defaults={
                    'total_transactions': data['total_trans'],
                    'total_voids': data['total_voids'],
                    'ai_summary': summary,
                    'raw_comments': "\n---\n".join(data['comments'] + data['product_issues'] + data['machine_issues']),
                }
            )
            generated_count += 1
            time.sleep(1)  # Brief pause between machines

        except Exception as e:
            error_msg = str(e)
            if "All models failed" in error_msg:
                # All models are rate-limited — stop immediately
                all_models_exhausted = True
                messages.warning(request, f'All AI models are rate-limited. Generated {generated_count} summaries. Try again in a few minutes for the rest.')
            else:
                # Single model error — save error and continue
                MonthlyReport.objects.update_or_create(
                    machine=data['machine'],
                    month=selected_month,
                    defaults={
                        'total_transactions': data['total_trans'],
                        'total_voids': data['total_voids'],
                        'ai_summary': f"Summary generation failed: {error_msg[:100]}",
                        'raw_comments': "\n---\n".join(data['comments'] + data['product_issues'] + data['machine_issues']),
                    }
                )
                generated_count += 1
            continue

    if not all_models_exhausted:
        messages.success(request, f'Generated AI summaries for {generated_count} machines.')
    return redirect(f'/dashboard/?month={month_str}')


@login_required
def machine_detail(request, machine_id):
    """View detailed logs for a specific machine."""
    try:
        machine = Machine.objects.get(id=machine_id)
    except Machine.DoesNotExist:
        messages.error(request, 'Machine not found.')
        return redirect('logistics:dashboard')

    # Get month filter
    month_str = request.GET.get('month')
    if month_str:
        try:
            selected_month = datetime.strptime(month_str, '%Y-%m').date().replace(day=1)
            month_end = (selected_month.replace(day=28) + timedelta(days=4)).replace(day=1)
            logs = VisitLog.objects.filter(
                machine=machine,
                timestamp__gte=selected_month,
                timestamp__lt=month_end
            ).order_by('-timestamp')
        except ValueError:
            logs = VisitLog.objects.filter(machine=machine).order_by('-timestamp')[:50]
    else:
        logs = VisitLog.objects.filter(machine=machine).order_by('-timestamp')[:50]

    # Aggregate stats
    from django.db.models import Sum, Avg
    log_list = list(logs)
    total_trans = sum(l.transactions for l in log_list)
    total_voids = sum(l.voids for l in log_list)
    void_pct = round((total_voids / total_trans * 100), 2) if total_trans > 0 else 0
    unique_ops = len(set(l.operator_id for l in log_list if l.operator_id))

    context = {
        'machine': machine,
        'logs': log_list,
        'aliases': machine.aliases.all(),
        'total_visits': len(log_list),
        'total_transactions': total_trans,
        'total_voids': total_voids,
        'void_percentage': void_pct,
        'unique_operators': unique_ops,
    }

    return render(request, 'logistics/machine_detail.html', context)


@require_POST
@login_required
def upload_onsite_logs(request):
    """Upload and process On-Site Operator Excel file."""
    if 'file' not in request.FILES:
        messages.error(request, 'No file uploaded.')
        return redirect('logistics:dashboard')

    excel_file = request.FILES['file']
    
    # Validate file extension
    if not excel_file.name.endswith(('.xlsx', '.xls')):
        messages.error(request, 'Invalid file type. Please upload an Excel file (.xlsx or .xls)')
        return redirect('logistics:dashboard')

    try:
        import pandas as pd
        from .utils import resolve_machine, clean_numeric_value, get_or_create_operator, find_best_column
        from .models import VisitLog
        from django.utils import timezone

        # Read the Excel file
        df = pd.read_excel(excel_file, engine='openpyxl')

        # Normalize column names
        df.columns = df.columns.astype(str).str.strip()

        # Column mapping
        column_mapping = {
            'timestamp': ['Timestamp', 'timestamp', 'التوقيت', 'التاريخ'],
            'operator': ['" الأسم " ثلاثي', 'Operator Name', 'الأسم ثلاثي', 'Name', 'Operator'],
            'machine': ['ما هو أسم الماكينه؟', 'Machine Name', 'Machine', 'machine'],
            'received_keys': ['هل استلمت مفاتيح الماكينه ؟'],
            'shipment_info': ['اللذي أوصل اليك الشحنه ؟ , توقيت وصوله و أسمه ثلاثي operator من ال'],
            'arrival_time': ['توقيت وصولك أمام الماكينه'],
            'pos_verified': ['يعمل ؟ POS هل تأكدت من أن ال'],
            'product_review_done': ['هل أتممت مراجعه الأسم و السعر و الصوره " للمنتج " ؟'],
            'no_sold_out': ['؟ stop sale أو Sold out هل تأكدت من عدم وجود'],
            'quantity_review_done': ['هل أتممت مراجعه الكميات قبل و بعد وضعها في الماكينه ؟'],
            'expiry_verified': ['هل تأكدت من تاريخ صلاحية المنتجات ؟'],
            'stock_details': ['أكتب الستوك المتواجد داخل مخزن الماكينة بلأسماء و الأعداد " تفصيليا'],
            'cleanliness_rating': ['من 1 الي 5 , قيم نظافه الماكينة'],
            'machine_photo': ['صوره للماكينه واضحة أو عدة صور واضحه'],
            'transactions': ['عدد المعامت خلال اليوم', 'Transactions', 'No of Transactions', 'transactions', 'عدد المعاملات'],
            'product_issue': ['هل توجد مشكله أثناء نزول المنتجات ؟'],
            'voids': ['في اليوم VOIDS عدد ال', 'Voids', 'voids', 'Void', 'الفويد'],
            'machine_issue': ['هل يوجد أي مشاكل بالماكينة'],
            'customer_satisfaction': ['ما مدي رضاء العملاء من أستخدام الماكينه ؟'],
            'comments': ['تعليقات', 'Comments', 'comments', 'Notes', 'ملاحظات']
        }

        # Find actual columns
        columns = {}
        columns['timestamp'] = find_best_column(df.columns, column_mapping['timestamp'], ['date', 'time', 'تاريخ', 'وقت'])
        columns['operator'] = find_best_column(df.columns, column_mapping['operator'], ['operator', 'driver', 'name', 'اسم'])
        columns['machine'] = find_best_column(df.columns, column_mapping['machine'], ['machine', 'ماكينه'])
        columns['received_keys'] = find_best_column(df.columns, column_mapping['received_keys'], ['مفاتيح', 'keys'])
        columns['shipment_info'] = find_best_column(df.columns, column_mapping['shipment_info'], ['شحنه', 'shipment'])
        columns['arrival_time'] = find_best_column(df.columns, column_mapping['arrival_time'], ['وصولك', 'arrival'])
        columns['pos_verified'] = find_best_column(df.columns, column_mapping['pos_verified'], ['POS', 'pos'])
        columns['product_review_done'] = find_best_column(df.columns, column_mapping['product_review_done'], ['مراجعه الأسم', 'product review'])
        columns['no_sold_out'] = find_best_column(df.columns, column_mapping['no_sold_out'], ['sold out', 'stop sale'])
        columns['quantity_review_done'] = find_best_column(df.columns, column_mapping['quantity_review_done'], ['مراجعه الكميات', 'quantity'])
        columns['expiry_verified'] = find_best_column(df.columns, column_mapping['expiry_verified'], ['صلاحية', 'expiry'])
        columns['stock_details'] = find_best_column(df.columns, column_mapping['stock_details'], ['ستوك', 'stock'])
        columns['cleanliness_rating'] = find_best_column(df.columns, column_mapping['cleanliness_rating'], ['نظافه', 'cleanliness'])
        columns['machine_photo'] = find_best_column(df.columns, column_mapping['machine_photo'], ['صوره', 'photo'])
        columns['transactions'] = find_best_column(df.columns, column_mapping['transactions'], ['transaction', 'sales', 'بيع', 'معاملات'])
        columns['product_issue'] = find_best_column(df.columns, column_mapping['product_issue'], ['مشكله أثناء نزول', 'product issue'])
        columns['voids'] = find_best_column(df.columns, column_mapping['voids'], ['void', 'refund', 'فويد', 'استرجاع'])
        columns['machine_issue'] = find_best_column(df.columns, column_mapping['machine_issue'], ['مشاكل بالماكينة', 'machine issue'])
        columns['customer_satisfaction'] = find_best_column(df.columns, column_mapping['customer_satisfaction'], ['رضاء', 'satisfaction'])
        columns['comments'] = find_best_column(df.columns, column_mapping['comments'], ['comment', 'note', 'ملاحظات'])

        # --- Batch AI Resolution Step ---
        if columns.get('machine'):
            from .utils import batch_ai_resolve_machines
            
            # Get unique machine names
            unique_names = df[columns['machine']].unique()
            unique_names = [str(n).strip() for n in unique_names if pd.notna(n) and str(n).strip()]
            
            # Identify which ones need AI (not in DB/Aliases)
            unresolved_names = []
            for name in unique_names:
                # Try local resolution first (no AI)
                if not resolve_machine(name, use_ai_fallback=False):
                    unresolved_names.append(name)
            
            # Batch process unresolved names
            if unresolved_names:
                messages.info(request, f"Resolving {len(unresolved_names)} new machine names with AI...")
                batch_ai_resolve_machines(unresolved_names)
        # -------------------------------

        def get_text_val(row, col_key):
            """Helper to safely get text value from row."""
            col = columns.get(col_key)
            if not col:
                return ''
            val = row.get(col)
            return str(val).strip() if pd.notna(val) else ''

        def get_int_val(row, col_key, default=0):
            """Helper to safely get integer value from row."""
            col = columns.get(col_key)
            if not col:
                return default
            return clean_numeric_value(row.get(col), default=default)

        created_count = 0
        skipped_count = 0
        new_machines_created = []

        for index, row in df.iterrows():
            try:
                # Parse timestamp
                ts = row.get(columns.get('timestamp')) if columns.get('timestamp') else None
                if pd.notna(ts):
                    if isinstance(ts, datetime):
                        timestamp = timezone.make_aware(ts) if timezone.is_naive(ts) else ts
                    else:
                        parsed = pd.to_datetime(ts)
                        timestamp = timezone.make_aware(parsed.to_pydatetime()) if timezone.is_naive(parsed) else parsed.to_pydatetime()
                else:
                    skipped_count += 1
                    continue

                # Get operator
                operator_name = row.get(columns.get('operator')) if columns.get('operator') else None
                operator = None
                if pd.notna(operator_name):
                    operator, _ = get_or_create_operator(str(operator_name))

                # Get machine - try to resolve with AI, or create new if not found
                raw_machine_name = row.get(columns.get('machine')) if columns.get('machine') else None
                if pd.isna(raw_machine_name) or not str(raw_machine_name).strip():
                    skipped_count += 1
                    continue

                # Resolve machine (AI handled in batch above)
                machine = resolve_machine(str(raw_machine_name), use_ai_fallback=False)
                
                if not machine:
                    # No match found - create a new machine with this name instead of using AI per row
                    from .models import Machine
                    clean_name = str(raw_machine_name).strip()
                    machine, created = Machine.objects.get_or_create(
                        name=clean_name,
                        defaults={'name': clean_name}
                    )
                    if created:
                        new_machines_created.append(clean_name)

                # Get all field values
                transactions = get_int_val(row, 'transactions')
                voids = get_int_val(row, 'voids')
                comments = get_text_val(row, 'comments')

                # Check for duplicates
                if not VisitLog.objects.filter(machine=machine, timestamp=timestamp).exists():
                    VisitLog.objects.create(
                        timestamp=timestamp,
                        operator=operator,
                        machine=machine,
                        # Checklist fields
                        received_keys=get_text_val(row, 'received_keys'),
                        shipment_info=get_text_val(row, 'shipment_info'),
                        arrival_time=get_text_val(row, 'arrival_time'),
                        pos_verified=get_text_val(row, 'pos_verified'),
                        product_review_done=get_text_val(row, 'product_review_done'),
                        no_sold_out=get_text_val(row, 'no_sold_out'),
                        quantity_review_done=get_text_val(row, 'quantity_review_done'),
                        expiry_verified=get_text_val(row, 'expiry_verified'),
                        stock_details=get_text_val(row, 'stock_details'),
                        # Ratings
                        cleanliness_rating=get_int_val(row, 'cleanliness_rating'),
                        customer_satisfaction=get_int_val(row, 'customer_satisfaction'),
                        # Core metrics
                        transactions=transactions,
                        voids=voids,
                        # Issues
                        product_issue=get_text_val(row, 'product_issue'),
                        machine_issue=get_text_val(row, 'machine_issue'),
                        # Comments
                        comments=comments,
                        raw_machine_name=str(raw_machine_name)
                    )
                    created_count += 1
                else:
                    skipped_count += 1

            except Exception as e:
                skipped_count += 1
                continue

        if new_machines_created:
            messages.info(request, f'New machines created: {", ".join(new_machines_created[:5])}{"..." if len(new_machines_created) > 5 else ""}')
        
        messages.success(request, f'Imported {created_count} on-site logs. Skipped {skipped_count} rows.')

    except Exception as e:
        messages.error(request, f'Error processing file: {e}')

    return redirect('logistics:dashboard')


@require_POST
@login_required
def upload_car_logs(request):
    """Upload and process Car Operator Excel file."""
    if 'file' not in request.FILES:
        messages.error(request, 'No file uploaded.')
        return redirect('logistics:dashboard')

    excel_file = request.FILES['file']
    
    if not excel_file.name.endswith(('.xlsx', '.xls')):
        messages.error(request, 'Invalid file type. Please upload an Excel file (.xlsx or .xls)')
        return redirect('logistics:dashboard')

    try:
        import pandas as pd
        from .utils import get_or_create_operator, find_best_column
        from .models import CarLog
        from django.utils import timezone

        df = pd.read_excel(excel_file, engine='openpyxl')

        # Normalize column names
        df.columns = df.columns.astype(str).str.strip()

        column_mapping = {
            'timestamp': ['Timestamp', 'timestamp', 'التوقيت', 'التاريخ'],
            'driver': ['" الأسم " ثلاثي', 'Driver Name', 'driver', 'السائق', 'Name', 'Operator Name'],
            'route_list': ['ما هو أسم الأماكن اللتي ستمر عليها اليوم؟', 'Route List', 'Routes', 'route_list', 'المسار'],
            'issues': [' أي مشاكل حدثت من بدايه الرحله حتي النهايه وضحها هنا بالتفاصيل', 'Issues', 'issues', 'Problems', 'المشاكل'],
            'photos': ['ضع صور لجميع الأوراق الخاصه بلرحله', 'Photos', 'photos', 'Photo Links', 'الصور']
        }

        columns = {}
        columns['timestamp'] = find_best_column(df.columns, column_mapping['timestamp'], ['date', 'time', 'تاريخ', 'وقت'])
        columns['driver'] = find_best_column(df.columns, column_mapping['driver'], ['operator', 'driver', 'name', 'اسم'])
        columns['route_list'] = find_best_column(df.columns, column_mapping['route_list'], ['route', 'location', 'مسار', 'أماكن'])
        columns['issues'] = find_best_column(df.columns, column_mapping['issues'], ['issue', 'problem', 'مشاكل', 'ملاحظات'])
        columns['photos'] = find_best_column(df.columns, column_mapping['photos'], ['photo', 'image', 'صور'])

        created_count = 0
        skipped_count = 0

        for index, row in df.iterrows():
            try:
                ts = row.get(columns.get('timestamp')) if columns.get('timestamp') else None
                if pd.notna(ts):
                    if isinstance(ts, datetime):
                        timestamp = timezone.make_aware(ts) if timezone.is_naive(ts) else ts
                    else:
                        parsed = pd.to_datetime(ts)
                        timestamp = timezone.make_aware(parsed.to_pydatetime()) if timezone.is_naive(parsed) else parsed.to_pydatetime()
                else:
                    skipped_count += 1
                    continue

                driver_name = row.get(columns.get('driver')) if columns.get('driver') else None
                driver = None
                if pd.notna(driver_name):
                    driver, _ = get_or_create_operator(str(driver_name), is_driver=True)

                route_list = row.get(columns.get('route_list')) if columns.get('route_list') else ''
                issues = row.get(columns.get('issues')) if columns.get('issues') else ''
                photos = row.get(columns.get('photos')) if columns.get('photos') else ''

                # Check for duplicates
                if not CarLog.objects.filter(driver=driver, timestamp=timestamp).exists():
                    CarLog.objects.create(
                        timestamp=timestamp,
                        driver=driver,
                        route_list=str(route_list) if pd.notna(route_list) else '',
                        issues=str(issues) if pd.notna(issues) else '',
                        photos_link=str(photos) if pd.notna(photos) else ''
                    )
                    created_count += 1
                else:
                    skipped_count += 1

            except Exception as e:
                skipped_count += 1
                continue

        messages.success(request, f'Imported {created_count} car logs. Skipped {skipped_count} rows.')

    except Exception as e:
        messages.error(request, f'Error processing file: {e}')

    return redirect('logistics:dashboard')


@login_required
def operator_detail(request, operator_id):
    """View detailed stats and rating for a specific operator."""
    try:
        operator = Operator.objects.get(id=operator_id)
    except Operator.DoesNotExist:
        messages.error(request, 'Operator not found.')
        return redirect('logistics:dashboard')

    # Get available months from all visit logs
    available_months_qs = (
        VisitLog.objects
        .annotate(month=TruncMonth('timestamp'))
        .values('month')
        .distinct()
        .order_by('-month')
    )
    available_months = [item['month'].date() if hasattr(item['month'], 'date') else item['month'] for item in available_months_qs]

    # Determine selected month
    month_str = request.GET.get('month')
    if month_str:
        try:
            selected_month = datetime.strptime(month_str, '%Y-%m').date().replace(day=1)
        except ValueError:
            selected_month = available_months[0] if available_months else date.today().replace(day=1)
    else:
        selected_month = available_months[0] if available_months else date.today().replace(day=1)

    # Calculate month range
    month_start = selected_month
    month_end = (selected_month.replace(day=28) + timedelta(days=4)).replace(day=1)

    # Handle Rating Submission
    if request.method == 'POST' and 'rating' in request.POST:
        try:
            rating_val = int(request.POST['rating'])
            if 0 <= rating_val <= 10:
                from .models import OperatorDailyRating
                rat_obj, created = OperatorDailyRating.objects.update_or_create(
                    operator=operator,
                    date=date.today(),
                    defaults={'rating': rating_val}
                )
                messages.success(request, f'Rating saved: {rating_val}/10')
            else:
                messages.error(request, 'Rating must be between 0 and 10.')
        except ValueError:
            messages.error(request, 'Invalid rating value.')
        return redirect(f'{request.path}?month={selected_month.strftime("%Y-%m")}')

    # Check for specific date filter
    filter_date = None
    filter_date_str = request.GET.get('filter_date')
    if filter_date_str:
        try:
            filter_date = datetime.strptime(filter_date_str, '%Y-%m-%d').date()
        except ValueError:
            filter_date = None

    # Get stats for the month
    month_visits = VisitLog.objects.filter(
        operator=operator,
        timestamp__gte=month_start,
        timestamp__lt=month_end
    )

    total_visits = month_visits.count()
    unique_machines = month_visits.values('machine').distinct().count()

    # If date filter is applied, narrow down the logs
    if filter_date:
        filtered_visits = month_visits.filter(
            timestamp__year=filter_date.year,
            timestamp__month=filter_date.month,
            timestamp__day=filter_date.day
        )
    else:
        filtered_visits = month_visits

    # Aggregate ratings and comments for the displayed logs
    month_comments = []
    cleanliness_vals = []
    satisfaction_vals = []
    for vl in filtered_visits.select_related('machine'):
        if vl.comments and vl.comments.strip():
            month_comments.append({
                'date': vl.timestamp,
                'machine': vl.machine.name if vl.machine else 'Unknown',
                'text': vl.comments.strip(),
                'cleanliness': vl.cleanliness_rating,
                'satisfaction': vl.customer_satisfaction,
                'product_issue': vl.product_issue,
                'machine_issue': vl.machine_issue,
            })
        if vl.cleanliness_rating:
            cleanliness_vals.append(vl.cleanliness_rating)
        if vl.customer_satisfaction:
            satisfaction_vals.append(vl.customer_satisfaction)

    avg_cleanliness = round(sum(cleanliness_vals) / len(cleanliness_vals), 1) if cleanliness_vals else None
    avg_satisfaction = round(sum(satisfaction_vals) / len(satisfaction_vals), 1) if satisfaction_vals else None

    # Get car logs for drivers
    from .models import CarLog, CarLogStop
    car_logs = []
    if operator.is_driver:
        car_logs = CarLog.objects.filter(
            driver=operator,
            trip_date__gte=month_start,
            trip_date__lt=month_end
        ).prefetch_related('stops__machine').order_by('-trip_date')

    # Get current rating
    try:
        from .models import OperatorDailyRating
        current_rating = OperatorDailyRating.objects.get(operator=operator, date=date.today()).rating
    except OperatorDailyRating.DoesNotExist:
        current_rating = 0

    context = {
        'operator': operator,
        'selected_month': selected_month,
        'available_months': available_months,
        'total_visits': total_visits,
        'unique_machines': unique_machines,
        'current_rating': current_rating,
        'logs': filtered_visits.select_related('machine').order_by('-timestamp')[:100],
        'filter_date': filter_date,
        'month_comments': month_comments,
        'avg_cleanliness': avg_cleanliness,
        'avg_satisfaction': avg_satisfaction,
        'car_logs': car_logs,
    }
    return render(request, 'logistics/operator_detail.html', context)


@login_required
def download_visit_log(request, log_id):
    """Download a single visit log record as CSV. Use ?lang=ar for Arabic."""
    try:
        log = VisitLog.objects.select_related('operator', 'machine').get(id=log_id)
    except VisitLog.DoesNotExist:
        messages.error(request, 'Visit log not found.')
        return redirect('logistics:dashboard')

    lang = request.GET.get('lang', 'en')
    is_ar = lang == 'ar'

    operator_name = log.operator.name if log.operator else ('غير معروف' if is_ar else 'Unknown')
    machine_name = log.machine.name if log.machine else ('غير معروف' if is_ar else 'Unknown')
    ts = log.timestamp.strftime('%Y-%m-%d_%H-%M') if log.timestamp else 'no-date'

    yes = 'نعم' if is_ar else 'Yes'
    no = 'لا' if is_ar else 'No'
    na = '-'

    def clean(text):
        if not text:
            return na
        return text.replace(chr(10), ' ').replace(chr(13), '').replace('"', "'")

    def bool_val(val):
        return yes if val else no

    # Build rows as (label, value) tuples
    if is_ar:
        header = 'الحقل,القيمة'
        rows = [
            ('رقم السجل', log.id),
            ('التاريخ', log.timestamp.strftime('%Y-%m-%d %H:%M') if log.timestamp else na),
            ('المشغل', operator_name),
            ('الماكينة', machine_name),
            ('اسم الماكينة الأصلي', clean(log.raw_machine_name)),
            ('نوع النموذج', 'تسجيل دخول' if log.is_check_in else 'تسجيل خروج'),
            ('الحالة', 'مكتمل' if log.is_completed else 'مسودة'),
            ('موقع الماكينة', clean(log.visit_location)),
            ('توقيت الوصول', log.arrival_time.strftime('%Y-%m-%d %H:%M') if log.arrival_time else na),
            ('عدد المعاملات', log.transactions),
            ('عدد الملغية', log.voids),
            ('نسبة الإلغاء', f'{log.void_percentage}%'),
            ('هل استلمت مفاتيح الماكينه ؟', bool_val(log.received_keys)),
            ('هل تأكدت من أن ال POS يعمل ؟', bool_val(log.pos_verified)),
            ('هل أتممت مراجعه الأسم و السعر و الصوره للمنتج ؟', bool_val(log.product_review_done)),
            ('هل تأكدت من عدم وجود Sold out أو stop sale ؟', bool_val(log.no_sold_out)),
            ('هل أتممت مراجعه الكميات قبل و بعد وضعها في الماكينه ؟', bool_val(log.quantity_review_done)),
            ('هل تأكدت من تاريخ صلاحية المنتجات ؟', bool_val(log.expiry_verified)),
            ('تقييم النظافة', f'{log.cleanliness_rating}/5'),
            ('رضا العملاء', f'{log.customer_satisfaction}/5'),
            ('معلومات الشحنة', clean(log.shipment_info)),
            ('الستوك المتواجد', clean(log.stock_details)),
            ('مشاكل المنتجات', clean(log.product_issue)),
            ('مشاكل الماكينة', clean(log.machine_issue)),
            ('ملاحظات', clean(log.comments)),
        ]
    else:
        header = 'Field,Value'
        rows = [
            ('Log ID', log.id),
            ('Date', log.timestamp.strftime('%Y-%m-%d %H:%M') if log.timestamp else na),
            ('Operator', operator_name),
            ('Machine', machine_name),
            ('Raw Machine Name', clean(log.raw_machine_name)),
            ('Form Type', 'Check In' if log.is_check_in else 'Check Out'),
            ('Status', 'Completed' if log.is_completed else 'Draft'),
            ('Visit Location', clean(log.visit_location)),
            ('Arrival Time', log.arrival_time.strftime('%Y-%m-%d %H:%M') if log.arrival_time else na),
            ('Transactions', log.transactions),
            ('Voids', log.voids),
            ('Void %', f'{log.void_percentage}%'),
            ('Received Keys', bool_val(log.received_keys)),
            ('POS Verified', bool_val(log.pos_verified)),
            ('Product Review Done', bool_val(log.product_review_done)),
            ('No Sold Out', bool_val(log.no_sold_out)),
            ('Quantity Review Done', bool_val(log.quantity_review_done)),
            ('Expiry Verified', bool_val(log.expiry_verified)),
            ('Cleanliness Rating', f'{log.cleanliness_rating}/5'),
            ('Customer Satisfaction', f'{log.customer_satisfaction}/5'),
            ('Shipment Info', clean(log.shipment_info)),
            ('Stock Details', clean(log.stock_details)),
            ('Product Issues', clean(log.product_issue)),
            ('Machine Issues', clean(log.machine_issue)),
            ('Comments', clean(log.comments)),
        ]

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="visit_log_{log.id}_{ts}.csv"'
    response.write('\ufeff')  # BOM for Excel Arabic support

    lines = [header]
    for label, value in rows:
        lines.append(f'"{label}","{value}"')

    response.write('\n'.join(lines))
    return response


@login_required
def operator_list(request):
    """List all operators with summary stats for the selected month."""
    # Auto check-out operators who have been checked in for more than 12 hours
    from .utils import auto_checkout_stale_visits
    auto_checkout_stale_visits()

    # Get available months
    available_months_qs = (
        VisitLog.objects
        .annotate(month=TruncMonth('timestamp'))
        .values('month')
        .distinct()
        .order_by('-month')
    )
    available_months = [item['month'].date() if hasattr(item['month'], 'date') else item['month'] for item in available_months_qs]

    # Determine selected month
    month_str = request.GET.get('month')
    if month_str:
        try:
            selected_month = datetime.strptime(month_str, '%Y-%m').date().replace(day=1)
        except ValueError:
            selected_month = available_months[0] if available_months else date.today().replace(day=1)
    else:
        selected_month = available_months[0] if available_months else date.today().replace(day=1)
    
    month_start = selected_month
    month_end = (selected_month.replace(day=28) + timedelta(days=4)).replace(day=1)

    # Get operators with stats (count only check-ins as visits; check-in + check-out = 1 visit)
    from .models import CarLog, CarLogStop
    operators = Operator.objects.annotate(
        visit_count=Count('visit_logs', filter=models.Q(visit_logs__timestamp__gte=month_start, visit_logs__timestamp__lt=month_end, visit_logs__is_check_in=True, visit_logs__is_completed=True)),
        machine_count=Count('visit_logs__machine', distinct=True, filter=models.Q(visit_logs__timestamp__gte=month_start, visit_logs__timestamp__lt=month_end)),
        car_trip_count=Count('car_logs', filter=models.Q(car_logs__trip_date__gte=month_start, car_logs__trip_date__lt=month_end)),
    ).order_by('name')

    # Build car stops map: driver_id -> set of machine names visited
    car_machine_map = {}
    car_stops_qs = CarLogStop.objects.filter(
        car_log__trip_date__gte=month_start,
        car_log__trip_date__lt=month_end
    ).select_related('car_log__driver', 'machine')
    for stop in car_stops_qs:
        driver_id = stop.car_log.driver_id
        if driver_id:
            if driver_id not in car_machine_map:
                car_machine_map[driver_id] = set()
            car_machine_map[driver_id].add(stop.machine.name)

    # Get average monthly ratings
    from .models import OperatorDailyRating
    from django.db.models import Avg
    ratings = (
        OperatorDailyRating.objects
        .filter(date__gte=month_start, date__lt=month_end)
        .values('operator_id')
        .annotate(avg_rating=Avg('rating'))
    )
    rating_map = {r['operator_id']: round(r['avg_rating'], 1) for r in ratings}

    # Get today's comments and ratings per operator
    today = date.today()
    todays_visit_logs = VisitLog.objects.filter(
        timestamp__year=today.year,
        timestamp__month=today.month,
        timestamp__day=today.day,
        is_completed=True
    ).select_related('operator', 'machine')

    # Build maps for today's data
    operator_today_comments = {}
    operator_today_ratings = {}
    for vl in todays_visit_logs:
        if vl.operator_id:
            if vl.operator_id not in operator_today_comments:
                operator_today_comments[vl.operator_id] = []
                operator_today_ratings[vl.operator_id] = {'cleanliness': [], 'satisfaction': []}
            if vl.comments and vl.comments.strip():
                operator_today_comments[vl.operator_id].append({
                    'machine': vl.machine.name if vl.machine else 'Unknown',
                    'text': vl.comments.strip(),
                })
            if vl.cleanliness_rating:
                operator_today_ratings[vl.operator_id]['cleanliness'].append(vl.cleanliness_rating)
            if vl.customer_satisfaction:
                operator_today_ratings[vl.operator_id]['satisfaction'].append(vl.customer_satisfaction)

    # Get current check-in/out status for each operator
    for op in operators:
        op.monthly_avg_rating = rating_map.get(op.id, '-')
        op.last_route = []

        # Today's comments and ratings
        op.today_comments = operator_today_comments.get(op.id, [])
        ratings_data = operator_today_ratings.get(op.id, {'cleanliness': [], 'satisfaction': []})
        op.avg_cleanliness = round(sum(ratings_data['cleanliness']) / len(ratings_data['cleanliness']), 1) if ratings_data['cleanliness'] else None
        op.avg_satisfaction = round(sum(ratings_data['satisfaction']) / len(ratings_data['satisfaction']), 1) if ratings_data['satisfaction'] else None

        # For drivers: use car trip data if no visit logs
        op.car_machines = car_machine_map.get(op.id, set())
        if op.is_driver:
            if op.visit_count == 0:
                op.visit_count = op.car_trip_count
            if op.machine_count == 0:
                op.machine_count = len(op.car_machines)

        # Find the last completed visit log
        last_completed = VisitLog.objects.filter(
            operator=op, is_completed=True
        ).order_by('-created_at').first()

        # Check for an active draft too
        active_draft = VisitLog.objects.filter(
            operator=op, is_completed=False
        ).order_by('-created_at').first()

        # For drivers: check last car log activity if no visit logs
        if op.is_driver and not last_completed and not active_draft:
            last_car_log = CarLog.objects.filter(driver=op).order_by('-created_at').first()
            if last_car_log:
                op.status = 'checked_out'
                op.status_label = '🚗 Trip Logged'
                op.status_time = last_car_log.created_at
                # Calculate trip duration
                if last_car_log.exit_time and last_car_log.return_time:
                    from datetime import datetime as dt_cls
                    exit_dt = dt_cls.combine(last_car_log.trip_date or date.today(), last_car_log.exit_time)
                    return_dt = dt_cls.combine(last_car_log.trip_date or date.today(), last_car_log.return_time)
                    duration = return_dt - exit_dt
                    total_minutes = int(duration.total_seconds() // 60)
                    if total_minutes > 0:
                        hours, minutes = divmod(total_minutes, 60)
                        op.time_spent = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                    else:
                        op.time_spent = None
                else:
                    op.time_spent = None
                # Show stops as machines visited
                stops = last_car_log.stops.select_related('machine').order_by('order')
                op.last_route = [s.machine.name for s in stops]
                continue

        if active_draft:
            # They have an incomplete form in progress
            if active_draft.is_check_in:
                op.status = 'checking_in'
                op.status_label = '🔄 Checking In...'
            else:
                op.status = 'checking_out'
                op.status_label = '🔄 Checking Out...'
            op.status_time = active_draft.created_at
            op.time_spent = None
        elif last_completed:
            op.status_time = last_completed.updated_at or last_completed.created_at
            if last_completed.is_check_in:
                # Last completed was a check-in → they're currently checked in
                op.status = 'checked_in'
                op.status_label = '✅ Checked In'
                op.time_spent = None
            else:
                # Last completed was a check-out → calculate duration
                op.status = 'checked_out'
                op.status_label = '📤 Checked Out'
                # Find the previous check out or the start of their shift to look for the first check-in
                prev_check_out = VisitLog.objects.filter(
                    operator=op,
                    is_completed=True,
                    is_check_in=False,
                    created_at__lt=last_completed.created_at
                ).order_by('-created_at').first()

                # Find the FIRST check-in after the previous check-out
                qs = VisitLog.objects.filter(
                    operator=op,
                    is_completed=True,
                    is_check_in=True,
                    created_at__lt=last_completed.created_at
                )
                if prev_check_out:
                    qs = qs.filter(created_at__gt=prev_check_out.created_at)
                
                first_check_in = qs.order_by('created_at').first()

                if first_check_in and first_check_in.updated_at and last_completed.updated_at:
                    duration = last_completed.updated_at - first_check_in.updated_at
                    total_minutes = int(duration.total_seconds() // 60)
                    hours, minutes = divmod(total_minutes, 60)
                    if hours > 0:
                        op.time_spent = f"{hours}h {minutes}m"
                    else:
                        op.time_spent = f"{minutes}m"
                else:
                    op.time_spent = None
        else:
            op.status = 'no_activity'
            op.status_label = '⚪ No Activity'
            op.status_time = None
            op.time_spent = None

    # Fetch today's supervisor report summary (if any)
    from .models import SupervisorDailyReport
    today_report = (
        SupervisorDailyReport.objects
        .filter(date=today)
        .select_related('supervisor')
        .prefetch_related('operator_reviews__operator', 'images')
        .first()
    )

    # Get cached AI operator report from session
    ai_report_cache_key = f'ai_operator_report_{today.isoformat()}'
    ai_operator_report = request.session.get(ai_report_cache_key, '')
    ai_report_time = request.session.get(f'{ai_report_cache_key}_time', '')
    has_ai_key = bool(getattr(settings, 'OPENROUTER_API_KEY', ''))

    context = {
        'operators': operators,
        'selected_month': selected_month,
        'available_months': available_months,
        'today_report': today_report,
        'ai_operator_report': ai_operator_report,
        'ai_report_time': ai_report_time,
        'has_ai_key': has_ai_key,
    }
    return render(request, 'logistics/operator_list.html', context)


@require_POST
@login_required
def generate_operator_report(request):
    """Generate an AI performance report for all operators based on today's data."""
    from .utils import get_openrouter_client, openrouter_generate
    from .models import CarLog, OperatorDailyRating, SupervisorDailyReport

    today = date.today()

    # Check if OpenRouter is configured
    api_key = getattr(settings, 'OPENROUTER_API_KEY', '')
    if not api_key:
        messages.error(request, 'OpenRouter API key not configured.')
        return redirect('logistics:operator_list')

    client = get_openrouter_client()
    if not client:
        messages.error(request, 'Failed to initialize OpenRouter client.')
        return redirect('logistics:operator_list')

    # Gather today's data
    todays_visits = VisitLog.objects.filter(
        timestamp__year=today.year,
        timestamp__month=today.month,
        timestamp__day=today.day,
        is_completed=True
    ).select_related('operator', 'machine')

    operators = Operator.objects.filter(is_active=True).order_by('name')

    # Build operator summaries
    operator_lines = []
    for op in operators:
        op_visits = [v for v in todays_visits if v.operator_id == op.id]
        if not op_visits and not op.is_driver:
            operator_lines.append(f"- {op.name} ({'Driver' if op.is_driver else 'Operator'}): No activity today")
            continue

        machines = list(set(v.machine.name for v in op_visits if v.machine))
        check_ins = [v for v in op_visits if v.is_check_in]
        check_outs = [v for v in op_visits if not v.is_check_in]
        comments = [v.comments for v in op_visits if v.comments and v.comments.strip()]
        issues = []
        for v in op_visits:
            if v.machine_issue and v.machine_issue.strip():
                issues.append(f"Machine: {v.machine_issue.strip()[:80]}")
            if v.product_issue and v.product_issue.strip():
                issues.append(f"Product: {v.product_issue.strip()[:80]}")

        cleanliness_vals = [v.cleanliness_rating for v in op_visits if v.cleanliness_rating]
        satisfaction_vals = [v.customer_satisfaction for v in op_visits if v.customer_satisfaction]
        avg_clean = round(sum(cleanliness_vals) / len(cleanliness_vals), 1) if cleanliness_vals else 'N/A'
        avg_sat = round(sum(satisfaction_vals) / len(satisfaction_vals), 1) if satisfaction_vals else 'N/A'

        line = f"- {op.name} ({'Driver' if op.is_driver else 'Operator'}): {len(check_ins)} check-ins, {len(check_outs)} check-outs, Machines: {', '.join(machines[:5]) if machines else 'None'}"
        line += f", Cleanliness: {avg_clean}/5, Satisfaction: {avg_sat}/5"
        if issues:
            line += f", Issues: {'; '.join(issues[:3])}"
        if comments:
            line += f", Comments: {'; '.join(c[:60] for c in comments[:3])}"
        operator_lines.append(line)

    # Supervisor report data
    sup_report = SupervisorDailyReport.objects.filter(date=today).first()
    sup_section = "No supervisor report submitted today."
    if sup_report:
        sup_section = f"Supervisor: {sup_report.supervisor.name}\n"
        if sup_report.general_comments:
            sup_section += f"  Comments: {sup_report.general_comments[:200]}\n"
        if sup_report.general_issues:
            sup_section += f"  Issues: {sup_report.general_issues[:200]}\n"
        if sup_report.reported_issues:
            sup_section += f"  Reported Issues: {sup_report.reported_issues[:200]}\n"

    total_ops = operators.count()
    active_ops = len(set(v.operator_id for v in todays_visits if v.operator_id))

    prompt = f"""You are an operations manager assistant for a vending machine company.
Generate a daily performance report for today ({today.strftime('%A, %B %d, %Y')}) based on operator activity data.

Team Overview:
- Total operators/drivers: {total_ops}
- Active today: {active_ops}
- Attendance rate: {round(active_ops / total_ops * 100) if total_ops > 0 else 0}%

Operator Activity:
{chr(10).join(operator_lines)}

Supervisor Report:
{sup_section}

Instructions:
1. Start with a brief overall assessment of today's team performance
2. Highlight top performers (most visits, good ratings)
3. Flag concerns (inactive operators, issues reported, low ratings)
4. Note any patterns or recommendations
5. Keep it concise (5-8 sentences), professional, and actionable
6. Use Arabic if operator names are in Arabic

Reply with ONLY the report text."""

    try:
        report_text = openrouter_generate(client, prompt)
        # Cache in session
        cache_key = f'ai_operator_report_{today.isoformat()}'
        request.session[cache_key] = report_text
        request.session[f'{cache_key}_time'] = datetime.now().strftime('%H:%M')
        messages.success(request, 'AI report generated successfully.')
    except Exception as e:
        messages.error(request, f'Failed to generate report: {str(e)[:100]}')

    return redirect('logistics:operator_list')


def visit_log_detail(request, log_id):
    """Show full details of a single visit log entry."""
    # Allow access for logged-in admin users OR session-based supervisors
    if not request.user.is_authenticated and not request.session.get('supervisor_id'):
        return redirect('logistics:dashboard_login')
    log = get_object_or_404(VisitLog.objects.select_related('operator', 'machine'), pk=log_id)
    images = log.images.all()
    context = {
        'log': log,
        'images': images,
    }
    return render(request, 'logistics/visit_log_detail.html', context)


# ===================================================================
# Phase 9: Operator Frontend - Login, Visit Form, Logout
# ===================================================================

def operator_login(request):
    """Code-based login for operators."""
    from .forms import OperatorLoginForm
    
    error = None
    form = OperatorLoginForm()

    if request.method == 'POST':
        form = OperatorLoginForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data['code'].strip().upper()
            try:
                operator = Operator.objects.get(code=code)
                request.session['operator_id'] = operator.id
                if operator.is_supervisor:
                    return redirect('logistics:supervisor_dashboard')
                elif operator.is_driver:
                    return redirect('logistics:car_form')
                return redirect('logistics:visit_form')
            except Operator.DoesNotExist:
                error = 'الكود غير صحيح. يرجى المحاولة مرة أخرى.'

    return render(request, 'logistics/operator_login.html', {
        'form': form,
        'error': error,
    })


def visit_log_form(request):
    """Form for operators to submit their visit logs with photos.
    Supports Check-In / Check-Out alternation and live-save (draft) mode.
    """
    import os
    from django.conf import settings as django_settings
    from .forms import VisitLogForm
    from .models import VisitLogImage

    # Check session auth
    operator_id = request.session.get('operator_id')
    if not operator_id:
        return redirect('logistics:operator_login')
    
    try:
        operator = Operator.objects.get(id=operator_id)
    except Operator.DoesNotExist:
        request.session.flush()
        return redirect('logistics:operator_login')

    # ----- Determine current state -----
    # 1) Check for an incomplete draft
    draft = VisitLog.objects.filter(operator=operator, is_completed=False).order_by('-created_at').first()

    # 2) Determine form type (check in or check out)
    if draft:
        # Resume the draft
        is_check_in = draft.is_check_in
        visit_instance = draft
    else:
        # Look at the last completed record
        last_completed = VisitLog.objects.filter(operator=operator, is_completed=True).order_by('-created_at').first()
        if last_completed and last_completed.is_check_in:
            # Last was check in -> next is check out
            is_check_in = False
        else:
            # Last was check out or no records -> next is check in
            is_check_in = True
        visit_instance = None

    form_type = 'check_in' if is_check_in else 'check_out'
    form_type_label = 'تسجيل الدخول (Check In)' if is_check_in else 'تسجيل الخروج (Check Out)'

    if request.method == 'POST':
        action = request.POST.get('action', 'complete')  # 'draft' or 'complete'
        is_draft = (action == 'draft')

        if visit_instance:
            form = VisitLogForm(request.POST, request.FILES, instance=visit_instance, draft=is_draft)
        else:
            form = VisitLogForm(request.POST, request.FILES, draft=is_draft)

        if form.is_valid():
            visit = form.save(commit=False)
            visit.operator = operator
            # is_check_in is now bound from the form, it will be automatically set
            if not visit.timestamp:
                visit.timestamp = timezone.now()
            visit.raw_machine_name = visit.machine.name if visit.machine else ''

            if is_draft:
                visit.is_completed = False
                visit.save()
                messages.info(request, 'تم حفظ المسودة بنجاح. يمكنك استكمال النموذج لاحقاً.')
            else:
                visit.is_completed = True
                visit.save()
                # Handle multiple photo uploads via VisitLogImage
                photos = request.FILES.getlist('machine_photos')
                for photo in photos:
                    VisitLogImage.objects.create(visit_log=visit, image=photo)
                messages.success(request, f'تم {form_type_label} بنجاح!')

            return redirect('logistics:visit_form')
    else:
        if visit_instance:
            form = VisitLogForm(instance=visit_instance, draft=True)
        else:
            form = VisitLogForm(draft=True)

    return render(request, 'logistics/visit_form.html', {
        'form': form,
        'operator': operator,
        'is_draft': visit_instance is not None,
    })


@require_POST
def visit_auto_save(request):
    """AJAX endpoint for live-saving visit form data as a draft."""
    from .forms import VisitLogForm
    from .models import VisitLogImage

    operator_id = request.session.get('operator_id')
    if not operator_id:
        return JsonResponse({'status': 'error', 'message': 'Not authenticated'}, status=401)

    try:
        operator = Operator.objects.get(id=operator_id)
    except Operator.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Operator not found'}, status=404)

    # Find or create draft
    draft = VisitLog.objects.filter(operator=operator, is_completed=False).order_by('-created_at').first()

    if draft:
        is_check_in = draft.is_check_in
    else:
        last_completed = VisitLog.objects.filter(operator=operator, is_completed=True).order_by('-created_at').first()
        is_check_in = not (last_completed and last_completed.is_check_in)

    if draft:
        form = VisitLogForm(request.POST, instance=draft, draft=True)
    else:
        form = VisitLogForm(request.POST, draft=True)

    if form.is_valid():
        visit = form.save(commit=False)
        visit.operator = operator
        # The form has the is_check_in choice now
        visit.is_completed = False
        if not visit.timestamp:
            visit.timestamp = timezone.now()
        if visit.machine:
            visit.raw_machine_name = visit.machine.name
        visit.save()
        return JsonResponse({'status': 'ok', 'draft_id': visit.id})
    else:
        # Even if form is invalid in draft mode, try to save what we can
        # by creating/updating raw instance
        if draft:
            # Update fields that were provided
            for field_name in form.fields:
                if field_name in request.POST and request.POST[field_name]:
                    try:
                        if field_name == 'machine':
                            from .models import Machine as MachineModel
                            mid = request.POST[field_name]
                            if mid:
                                draft.machine = MachineModel.objects.get(id=int(mid))
                        elif hasattr(draft, field_name):
                            setattr(draft, field_name, request.POST[field_name])
                    except Exception:
                        pass
            draft.save()
            return JsonResponse({'status': 'ok', 'draft_id': draft.id})
        return JsonResponse({'status': 'error', 'errors': form.errors}, status=400)


def car_log_form(request):
    """Form for car operators (drivers) to submit their trip logs."""
    import os
    import json
    from django.conf import settings as django_settings
    from .forms import CarLogForm
    from .models import CarLogImage, CarLogStop

    operator_id = request.session.get('operator_id')
    if not operator_id:
        return redirect('logistics:operator_login')

    try:
        operator = Operator.objects.get(id=operator_id)
    except Operator.DoesNotExist:
        request.session.flush()
        return redirect('logistics:operator_login')

    success = False
    machines = Machine.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        form = CarLogForm(request.POST, request.FILES)
        if form.is_valid():
            car_log = form.save(commit=False)
            car_log.driver = operator
            car_log.save()

            # Handle ordered machine stops from hidden input
            stop_ids_raw = request.POST.get('stop_ids', '')
            if stop_ids_raw:
                for idx, mid in enumerate(stop_ids_raw.split(',')):
                    mid = mid.strip()
                    if mid.isdigit():
                        try:
                            machine = Machine.objects.get(id=int(mid))
                            CarLogStop.objects.create(car_log=car_log, machine=machine, order=idx)
                        except Machine.DoesNotExist:
                            pass

            # Handle multiple paper image uploads
            images = request.FILES.getlist('paper_images')
            for img in images:
                CarLogImage.objects.create(car_log=car_log, image=img)

            messages.success(request, 'تم تسجيل الرحلة بنجاح!')
            return redirect('logistics:car_form')
    else:
        form = CarLogForm()

    return render(request, 'logistics/car_log_form.html', {
        'form': form,
        'operator': operator,
        'machines': machines,
    })


def operator_logout(request):
    """Clear operator session."""
    request.session.pop('operator_id', None)
    return redirect('logistics:operator_login')


# ===================================================================
# Phase 10: Supervisor Frontend
# ===================================================================

def supervisor_dashboard(request):
    """Main supervisor dashboard showing all operators for today."""
    operator_id = request.session.get('operator_id')
    if not operator_id:
        return redirect('logistics:operator_login')
    try:
        supervisor = Operator.objects.get(id=operator_id, is_supervisor=True)
    except Operator.DoesNotExist:
        request.session.flush()
        return redirect('logistics:operator_login')

    # Date filter (default today)
    date_str = request.GET.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = date.today()
    else:
        selected_date = date.today()

    # All non-supervisor operators
    operators = Operator.objects.filter(is_active=True, is_supervisor=False).order_by('name')

    # Get existing supervisor logs for this date
    today_logs = SupervisorDailyLog.objects.filter(
        supervisor=supervisor, date=selected_date
    ).select_related('operator').prefetch_related('images')
    log_map = {log.operator_id: log for log in today_logs}

    # Get today's visit logs for operator status
    visit_logs_today = VisitLog.objects.filter(
        timestamp__date=selected_date,
        is_completed=True
    ).select_related('operator', 'machine')
    visit_map = {}
    for vl in visit_logs_today:
        if vl.operator_id not in visit_map:
            visit_map[vl.operator_id] = []
        visit_map[vl.operator_id].append(vl)

    operator_data = []
    completed_count = 0
    attended_count = 0
    for op in operators:
        log = log_map.get(op.id)
        visits = visit_map.get(op.id, [])

        # Determine check-in status from visit logs
        last_visit = visits[-1] if visits else None
        if last_visit:
            if last_visit.is_check_in:
                checkin_status = 'checked_in'
                checkin_label = 'مسجل دخول'
            else:
                checkin_status = 'checked_out'
                checkin_label = 'مسجل خروج'
        else:
            checkin_status = 'no_activity'
            checkin_label = 'لا نشاط'

        # Collect operator issues from today's visits
        op_issues = []
        for v in visits:
            if v.product_issue and v.product_issue.strip():
                op_issues.append(v.product_issue.strip())
            if v.machine_issue and v.machine_issue.strip():
                op_issues.append(v.machine_issue.strip())

        if log and log.is_completed:
            completed_count += 1
        if log and log.attended:
            attended_count += 1

        operator_data.append({
            'operator': op,
            'log': log,
            'attended': log.attended if log else None,
            'rating': log.rating if log else None,
            'is_completed': log.is_completed if log else False,
            'is_draft': log is not None and not log.is_completed,
            'checkin_status': checkin_status,
            'checkin_label': checkin_label,
            'visit_count': len(visits),
            'op_issues': op_issues,
        })

    context = {
        'supervisor': supervisor,
        'selected_date': selected_date,
        'operator_data': operator_data,
        'total_operators': len(operator_data),
        'completed_count': completed_count,
        'attended_count': attended_count,
        'pending_count': len(operator_data) - completed_count,
    }
    return render(request, 'logistics/supervisor_dashboard.html', context)


def supervisor_operator_form(request, operator_id):
    """Per-operator daily review form for supervisors."""
    from .forms import SupervisorDailyLogForm

    sid = request.session.get('operator_id')
    if not sid:
        return redirect('logistics:operator_login')
    try:
        supervisor = Operator.objects.get(id=sid, is_supervisor=True)
    except Operator.DoesNotExist:
        request.session.flush()
        return redirect('logistics:operator_login')

    try:
        target_operator = Operator.objects.get(id=operator_id)
    except Operator.DoesNotExist:
        messages.error(request, 'المشغل غير موجود')
        return redirect('logistics:supervisor_dashboard')

    today = date.today()

    # Get or find existing log
    try:
        log_instance = SupervisorDailyLog.objects.get(
            supervisor=supervisor, operator=target_operator, date=today
        )
    except SupervisorDailyLog.DoesNotExist:
        log_instance = None

    # Pre-populate operator issues from today's visit logs
    today_visits = VisitLog.objects.filter(
        operator=target_operator,
        timestamp__date=today,
        is_completed=True
    )
    auto_issues = []
    for v in today_visits:
        if v.product_issue and v.product_issue.strip():
            auto_issues.append(f"منتجات: {v.product_issue.strip()}")
        if v.machine_issue and v.machine_issue.strip():
            auto_issues.append(f"ماكينة: {v.machine_issue.strip()}")
        if v.comments and v.comments.strip():
            auto_issues.append(f"تعليق: {v.comments.strip()}")

    if request.method == 'POST':
        action = request.POST.get('action', 'complete')
        is_draft = (action == 'draft')

        if log_instance:
            form = SupervisorDailyLogForm(request.POST, instance=log_instance, draft=is_draft)
        else:
            form = SupervisorDailyLogForm(request.POST, draft=is_draft)

        if form.is_valid():
            log = form.save(commit=False)
            log.supervisor = supervisor
            log.operator = target_operator
            if not log.date:
                log.date = today

            if is_draft:
                log.is_completed = False
                log.save()
                messages.info(request, 'تم حفظ المسودة بنجاح')
            else:
                log.is_completed = True
                log.save()
                # Handle photo uploads
                photos = request.FILES.getlist('supervisor_photos')
                for photo in photos:
                    SupervisorDailyLogImage.objects.create(log=log, image=photo)
                messages.success(request, f'تم حفظ تقييم {target_operator.name} بنجاح!')

            return redirect('logistics:supervisor_dashboard')
    else:
        initial = {'date': today}
        if not log_instance and auto_issues:
            initial['operator_reported_issues'] = '\n'.join(auto_issues)
        if log_instance:
            form = SupervisorDailyLogForm(instance=log_instance, draft=True)
        else:
            form = SupervisorDailyLogForm(initial=initial, draft=True)

    # Get existing images
    existing_images = log_instance.images.all() if log_instance else []

    context = {
        'form': form,
        'supervisor': supervisor,
        'target_operator': target_operator,
        'today': today,
        'is_draft': log_instance is not None and not log_instance.is_completed,
        'is_edit': log_instance is not None and log_instance.is_completed,
        'existing_images': existing_images,
        'today_visits': today_visits,
    }
    return render(request, 'logistics/supervisor_form.html', context)


# ===================================================================
# Dashboard Authentication
# ===================================================================

def dashboard_login_view(request):
    """Username/password login for the admin dashboard."""
    from django.contrib.auth import authenticate, login

    if request.user.is_authenticated:
        return redirect('logistics:dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.POST.get('next') or request.GET.get('next') or 'logistics:dashboard'
            # If it looks like a URL path, redirect directly; otherwise use named URL
            if next_url.startswith('/'):
                return redirect(next_url)
            return redirect(next_url)
        else:
            from django import forms
            form_obj = type('LoginForm', (), {'errors': {'__all__': ['Invalid credentials']}})()
            return render(request, 'logistics/dashboard_login.html', {'form': form_obj})

    return render(request, 'logistics/dashboard_login.html', {'form': None})


def dashboard_logout_view(request):
    """Logout from the admin dashboard."""
    from django.contrib.auth import logout
    logout(request)
    return redirect('logistics:dashboard_login')


@login_required
def daily_machine_summary(request):
    """
    Shows a daily summary of all machines:
    - Did an Operator visit? (VisitLog)
    - Did a Car driver visit? (CarLogStop)
    - Any issues reported?
    - Distance between machine and visit location.
    """
    import math
    from .models import CarLogStop, VisitLog, Machine, SupervisorDailyReport

    def haversine(lat1, lon1, lat2, lon2):
        """Calculate distance in meters between two GPS points."""
        R = 6371000  # Earth radius in meters
        phi1 = math.radians(float(lat1))
        phi2 = math.radians(float(lat2))
        dphi = math.radians(float(lat2) - float(lat1))
        dlambda = math.radians(float(lon2) - float(lon1))
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def parse_coords(location_str):
        """Parse 'lat, lng' string into (lat, lng) tuple or None."""
        if not location_str:
            return None
        parts = location_str.strip().split(',')
        if len(parts) == 2:
            try:
                return float(parts[0].strip()), float(parts[1].strip())
            except ValueError:
                return None
        return None

    # 1. Get Date
    date_str = request.GET.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = date.today()
    else:
        selected_date = date.today()

    # 2. Get All Machines
    machines = Machine.objects.filter(is_active=True).order_by('name')

    # 3. Get Operator Visits for the day (with images prefetched)
    visit_logs = VisitLog.objects.filter(
        timestamp__year=selected_date.year,
        timestamp__month=selected_date.month,
        timestamp__day=selected_date.day
    ).select_related('operator', 'machine').prefetch_related('images')

    # Build a map: machine_id -> list of visit logs
    visit_log_map = {}
    for log in visit_logs:
        if log.machine_id not in visit_log_map:
            visit_log_map[log.machine_id] = []
        visit_log_map[log.machine_id].append(log)

    # 4. Get Car Visits (CarLogStop) for the day
    car_stops = CarLogStop.objects.filter(
        car_log__trip_date=selected_date
    ).select_related('car_log', 'car_log__driver', 'machine').order_by('car_log__driver__name', 'order')

    car_stop_map = {}
    for stop in car_stops:
        if stop.machine_id not in car_stop_map:
            car_stop_map[stop.machine_id] = []
        car_stop_map[stop.machine_id].append(stop)

    # 5. Get Supervisor Reports for the day
    supervisor_reports = SupervisorDailyReport.objects.filter(date=selected_date).select_related('supervisor').prefetch_related('operator_reviews__operator', 'images')

    # 6. Build Summary Data
    summary_data = []
    total_visited_operator = 0
    total_visited_car = 0
    issues_count = 0
    total_voids_today = 0
    total_transactions_today = 0

    for machine in machines:
        v_logs = visit_log_map.get(machine.id, [])
        v_log = v_logs[0] if v_logs else None  # primary log for backward compat
        c_stops = car_stop_map.get(machine.id)

        has_issue = False
        car_issues_text = ""
        distance_display = ""
        machine_map_url = ""
        visit_map_url = ""
        both_map_url = ""

        # Machine coordinates
        machine_coords = None
        if machine.latitude and machine.longitude:
            machine_coords = (float(machine.latitude), float(machine.longitude))
            machine_map_url = f"https://www.google.com/maps?q={machine.latitude},{machine.longitude}"

        if v_log:
            total_visited_operator += 1
            for vl in v_logs:
                total_voids_today += vl.voids or 0
                total_transactions_today += vl.transactions or 0
                if vl.machine_issue or vl.product_issue:
                    has_issue = True

            # Parse visit location coordinates (from primary log)
            visit_coords = parse_coords(v_log.visit_location)
            if visit_coords:
                visit_map_url = f"https://www.google.com/maps?q={visit_coords[0]},{visit_coords[1]}"

                # Calculate distance if both coordinates exist
                if machine_coords:
                    distance_m = haversine(
                        machine_coords[0], machine_coords[1],
                        visit_coords[0], visit_coords[1]
                    )
                    if distance_m < 1000:
                        distance_display = f"{distance_m:.0f}m"
                    else:
                        distance_display = f"{distance_m / 1000:.1f}km"

                    # Google Maps directions link (machine -> visit)
                    both_map_url = (
                        f"https://www.google.com/maps/dir/"
                        f"{machine_coords[0]},{machine_coords[1]}/"
                        f"{visit_coords[0]},{visit_coords[1]}"
                    )

        if c_stops:
            total_visited_car += 1
            issues_list = []
            for stop in c_stops:
                if stop.car_log.issues:
                    issues_list.append(stop.car_log.issues)
            if issues_list:
                car_issues_text = "; ".join(list(set(issues_list)))

        if has_issue:
            issues_count += 1

        summary_data.append({
            'machine': machine,
            'visit_log': v_log,
            'visit_logs': v_logs,  # all visit logs for this machine today
            'car_stops': c_stops,
            'car_issues': car_issues_text,
            'distance_display': distance_display,
            'machine_map_url': machine_map_url,
            'visit_map_url': visit_map_url,
            'both_map_url': both_map_url,
        })

    context = {
        'selected_date': selected_date,
        'prev_date': selected_date - timedelta(days=1),
        'next_date': selected_date + timedelta(days=1),
        'summary_data': summary_data,
        'total_machines': machines.count(),
        'visited_count': total_visited_operator,
        'car_visited_count': total_visited_car,
        'issues_count': issues_count,
        'total_voids_today': total_voids_today,
        'total_transactions_today': total_transactions_today,
        'supervisor_reports': supervisor_reports,
    }

    return render(request, 'logistics/daily_machine_summary.html', context)


# ===================================================================
# Supervisor (Head of Operators) Views
# ===================================================================

def supervisor_login(request):
    """Code-based login for supervisor."""
    from .models import SupervisorProfile

    error = None
    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        try:
            supervisor = SupervisorProfile.objects.get(code=code, is_active=True)
            request.session['supervisor_id'] = supervisor.id
            return redirect('logistics:supervisor_dashboard')
        except SupervisorProfile.DoesNotExist:
            error = 'الكود غير صحيح. يرجى المحاولة مرة أخرى.'

    return render(request, 'logistics/supervisor_login.html', {'error': error})


def supervisor_required(view_func):
    """Decorator to check supervisor session."""
    from functools import wraps
    from .models import SupervisorProfile

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        supervisor_id = request.session.get('supervisor_id')
        if not supervisor_id:
            return redirect('logistics:supervisor_login')
        try:
            request.supervisor = SupervisorProfile.objects.get(id=supervisor_id, is_active=True)
        except SupervisorProfile.DoesNotExist:
            request.session.pop('supervisor_id', None)
            return redirect('logistics:supervisor_login')
        return view_func(request, *args, **kwargs)
    return wrapper


@supervisor_required
def supervisor_dashboard(request):
    """Read-only dashboard for supervisor showing today's overview."""
    from .models import (
        SupervisorDailyReport, SupervisorOperatorReview,
        CarLog, CarLogStop, OperatorDailyRating
    )

    selected_date = date.today()
    date_str = request.GET.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = date.today()

    supervisor = request.supervisor

    # Get all active operators
    operators = Operator.objects.filter(is_active=True).order_by('name')

    # Get today's visit logs
    todays_visits = VisitLog.objects.filter(
        timestamp__year=selected_date.year,
        timestamp__month=selected_date.month,
        timestamp__day=selected_date.day,
        is_completed=True,
    ).select_related('operator', 'machine')

    visit_map = {}
    for vl in todays_visits:
        if vl.operator_id:
            if vl.operator_id not in visit_map:
                visit_map[vl.operator_id] = []
            visit_map[vl.operator_id].append(vl)

    # Get today's car logs
    car_logs = CarLog.objects.filter(trip_date=selected_date).select_related('driver').prefetch_related('stops__machine')
    car_map = {}
    for cl in car_logs:
        if cl.driver_id:
            car_map[cl.driver_id] = cl

    # Get supervisor's existing report for this date
    existing_report = SupervisorDailyReport.objects.filter(
        supervisor=supervisor, date=selected_date
    ).prefetch_related('operator_reviews__operator', 'images').first()

    review_map = {}
    if existing_report:
        for rev in existing_report.operator_reviews.all():
            review_map[rev.operator_id] = rev

    # Build operator data
    operator_data = []
    for op in operators:
        visits = visit_map.get(op.id, [])
        car_log = car_map.get(op.id)
        review = review_map.get(op.id)

        # Build machines with visit log IDs for linking
        machines_visited = []
        seen_ids = set()
        if visits:
            for v in visits:
                if v.machine and v.machine.id not in seen_ids:
                    seen_ids.add(v.machine.id)
                    machines_visited.append({'name': v.machine.name, 'visit_log_id': v.id})
        if car_log:
            for s in car_log.stops.all():
                if s.machine and s.machine.id not in seen_ids:
                    seen_ids.add(s.machine.id)
                    machines_visited.append({'name': s.machine.name, 'visit_log_id': None})

        comments = [v.comments for v in visits if v.comments and v.comments.strip()]

        operator_data.append({
            'operator': op,
            'visits': visits,
            'car_log': car_log,
            'machines_visited': machines_visited,
            'comments': comments,
            'review': review,
            'has_activity': bool(visits or car_log),
        })

    context = {
        'supervisor': supervisor,
        'selected_date': selected_date,
        'operator_data': operator_data,
        'existing_report': existing_report,
        'total_operators': operators.count(),
        'active_today': sum(1 for d in operator_data if d['has_activity']),
        'inactive_count': sum(1 for d in operator_data if not d['has_activity']),
        'prev_date': selected_date - timedelta(days=1),
        'next_date': selected_date + timedelta(days=1),
    }
    return render(request, 'logistics/supervisor_dashboard.html', context)


@supervisor_required
def supervisor_daily_form(request):
    """Form for supervisor to submit daily review of all operators."""
    from .models import (
        SupervisorDailyReport, SupervisorOperatorReview, SupervisorReportImage
    )

    supervisor = request.supervisor
    today = date.today()

    # Get all active operators
    operators = Operator.objects.filter(is_active=True).order_by('name')

    # Check for existing report
    existing_report = SupervisorDailyReport.objects.filter(
        supervisor=supervisor, date=today
    ).first()

    existing_reviews_map = {}
    if existing_report:
        for rev in existing_report.operator_reviews.all():
            existing_reviews_map[rev.operator_id] = rev

    if request.method == 'POST':
        # Save the main report
        location = request.POST.get('location', '')
        general_issues = request.POST.get('general_issues', '')
        reported_issues = request.POST.get('reported_issues', '')
        general_comments = request.POST.get('general_comments', '')

        report, created = SupervisorDailyReport.objects.update_or_create(
            supervisor=supervisor,
            date=today,
            defaults={
                'location': location,
                'general_issues': general_issues,
                'reported_issues': reported_issues,
                'general_comments': general_comments,
            }
        )

        # Save per-operator reviews
        for op in operators:
            attended = request.POST.get(f'attended_{op.id}') == 'on'
            op_location = request.POST.get(f'location_{op.id}', '')
            rating = request.POST.get(f'rating_{op.id}', '0')
            op_comments = request.POST.get(f'comments_{op.id}', '')

            try:
                rating_val = int(rating)
            except ValueError:
                rating_val = 0

            SupervisorOperatorReview.objects.update_or_create(
                report=report,
                operator=op,
                defaults={
                    'attended': attended,
                    'location': op_location,
                    'rating': max(0, min(10, rating_val)),
                    'comments': op_comments,
                }
            )

            # Also sync to OperatorDailyRating
            if rating_val > 0:
                from .models import OperatorDailyRating
                OperatorDailyRating.objects.update_or_create(
                    operator=op,
                    date=today,
                    defaults={'rating': max(0, min(10, rating_val))}
                )

        # Handle image uploads
        images = request.FILES.getlist('report_images')
        for img in images:
            SupervisorReportImage.objects.create(report=report, image=img)

        messages.success(request, 'تم حفظ التقرير اليومي بنجاح!')
        return redirect('logistics:supervisor_dashboard')

    # Attach reviews to operators for easy template access
    operators_with_reviews = []
    for op in operators:
        rev = existing_reviews_map.get(op.id)
        operators_with_reviews.append({
            'op': op,
            'review': rev,
        })

    context = {
        'supervisor': supervisor,
        'today': today,
        'operators_with_reviews': operators_with_reviews,
        'existing_report': existing_report,
    }
    return render(request, 'logistics/supervisor_daily_form.html', context)


@supervisor_required
def supervisor_report_history(request):
    """View past supervisor reports."""
    from .models import SupervisorDailyReport

    supervisor = request.supervisor
    reports = SupervisorDailyReport.objects.filter(
        supervisor=supervisor
    ).prefetch_related('operator_reviews__operator', 'images').order_by('-date')[:30]

    context = {
        'supervisor': supervisor,
        'reports': reports,
    }
    return render(request, 'logistics/supervisor_history.html', context)


def supervisor_logout(request):
    """Clear supervisor session."""
    request.session.pop('supervisor_id', None)
    return redirect('logistics:supervisor_login')

