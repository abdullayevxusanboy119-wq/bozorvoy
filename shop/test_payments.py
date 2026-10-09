import base64
import json
from datetime import timedelta

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import payments as pay
from .click import make_sign
from .models import Category, Order, Payment, Product

PAYME = {"MERCHANT_ID": "m1", "KEY": "secret-key", "CHECKOUT_URL": "https://test.paycom.uz"}
CLICK = {"SERVICE_ID": "77", "MERCHANT_ID": "9", "SECRET_KEY": "click-secret"}
AUTH = "Basic " + base64.b64encode(b"Paycom:secret-key").decode()


@override_settings(PAYME=PAYME, CLICK=CLICK, PAYMENT_LOCAL_SANDBOX=False)
class PaymentBase(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()  # throttle hisobi testlar orasida to'planib qolmasin
        cat = Category.objects.create(name="Test")
        self.product = Product.objects.create(name="Quloqchin", category=cat, price=100000, stock=10)

    def make_order(self, method="payme", qty=2):
        r = self.client.post("/api/orders/", {
            "name": "Aziza", "phone": "+998901112233", "city": "Toshkent", "address": "Chilonzor",
            "delivery": "pickup", "payment_method": method,
            "items": [{"product": self.product.id, "qty": qty}]}, content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    # ---- Payme
    def rpc(self, method, params, auth=AUTH):
        kw = {"HTTP_AUTHORIZATION": auth} if auth else {}
        r = self.client.post("/api/pay/payme/", json.dumps({"id": 5, "method": method, "params": params}),
                             content_type="application/json", **kw)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def payme_create(self, order, tid="tx1", amount=None):
        return self.rpc("CreateTransaction", {"id": tid, "time": 1700000000000,
                                              "amount": amount or order["total"] * 100,
                                              "account": {"order_id": order["number"]}})


class PaymeTests(PaymentBase):
    def test_full_flow_pays_order(self):
        o = self.make_order()
        acc = {"order_id": o["number"]}
        self.assertEqual(self.rpc("CheckPerformTransaction", {"amount": 20000000, "account": acc})["result"], {"allow": True})
        c = self.payme_create(o)["result"]
        self.assertEqual(c["state"], 1)
        again = self.payme_create(o)["result"]          # takroriy so'rov: bir xil javob
        self.assertEqual(again, c)
        p = self.rpc("PerformTransaction", {"id": "tx1"})["result"]
        self.assertEqual(p["state"], 2)
        self.assertEqual(self.rpc("PerformTransaction", {"id": "tx1"})["result"], p)   # idempotent
        order = Order.objects.get(number=o["number"])
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.payments.get().status, "paid")
        self.assertEqual(order.payments.count(), 1)     # yangi qator ochilmadi, mavjudi ishlatildi
        chk = self.rpc("CheckTransaction", {"id": "tx1"})["result"]
        self.assertEqual((chk["state"], chk["reason"]), (2, None))

    def test_auth_required(self):
        o = self.make_order()
        bad = self.rpc("CheckTransaction", {"id": "x"}, auth="Basic " + base64.b64encode(b"Paycom:wrong").decode())
        self.assertEqual(bad["error"]["code"], -32504)
        self.assertEqual(self.rpc("CheckTransaction", {"id": "x"}, auth=None)["error"]["code"], -32504)
        self.assertEqual(o["payment_status"], "pending")

    def test_amount_and_account_errors(self):
        o = self.make_order()
        self.assertEqual(self.payme_create(o, amount=100)["error"]["code"], -31001)
        r = self.rpc("CheckPerformTransaction", {"amount": 1, "account": {"order_id": "BZ-9999"}})
        self.assertEqual((r["error"]["code"], r["error"]["data"]), (-31050, "order_id"))
        cash = self.make_order("cash")
        r = self.rpc("CheckPerformTransaction", {"amount": cash["total"] * 100, "account": {"order_id": cash["number"]}})
        self.assertEqual(r["error"]["code"], -31051)

    def test_second_open_transaction_rejected(self):
        o = self.make_order()
        self.payme_create(o, "tx1")
        self.assertEqual(self.payme_create(o, "tx2")["error"]["code"], -31008)

    def test_cancel_before_pay_keeps_order_payable(self):
        o = self.make_order()
        self.payme_create(o)
        c = self.rpc("CancelTransaction", {"id": "tx1", "reason": 4})["result"]
        self.assertEqual(c["state"], -1)
        order = Order.objects.get(number=o["number"])
        self.assertEqual((order.payment_status, order.status), ("pending", "new"))
        self.assertEqual(self.rpc("PerformTransaction", {"id": "tx1"})["error"]["code"], -31008)
        # mijoz qayta to'lashi mumkin: yangi tranzaksiya
        self.assertEqual(self.payme_create(o, "tx2")["result"]["state"], 1)

    def test_cancel_after_pay_refunds_and_restores_stock(self):
        o = self.make_order(qty=3)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 7)
        self.payme_create(o)
        self.rpc("PerformTransaction", {"id": "tx1"})
        c = self.rpc("CancelTransaction", {"id": "tx1", "reason": 5})["result"]
        self.assertEqual(c["state"], -2)
        order = Order.objects.get(number=o["number"])
        self.assertEqual((order.status, order.payment_status), ("cancelled", "failed"))
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 10)
        self.assertEqual(self.rpc("CancelTransaction", {"id": "tx1", "reason": 5})["result"]["state"], -2)  # idempotent

    def test_cancel_after_delivery_refused(self):
        o = self.make_order()
        self.payme_create(o)
        self.rpc("PerformTransaction", {"id": "tx1"})
        Order.objects.filter(number=o["number"]).update(status="shipping")
        self.assertEqual(self.rpc("CancelTransaction", {"id": "tx1", "reason": 5})["error"]["code"], -31007)

    def test_expired_transaction_cancelled_on_perform(self):
        o = self.make_order()
        self.payme_create(o)
        Payment.objects.filter(provider_trans_id="tx1").update(provider_create_time=pay.now_ms() - 13 * 3600 * 1000)
        self.assertEqual(self.rpc("PerformTransaction", {"id": "tx1"})["error"]["code"], -31008)
        p = Payment.objects.get(provider_trans_id="tx1")
        self.assertEqual((p.provider_state, p.provider_reason, p.status), (-1, 4, "failed"))

    def test_unknown_transaction_and_method(self):
        self.assertEqual(self.rpc("CheckTransaction", {"id": "nope"})["error"]["code"], -31003)
        self.assertEqual(self.rpc("Nope", {})["error"]["code"], -32601)
        self.assertEqual(self.client.get("/api/pay/payme/").json()["error"]["code"], -32300)

    def test_get_statement(self):
        o = self.make_order()
        self.payme_create(o)
        r = self.rpc("GetStatement", {"from": 1699999999999, "to": 1700000000001})["result"]["transactions"]
        self.assertEqual([(t["id"], t["amount"], t["account"]["order_id"]) for t in r],
                         [("tx1", o["total"] * 100, o["number"])])
        self.assertEqual(self.rpc("GetStatement", {"from": 1, "to": 2})["result"]["transactions"], [])

    def test_payme_link_and_order_response(self):
        o = self.make_order()
        url = o["payment_url"]
        self.assertTrue(url.startswith("https://test.paycom.uz/"))
        decoded = base64.b64decode(url.rsplit("/", 1)[1]).decode()
        self.assertIn(f"m=m1;ac.order_id={o['number']};a={o['total'] * 100};", decoded)
        self.assertEqual(self.make_order("cash")["payment_url"], "")


