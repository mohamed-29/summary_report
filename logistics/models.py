import uuid
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal


def generate_short_code():
    """Generate a unique 6-character uppercase code."""
    return uuid.uuid4().hex[:6].upper()


class Operator(models.Model):
    """Stores standardized operator/driver names."""
    name = models.CharField(max_length=255, unique=True, help_text="Standardized operator name")
    code = models.CharField(
        max_length=10, unique=True, null=True, blank=True,
        help_text="Unique login code for the operator (e.g., 'A3F1B2')"
    )
    is_driver = models.BooleanField(default=False, help_text="Flag to identify if this operator is a car driver")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            # Auto-generate a unique code
            for _ in range(10):
                new_code = generate_short_code()
                if not Operator.objects.filter(code=new_code).exists():
                    self.code = new_code
                    break
        super().save(*args, **kwargs)


class Machine(models.Model):
    """Stores the canonical (correct) name of every vending machine."""
    name = models.CharField(max_length=255, unique=True, help_text="Canonical machine name")
    code = models.CharField(
        max_length=10, unique=True, null=True, blank=True,
        help_text="Unique code for the machine (e.g., 'M7K2X9')"
    )
    location = models.CharField(max_length=500, blank=True, help_text="Optional location description")
    latitude = models.DecimalField(
        max_digits=18, decimal_places=12, null=True, blank=True,
        help_text="Machine GPS latitude (e.g., 24.774265123456)"
    )
    longitude = models.DecimalField(
        max_digits=18, decimal_places=12, null=True, blank=True,
        help_text="Machine GPS longitude (e.g., 46.738586123456)"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            # Auto-generate a unique code
            for _ in range(10):
                new_code = generate_short_code()
                if not Machine.objects.filter(code=new_code).exists():
                    self.code = new_code
                    break
        super().save(*args, **kwargs)


class MachineAlias(models.Model):
    """
    Links incorrect variations (typos, abbreviations) to the canonical Machine.
    This is the 'learning' mechanism: once a typo is resolved, it's stored here
    for instant lookup next time.
    """
    alias = models.CharField(
        max_length=255, 
        unique=True, 
        help_text="The raw/incorrect name found in Excel (e.g., 'Universty')"
    )
    machine = models.ForeignKey(
        Machine, 
        on_delete=models.CASCADE, 
        related_name='aliases',
        help_text="The canonical machine this alias maps to"
    )
    source = models.CharField(
        max_length=50, 
        default='manual',
        choices=[
            ('manual', 'Manual Entry'),
            ('fuzzy', 'Fuzzy Match'),
            ('ai', 'AI (Gemini)'),
            ('ai_batch', 'AI Batch'),
            ('exact', 'Exact Match'),
        ],
        help_text="How this alias was resolved"
    )
    confidence_score = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0)],
        help_text="Confidence score of the match (0.0 to 1.0)"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Machine Aliases"
        ordering = ['alias']

    def __str__(self):
        return f"{self.alias} -> {self.machine.name}"


