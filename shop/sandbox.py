"""Lokal SANDBOX: Payme/Click hisobi bo'lmaganda to'lov oqimini o'zimizning webhook'lar orqali sinash.

Faqat settings.PAYMENT_LOCAL_SANDBOX = True bo'lganda ishlaydi. "To'lash" bosilganda bu sahifa
Payme/Click o'rnida turib, haqiqiy webhook'larni (imzo/Basic-auth bilan) ketma-ket chaqiradi.
"""
import base64
import json
import uuid

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.test import RequestFactory
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

from . import payments as pay
from .click import complete_view, make_sign, prepare_view
from .models import Order
from .payme import payme_view

PAGE = """<!doctype html><html lang="uz"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} sandbox</title><body style="font-family:system-ui;max-width:420px;margin:60px auto;padding:0 16px">
<h2>{name} — test to'lov (SANDBOX)</h2>
<p>Buyurtma: <b>{number}</b><br>Summa: <b>{total:,} so'm</b></p>
<p style="color:#888;font-size:13px">Bu haqiqiy to'lov emas. Tugmalar shu loyihaning haqiqiy {name} webhook'larini chaqiradi.</p>
<form method="post"><button name="do" value="pay" style="padding:12px 22px;font-size:16px">To'lash</button>
<button name="do" value="cancel" style="padding:12px 22px;font-size:16px;margin-left:8px">Bekor qilish</button></form></body></html>"""


def _payme_call(method, params):
    key = pay.payme_cfg()["KEY"]
    auth = "Basic " + base64.b64encode(f"Paycom:{key}".encode()).decode()
    req = RequestFactory().post("/api/pay/payme/", data=json.dumps({"id": 1, "method": method, "params": params}),
                                content_type="application/json", HTTP_AUTHORIZATION=auth)
    return json.loads(payme_view(req).content)


def _run_payme(order, do):
    acc, amount = {"order_id": order.number}, order.total * 100
    r = _payme_call("CheckPerformTransaction", {"amount": amount, "account": acc})
    if "error" in r:
        return "failed"
    tid = uuid.uuid4().hex[:24]
    r = _payme_call("CreateTransaction", {"id": tid, "time": pay.now_ms(), "amount": amount, "account": acc})
    if "error" in r:
        return "failed"
    if do == "cancel":
        _payme_call("CancelTransaction", {"id": tid, "reason": 3})
        return "cancel"
    r = _payme_call("PerformTransaction", {"id": tid})
    return "failed" if "error" in r else "success"


def _click_call(view, action, order, extra):
    cfg = pay.click_cfg()
    data = {"click_trans_id": extra["click_trans_id"], "service_id": str(cfg["SERVICE_ID"]),
            "click_paydoc_id": "555", "merchant_trans_id": order.number, "amount": f"{order.total}.0",
            "action": str(action), "error": extra.get("error", "0"), "error_note": "",
            "sign_time": "2026-01-01 12:00:00", **({"merchant_prepare_id": extra["prepare_id"]} if action == 1 else {})}
    data["sign_string"] = make_sign(data, cfg["SECRET_KEY"], action)
    return json.loads(view(RequestFactory().post("/x/", data=data)).content)


def _run_click(order, do):
    ctid = str(uuid.uuid4().int)[:10]
    r = _click_call(prepare_view, 0, order, {"click_trans_id": ctid})
    if r["error"] != 0:
        return "failed"
    r = _click_call(complete_view, 1, order, {"click_trans_id": ctid, "prepare_id": str(r["merchant_prepare_id"]),
                                             "error": "0" if do == "pay" else "-5017"})
    if do == "cancel":
        return "cancel"
    return "success" if r["error"] == 0 else "failed"


@csrf_exempt
def sandbox_view(request, method, number):
    if not pay.local_sandbox():
        raise Http404
    order = Order.objects.filter(number=number, payment_method=method).first()
    if not order or method not in ("payme", "click"):
        raise Http404
    if request.method == "POST":
        do = "pay" if request.POST.get("do") == "pay" else "cancel"
        result = (_run_payme if method == "payme" else _run_click)(order, do)
        return HttpResponseRedirect(f"{pay.return_url(order, request)}&pay={result}")
    return HttpResponse(PAGE.format(name=method.capitalize(), number=escape(order.number), total=order.total))
