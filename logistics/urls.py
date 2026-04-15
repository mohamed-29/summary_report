from django.urls import path
from . import views

app_name = 'logistics'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('set-language/', views.set_language, name='set_language'),
    path('machine/<int:machine_id>/', views.machine_detail, name='machine_detail'),
    path('generate-summaries/', views.generate_summaries, name='generate_summaries'),
    path('upload/onsite/', views.upload_onsite_logs, name='upload_onsite'),
    path('upload/car/', views.upload_car_logs, name='upload_car'),
    path('operator/<int:operator_id>/', views.operator_detail, name='operator_detail'),
    path('operators/', views.operator_list, name='operator_list'),
    path('operators/generate-report/', views.generate_operator_report, name='generate_operator_report'),
    path('visit-log/<int:log_id>/download/', views.download_visit_log, name='download_visit_log'),
    path('visit-log/<int:log_id>/', views.visit_log_detail, name='visit_log_detail'),
    path('daily-summary/', views.daily_machine_summary, name='daily_machine_summary'),
    # Operator Frontend (Phase 9)
    path('form/login/', views.operator_login, name='operator_login'),
    path('form/', views.visit_log_form, name='visit_form'),
    path('form/auto-save/', views.visit_auto_save, name='visit_auto_save'),
    path('form/car/', views.car_log_form, name='car_form'),
    path('form/logout/', views.operator_logout, name='operator_logout'),
    # Supervisor Frontend
    path('supervisor/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('supervisor/operator/<int:operator_id>/', views.supervisor_operator_form, name='supervisor_operator_form'),
    # Dashboard Auth
    path('auth/login/', views.dashboard_login_view, name='dashboard_login'),
    path('auth/logout/', views.dashboard_logout_view, name='dashboard_logout'),
    # Supervisor (Head of Operators)
    path('supervisor/login/', views.supervisor_login, name='supervisor_login'),
    path('supervisor/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('supervisor/form/', views.supervisor_daily_form, name='supervisor_daily_form'),
    path('supervisor/history/', views.supervisor_report_history, name='supervisor_report_history'),
    path('supervisor/logout/', views.supervisor_logout, name='supervisor_logout'),
]