class VisitLog(models.Model):
    """Stores daily stats from on-site operator logs."""
    timestamp = models.DateTimeField(null=True, blank=True, help_text="Original timestamp from the log")
    operator = models.ForeignKey(
        Operator, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='visit_logs'
    )
    machine = models.ForeignKey(
        Machine, 
        on_delete=models.CASCADE, 
        related_name='visit_logs'
    )

    visit_location = models.TextField(blank=True, default='', help_text="موقع الماكينة (Coordinates or description)")
    
    # --- Checklist / Verification Fields ---
    received_keys = models.BooleanField(default=False, help_text="هل استلمت مفاتيح الماكينه ؟")
    pos_verified = models.BooleanField(default=False, help_text="هل تأكدت من أن ال POS يعمل ؟")
    product_review_done = models.BooleanField(default=False, help_text="هل أتممت مراجعه الأسم و السعر و الصوره للمنتج ؟")
    no_sold_out = models.BooleanField(default=False, help_text="هل تأكدت من عدم وجود Sold out أو stop sale ؟")
    quantity_review_done = models.BooleanField(default=False, help_text="هل أتممت مراجعه الكميات قبل و بعد وضعها في الماكينه ؟")
    expiry_verified = models.BooleanField(default=False, help_text="هل تأكدت من تاريخ صلاحية المنتجات ؟")
    
    shipment_info = models.TextField(blank=True, default='', help_text="معلومات الشحنة والمشغل الذي أوصلها")
    arrival_time = models.DateTimeField(null=True, blank=True, help_text="توقيت وصولك أمام الماكينه")
    stock_details = models.TextField(blank=True, default='', help_text="الستوك المتواجد داخل مخزن الماكينة بلأسماء و الأعداد")

    # --- Ratings ---
    cleanliness_rating = models.IntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="Machine cleanliness rating (1-5)"
    )
    customer_satisfaction = models.IntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="Customer satisfaction rating (1-5)"
    )

    # --- Photo (via VisitLogImage) ---

    # --- Core Metrics ---
    transactions = models.IntegerField(default=0, help_text="Number of transactions")
    voids = models.IntegerField(default=0, help_text="Number of void transactions")
    void_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="Calculated: (voids / transactions) * 100"
    )

    # --- Issues ---
    product_issue = models.TextField(blank=True, default='', help_text="هل توجد مشكله أثناء نزول المنتجات ؟")
    machine_issue = models.TextField(blank=True, default='', help_text="هل يوجد أي مشاكل بالماكينة")

    # --- Comments ---
    comments = models.TextField(blank=True, help_text="Raw comments/issues from the log")
    raw_machine_name = models.CharField(
        max_length=255, 
        blank=True,
        help_text="Original machine name from Excel (before resolution)"
    )

    # --- Check In/Out & Draft ---
    is_check_in = models.BooleanField(default=True, help_text="True = Check In form, False = Check Out form")
    is_completed = models.BooleanField(default=False, help_text="False = draft (live-save), True = submitted")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Visit Log"
        verbose_name_plural = "Visit Logs"

    def __str__(self):
        ts = self.timestamp.strftime('%Y-%m-%d') if self.timestamp else 'Draft'
        machine_name = self.machine.name if self.machine else 'No Machine'
        return f"{machine_name} - {ts}"

    def save(self, *args, **kwargs):
        """Calculate void_percentage and ensure timezone-aware timestamp."""
        from django.utils import timezone as tz
        # Ensure timestamp is timezone-aware
        if self.timestamp and tz.is_naive(self.timestamp):
            self.timestamp = tz.make_aware(self.timestamp)
        if self.transactions > 0:
            self.void_percentage = Decimal(str((self.voids / self.transactions) * 100)).quantize(Decimal('0.01'))
        else:
            self.void_percentage = Decimal('0.00')
        super().save(*args, **kwargs)


