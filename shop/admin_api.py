"""Admin panel (Bozorcha) uchun API.

Hammasi /api/admin/ ostida va FAQAT xodimlar (is_staff) uchun:
    kirmagan -> 401, oddiy mijoz -> 403.

    GET   /api/admin/dashboard/          statistika
    GET   /api/admin/orders/             ro'yxat (?status= &payment_status= &payment_method= &q= &date_from= &date_to=)
    GET   /api/admin/orders/BZ-1001/
    PATCH /api/admin/orders/BZ-1001/     {"status": "...", "payment_status": "..."}
    CRUD  /api/admin/products/           (DELETE = arxivlash, is_active=false)
    CRUD  /api/admin/categories/         (mahsuloti bor kategoriyani o'chirib bo'lmaydi)
    CRUD  /api/admin/promos/             (DELETE = faolsizlantirish)
    GET   /api/admin/customers/          faqat o'qish
    GET   /api/admin/payments/           faqat o'qish
"""
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count, F, Q, Sum, Value
from django.db.models.functions import Coalesce, Greatest, TruncDate, TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import Category, Customer, Order, Payment, Product, PromoCode
from .orders import OrderSerializer

LOW_STOCK = 5  # shu miqdor va undan kam qolgan mahsulotlar "kam qolgan" hisoblanadi


class AdminPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class StaffOnly:
    permission_classes = [IsAdminUser]
    pagination_class = AdminPagination


def _flag(value):
    return str(value).lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- buyurtmalar

class PaymentBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ("id", "transaction_id", "method", "amount", "status", "created_at")


class AdminOrderSerializer(OrderSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    payments = PaymentBriefSerializer(many=True, read_only=True)

    class Meta(OrderSerializer.Meta):
        fields = ("id", "user", "payments") + OrderSerializer.Meta.fields


class OrderUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices, required=False)
    payment_status = serializers.ChoiceField(choices=Order.PayStatus.choices, required=False)

    def validate(self, attrs):
        if not attrs:
            raise ValidationError("status yoki payment_status yuboring")
        return attrs


def order_qs():
    return Order.objects.select_related("promo", "user").prefetch_related("items", "payments")


def _restore_stock(order):
    """Bekor qilingan buyurtmadagi mahsulotlarni omborga qaytaradi (bir marta)."""
    if order.stock_restored:
        return
    for item in order.items.all():
        if item.product_id:
            Product.objects.filter(pk=item.product_id).update(
                stock=F("stock") + item.qty,
                sold=Greatest(F("sold") - item.qty, Value(0)),
            )
    if order.promo_id:
        PromoCode.objects.filter(pk=order.promo_id).update(used=Greatest(F("used") - 1, Value(0)))
    order.stock_restored = True


@transaction.atomic
def update_order(order_id, status=None, payment_status=None):
    """Holat/to'lov holatini o'zgartiradi. Bekor qilinsa ombor qaytadi."""
    order = Order.objects.select_for_update().get(pk=order_id)

    if order.status == Order.Status.CANCELLED:
        if status and status != Order.Status.CANCELLED:
            raise ValidationError({"status": ["Bekor qilingan buyurtmani qayta ochib bo'lmaydi"]})
        if payment_status == Order.PayStatus.PAID:
            raise ValidationError({"payment_status": ["Bekor qilingan buyurtma uchun to'lov belgilab bo'lmaydi"]})

    if status and status != order.status:
        if status == Order.Status.CANCELLED:
            if order.status == Order.Status.DONE:
                raise ValidationError({"status": ["Yakunlangan buyurtmani bekor qilib bo'lmaydi"]})
            _restore_stock(order)
        order.status = status
        order.status_seen = False  # mijozda "Yangi holat!" belgisi chiqadi

    if payment_status and payment_status != order.payment_status:
        order.payment_status = payment_status
        order.payments.update(status=payment_status)

    order.save()
    return order_qs().get(pk=order.pk)


class AdminOrderViewSet(StaffOnly, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = AdminOrderSerializer
    lookup_field = "number"
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        qs = order_qs()
        p = self.request.query_params
        for field in ("status", "payment_status", "payment_method"):
            if p.get(field):
                qs = qs.filter(**{field: p[field]})
        q = p.get("q", "").strip()
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(name__icontains=q) | Q(phone__icontains=q))
        date_from, date_to = parse_date(p.get("date_from", "")), parse_date(p.get("date_to", ""))
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        s = OrderUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        order = update_order(order.pk, **s.validated_data)
        return Response(self.get_serializer(order).data)


# ------------------------------------------------------------------ mahsulotlar

class AdminProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    image = serializers.ImageField(required=False, allow_null=True)
    image_src = serializers.SerializerMethodField()
    on_sale = serializers.BooleanField(source="is_on_sale", read_only=True)

    class Meta:
        model = Product
        fields = (
            "id", "name", "category", "category_name", "price", "old_price", "on_sale", "stock", "sold",
            "description", "icon", "image", "image_url", "image_src", "bg", "colors", "sizes",
            "is_active", "created_at",
        )
        read_only_fields = ("sold", "created_at")
        extra_kwargs = {"price": {"min_value": 1}}

    def get_image_src(self, obj):
        src = obj.image_src
        request = self.context.get("request")
        if src and request and src.startswith("/"):
            return request.build_absolute_uri(src)
        return src

    def _clean_options(self, value, label):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValidationError(f"{label} matnlar ro'yxati bo'lishi kerak")
        cleaned = list(dict.fromkeys(v.strip() for v in value if v.strip()))  # bo'shlarini olib, takrorini tashlaydi
        if len(cleaned) > 30:
            raise ValidationError(f"{label} 30 tadan oshmasin")
        return cleaned

    def validate_colors(self, value):
        return self._clean_options(value, "Ranglar")

    def validate_sizes(self, value):
        return self._clean_options(value, "O'lchamlar")

    def validate(self, attrs):
        if "price" in attrs or "old_price" in attrs:
            price = attrs.get("price", getattr(self.instance, "price", None))
            old = attrs["old_price"] if "old_price" in attrs else getattr(self.instance, "old_price", None)
            if old is not None and price is not None and old <= price:
                raise ValidationError({"old_price": ["Eski narx yangi narxdan katta bo'lishi kerak (yoki bo'sh qoldiring)"]})
        return attrs


PRODUCT_SORTS = {
    "new": "-id", "stock": "stock", "sold": "-sold", "price-asc": "price", "price-desc": "-price", "name": "name",
}


class AdminProductViewSet(StaffOnly, viewsets.ModelViewSet):
    """Faolsiz (arxivdagi) mahsulotlar ham ko'rinadi. DELETE mahsulotni arxivlaydi — buyurtma tarixi buzilmaydi."""
    serializer_class = AdminProductSerializer

    def get_queryset(self):
        qs = Product.objects.select_related("category")
        p = self.request.query_params
        if p.get("category", "").isdigit():
            qs = qs.filter(category_id=int(p["category"]))
        if p.get("is_active"):
            qs = qs.filter(is_active=_flag(p["is_active"]))
        if p.get("on_sale") and _flag(p["on_sale"]):
            qs = qs.filter(old_price__gt=F("price"))
        if p.get("low_stock") and _flag(p["low_stock"]):
            qs = qs.filter(stock__lte=LOW_STOCK)
        q = p.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(category__name__icontains=q))
        return qs.order_by(PRODUCT_SORTS.get(p.get("sort", "new"), "-id"), "-id")

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])


# ------------------------------------------------------------------ kategoriyalar

class AdminCategorySerializer(serializers.ModelSerializer):
    products_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ("id", "name", "icon", "color", "is_active", "products_count")


class AdminCategoryViewSet(StaffOnly, viewsets.ModelViewSet):
    serializer_class = AdminCategorySerializer
    pagination_class = None

    def get_queryset(self):
        return Category.objects.annotate(products_count=Count("products"))

    def destroy(self, request, *args, **kwargs):
        category = self.get_object()
        if category.products.exists():
            return Response(
                {"detail": "Bu kategoriyada mahsulotlar bor. Avval ularni boshqa kategoriyaga o'tkazing "
                           "yoki kategoriyani faolsiz qiling."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        category.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- promo-kodlar

class AdminPromoSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromoCode
        fields = ("id", "code", "percent", "used", "expires", "is_active")
        read_only_fields = ("used",)

    def validate_code(self, value):
        value = value.strip().upper()
        taken = PromoCode.objects.filter(code=value)
        if self.instance:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise ValidationError("Bunday promo-kod allaqachon bor")
        return value


class AdminPromoViewSet(StaffOnly, viewsets.ModelViewSet):
    """DELETE promo-kodni o'chirmaydi, faolsizlantiradi (buyurtmalardagi izi saqlanadi)."""
    serializer_class = AdminPromoSerializer
    pagination_class = None
    queryset = PromoCode.objects.order_by("-id")

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])


# ----------------------------------------------------------- mijozlar, to'lovlar

class AdminCustomerSerializer(serializers.ModelSerializer):
    orders_count = serializers.IntegerField(read_only=True)
    total_spent = serializers.IntegerField(read_only=True)

    class Meta:
        model = Customer
        fields = ("id", "name", "phone", "orders_count", "total_spent", "created_at")


class AdminCustomerViewSet(StaffOnly, viewsets.ReadOnlyModelViewSet):
    """total_spent — bekor qilinmagan buyurtmalar yig'indisi."""
    serializer_class = AdminCustomerSerializer

    def get_queryset(self):
        qs = Customer.objects.annotate(
            orders_count=Count("orders"),
            total_spent=Coalesce(Sum("orders__total", filter=~Q(orders__status=Order.Status.CANCELLED)), Value(0)),
        )
        p = self.request.query_params
        q = p.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))
        sort = {"spent": "-total_spent", "orders": "-orders_count"}.get(p.get("sort"), "-id")
        return qs.order_by(sort, "-id")


