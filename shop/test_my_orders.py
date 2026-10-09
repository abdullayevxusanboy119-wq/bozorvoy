from django.core.cache import cache
from django.test import TestCase

from .models import Category, Order, Product, PromoCode

PWD = "Sinov-parol-482"


class MyOrdersAndQuoteTests(TestCase):
    def setUp(self):
        cache.clear()
        cat = Category.objects.create(name="Kiyim")
        self.shirt = Product.objects.create(name="Ko'ylak", category=cat, price=100000, stock=20,
                                            colors=["Qora"], sizes=["M"])
        self.mug = Product.objects.create(name="Krujka", category=cat, price=20000, stock=3)
        PromoCode.objects.create(code="YANGI10", percent=10)
        self.a = self.register("a@mail.uz")
        self.b = self.register("b@mail.uz")

    def register(self, email):
        r = self.client.post("/api/auth/register/", {"name": email[0], "contact": email, "password": PWD}, content_type="application/json")
        return r.json()["access"]

    def place(self, token=None, qty=1, **kw):
        body = {"name": "X", "phone": "+998 90 111 22 33", "city": "T", "address": "A", "delivery": "standard",
                "payment_method": "cash", "items": [{"product": self.shirt.id, "qty": qty, "color": "Qora", "size": "M"}]}
        body.update(kw)
        h = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post("/api/orders/", body, content_type="application/json", **h)

    def get(self, url, token=None):
        h = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(url, **h)

    def post(self, url, body, token=None):
        h = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(url, body, content_type="application/json", **h)

    # ---- mening buyurtmalarim ----
    def test_list_requires_login(self):
        self.assertEqual(self.get("/api/orders/").status_code, 401)

    def test_list_shows_only_my_orders_newest_first(self):
        n1 = self.place(self.a).json()["number"]
        n2 = self.place(self.a, qty=2).json()["number"]
        self.place(self.b)
        self.place()  # mehmon buyurtmasi
        data = self.get("/api/orders/", self.a).json()
        self.assertEqual(data["count"], 2)
        self.assertEqual([o["number"] for o in data["results"]], [n2, n1])
        self.assertEqual(self.get("/api/orders/", self.b).json()["count"], 1)

    def test_detail_owner_only(self):
        number = self.place(self.a).json()["number"]
        self.assertEqual(self.get(f"/api/orders/{number}/", self.a).status_code, 200)
        self.assertEqual(self.get(f"/api/orders/{number}/", self.b).status_code, 404)  # mavjudligini ham bildirmaymiz
        self.assertEqual(self.get(f"/api/orders/{number}/").status_code, 401)
        self.assertEqual(self.get("/api/orders/BZ-99999/", self.a).status_code, 404)

    def test_seen_flag_flow(self):
        number = self.place(self.a).json()["number"]
        self.place(self.b)
        Order.objects.update(status="processing", status_seen=False)  # admin holatni o'zgartirdi
        self.assertFalse(self.get(f"/api/orders/{number}/", self.a).json()["status_seen"])
        r = self.post("/api/orders/seen/", {}, self.a)
        self.assertEqual(r.json(), {"updated": 1})
        self.assertTrue(self.get(f"/api/orders/{number}/", self.a).json()["status_seen"])
        self.assertEqual(Order.objects.filter(status_seen=False).count(), 1)  # B ning belgisi tegilmadi
        self.assertEqual(self.post("/api/orders/seen/", {}).status_code, 401)

    # ---- quote (hisoblab ko'rish) ----
    def quote(self, **kw):
        body = {"delivery": "standard", "items": [{"product": self.shirt.id, "qty": 2, "color": "Qora", "size": "M"}]}
        body.update(kw)
        return self.post("/api/orders/quote/", body)

    def test_quote_math_and_no_side_effects(self):
        r = self.quote(promo="yangi10", delivery="express")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual((d["subtotal"], d["discount_amount"], d["delivery_fee"], d["total"]), (200000, 20000, 45000, 225000))
        self.assertEqual(d["promo"], {"code": "YANGI10", "percent": 10})
        self.assertEqual(d["items"][0]["line_total"], 200000)
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.stock, 20)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(PromoCode.objects.get(code="YANGI10").used, 0)

    def test_quote_matches_real_order_exactly(self):
        for delivery in ("standard", "express", "pickup"):
            for promo in ("", "YANGI10"):
                q = self.quote(delivery=delivery, promo=promo).json()
                o = self.place(delivery=delivery, promo=promo, qty=2).json()
                self.assertEqual((q["subtotal"], q["discount_amount"], q["delivery_fee"], q["total"]),
                                 (o["subtotal"], o["discount_amount"], o["delivery_fee"], o["total"]))

    def test_quote_validates_promo_stock_and_variants(self):
        self.assertEqual(self.quote(promo="YOQ").status_code, 400)
        self.assertIn("promo", self.quote(promo="YOQ").json())
        low = self.post("/api/orders/quote/", {"items": [{"product": self.mug.id, "qty": 5}]})
        self.assertEqual(low.status_code, 400)
        self.assertIn("faqat 3 dona", low.json()["items"][0])
        nocolor = self.post("/api/orders/quote/", {"items": [{"product": self.shirt.id, "qty": 1, "size": "M"}]})
        self.assertEqual(nocolor.status_code, 400)
        self.assertEqual(self.post("/api/orders/quote/", {"items": []}).status_code, 400)