class ClickTests(PaymentBase):
    def click(self, action, order, click_trans_id="1001", prepare_id=None, error="0", amount=None, sign=None):
        d = {"click_trans_id": click_trans_id, "service_id": "77", "click_paydoc_id": "5", "merchant_trans_id": order["number"],
             "amount": amount or f"{order['total']}.0", "action": str(action), "error": error, "error_note": "",
             "sign_time": "2026-09-28 10:00:00"}
        if action == 1:
            d["merchant_prepare_id"] = str(prepare_id)
        d["sign_string"] = sign or make_sign(d, "click-secret", action)
        return self.client.post(f"/api/pay/click/{'prepare' if action == 0 else 'complete'}/", d).json()

    def test_full_flow_pays_order(self):
        o = self.make_order("click")
        p = self.click(0, o)
        self.assertEqual(p["error"], 0)
        self.assertEqual(self.click(0, o)["merchant_prepare_id"], p["merchant_prepare_id"])   # takror prepare
        c = self.click(1, o, prepare_id=p["merchant_prepare_id"])
        self.assertEqual((c["error"], c["merchant_confirm_id"]), (0, p["merchant_prepare_id"]))
        order = Order.objects.get(number=o["number"])
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.payments.count(), 1)
        self.assertEqual(self.click(1, o, prepare_id=p["merchant_prepare_id"])["error"], -4)   # takror complete
        self.assertEqual(self.click(0, o)["error"], -4)

    def test_bad_signature_and_wrong_amount(self):
        o = self.make_order("click")
        self.assertEqual(self.click(0, o, sign="0" * 32)["error"], -1)
        self.assertEqual(self.click(0, o, amount="1.0")["error"], -2)
        self.assertEqual(Order.objects.get(number=o["number"]).payment_status, "pending")

    def test_not_found_and_wrong_method(self):
        o = self.make_order("click")
        self.assertEqual(self.click(0, {"number": "BZ-9999", "total": 5})["error"], -5)
        cash = self.make_order("cash")
        self.assertEqual(self.click(0, cash)["error"], -5)
        self.assertEqual(self.click(1, o, prepare_id=999)["error"], -6)

    def test_click_reports_failure(self):
        o = self.make_order("click")
        p = self.click(0, o)
        c = self.click(1, o, prepare_id=p["merchant_prepare_id"], error="-5017")
        self.assertEqual(c["error"], -9)
        order = Order.objects.get(number=o["number"])
        self.assertEqual(order.payment_status, "pending")          # buyurtma ochiq, qayta to'lash mumkin
        self.assertEqual(order.payments.get().status, "failed")
        p2 = self.click(0, o, click_trans_id="1002")               # yangi urinish
        self.assertEqual(p2["error"], 0)
        self.assertEqual(self.click(1, o, click_trans_id="1002", prepare_id=p2["merchant_prepare_id"])["error"], 0)

    def test_cancelled_order_cannot_be_paid(self):
        o = self.make_order("click")
        Order.objects.filter(number=o["number"]).update(status="cancelled")
        self.assertEqual(self.click(0, o)["error"], -9)

    def test_click_link(self):
        o = self.make_order("click")
        self.assertIn("my.click.uz/services/pay?service_id=77&merchant_id=9", o["payment_url"])
        self.assertIn(f"transaction_param={o['number']}", o["payment_url"])


