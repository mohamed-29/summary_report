from django.contrib import admin
from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget
from .models import Operator, Machine, MachineAlias, VisitLog, CarLog, MonthlyReport, CarLogImage, CarLogStop, VisitLogImage


# ── Resources ──────────────────────────────────────────────

class OperatorResource(resources.ModelResource):
    class Meta:
        model = Operator
        fields = ('id', 'name', 'code', 'is_driver', 'is_active', 'created_at', 'updated_at')
        export_order = fields


class MachineResource(resources.ModelResource):
    class Meta:
        model = Machine
        fields = ('id', 'name', 'code', 'location', 'latitude', 'longitude', 'is_active', 'created_at')
        export_order = fields


class VisitLogResource(resources.ModelResource):
    operator_name = fields.Field(column_name='operator_name', attribute='operator', widget=ForeignKeyWidget(Operator, field='name'))
    machine_name = fields.Field(column_name='machine_name', attribute='machine', widget=ForeignKeyWidget(Machine, field='name'))

    class Meta:
        model = VisitLog
        fields = (
            'id', 'timestamp', 'operator_name', 'machine_name',
            'is_check_in', 'is_completed',
            'visit_location',
            'received_keys', 'pos_verified', 'product_review_done',
            'no_sold_out', 'quantity_review_done', 'expiry_verified',
            'shipment_info', 'arrival_time', 'stock_details',
            'cleanliness_rating', 'customer_satisfaction',
            'transactions', 'voids', 'void_percentage',
            'product_issue', 'machine_issue', 'comments',
            'created_at', 'updated_at',
        )
        export_order = fields


class CarLogResource(resources.ModelResource):
    driver_name = fields.Field(column_name='driver_name', attribute='driver', widget=ForeignKeyWidget(Operator, field='name'))

    class Meta:
        model = CarLog
        fields = (
            'id', 'trip_date', 'driver_name',
            'received_keys', 'received_locations', 'received_shipment_full',
            'issues', 'exit_time', 'return_time',
            'created_at', 'updated_at',
        )
        export_order = fields


class MachineAliasResource(resources.ModelResource):
    machine_name = fields.Field(column_name='machine_name', attribute='machine', widget=ForeignKeyWidget(Machine, field='name'))

    class Meta:
        model = MachineAlias
        fields = ('id', 'alias', 'machine_name', 'source', 'confidence_score', 'created_at')
        export_order = fields


# ── Admin ──────────────────────────────────────────────────

@admin.register(Operator)
class OperatorAdmin(ImportExportModelAdmin):
    resource_class = OperatorResource
    list_display = ['name', 'code', 'is_driver', 'is_active', 'created_at']
    list_filter = ['is_driver', 'is_active']
    search_fields = ['name']
    ordering = ['name']


@admin.register(Machine)
class MachineAdmin(ImportExportModelAdmin):
    resource_class = MachineResource
    list_display = ['name', 'code', 'location', 'latitude', 'longitude', 'is_active', 'alias_count', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'location']
    list_editable = ['latitude', 'longitude']
    ordering = ['name']

    def alias_count(self, obj):
        return obj.aliases.count()
    alias_count.short_description = 'Aliases'


@admin.register(MachineAlias)
class MachineAliasAdmin(ImportExportModelAdmin):
    resource_class = MachineAliasResource
    list_display = ['alias', 'machine', 'source', 'confidence_score', 'created_at']
    list_filter = ['source', 'machine']
    search_fields = ['alias', 'machine__name']
    ordering = ['alias']
    autocomplete_fields = ['machine']



class VisitLogImageInline(admin.TabularInline):
    model = VisitLogImage
    extra = 0
    readonly_fields = ['uploaded_at']


@admin.register(VisitLog)
class VisitLogAdmin(ImportExportModelAdmin):
    resource_class = VisitLogResource
    list_display = ['timestamp', 'operator', 'machine', 'is_check_in', 'is_completed', 'transactions', 'voids', 'void_percentage']
    list_filter = ['is_check_in', 'is_completed', 'machine', 'operator', 'timestamp']
    search_fields = ['machine__name', 'operator__name', 'comments']
    date_hierarchy = 'timestamp'
    ordering = ['-timestamp']
    autocomplete_fields = ['machine', 'operator']
    readonly_fields = ['void_percentage', 'raw_machine_name']
    inlines = [VisitLogImageInline]



class CarLogImageInline(admin.TabularInline):
    model = CarLogImage
    extra = 0
    readonly_fields = ['uploaded_at']


class CarLogStopInline(admin.TabularInline):
    model = CarLogStop
    extra = 0
    autocomplete_fields = ['machine']
    ordering = ['order']


@admin.register(CarLog)
class CarLogAdmin(ImportExportModelAdmin):
    resource_class = CarLogResource
    list_display = ['trip_date', 'driver', 'stop_count', 'exit_time', 'return_time', 'has_issues', 'created_at']
    list_filter = ['driver', 'trip_date', 'received_keys', 'received_locations', 'received_shipment_full']
    search_fields = ['driver__name', 'issues']
    date_hierarchy = 'trip_date'
    ordering = ['-trip_date']
    autocomplete_fields = ['driver']
    inlines = [CarLogStopInline, CarLogImageInline]

    def stop_count(self, obj):
        return obj.stops.count()
    stop_count.short_description = 'Stops'

    def has_issues(self, obj):
        return bool(obj.issues.strip())
    has_issues.boolean = True
    has_issues.short_description = 'Issues?'


@admin.register(MonthlyReport)
class MonthlyReportAdmin(admin.ModelAdmin):
    list_display = ['machine', 'month', 'total_transactions', 'total_voids', 'void_percentage', 'has_ai_summary']
    list_filter = ['machine', 'month']
    search_fields = ['machine__name', 'ai_summary']
    date_hierarchy = 'month'
    ordering = ['-month', 'machine__name']
    readonly_fields = ['void_percentage', 'generated_at']

    def has_ai_summary(self, obj):
        return bool(obj.ai_summary.strip())
    has_ai_summary.boolean = True
    has_ai_summary.short_description = 'AI Summary?'


from .models import OperatorDailyRating

@admin.register(OperatorDailyRating)
class OperatorDailyRatingAdmin(admin.ModelAdmin):
    list_display = ['operator', 'date', 'rating', 'updated_at']
    list_filter = ['date', 'operator']
    search_fields = ['operator__name']
    date_hierarchy = 'date'
    ordering = ['-date', 'operator__name']
    autocomplete_fields = ['operator']
