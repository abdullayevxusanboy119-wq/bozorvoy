import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Category, Customer, Order, Payment, Product, PromoCode, User

PWD = "Sinov-parol-482"
ADMIN_URLS = [
    "/api/admin/dashboard/", "/api/admin/orders/", "/api/admin/products/", "/api/admin/categories/",
    "/api/admin/promos/", "/api/admin/customers/", "/api/admin/payments/",
]


class AdminApiBase(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(username="staff", email="staff@bozorvoy.uz", password=PWD, is_staff=True)
        self.shopper = User.objects.create_user(username="shopper", email="shopper@mail.uz", password=PWD)
        self.tok = self.token(self.staff)
        self.cat = Category.objects.create(name="Kiyim")
        self.shirt = Product.objects.create(name="Ko'ylak", category=self.cat, price=100000, stock=10,
                                            colors=["Qora"], sizes=["M"])
        self.mug = Product.objects.create(name="Krujka", category=self.cat, price=20000, stock=3)
        self.promo = PromoCode.objects.create(code="YANGI10", percent=10)

    @staticmethod
    def token(user):
        return str(RefreshToken.for_user(user).access_token)

    def call(self, method, url, body=None, token="staff", **extra):
        headers = {}
        if token:
            t = self.tok if token == "staff" else token
            headers["HTTP_AUTHORIZATION"] = f"Bearer {t}"
        kwargs = {"content_type": "application/json"} if body is not None and "format" not in extra else {}
        kwargs.update(headers)
        if body is None:
            return getattr(self.client, method)(url, **headers)
        return getattr(self.client, method)(url, body, **kwargs)

    def get(self, url, **kw):
        return self.call("get", url, **kw)

    def place(self, qty=2, user_token=None, **kw):
        body = {"name": "Aziza", "phone": "+998 90 111 22 33", "city": "Toshkent", "address": "Chilonzor 5",
                "delivery": "standard", "payment_method": "cash",
                "items": [{"product": self.shirt.id, "qty": qty, "color": "Qora", "size": "M"}]}
        body.update(kw)
        h = {"HTTP_AUTHORIZATION": f"Bearer {user_token}"} if user_token else {}
        r = self.client.post("/api/orders/", body, content_type="application/json", **h)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["number"]


class PermissionTests(AdminApiBase):
    def test_anonymous_gets_401_and_customer_gets_403(self):
        shopper = self.token(self.shopper)
        for url in ADMIN_URLS:
            self.assertEqual(self.get(url, token=None).status_code, 401, url)
            self.assertEqual(self.get(url, token=shopper).status_code, 403, url)
            self.assertEqual(self.get(url).status_code, 200, url)

    def test_customer_cannot_change_anything(self):
        shopper = self.token(self.shopper)
        r = self.call("post", "/api/admin/products/", {"name": "X", "category": self.cat.id, "price": 1}, token=shopper)
        self.assertEqual(r.status_code, 403)
        number = self.place()
        r = self.call("patch", f"/api/admin/orders/{number}/", {"status": "done"}, token=shopper)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Order.objects.get(number=number).status, "new")