@override_settings(PAYME={"KEY": "dev-payme-key"}, CLICK={"SERVICE_ID": "1", "SECRET_KEY": "dev-click-secret"},
                   PAYMENT_LOCAL_SANDBOX=True, PAYMENT_RETURN_URL="http://x.test/")
class SandboxTests(PaymentBase):
    def test_sandbox_pay_and_cancel_both_methods(self):
        for method in ("payme", "click"):
            o = self.make_order(method)
            self.assertEqual(o["payment_url"], f"/api/pay/sandbox/{method}/{o['number']}/")
            self.assertEqual(self.client.get(o["payment_url"]).status_code, 200)
            r = self.client.post(o["payment_url"], {"do": "cancel"})
            self.assertTrue(r["Location"].endswith("&pay=cancel"), r["Location"])
            self.assertEqual(Order.objects.get(number=o["number"]).payment_status, "pending")
            r = self.client.post(o["payment_url"], {"do": "pay"})
            self.assertTrue(r["Location"].endswith("&pay=success"), r["Location"])
            order = Order.objects.get(number=o["number"])
            self.assertEqual(order.payment_status, "paid")

    @override_settings(PAYMENT_LOCAL_SANDBOX=False)
    def test_sandbox_disabled_in_production(self):
        o = self.make_order("payme")
        self.assertEqual(self.client.get(f"/api/pay/sandbox/payme/{o['number']}/").status_code, 404)


