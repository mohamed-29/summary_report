from django.urls import path
from . import views

app_name = 'logistics'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('machine/<int:machine_id>/', views.machine_detail, name='machine_detail'),
    path('generate-summaries/', views.generate_summaries, name='generate_summaries'),
    path('upload/onsite/', views.upload_onsite_logs, name='upload_onsite'),
    path('upload/car/', views.upload_car_logs, name='upload_car'),
    path('operator/<int:operator_id>/', views.operator_detail, name='operator_detail'),
    path('operators/', views.operator_list, name='operator_list'),
    path('daily-summary/', views.daily_machine_summary, name='daily_machine_summary'),
    # Operator Frontend (Phase 9)
    path('form/login/', views.operator_login, name='operator_login'),
    path('form/', views.visit_log_form, name='visit_form'),
    path('form/auto-save/', views.visit_auto_save, name='visit_auto_save'),
    path('form/car/', views.car_log_form, name='car_form'),
    path('form/logout/', views.operator_logout, name='operator_logout'),
    # Dashboard Auth
    path('auth/login/', views.dashboard_login_view, name='dashboard_login'),
    path('auth/logout/', views.dashboard_logout_view, name='dashboard_logout'),
]
