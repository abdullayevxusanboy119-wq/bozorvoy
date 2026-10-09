"""Buyurtma mantig'i. Narx, chegirma, yetkazib berish narxi va ombor FAQAT shu yerda,
serverda hisoblanadi — mijozdan kelgan summalarga ishonilmaydi.

quote_order()  — hech narsani o'zgartirmasdan hisoblab ko'rsatadi (savat/checkout uchun)
create_order() — xuddi shu hisob-kitob + omborni kamaytirish + buyurtmani saqlash
"""
from collections import defaultdict

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from .models import Customer, Order, OrderItem, Payment, Product, PromoCode

DELIVERY_FEES = {"standard": 25000, "express": 45000, "pickup": 0}


def _fail(field, message):
    raise ValidationError({field: [message]})


def _stock_message(name, current):
    return f"«{name}» dan omborda faqat {current} dona qoldi" if current else f"«{name}» tugagan"


def _check_variant(product, value, options, label):
    if options:
        if not value:
            _fail("items", f"«{product.name}» uchun {label}ni tanlang")
        if value not in options:
            _fail("items", f"«{product.name}» uchun bunday {label} yo'q: {value}")
    elif value:
        _fail("items", f"«{product.name}» uchun {label} tanlovi yo'q")


def _prepare(data):
    """Qatorlarni tekshiradi, summani serverdagi narxdan hisoblaydi, promo-kodni tekshiradi."""
    lines = data["items"]
    ids = {l["product"] for l in lines}
    products = {p.id: p for p in Product.objects.filter(pk__in=ids, is_active=True, category__is_active=True)}
    if ids - products.keys():
        _fail("items", "Mahsulot topilmadi yoki sotuvda emas")

    rows, qty_by_product, subtotal = [], defaultdict(int), 0
    for l in lines:
        p = products[l["product"]]
        color, size = (l.get("color") or "").strip(), (l.get("size") or "").strip()
        _check_variant(p, color, p.colors, "rang")
        _check_variant(p, size, p.sizes, "o'lcham")
        rows.append({"product": p, "qty": l["qty"], "color": color, "size": size})
        qty_by_product[p.id] += l["qty"]
        subtotal += p.price * l["qty"]

    promo, discount = None, 0
    code = (data.get("promo") or "").strip().upper()
    if code:
        promo = PromoCode.objects.filter(code=code).first()
        if not promo:
            _fail("promo", "Promo-kod topilmadi")
        error = promo.validation_error()
        if error:
            _fail("promo", error)
        discount = (subtotal * promo.percent + 50) // 100  # butun songa yaxlitlash

    fee = DELIVERY_FEES[data.get("delivery") or "standard"]
    return {
        "products": products, "rows": rows, "qty_by_product": dict(qty_by_product),
        "subtotal": subtotal, "promo": promo, "discount": discount, "fee": fee,
        "total": subtotal - discount + fee,
    }


def quote_order(data):
    """Buyurtmani yaratmasdan summalarni hisoblaydi (ombor faqat o'qib tekshiriladi)."""
    q = _prepare(data)
    stock = dict(Product.objects.filter(pk__in=q["qty_by_product"]).values_list("id", "stock"))
    for pid, qty in q["qty_by_product"].items():
        if stock[pid] < qty:
            _fail("items", _stock_message(q["products"][pid].name, stock[pid]))
    return {
        "subtotal": q["subtotal"],
        "discount_amount": q["discount"],
        "delivery_fee": q["fee"],
        "total": q["total"],
        "promo": {"code": q["promo"].code, "percent": q["promo"].percent} if q["promo"] else None,
        "items": [
            {"product": r["product"].id, "product_name": r["product"].name, "price": r["product"].price,
             "qty": r["qty"], "color": r["color"], "size": r["size"], "line_total": r["product"].price * r["qty"]}
            for r in q["rows"]
        ],
    }


@transaction.atomic
def create_order(data, user=None):
    """Hamma narsa bitta tranzaksiyada: biror qadam xato bersa, ombor ham qaytadi."""
    q = _prepare(data)

    # ombor: shartli UPDATE — parallel buyurtmalarda ham manfiy qoldiq bo'lmaydi
    for pid in sorted(q["qty_by_product"]):
        qty = q["qty_by_product"][pid]
        updated = Product.objects.filter(pk=pid, stock__gte=qty).update(
            stock=F("stock") - qty, sold=F("sold") + qty
        )
        if not updated:
            current = Product.objects.values_list("stock", flat=True).get(pk=pid)
            _fail("items", _stock_message(q["products"][pid].name, current))

    promo = q["promo"]
    if promo:
        PromoCode.objects.filter(pk=promo.pk).update(used=F("used") + 1)

    customer, _ = Customer.objects.get_or_create(phone=data["phone"], defaults={"name": data["name"]})
    order = Order.objects.create(
        customer=customer,
        user=user if user and user.is_authenticated else None,
        name=data["name"], phone=data["phone"], city=data["city"], address=data["address"],
        delivery=data["delivery"], delivery_fee=q["fee"],
        subtotal=q["subtotal"], promo=promo, discount_amount=q["discount"], total=q["total"],
        payment_method=data["payment_method"],
        # to'lov holatini mijoz belgilay olmaydi: Payme/Click tasdig'i 9-bosqichda (webhook) keladi
        payment_status=Order.PayStatus.PENDING,
        status=Order.Status.NEW,
    )
    OrderItem.objects.bulk_create([
        OrderItem(order=order, product=r["product"], product_name=r["product"].name,
                  price=r["product"].price, qty=r["qty"], color=r["color"], size=r["size"])
        for r in q["rows"]
    ])
    Payment.objects.create(order=order, method=order.payment_method, amount=order.total)
    return order
