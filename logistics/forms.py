from django import forms
from .models import VisitLog, CarLog, Machine


class OperatorLoginForm(forms.Form):
    """Simple form for operator code-based login."""
    code = forms.CharField(
        max_length=10,
        widget=forms.TextInput(attrs={
            'placeholder': 'أدخل الكود الخاص بك',
            'class': 'form-input',
            'autofocus': True,
            'autocomplete': 'off',
        }),
        label='كود المشغل'
    )


class VisitLogForm(forms.ModelForm):
    """Form for operators to log their visit data."""
    machine = forms.ModelChoiceField(
        queryset=Machine.objects.filter(is_active=True).order_by('name'),
        empty_label='-- اختر الماكينة --',
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='اسم الماكينة'
    )

    FORM_TYPE_CHOICES = [('True', '📥 Check In (تسجيل دخول)'), ('False', '📤 Check Out (تسجيل خروج)')]
    is_check_in = forms.TypedChoiceField(
        coerce=lambda x: x == 'True',
        choices=FORM_TYPE_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'form-type-radio'}),
        label='نوع العملية'
    )

    visit_location = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'readonly': 'readonly',
            'placeholder': 'اضغط على زر تحديد الموقع...'
        }),
        label='موقع الزيارة'
    )

    arrival_time = forms.DateTimeField(
        required=True,
        widget=forms.DateTimeInput(attrs={
            'class': 'form-input',
            'type': 'datetime-local'
        }),
        label='وقت الوصول'
    )



    # Checklist fields as TypedChoiceField to map 'نعم'/'لا' to True/False
    CHECKLIST_CHOICES = [('نعم', 'نعم'), ('لا', 'لا')]

    received_keys = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل استلمت مفاتيح الماكينة؟'
    )
    pos_verified = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل تأكدت من أن ال POS يعمل ؟'
    )
    product_review_done = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل أتممت مراجعه الأسم و السعر و الصوره " للمنتج " ؟'
    )
    no_sold_out = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل تأكدت من عدم وجود Sold out أو stop sale ؟'
    )
    quantity_review_done = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل أتممت مراجعه الكميات قبل و بعد وضعها في الماكينه ؟'
    )
    expiry_verified = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل تأكدت من تاريخ صلاحية المنتجات ؟'
    )

    class Meta:
        model = VisitLog
        fields = [
            'is_check_in',
            'machine',
            'visit_location',
            'received_keys',
            'shipment_info',
            'arrival_time',
            'pos_verified',
            'product_review_done',
            'no_sold_out',
            'quantity_review_done',
            'expiry_verified',
            'stock_details',
            'cleanliness_rating',
            'customer_satisfaction',
            'transactions',
            'voids',
            'product_issue',
            'machine_issue',
            'comments',
        ]
        widgets = {
            'shipment_info': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'اسم المشغل الذي أوصل الشحنة وتوقيت وصوله'}),
            'stock_details': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3, 'placeholder': 'اكتب الستوك المتواجد بالتفصيل'}),
            'cleanliness_rating': forms.NumberInput(attrs={'class': 'form-input', 'min': 1, 'max': 5, 'placeholder': '1-5'}),
            'customer_satisfaction': forms.NumberInput(attrs={'class': 'form-input', 'min': 1, 'max': 5, 'placeholder': '1-5'}),
            'transactions': forms.NumberInput(attrs={'class': 'form-input', 'min': 0, 'placeholder': 'عدد المعاملات'}),
            'voids': forms.NumberInput(attrs={'class': 'form-input', 'min': 0, 'placeholder': 'عدد الفويد'}),
            'product_issue': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'هل توجد مشكلة أثناء نزول المنتجات؟'}),
            'machine_issue': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'هل يوجد أي مشاكل بالماكينة؟'}),
            'comments': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'تعليقات إضافية'}),
        }
        labels = {
            'shipment_info': 'معلومات الشحنة والمشغل',
            'stock_details': 'الستوك المتواجد داخل مخزن الماكينة بالأعداد والأسماء',
            'cleanliness_rating': 'تقييم نظافة الماكينة (1-5)',
            'customer_satisfaction': 'رضاء العملاء (1-5)',
            'transactions': 'عدد المعاملات خلال اليوم',
            'voids': 'عدد الفويد في اليوم',
            'product_issue': 'مشاكل نزول المنتجات',
            'machine_issue': 'مشاكل بالماكينة',
            'comments': 'تعليقات',
        }

    def __init__(self, *args, **kwargs):
        self.draft = kwargs.pop('draft', False)
        super().__init__(*args, **kwargs)
        
        if self.draft:
            # Draft / live-save mode: nothing is required
            for field_name, field in self.fields.items():
                field.required = False
        else:
            # Completion mode: everything required except last 3
            not_required = ['product_issue', 'machine_issue', 'comments']
            for field_name, field in self.fields.items():
                if field_name in not_required:
                    field.required = False
                else:
                    field.required = True
                
        # Set initial values for Boolean fields to match 'نعم'/'لا' choice keys
        if self.instance.pk:
            checklist_fields = [
                'received_keys', 'pos_verified', 'product_review_done', 
                'no_sold_out', 'quantity_review_done', 'expiry_verified'
            ]
            for field in checklist_fields:
                val = getattr(self.instance, field)
                self.initial[field] = 'نعم' if val else 'لا'
            self.initial['is_check_in'] = 'True' if self.instance.is_check_in else 'False'


class CarLogForm(forms.ModelForm):
    """Form for car operators / drivers to log their trips."""
    CHECKLIST_CHOICES = [('نعم', 'نعم'), ('لا', 'لا')]

    trip_date = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
        label='التاريخ'
    )

    received_keys = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل استلمت مفاتيح الماكينات قبل التحرك من الشركة ؟'
    )
    received_locations = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل أستلمت جميع اللوكيشنز اللتي ستمر عليها ؟'
    )
    received_shipment_full = forms.TypedChoiceField(
        coerce=lambda x: x == 'نعم',
        choices=CHECKLIST_CHOICES,
        widget=forms.RadioSelect,
        label='هل أستلمت الشحنه كامله و الأوراق الخاصه بالشحنة و تصاريح الخروج ؟'
    )

    exit_time = forms.TimeField(
        required=True,
        widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
        label='ما هو معاد خروجك من المصنع "بلدقيقه" ؟'
    )
    return_time = forms.TimeField(
        required=True,
        widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
        label='ما هو معاد عودتك الي المصنع "بلدقيقه" ؟'
    )

    class Meta:
        model = CarLog
        fields = [
            'trip_date',
            'received_keys',
            'received_locations',
            'received_shipment_full',
            'issues',
            'exit_time',
            'return_time',
        ]
        widgets = {
            'issues': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3, 'placeholder': 'أي مشاكل حدثت من بداية الرحلة حتي النهاية وضحها هنا بالتفاصيل'}),
        }
        labels = {
            'issues': 'أي مشاكل حدثت من بدايه الرحله حتي النهايه',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Iterate over all fields and set required=True unless it's 'issues'
        for field_name, field in self.fields.items():
            if field_name == 'issues':
                field.required = False
            else:
                field.required = True
                
        if self.instance.pk:
            for field in ['received_keys', 'received_locations', 'received_shipment_full']:
                val = getattr(self.instance, field)
                self.initial[field] = 'نعم' if val else 'لا'
