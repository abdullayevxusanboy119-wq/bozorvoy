from django.db.models import Max
from rest_framework import serializers

from .models import Category, Product


class CategorySerializer(serializers.ModelSerializer):
    products_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ("id", "name", "icon", "color", "products_count")


class ProductSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    on_sale = serializers.BooleanField(source="is_on_sale", read_only=True)
    discount_percent = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id", "name", "category", "price", "old_price", "on_sale", "discount_percent",
            "stock", "sold", "description", "icon", "image", "bg", "colors", "sizes", "created_at",
        )

    def get_category(self, obj):
        return {"id": obj.category_id, "name": obj.category.name}

    def get_image(self, obj):
        src = obj.image_src
        request = self.context.get("request")
        if src and request and src.startswith("/"):
            return request.build_absolute_uri(src)
        return src

    def get_discount_percent(self, obj):
        return round((1 - obj.price / obj.old_price) * 100) if obj.is_on_sale else 0
