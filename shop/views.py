from django.db.models import Count, F, Max, Q
from rest_framework import viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from .models import Category, Product
from .serializers import CategorySerializer, ProductSerializer

SORTS = {
    "popular": "-sold",
    "new": "-id",
    "price-asc": "price",
    "price-desc": "-price",
}


def visible_products():
    return Product.objects.filter(is_active=True, category__is_active=True).select_related("category")


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/categories/ — mahsulotlar soni bilan (faqat faol mahsulotlar)."""
    serializer_class = CategorySerializer
    pagination_class = None

    def get_queryset(self):
        return Category.objects.filter(is_active=True).annotate(
            products_count=Count("products", filter=Q(products__is_active=True))
        )


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/products/
      ?category=3        kategoriya id
      ?on_sale=true      aksiyadagi mahsulotlar (old_price > price)
      ?max_price=500000  narx yuqori chegarasi (min_price ham bor)
      ?q=krossovka       nom yoki kategoriya nomi bo'yicha qidiruv
      ?sort=popular|new|price-asc|price-desc
      ?page=2&page_size=12
    GET /api/products/{id}/
    GET /api/products/{id}/related/   — shu kategoriyadagi 4 ta boshqa mahsulot
    """
    serializer_class = ProductSerializer

    def get_queryset(self):
        qs = visible_products()
        p = self.request.query_params

        if p.get("category", "").isdigit():
            qs = qs.filter(category_id=int(p["category"]))
        if p.get("on_sale", "").lower() in ("1", "true", "yes"):
            qs = qs.filter(old_price__gt=F("price"))
        if p.get("min_price", "").isdigit():
            qs = qs.filter(price__gte=int(p["min_price"]))
        if p.get("max_price", "").isdigit():
            qs = qs.filter(price__lte=int(p["max_price"]))
        q = p.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(category__name__icontains=q))

        return qs.order_by(SORTS.get(p.get("sort", "popular"), "-sold"), "-id")

    @action(detail=True, methods=["get"])
    def related(self, request, pk=None):
        product = self.get_object()
        qs = visible_products().filter(category_id=product.category_id).exclude(pk=product.pk)[:4]
        return Response(self.get_serializer(qs, many=True).data)

    def paginate_queryset(self, queryset):
        # ?page_size= ni qo'llab-quvvatlaymiz (masalan bosh sahifada 8 ta mashhur mahsulot)
        size = self.request.query_params.get("page_size", "")
        if size.isdigit() and 0 < int(size) <= 60:
            self.paginator.page_size = int(size)
        else:
            self.paginator.page_size = 12
        return super().paginate_queryset(queryset)


@api_view(["GET"])
def catalog_meta(request):
    """Katalog filtrlari uchun: jami soni, aksiya soni, eng qimmat narx (narx slayderi uchun)."""
    qs = visible_products()
    return Response({
        "total": qs.count(),
        "on_sale": qs.filter(old_price__gt=F("price")).count(),
        "max_price": qs.aggregate(m=Max("price"))["m"] or 0,
    })

