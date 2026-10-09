"""Frontenddagi boshlang'ich ma'lumotlarni bazaga yuklaydi: python manage.py seed"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from shop.models import Category, Product, PromoCode

AKSIYA_ID = 6  # frontendda qattiq yozilgan virtual kategoriya — backendda kerak emas


class Command(BaseCommand):
    help = "Kategoriya, mahsulot va promo-kodlarni yuklaydi"

    @transaction.atomic
    def handle(self, *args, **opts):
        data = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "seed.json").read_text(encoding="utf-8"))

        cats = {}
        for c in data["categories"]:
            if c["id"] == AKSIYA_ID:
                continue
            obj, _ = Category.objects.update_or_create(
                name=c["name"], defaults={"icon": c["icon"], "color": c["color"]}
            )
            cats[c["id"]] = obj

        for p in data["products"]:
            Product.objects.update_or_create(
                name=p["name"],
                defaults={
                    "category": cats[p["category"]],
                    "price": p["price"],
                    "old_price": p.get("oldPrice"),
                    "stock": p["stock"],
                    "sold": p["sold"],
                    "icon": p["icon"],
                    "image_url": p.get("image", ""),
                    "bg": p.get("bg", ""),
                    "description": p.get("desc", ""),
                    "colors": p.get("colors", []),
                    "sizes": p.get("sizes", []),
                },
            )

        for d in data["discounts"]:
            PromoCode.objects.update_or_create(
                code=d["code"],
                defaults={"percent": d["percent"], "used": d["used"], "expires": d["expires"], "is_active": d["active"]},
            )

        self.stdout.write(self.style.SUCCESS(
            f"Tayyor: {Category.objects.count()} kategoriya, {Product.objects.count()} mahsulot, {PromoCode.objects.count()} promo-kod"
        ))
