from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Mijoz yoki admin. Frontendda kirish telefon YOKI email orqali bo'ladi."""
    phone = models.CharField(max_length=13, unique=True, null=True, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)

    def __str__(self):
        return self.get_full_name() or self.email or self.phone or self.username


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    icon = models.CharField(max_length=40, default="fa-tag")
    color = models.CharField(max_length=9, default="#EDE6D6")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["id"]

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    price = models.PositiveIntegerField(help_text="so'mda")
    # old_price bor bo'lsa mahsulot "Aksiya"da hisoblanadi (alohida kategoriya kerak emas)
    old_price = models.PositiveIntegerField(null=True, blank=True)
    stock = models.PositiveIntegerField(default=0)
    sold = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=40, default="fa-box", help_text="rasm bo'lmasa FontAwesome ikonka")
    image = models.ImageField(upload_to="products/", blank=True)
    image_url = models.URLField(max_length=500, blank=True, help_text="tashqi rasm manzili (ixtiyoriy)")
    bg = models.CharField(max_length=9, blank=True)
    colors = models.JSONField(default=list, blank=True)
    sizes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.name

    @property
    def is_on_sale(self):
        return bool(self.old_price and self.old_price > self.price)

    @property
    def image_src(self):
        return self.image.url if self.image else self.image_url


class PromoCode(models.Model):
    code = models.CharField(max_length=30, unique=True)
    percent = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(90)])
    used = models.PositiveIntegerField(default=0)
    expires = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} (-{self.percent}%)"

    def validation_error(self):
        """None qaytarsa promo yaroqli, aks holda xato matni."""
        if not self.is_active:
            return "Bu promo-kod faol emas"
        if self.expires and self.expires < timezone.localdate():
            return "Promo-kodning muddati tugagan"
        return None


class Customer(models.Model):
    """Xaridor. Telefon raqami bo'yicha birlashtiriladi, shuning uchun mehmon
    (ro'yxatdan o'tmagan) buyurtmalari ham yo'qolmaydi."""
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="customer")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=13, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} {self.phone}"


class Order(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Yangi"
        PROCESSING = "processing", "Jarayonda"
        SHIPPING = "shipping", "Yetkazilmoqda"
        DONE = "done", "Yakunlangan"
        CANCELLED = "cancelled", "Bekor qilingan"

    class Delivery(models.TextChoices):
        STANDARD = "standard", "Standart"
        EXPRESS = "express", "Ekspress"
        PICKUP = "pickup", "O'zi olib ketish"

    class Method(models.TextChoices):
        PAYME = "payme", "Payme"
        CLICK = "click", "Click"
        CASH = "cash", "Naqd"

    class PayStatus(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        PAID = "paid", "To'landi"
        FAILED = "failed", "Muvaffaqiyatsiz"

    number = models.CharField(max_length=20, unique=True, blank=True, editable=False)  # BZ-1043
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")

    # buyurtma paytidagi ma'lumotlar (keyin profil o'zgarsa ham tarix buzilmaydi)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=13)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=255)

    delivery = models.CharField(max_length=10, choices=Delivery.choices, default=Delivery.STANDARD)
    delivery_fee = models.PositiveIntegerField(default=0)
    subtotal = models.PositiveIntegerField(default=0)
    promo = models.ForeignKey(PromoCode, null=True, blank=True, on_delete=models.SET_NULL)
    discount_amount = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)

    payment_method = models.CharField(max_length=10, choices=Method.choices, default=Method.PAYME)
    payment_status = models.CharField(max_length=10, choices=PayStatus.choices, default=PayStatus.PENDING)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    stock_restored = models.BooleanField(default=False)
    status_seen = models.BooleanField(default=True, help_text="mijoz yangi holatni ko'rdimi")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"BZ-{1000 + self.pk}"
            super().save(update_fields=["number"])

    def __str__(self):
        return self.number or f"Order #{self.pk}"

    @property
    def items_count(self):
        return sum(i.qty for i in self.items.all())


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL, related_name="order_items")
    product_name = models.CharField(max_length=200)   # snapshot
    price = models.PositiveIntegerField()             # snapshot: sotilgan paytdagi narx
    qty = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    color = models.CharField(max_length=40, blank=True)
    size = models.CharField(max_length=40, blank=True)

    def __str__(self):
        return f"{self.product_name} x{self.qty}"

    @property
    def line_total(self):
        return self.price * self.qty


class Payment(models.Model):
    transaction_id = models.CharField(max_length=64, unique=True, blank=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=10, choices=Order.Method.choices)
    amount = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Order.PayStatus.choices, default=Order.PayStatus.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- Payme / Click tomonidagi tranzaksiya ma'lumotlari (webhook'lar to'ldiradi) ---
    # Payme: state 1=yaratilgan, 2=to'langan, -1=to'lovdan oldin bekor, -2=to'lovdan keyin bekor
    # Click: state 1=prepare qilingan, 2=to'langan, -1=bekor/xato
    provider_trans_id = models.CharField(max_length=64, blank=True, db_index=True)
    provider_state = models.SmallIntegerField(default=0)
    provider_time = models.BigIntegerField(null=True, blank=True, help_text="Payme so'rovidagi time (ms)")
    provider_create_time = models.BigIntegerField(null=True, blank=True)
    provider_perform_time = models.BigIntegerField(default=0)
    provider_cancel_time = models.BigIntegerField(default=0)
    provider_reason = models.SmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.transaction_id:
            self.transaction_id = f"TXN-{8000 + self.pk}"
            super().save(update_fields=["transaction_id"])

    def __str__(self):
        return self.transaction_id or f"Payment #{self.pk}"
