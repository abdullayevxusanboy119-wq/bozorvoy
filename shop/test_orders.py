import datetime

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from .models import Category, Customer, Order, Payment, Product, PromoCode, User


class OrderApiTests(TestCase):
    def setUp(self):
        cache.clear()
        cat = Category.objects.create(name="Kiyim")
        self.shirt = Product.objects.create(
            name="Ko'ylak", category=cat, price=100000, stock=5, sold=1,
            colors=["Qora", "Oq"], sizes=["S", "M"],
        )
        self.mug = Product.objects.create(name="Krujka", category=cat, price=20000, stock=3)
        self.promo = PromoCode.objects.create(code="YANGI10", percent=10)

    def order(self, items=None, token=None, **kw):
        body = {
            "name": "Aziza", "phone": "+998 90 111 22 33", "city": "Toshkent", "address": "Chilonzor 5",
            "delivery": "standard", "payment_method": "payme",
            "items": items if items is not None else [{"product": self.shirt.id, "qty": 2, "color": "Qora", "size": "M"}],
        }
        body.update(kw)
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post("/api/orders/", body, content_type="application/json", **extra)

    def stock(self, p):
        p.refresh_from_db()
        return p.stock, p.sold

    def test_happy_path_server_computes_everything(self):
        r = self.order(total=1, subtotal=1, delivery_fee=0, status="done", payment_status="paid")  # bular e'tiborga olinmaydi
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertTrue(d["number"].startswith("BZ-"))
        self.assertEqual(d["subtotal"], 200000)
        self.assertEqual(d["delivery_fee"], 25000)
        self.assertEqual(d["total"], 225000)
        self.assertEqual(d["status"], "new")
        self.assertEqual(d["payment_status"], "pending")
        self.assertEqual(d["items_count"], 2)
        self.assertEqual(d["items"][0]["price"], 100000)
        self.assertEqual(d["items"][0]["line_total"], 200000)
        self.assertEqual(d["phone"], "+998901112233")
        self.assertEqual(self.stock(self.shirt), (3, 3))

    def restock(self, n=50):
        Product.objects.filter(pk=self.shirt.pk).update(stock=n)

    def test_payment_record_created_for_every_method(self):
        self.restock()
        for method in ("payme", "click", "cash"):
            d = self.order(payment_method=method).json()
            pay = Payment.objects.get(order__number=d["number"])
            self.assertEqual((pay.method, pay.amount, pay.status), (method, d["total"], "pending"))

    def test_delivery_fees(self):
        self.restock()
        for delivery, fee in (("standard", 25000), ("express", 45000), ("pickup", 0)):
            self.assertEqual(self.order(delivery=delivery).json()["delivery_fee"], fee)

    def test_promo_discount_and_usage(self):
        d = self.order(promo="yangi10").json()  # kichik harfda ham ishlaydi
        self.assertEqual(d["discount_amount"], 20000)
        self.assertEqual(d["total"], 200000 - 20000 + 25000)
        self.assertEqual(d["promo"], "YANGI10")
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.used, 1)

    def test_bad_promo_rejected_and_nothing_changes(self):
        expired = PromoCode.objects.create(code="ESKI", percent=5, expires=timezone.localdate() - datetime.timedelta(days=1))
        off = PromoCode.objects.create(code="OFF", percent=5, is_active=False)
        for code in ("YOQ", "ESKI", "OFF"):
            self.assertEqual(self.order(promo=code).status_code, 400, code)
        self.assertEqual(self.stock(self.shirt), (5, 1))
        self.assertEqual(Order.objects.count(), 0)

    def test_insufficient_stock(self):
        r = self.order(items=[{"product": self.shirt.id, "qty": 6, "color": "Qora", "size": "M"}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("faqat 5 dona", r.json()["items"][0])
        self.assertEqual(Order.objects.count(), 0)

    def test_failure_on_second_line_rolls_back_first(self):
        r = self.order(items=[
            {"product": self.shirt.id, "qty": 2, "color": "Qora", "size": "M"},
            {"product": self.mug.id, "qty": 99},
        ])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.stock(self.shirt), (5, 1))  # birinchi qator ombori qaytdi
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Customer.objects.count(), 0)

    def test_same_product_two_variants_share_one_stock(self):
        r = self.order(items=[
            {"product": self.shirt.id, "qty": 3, "color": "Qora", "size": "M"},
            {"product": self.shirt.id, "qty": 3, "color": "Oq", "size": "S"},
        ])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.stock(self.shirt), (5, 1))

    def test_out_of_stock_message(self):
        self.mug.stock = 0
        self.mug.save()
        r = self.order(items=[{"product": self.mug.id, "qty": 1}])
        self.assertIn("tugagan", r.json()["items"][0])

    def test_variant_validation(self):
        no_color = self.order(items=[{"product": self.shirt.id, "qty": 1, "size": "M"}])
        bad_size = self.order(items=[{"product": self.shirt.id, "qty": 1, "color": "Qora", "size": "XXL"}])
        needless = self.order(items=[{"product": self.mug.id, "qty": 1, "color": "Qora"}])
        for r in (no_color, bad_size, needless):
            self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_unavailable_and_unknown_products(self):
        self.mug.is_active = False
        self.mug.save()
        self.assertEqual(self.order(items=[{"product": self.mug.id, "qty": 1}]).status_code, 400)
        self.assertEqual(self.order(items=[{"product": 99999, "qty": 1}]).status_code, 400)

    def test_input_validation(self):
        self.assertEqual(self.order(items=[]).status_code, 400)
        self.assertEqual(self.order(phone="123").status_code, 400)
        self.assertEqual(self.order(name="").status_code, 400)
        self.assertEqual(self.order(delivery="drone").status_code, 400)
        self.assertEqual(self.order(payment_method="bitcoin").status_code, 400)
        self.assertEqual(self.order(items=[{"product": self.mug.id, "qty": 0}]).status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_price_snapshot_survives_price_change(self):
        d = self.order().json()
        self.shirt.price = 999999
        self.shirt.save()
        item = Order.objects.get(number=d["number"]).items.get()
        self.assertEqual(item.price, 100000)

    def test_customer_reused_by_phone(self):
        self.order()
        self.order(phone="901112233", name="Boshqa ism")
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Customer.objects.get().orders.count(), 2)

    def test_guest_and_authenticated_orders(self):
        guest = Order.objects.get(number=self.order().json()["number"])
        self.assertIsNone(guest.user)
        reg = self.client.post("/api/auth/register/", {"name": "B", "contact": "b@mail.uz", "password": "Sinov-parol-482"}, content_type="application/json").json()
        mine = Order.objects.get(number=self.order(token=reg["access"]).json()["number"])
        self.assertEqual(mine.user.email, "b@mail.uz")

    def test_invalid_token_is_rejected(self):
        self.assertEqual(self.order(token="yolg'on.token.bor").status_code, 401)
