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
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.utils import timezone



from .models import Machine, VisitLog, MonthlyReport, Operator


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

    context = {
        'summary_data': summary_data,
        'grand_totals': grand_totals,
        'selected_month': selected_month,
        'available_months': available_months,
        'has_gemini_key': bool(getattr(settings, 'GEMINI_API_KEY', '')),
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

    # Check if Gemini is configured
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        messages.error(request, 'Gemini API key not configured.')
        return redirect('logistics:dashboard')

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-3-flash-preview')
    except Exception as e:
        messages.error(request, f'Failed to initialize Gemini: {e}')
        return redirect('logistics:dashboard')

    # Get date range
    month_start = selected_month
    month_end = (selected_month.replace(day=28) + timedelta(days=4)).replace(day=1)

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
            total_trans = sum(log.transactions for log in visit_logs)
            total_voids = sum(log.voids for log in visit_logs)
            
            machines_data.append({
                'machine': machine,
                'comments': comments,
                'total_trans': total_trans,
                'total_voids': total_voids
            })
        except Machine.DoesNotExist:
            continue

    # 2. Process in Batches
    BATCH_SIZE = 5
    for i in range(0, len(machines_data), BATCH_SIZE):
        batch = machines_data[i:i+BATCH_SIZE]
        
        # Filter for machines that need AI analysis (have comments)
        ai_batch = []
        for data in batch:
            if not data['comments']:
                # No comments -> No AI needed
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
            else:
                ai_batch.append(data)
        
        if not ai_batch:
            continue

        # 3. Build Prompt for AI Batch
        prompt = """Analyze the maintenance logs for these vending machines.
For each machine, provide a single concise sentence (max 30 words) summarizing its mechanical health and any recurring issues.
If a machine has no significant issues reported, state "No significant issues reported."

Input Data:
"""
        for data in ai_batch:
            comments_text = "; ".join(data['comments'])[:1000]  # Limit context per machine
            prompt += f"Machine {data['machine'].id} ({data['machine'].name}): {comments_text}\n\n"

        prompt += """
Return a VALID JSON object where keys are the Machine IDs (as strings) and values are the summaries.
Example: {"123": "Coins jammed repeatedly.", "456": "No significant issues reported."}
"""

        # 4. Call AI
        try:
            response = model.generate_content(prompt)
            text_response = response.text.strip()
            # Clean markdown code blocks if present
            if text_response.startswith('```json'):
                text_response = text_response[7:-3]
            elif text_response.startswith('```'):
                text_response = text_response[3:-3]
                
            results = json.loads(text_response)

            # 5. Save Results
            for data in ai_batch:
                m_id = str(data['machine'].id)
                summary = results.get(m_id, "Summary generation failed.")
                
                MonthlyReport.objects.update_or_create(
                    machine=data['machine'],
                    month=selected_month,
                    defaults={
                        'total_transactions': data['total_trans'],
                        'total_voids': data['total_voids'],
                        'ai_summary': summary,
                        'raw_comments': "\n---\n".join(data['comments']),
                    }
                )
                generated_count += 1
            
            # Rate limiting pause
            time.sleep(2)

        except Exception as e:
            messages.warning(request, f"Batch failed: {e}")
            continue

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

    context = {
        'machine': machine,
        'logs': logs,
        'aliases': machine.aliases.all(),
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

    # Get stats for the month
    month_visits = VisitLog.objects.filter(
        operator=operator,
        timestamp__gte=month_start,
        timestamp__lt=month_end
    )

    total_visits = month_visits.count()
    unique_machines = month_visits.values('machine').distinct().count()
    
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
        'logs': month_visits.select_related('machine').order_by('-timestamp')[:100],  # Show recent 100 logs
    }
    return render(request, 'logistics/operator_detail.html', context)


@login_required
def operator_list(request):
    """List all operators with summary stats for the selected month."""
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

    # Get operators with stats
    operators = Operator.objects.annotate(
        visit_count=Count('visit_logs', filter=models.Q(visit_logs__timestamp__gte=month_start, visit_logs__timestamp__lt=month_end)),
        machine_count=Count('visit_logs__machine', distinct=True, filter=models.Q(visit_logs__timestamp__gte=month_start, visit_logs__timestamp__lt=month_end))
    ).order_by('name')

    # Get ratings map
    from .models import OperatorDailyRating
    ratings = OperatorDailyRating.objects.filter(date=date.today()).values('operator_id', 'rating')
    rating_map = {r['operator_id']: r['rating'] for r in ratings}

    # Attach ratings to operators manually
    for op in operators:
        op.daily_rating = rating_map.get(op.id, '-')

    context = {
        'operators': operators,
        'selected_month': selected_month,
        'available_months': available_months,
    }
    return render(request, 'logistics/operator_list.html', context)


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
                if operator.is_driver:
                    return redirect('logistics:car_form')
                return redirect('logistics:visit_form')
            except Operator.DoesNotExist:
                error = 'الكود غير صحيح. يرجى المحاولة مرة أخرى.'

    return render(request, 'logistics/operator_login.html', {
        'form': form,
        'error': error,
    })


def visit_log_form(request):
    """Form for operators to submit their visit logs with photos."""
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

    success = False

    if request.method == 'POST':
        form = VisitLogForm(request.POST, request.FILES)
        if form.is_valid():
            visit = form.save(commit=False)
            visit.operator = operator
            visit.timestamp = timezone.now()
            visit.raw_machine_name = visit.machine.name

            # Handle multiple photo uploads via VisitLogImage
            photos = request.FILES.getlist('machine_photos')
            visit.save()
            for photo in photos:
                VisitLogImage.objects.create(visit_log=visit, image=photo)
            
            messages.success(request, 'تم تسجيل الزيارة بنجاح!')
            return redirect('logistics:visit_form')

    else:
        form = VisitLogForm()

    return render(request, 'logistics/visit_form.html', {
        'form': form,
        'operator': operator,
    })


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
    from .models import CarLogStop, VisitLog, Machine

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

    # 3. Get Operator Visits for the day
    visit_logs = VisitLog.objects.filter(
        timestamp__year=selected_date.year,
        timestamp__month=selected_date.month,
        timestamp__day=selected_date.day
    ).select_related('operator', 'machine')

    visit_log_map = {log.machine_id: log for log in visit_logs}

    # 4. Get Car Visits (CarLogStop) for the day
    car_stops = CarLogStop.objects.filter(
        car_log__trip_date=selected_date
    ).select_related('car_log', 'car_log__driver', 'machine').order_by('car_log__driver__name', 'order')

    car_stop_map = {}
    for stop in car_stops:
        if stop.machine_id not in car_stop_map:
            car_stop_map[stop.machine_id] = []
        car_stop_map[stop.machine_id].append(stop)

    # 5. Build Summary Data
    summary_data = []
    total_visited_operator = 0
    total_visited_car = 0
    issues_count = 0

    for machine in machines:
        v_log = visit_log_map.get(machine.id)
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
            if v_log.machine_issue or v_log.product_issue:
                has_issue = True

            # Parse visit location coordinates
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
            'car_stops': c_stops,
            'car_issues': car_issues_text,
            'distance_display': distance_display,
            'machine_map_url': machine_map_url,
            'visit_map_url': visit_map_url,
            'both_map_url': both_map_url,
        })

    context = {
        'selected_date': selected_date,
        'summary_data': summary_data,
        'total_machines': machines.count(),
        'visited_count': total_visited_operator,
        'car_visited_count': total_visited_car,
        'issues_count': issues_count,
    }

    return render(request, 'logistics/daily_machine_summary.html', context)