class VisitLogImage(models.Model):
    """Stores uploaded machine photos for a visit log entry."""
    visit_log = models.ForeignKey(
        VisitLog,
        on_delete=models.CASCADE,
        related_name='images'
    )
    image = models.ImageField(upload_to='visit_log_images/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for VisitLog #{self.visit_log_id}"


class CarLog(models.Model):
    """Stores daily driver routes and issues from car operator logs."""
    timestamp = models.DateTimeField(auto_now_add=True, help_text="Record creation timestamp")
    driver = models.ForeignKey(
        Operator, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='car_logs',
        limit_choices_to={'is_driver': True}
    )
    trip_date = models.DateField(null=True, blank=True, help_text="التاريخ")

    # --- Checklist ---
    received_keys = models.BooleanField(default=False, help_text="هل استلمت مفاتيح الماكينات قبل التحرك من الشركة ؟")
    received_locations = models.BooleanField(default=False, help_text="هل أستلمت جميع اللوكيشنز اللتي ستمر عليها ؟")
    received_shipment_full = models.BooleanField(default=False, help_text="هل أستلمت الشحنه كامله و الأوراق الخاصه بالشحنة و تصاريح الخروج ؟")

    # --- Issues ---
    issues = models.TextField(blank=True, default='', help_text="أي مشاكل حدثت من بدايه الرحله حتي النهايه وضحها هنا بالتفاصيل")

    # --- Times ---
    exit_time = models.TimeField(null=True, blank=True, help_text="ما هو معاد خروجك من المصنع بلدقيقه ؟")
    return_time = models.TimeField(null=True, blank=True, help_text="ما هو معاد عودتك الي المصنع بلدقيقه ؟")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Car Log"
        verbose_name_plural = "Car Logs"

    def __str__(self):
        driver_name = self.driver.name if self.driver else "Unknown Driver"
        date_str = self.trip_date.strftime('%Y-%m-%d') if self.trip_date else 'No Date'
        return f"{driver_name} - {date_str}"


class CarLogImage(models.Model):
    """Stores uploaded paper/document images for a car log entry."""
    car_log = models.ForeignKey(
        CarLog,
        on_delete=models.CASCADE,
        related_name='images'
    )
    image = models.ImageField(upload_to='car_log_images/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for CarLog #{self.car_log_id}"


class CarLogStop(models.Model):
    """Stores an ordered stop (machine location) for a car log trip."""
    car_log = models.ForeignKey(
        CarLog,
        on_delete=models.CASCADE,
        related_name='stops'
    )
    machine = models.ForeignKey(
        Machine,
        on_delete=models.CASCADE,
        related_name='car_stops'
    )
    order = models.PositiveIntegerField(default=0, help_text="Visit order (0-based)")

    class Meta:
        ordering = ['order']
        verbose_name = "Car Log Stop"
        verbose_name_plural = "Car Log Stops"

    def __str__(self):
        return f"Stop #{self.order + 1}: {self.machine.name}"


class MonthlyReport(models.Model):
    """Caches monthly AI-generated summaries for each machine."""
    machine = models.ForeignKey(
        Machine, 
        on_delete=models.CASCADE, 
        related_name='monthly_reports'
    )
    month = models.DateField(help_text="First day of the month (e.g., 2024-01-01)")
    total_transactions = models.IntegerField(default=0)
    total_voids = models.IntegerField(default=0)
    void_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=Decimal('0.00')
    )
    ai_summary = models.TextField(
        blank=True, 
        help_text="Gemini-generated summary of mechanical health/issues"
    )
    raw_comments = models.TextField(
        blank=True, 
        help_text="Concatenated raw comments for the month"
    )
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-month', 'machine__name']
        unique_together = ['machine', 'month']
        verbose_name = "Monthly Report"
        verbose_name_plural = "Monthly Reports"

    def __str__(self):
        return f"{self.machine.name} - {self.month.strftime('%Y-%m')}"

    def save(self, *args, **kwargs):
        """Calculate void_percentage before saving."""
        if self.total_transactions > 0:
            self.void_percentage = Decimal(str((self.total_voids / self.total_transactions) * 100)).quantize(Decimal('0.01'))
        else:
            self.void_percentage = Decimal('0.00')
        super().save(*args, **kwargs)


class OperatorDailyRating(models.Model):
    """Stores manual daily ratings (1-10) for operators."""
    operator = models.ForeignKey(
        Operator, 
        on_delete=models.CASCADE, 
        related_name='daily_ratings'
    )
    date = models.DateField(help_text="The date of the rating")
    rating = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        help_text="Manual rating from 0 to 10"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['operator', 'date']
        verbose_name = "Operator Daily Rating"
        verbose_name_plural = "Operator Daily Ratings"

    def __str__(self):
        return f"{self.operator.name} - {self.date.strftime('%Y-%m-%d')}: {self.rating}/10"
