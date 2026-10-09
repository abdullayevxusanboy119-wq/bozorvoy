from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from .accounts import normalize_phone
from . import payments
from .models import Order, OrderItem
from .services import create_order, quote_order


class OrderItemInputSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    qty = serializers.IntegerField(min_value=1, max_value=100)
    color = serializers.CharField(required=False, allow_blank=True, default="")
    size = serializers.CharField(required=False, allow_blank=True, default="")


class OrderCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    phone = serializers.CharField()
    city = serializers.CharField(max_length=100)
    address = serializers.CharField(max_length=255)
    delivery = serializers.ChoiceField(choices=Order.Delivery.choices, default=Order.Delivery.STANDARD)
    payment_method = serializers.ChoiceField(choices=Order.Method.choices)
    promo = serializers.CharField(required=False, allow_blank=True, default="")
    items = OrderItemInputSerializer(many=True, allow_empty=False, max_length=50)

    def validate_phone(self, value):
        phone = normalize_phone(value)
        if not phone:
            raise serializers.ValidationError("Telefon raqami noto'g'ri (masalan: +998 90 123 45 67)")
        return phone


class QuoteSerializer(serializers.Serializer):
    delivery = serializers.ChoiceField(choices=Order.Delivery.choices, default=Order.Delivery.STANDARD)
    promo = serializers.CharField(required=False, allow_blank=True, default="")
    items = OrderItemInputSerializer(many=True, allow_empty=False, max_length=50)


class OrderItemSerializer(serializers.ModelSerializer):
    line_total = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrderItem
        fields = ("product", "product_name", "price", "qty", "color", "size", "line_total")


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    items_count = serializers.IntegerField(read_only=True)
    promo = serializers.SlugRelatedField(slug_field="code", read_only=True)
    payment_url = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            "number", "status", "payment_method", "payment_status", "delivery", "delivery_fee",
            "subtotal", "promo", "discount_amount", "total", "name", "phone", "city", "address",
            "items_count", "items", "status_seen", "created_at", "payment_url",
        )

    def get_payment_url(self, order):
        """To'lanmagan Payme/Click buyurtmasi uchun to'lov sahifasi havolasi (aks holda bo'sh)."""
        return payments.payment_url(order, self.context.get("request"))


def my_orders():
    return Order.objects.select_related("promo").prefetch_related("items")


class OrderListCreateView(GenericAPIView):
    """
    GET  /api/orders/   — mening buyurtmalarim (kirish kerak), yangisi birinchi
    POST /api/orders/   — buyurtma berish (kirmagan mijoz ham bera oladi)
    {
      "name": "Aziza", "phone": "+998 90 111 22 33", "city": "Toshkent", "address": "Chilonzor 5",
      "delivery": "standard" | "express" | "pickup",
      "payment_method": "payme" | "click" | "cash",
      "promo": "YANGI10",                                  (ixtiyoriy)
      "items": [{"product": 1, "qty": 2, "color": "Qora", "size": "Standart"}]
    }
    Narx, chegirma, yetkazib berish va jami summa serverda hisoblanadi.
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "order"

    def get_serializer_class(self):
        return OrderSerializer if self.request.method == "GET" else OrderCreateSerializer

    def get_permissions(self):
        return [IsAuthenticated()] if self.request.method == "GET" else [AllowAny()]

    def get(self, request):
        qs = my_orders().filter(user=request.user)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(OrderSerializer(page, many=True, context={"request": request}).data)

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        order = create_order(s.validated_data, request.user)
        return Response(OrderSerializer(order, context={"request": request}).data, status=status.HTTP_201_CREATED)


class OrderDetailView(RetrieveAPIView):
    """GET /api/orders/BZ-1001/ — faqat o'z buyurtmangiz (boshqaniki uchun 404)."""
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "number"

    def get_queryset(self):
        return my_orders().filter(user=self.request.user)


class OrderSeenView(GenericAPIView):
    """POST /api/orders/seen/ — 'Yangi holat!' belgisini o'chiradi (mijoz buyurtmalarini ochganda)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        n = Order.objects.filter(user=request.user, status_seen=False).update(status_seen=True)
        return Response({"updated": n})


class OrderQuoteView(GenericAPIView):
    """
    POST /api/orders/quote/  {delivery, promo, items} — buyurtma YARATMAYDI.
    Savat va checkout uchun server hisoblagan summalarni qaytaradi (promo-kod ham shu yerda tekshiriladi).
    """
    serializer_class = QuoteSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "quote"

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(quote_order(s.validated_data))