class CancelUnpaidTests(PaymentBase):
    def age(self, order, hours):
        Order.objects.filter(number=order["number"]).update(created_at=timezone.now() - timedelta(hours=hours))

    def test_cancels_only_old_unpaid_online_orders(self):
        old_payme = self.make_order("payme", qty=2)
        old_click = self.make_order("click", qty=1)
        fresh = self.make_order("payme", qty=1)
        cash = self.make_order("cash", qty=1)
        paid = self.make_order("payme", qty=1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 4)
        self.payme_create(paid, "txp")
        self.rpc("PerformTransaction", {"id": "txp"})
        for o in (old_payme, old_click, cash, paid):
            self.age(o, 30)
        self.payme_create(old_payme, "txo")   # ochiq (to'lanmagan) Payme tranzaksiyasi ham yopilishi kerak

        call_command("cancel_unpaid", "--dry-run")
        self.assertEqual(Order.objects.filter(status="cancelled").count(), 0)   # dry-run hech narsa o'zgartirmaydi

        call_command("cancel_unpaid")
        st = {n: Order.objects.get(number=o["number"]) for n, o in
              dict(old_payme=old_payme, old_click=old_click, fresh=fresh, cash=cash, paid=paid).items()}
        self.assertEqual([st[k].status for k in ("old_payme", "old_click")], ["cancelled", "cancelled"])
        self.assertEqual(st["old_payme"].payment_status, "failed")
        self.assertEqual([st[k].status for k in ("fresh", "cash", "paid")], ["new", "new", "new"])
        self.assertEqual(st["paid"].payment_status, "paid")
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 7)     # 4 + 2 + 1 qaytdi
        self.assertEqual(Payment.objects.get(provider_trans_id="txo").provider_state, -1)
        # bekor qilingan buyurtmani Payme endi to'lata olmaydi
        self.assertEqual(self.payme_create(old_payme, "txn")["error"]["code"], -31051)

        call_command("cancel_unpaid")   # qayta ishga tushirish xavfsiz: ikki marta qaytarmaydi
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 7)


class PaidOnlyRevenueTests(PaymentBase):
    def test_reports_and_dashboard_count_only_paid(self):
        from django.contrib.auth import get_user_model
        admin = get_user_model().objects.create_user(username="adm", password="x", is_staff=True)
        # Ilova faqat JWT bilan ishlaydi, shuning uchun force_login emas, force_authenticate
        api = APIClient()
        api.force_authenticate(user=admin)

        paid = self.make_order("payme", qty=2)       # 200000
        self.make_order("payme", qty=1)              # to'lanmagan
        self.payme_create(paid)
        self.rpc("PerformTransaction", {"id": "tx1"})

        r = api.get("/api/admin/dashboard/")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual((d["totals"]["revenue"], d["today"]["revenue"]), (200000, 200000))
        self.assertEqual(d["totals"]["orders"], 2)

        r = api.get("/api/admin/reports/")
        self.assertEqual(r.status_code, 200, r.content)
        rep = r.json()
        self.assertEqual(sum(m["revenue"] for m in rep["monthly_revenue"]), 200000)