class AdminPaymentSerializer(serializers.ModelSerializer):
    order = serializers.CharField(source="order.number")
    customer = serializers.CharField(source="order.name")

    class Meta:
        model = Payment
        fields = ("id", "transaction_id", "order", "customer", "method", "amount", "status", "created_at")


class AdminPaymentViewSet(StaffOnly, viewsets.ReadOnlyModelViewSet):
    serializer_class = AdminPaymentSerializer

    def get_queryset(self):
        qs = Payment.objects.select_related("order")
        p = self.request.query_params
        for field in ("method", "status"):
            if p.get(field):
                qs = qs.filter(**{field: p[field]})
        q = p.get("q", "").strip()
        if q:
            qs = qs.filter(Q(transaction_id__icontains=q) | Q(order__number__icontains=q) | Q(order__name__icontains=q))
        return qs


# -------------------------------------------------------------------- dashboard

@api_view(["GET"])
@permission_classes([IsAdminUser])
def dashboard(request):
    """Bosh sahifa statistikasi. Buyurtmalar soni — bekor qilinmaganlar bo'yicha,
    daromad — faqat TO'LANGAN (payment_status=paid) va bekor qilinmagan buyurtmalar bo'yicha."""
    active = Order.objects.exclude(status=Order.Status.CANCELLED)
    paid = active.filter(payment_status=Order.PayStatus.PAID)
    today = timezone.localdate()
    start = today - timedelta(days=6)

    def money(qs):
        return qs.aggregate(s=Coalesce(Sum("total"), Value(0)))["s"]

    per_day = {
        row["d"]: row
        for row in active.filter(created_at__date__gte=start)
        .annotate(d=TruncDate("created_at")).order_by().values("d")
        .annotate(orders=Count("id"), revenue=Sum("total", filter=Q(payment_status=Order.PayStatus.PAID)))
    }
    last_7_days = []
    for i in range(7):
        day = start + timedelta(days=i)
        row = per_day.get(day)
        last_7_days.append({"date": day.isoformat(), "orders": row["orders"] if row else 0,
                            "revenue": (row["revenue"] or 0) if row else 0})

    counts = dict(Order.objects.order_by().values_list("status").annotate(n=Count("id")))
    today_qs = active.filter(created_at__date=today)

    return Response({
        "totals": {
            "orders": Order.objects.count(),
            "revenue": money(paid),
            "paid_revenue": money(paid),  # eski nom (revenue bilan bir xil), frontend moslik uchun
            "pending_payments": active.filter(payment_status=Order.PayStatus.PENDING).count(),
            "customers": Customer.objects.count(),
            "products": Product.objects.filter(is_active=True).count(),
            "low_stock_count": Product.objects.filter(is_active=True, stock__lte=LOW_STOCK).count(),
        },
        "today": {"orders": today_qs.count(), "revenue": money(today_qs.filter(payment_status=Order.PayStatus.PAID))},
        "by_status": {value: counts.get(value, 0) for value, _ in Order.Status.choices},
        "last_7_days": last_7_days,
        "top_products": list(
            Product.objects.filter(sold__gt=0).order_by("-sold", "-id").values("id", "name", "sold", "stock")[:5]
        ),
        "low_stock_threshold": LOW_STOCK,
        "low_stock": list(
            Product.objects.filter(is_active=True, stock__lte=LOW_STOCK).order_by("stock", "-id")
            .values("id", "name", "stock")[:10]
        ),
        "recent_orders": list(
            Order.objects.order_by("-id").values("number", "name", "total", "status", "payment_status", "created_at")[:5]
        ),
    })


# --------------------------------------------------------------------- hisobotlar

def _shift_month(year, month, delta):
    """(yil, oy) juftligini `delta` oyga siljitadi (manfiy ham bo'lishi mumkin)."""
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


@api_view(["GET"])
@permission_classes([IsAdminUser])
def reports(request):
    """Hisobotlar sahifasi: oxirgi 6 oylik daromad (faqat to'langan buyurtmalar) va eng ko'p sotilgan mahsulotlar."""
    paid = Order.objects.exclude(status=Order.Status.CANCELLED).filter(payment_status=Order.PayStatus.PAID)
    today = timezone.localdate()
    y0, m0 = _shift_month(today.year, today.month, -5)
    start = date(y0, m0, 1)

    rows = {
        (row["m"].year, row["m"].month): row["revenue"]
        for row in paid.filter(created_at__date__gte=start)
        .annotate(m=TruncMonth("created_at")).order_by()
        .values("m").annotate(revenue=Sum("total"))
    }
    monthly_revenue = []
    for i in range(6):
        y, m = _shift_month(today.year, today.month, -5 + i)
        monthly_revenue.append({"year": y, "month": m, "revenue": rows.get((y, m), 0)})

    return Response({
        "monthly_revenue": monthly_revenue,
        "top_products": list(
            Product.objects.filter(sold__gt=0).order_by("-sold", "-id").values("id", "name", "sold", "image_url")[:10]
        ),
    })