class OrderAdminTests(AdminApiBase):
    def test_list_detail_and_filters(self):
        n1 = self.place()
        n2 = self.place(name="Bobur", phone="+998 91 222 33 44", payment_method="click")
        data = self.get("/api/admin/orders/").json()
        self.assertEqual(data["count"], 2)
        self.assertEqual([o["number"] for o in data["results"]], [n2, n1])  # yangisi birinchi
        self.assertEqual(self.get("/api/admin/orders/?payment_method=click").json()["count"], 1)
        self.assertEqual(self.get("/api/admin/orders/?q=bobur").json()["results"][0]["number"], n2)
        self.assertEqual(self.get("/api/admin/orders/?q=901112233").json()["results"][0]["number"], n1)
        self.assertEqual(self.get("/api/admin/orders/?status=done").json()["count"], 0)
        Order.objects.filter(number=n1).update(created_at=timezone.now() - timedelta(days=10))
        today = timezone.localdate().isoformat()
        self.assertEqual(self.get(f"/api/admin/orders/?date_from={today}").json()["count"], 1)
        self.assertEqual(self.get(f"/api/admin/orders/?date_to={today}").json()["count"], 2)
        d = self.get(f"/api/admin/orders/{n1}/").json()
        self.assertEqual(d["number"], n1)
        self.assertEqual(len(d["items"]), 1)
        self.assertEqual(d["payments"][0]["status"], "pending")
        self.assertEqual(self.get("/api/admin/orders/BZ-99999/").status_code, 404)

    def test_status_change_flags_customer_and_put_is_not_allowed(self):
        buyer = self.token(self.shopper)
        number = self.place(user_token=buyer)
        r = self.call("patch", f"/api/admin/orders/{number}/", {"status": "processing"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "processing")
        self.assertFalse(r.json()["status_seen"])
        mine = self.get("/api/orders/", token=buyer).json()["results"][0]
        self.assertEqual(mine["status"], "processing")
        self.assertFalse(mine["status_seen"])  # mijozda "Yangi holat!"
        self.assertEqual(self.call("put", f"/api/admin/orders/{number}/", {"status": "done"}).status_code, 405)

    def test_same_status_does_not_reflag(self):
        number = self.place()
        self.call("patch", f"/api/admin/orders/{number}/", {"status": "processing"})
        Order.objects.filter(number=number).update(status_seen=True)
        self.call("patch", f"/api/admin/orders/{number}/", {"status": "processing"})
        self.assertTrue(Order.objects.get(number=number).status_seen)

    def test_payment_status_updates_order_and_payment_record(self):
        number = self.place()
        r = self.call("patch", f"/api/admin/orders/{number}/", {"payment_status": "paid"})
        self.assertEqual(r.json()["payment_status"], "paid")
        self.assertEqual(r.json()["payments"][0]["status"], "paid")
        self.assertEqual(Payment.objects.get(order__number=number).status, "paid")

    def test_cancel_restores_stock_sold_and_promo_once(self):
        number = self.place(qty=4, promo="YANGI10")
        self.shirt.refresh_from_db()
        self.promo.refresh_from_db()
        self.assertEqual((self.shirt.stock, self.shirt.sold, self.promo.used), (6, 4, 1))
        url = f"/api/admin/orders/{number}/"
        self.assertEqual(self.call("patch", url, {"status": "cancelled"}).status_code, 200)
        self.call("patch", url, {"status": "cancelled"})  # ikkinchi marta — qaytmaydi
        self.shirt.refresh_from_db()
        self.promo.refresh_from_db()
        self.assertEqual((self.shirt.stock, self.shirt.sold, self.promo.used), (10, 0, 0))
        self.assertTrue(Order.objects.get(number=number).stock_restored)

    def test_cancelled_order_is_final(self):
        number = self.place()
        url = f"/api/admin/orders/{number}/"
        self.call("patch", url, {"status": "cancelled"})
        r = self.call("patch", url, {"status": "processing"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("status", r.json())
        r = self.call("patch", url, {"payment_status": "paid"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.get(number=number).status, "cancelled")

    def test_done_order_cannot_be_cancelled(self):
        number = self.place()
        url = f"/api/admin/orders/{number}/"
        self.call("patch", url, {"status": "done"})
        r = self.call("patch", url, {"status": "cancelled"})
        self.assertEqual(r.status_code, 400)
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.stock, 8)  # ombor tegilmadi

    def test_update_validation(self):
        number = self.place()
        url = f"/api/admin/orders/{number}/"
        self.assertEqual(self.call("patch", url, {}).status_code, 400)
        self.assertEqual(self.call("patch", url, {"status": "uchib ketdi"}).status_code, 400)
        self.assertEqual(self.call("patch", url, {"payment_status": "yarim"}).status_code, 400)


class ProductAdminTests(AdminApiBase):
    def create(self, **kw):
        body = {"name": "Krossovka", "category": self.cat.id, "price": 300000, "stock": 7,
                "colors": ["Oq", " Qora ", "Oq", ""], "sizes": ["40", "41"]}
        body.update(kw)
        return self.call("post", "/api/admin/products/", body)

    def test_create_cleans_options_and_shows_up_publicly(self):
        r = self.create()
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["colors"], ["Oq", "Qora"])
        self.assertEqual(d["sold"], 0)
        self.assertEqual(d["category_name"], "Kiyim")
        public = self.client.get("/api/products/?q=krossovka").json()
        self.assertEqual(public["count"], 1)

    def test_validation(self):
        self.assertEqual(self.create(price=0).status_code, 400)
        self.assertEqual(self.create(colors="qora").status_code, 400)
        self.assertEqual(self.create(colors=[1, 2]).status_code, 400)
        self.assertEqual(self.create(category=9999).status_code, 400)
        r = self.create(price=300000, old_price=250000)
        self.assertEqual(r.status_code, 400)
        self.assertIn("old_price", r.json())
        r = self.create(price=300000, old_price=400000)
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["on_sale"])

    def test_partial_update_and_sale_rules(self):
        url = f"/api/admin/products/{self.shirt.id}/"
        r = self.call("patch", url, {"stock": 50, "price": 90000})
        self.assertEqual((r.status_code, r.json()["stock"], r.json()["price"]), (200, 50, 90000))
        self.assertEqual(self.call("patch", url, {"old_price": 80000}).status_code, 400)  # narxdan arzon
        self.assertEqual(self.call("patch", url, {"old_price": 120000}).status_code, 200)
        r = self.call("patch", url, {"price": 130000})  # eski narxdan qimmatlashtirib bo'lmaydi
        self.assertEqual(r.status_code, 400)
        r = self.call("patch", url, {"old_price": None})  # aksiyani tugatish
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["on_sale"])
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.sold, 0)  # sold qo'lda o'zgarmaydi
        self.call("patch", url, {"sold": 999})
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.sold, 0)

    def test_delete_archives_and_hides_from_shop_but_not_from_admin(self):
        number = self.place()
        r = self.call("delete", f"/api/admin/products/{self.shirt.id}/")
        self.assertEqual(r.status_code, 204)
        self.shirt.refresh_from_db()
        self.assertFalse(self.shirt.is_active)
        self.assertEqual(self.client.get(f"/api/products/{self.shirt.id}/").status_code, 404)
        self.assertEqual(self.get(f"/api/admin/products/{self.shirt.id}/").status_code, 200)
        self.assertEqual(self.get("/api/admin/products/?is_active=false").json()["count"], 1)
        self.assertEqual(self.get(f"/api/admin/orders/{number}/").json()["items"][0]["product_name"], "Ko'ylak")
        self.call("patch", f"/api/admin/products/{self.shirt.id}/", {"is_active": True})  # qaytarish
        self.assertEqual(self.client.get(f"/api/products/{self.shirt.id}/").status_code, 200)

    def test_filters_and_sorting(self):
        self.mug.old_price = 30000
        self.mug.save()
        self.assertEqual(self.get("/api/admin/products/?low_stock=true").json()["count"], 1)  # krujka: 3 ta
        self.assertEqual(self.get("/api/admin/products/?on_sale=true").json()["results"][0]["name"], "Krujka")
        self.assertEqual(self.get("/api/admin/products/?q=ko'yl").json()["count"], 1)
        names = [p["name"] for p in self.get("/api/admin/products/?sort=price-asc").json()["results"]]
        self.assertEqual(names, ["Krujka", "Ko'ylak"])
        self.assertEqual(len(self.get("/api/admin/products/?page_size=1").json()["results"]), 1)

    def test_image_upload_multipart_with_json_string_options(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (4, 4), "red").save(buf, "PNG")
        img = SimpleUploadedFile("kross.png", buf.getvalue(), content_type="image/png")
        with override_settings(MEDIA_ROOT=media):
            r = self.client.post(
                "/api/admin/products/",
                {"name": "Krossovka", "category": self.cat.id, "price": 300000, "stock": 5,
                 "colors": '["Oq", "Qora"]', "image": img},
                HTTP_AUTHORIZATION=f"Bearer {self.tok}",
            )
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["colors"], ["Oq", "Qora"])
        self.assertTrue(d["image_src"].startswith("http"))
        self.assertIn("/media/products/", d["image_src"])


class CategoryAdminTests(AdminApiBase):
    def test_crud_and_counts(self):
        r = self.call("post", "/api/admin/categories/", {"name": "Aksessuar", "icon": "fa-gem"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.call("post", "/api/admin/categories/", {"name": "Aksessuar"}).status_code, 400)
        rows = {c["name"]: c for c in self.get("/api/admin/categories/").json()}
        self.assertEqual(rows["Kiyim"]["products_count"], 2)
        self.assertEqual(rows["Aksessuar"]["products_count"], 0)
        cid = r.json()["id"]
        r = self.call("patch", f"/api/admin/categories/{cid}/", {"is_active": False})
        self.assertFalse(r.json()["is_active"])
        self.assertNotIn("Aksessuar", [c["name"] for c in self.client.get("/api/categories/").json()])

    def test_delete_blocked_when_it_has_products(self):
        r = self.call("delete", f"/api/admin/categories/{self.cat.id}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("detail", r.json())
        self.assertTrue(Category.objects.filter(pk=self.cat.pk).exists())
        empty = Category.objects.create(name="Bo'sh")
        self.assertEqual(self.call("delete", f"/api/admin/categories/{empty.id}/").status_code, 204)
        self.assertFalse(Category.objects.filter(pk=empty.pk).exists())


class PromoAdminTests(AdminApiBase):
    def test_create_uppercases_and_rejects_duplicates_in_any_case(self):
        r = self.call("post", "/api/admin/promos/", {"code": " qish20 ", "percent": 20})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["code"], "QISH20")
        self.assertEqual(self.call("post", "/api/admin/promos/", {"code": "Qish20", "percent": 5}).status_code, 400)
        self.assertEqual(self.call("post", "/api/admin/promos/", {"code": "BIG", "percent": 95}).status_code, 400)
        self.assertEqual(self.call("post", "/api/admin/promos/", {"code": "ZERO", "percent": 0}).status_code, 400)

    def test_update_keeps_own_code_and_used_is_read_only(self):
        url = f"/api/admin/promos/{self.promo.id}/"
        r = self.call("patch", url, {"code": "yangi10", "percent": 15, "used": 500})
        self.assertEqual(r.status_code, 200, r.content)
        self.promo.refresh_from_db()
        self.assertEqual((self.promo.code, self.promo.percent, self.promo.used), ("YANGI10", 15, 0))

    def test_delete_deactivates_and_shop_rejects_it(self):
        self.assertEqual(self.call("delete", f"/api/admin/promos/{self.promo.id}/").status_code, 204)
        self.promo.refresh_from_db()
        self.assertFalse(self.promo.is_active)
        r = self.client.post("/api/orders/quote/", {"promo": "YANGI10", "items": [{"product": self.mug.id, "qty": 1}]},
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)


class CustomerPaymentTests(AdminApiBase):
    def test_customers_totals_ignore_cancelled(self):
        a = self.place(qty=1)                                  # 100000 + 25000 yetkazish
        self.place(qty=1)
        self.place(qty=1, name="Bobur", phone="+998 91 222 33 44")
        self.call("patch", f"/api/admin/orders/{a}/", {"status": "cancelled"})
        rows = {c["name"]: c for c in self.get("/api/admin/customers/").json()["results"]}
        self.assertEqual(rows["Aziza"]["orders_count"], 2)
        self.assertEqual(rows["Aziza"]["total_spent"], 125000)  # bittasi bekor qilingan
        self.assertEqual(self.get("/api/admin/customers/?q=91 222".replace(" ", "")).json()["count"], 1)
        top = self.get("/api/admin/customers/?sort=orders").json()["results"][0]
        self.assertEqual(top["name"], "Aziza")
        self.assertEqual(Customer.objects.count(), 2)

    def test_payments_list_and_filters(self):
        n1 = self.place()
        self.place(payment_method="click")
        self.call("patch", f"/api/admin/orders/{n1}/", {"payment_status": "paid"})
        d = self.get("/api/admin/payments/").json()
        self.assertEqual(d["count"], 2)
        self.assertEqual(self.get("/api/admin/payments/?status=paid").json()["results"][0]["order"], n1)
        self.assertEqual(self.get("/api/admin/payments/?method=click").json()["count"], 1)
        self.assertEqual(self.get(f"/api/admin/payments/?q={n1}").json()["count"], 1)
        row = d["results"][0]
        self.assertEqual(set(row), {"id", "transaction_id", "order", "customer", "method", "amount", "status", "created_at"})


class DashboardTests(AdminApiBase):
    def test_empty_dashboard(self):
        d = self.get("/api/admin/dashboard/").json()
        self.assertEqual(d["totals"]["orders"], 0)
        self.assertEqual(d["totals"]["revenue"], 0)
        self.assertEqual(len(d["last_7_days"]), 7)
        self.assertEqual(d["last_7_days"][-1]["date"], timezone.localdate().isoformat())
        self.assertEqual(d["by_status"], {"new": 0, "processing": 0, "shipping": 0, "done": 0, "cancelled": 0})
        self.assertEqual(d["top_products"], [])
        self.assertEqual(d["low_stock"][0]["name"], "Krujka")  # 3 ta qolgan

    def test_numbers(self):
        n1 = self.place(qty=1)                       # 100000 + 25000 = 125000
        n2 = self.place(qty=2, delivery="pickup")    # 200000
        n3 = self.place(qty=1)                       # bekor qilinadi
        self.call("patch", f"/api/admin/orders/{n1}/", {"payment_status": "paid", "status": "done"})
        self.call("patch", f"/api/admin/orders/{n3}/", {"status": "cancelled"})
        Order.objects.filter(number=n2).update(created_at=timezone.now() - timedelta(days=3))
        d = self.get("/api/admin/dashboard/").json()
        t = d["totals"]
        self.assertEqual(t["orders"], 3)
        self.assertEqual(t["revenue"], 125000)          # daromad = faqat to'langan (n2 kutilmoqda, n3 bekor)
        self.assertEqual(t["paid_revenue"], 125000)
        self.assertEqual(t["pending_payments"], 1)      # n2
        self.assertEqual(t["customers"], 1)
        self.assertEqual(t["products"], 2)
        self.assertEqual(d["today"], {"orders": 1, "revenue": 125000})
        self.assertEqual(d["by_status"], {"new": 1, "processing": 0, "shipping": 0, "done": 1, "cancelled": 1})
        days = {x["date"]: x for x in d["last_7_days"]}
        three_ago = (timezone.localdate() - timedelta(days=3)).isoformat()
        self.assertEqual(days[three_ago], {"date": three_ago, "orders": 1, "revenue": 0})   # to'lanmagan
        self.assertEqual(sum(x["revenue"] for x in d["last_7_days"]), 125000)
        top = d["top_products"][0]
        self.assertEqual((top["name"], top["sold"]), ("Ko'ylak", 3))  # 1+2 (bekor qilingani qaytdi)
        self.assertEqual(d["recent_orders"][0]["number"], n3)
