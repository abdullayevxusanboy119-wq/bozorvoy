"""Click Shop API — POST /api/pay/click/prepare/ va /api/pay/click/complete/

Click ikki bosqichda so'raydi: Prepare (action=0) — buyurtma va summani tekshiring,
Complete (action=1) — pul yechildi, buyurtmani to'langan deb belgilang.
So'rov form-urlencoded keladi, javob JSON. Summa so'mda.
Imzo: md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id [+ merchant_prepare_id] + amount + action + sign_time)
"""
import hashlib
import hmac
import json

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import payments as pay
from .models import Order, Payment

OK, E_SIGN, E_AMOUNT, E_ACTION, E_PAID, E_NOT_FOUND, E_NO_TRANS, E_REQUEST, E_CANCELLED = 0, -1, -2, -3, -4, -5, -6, -8, -9
NOTES = {OK: "Success", E_SIGN: "SIGN CHECK FAILED!", E_AMOUNT: "Incorrect parameter amount", E_ACTION: "Action not found",
         E_PAID: "Already paid", E_NOT_FOUND: "Order not found", E_NO_TRANS: "Transaction does not exist",
         E_REQUEST: "Error in request from click", E_CANCELLED: "Transaction cancelled"}

FIELDS = ("click_trans_id", "service_id", "click_paydoc_id", "merchant_trans_id", "merchant_prepare_id",
          "amount", "action", "error", "error_note", "sign_time", "sign_string")


def make_sign(data, secret, action):
    parts = [data["click_trans_id"], data["service_id"], secret, data["merchant_trans_id"]]
    if action == 1:
        parts.append(data["merchant_prepare_id"])
    parts += [data["amount"], data["action"], data["sign_time"]]
    return hashlib.md5("".join(parts).encode()).hexdigest()


def _params(request):
    if request.content_type == "application/json":
        try:
            raw = json.loads(request.body)
        except ValueError:
            raw = {}
    else:
        raw = request.POST
    return {k: str(raw.get(k, "")) for k in FIELDS}


def _reply(data, code, extra=None):
    body = {"click_trans_id": data.get("click_trans_id"), "merchant_trans_id": data.get("merchant_trans_id"),
            **(extra or {}), "error": code, "error_note": NOTES[code]}
    return JsonResponse(body)


def _verify(data, action):
    """Xato kodini qaytaradi yoki None (hammasi joyida)."""
    cfg = pay.click_cfg()
    if not cfg["SECRET_KEY"] or not data["click_trans_id"] or not data["merchant_trans_id"]:
        return E_REQUEST if cfg["SECRET_KEY"] else E_SIGN
    if data["service_id"] != str(cfg["SERVICE_ID"]):
        return E_REQUEST
    if action == 1 and not data["merchant_prepare_id"]:
        return E_REQUEST
    if not hmac.compare_digest(make_sign(data, cfg["SECRET_KEY"], action), data["sign_string"].lower()):
        return E_SIGN
    if data["action"] != str(action):
        return E_ACTION
    try:
        float(data["amount"])
    except ValueError:
        return E_REQUEST
    return None


def _amount_ok(data, order):
    return abs(float(data["amount"]) - order.total) < 0.01


@csrf_exempt
def prepare_view(request):
    if request.method != "POST":
        return JsonResponse({"error": E_REQUEST, "error_note": NOTES[E_REQUEST]})
    data = _params(request)
    bad = _verify(data, 0)
    if bad is not None:
        return _reply(data, bad)
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(number=data["merchant_trans_id"]).first()
        if not order or order.payment_method != Order.Method.CLICK:
            return _reply(data, E_NOT_FOUND)
        if order.payment_status == Order.PayStatus.PAID:
            return _reply(data, E_PAID)
        if order.status == Order.Status.CANCELLED:
            return _reply(data, E_CANCELLED)
        if not _amount_ok(data, order):
            return _reply(data, E_AMOUNT)
        p = order.payments.select_for_update().filter(method=Order.Method.CLICK).exclude(provider_state=2).first() or \
            Payment(order=order, method=Order.Method.CLICK, amount=order.total)
        p.provider_trans_id, p.provider_state = data["click_trans_id"], 1  # oldingi tugatilmagan urinish ustiga yoziladi
        p.provider_time = p.provider_create_time = pay.now_ms()
        p.status = Order.PayStatus.PENDING
        p.save()
    return _reply(data, OK, {"merchant_prepare_id": p.pk})


@csrf_exempt
def complete_view(request):
    if request.method != "POST":
        return JsonResponse({"error": E_REQUEST, "error_note": NOTES[E_REQUEST]})
    data = _params(request)
    bad = _verify(data, 1)
    if bad is not None:
        return _reply(data, bad)
    with transaction.atomic():
        try:
            p = Payment.objects.select_for_update().select_related("order").get(
                pk=int(data["merchant_prepare_id"]), method=Order.Method.CLICK)
        except (ValueError, Payment.DoesNotExist):
            return _reply(data, E_NO_TRANS)
        order = p.order
        if p.provider_trans_id != data["click_trans_id"] or order.number != data["merchant_trans_id"]:
            return _reply(data, E_NO_TRANS)
        if p.provider_state == 2 or order.payment_status == Order.PayStatus.PAID:
            return _reply(data, E_PAID)
        if p.provider_state == -1 or order.status == Order.Status.CANCELLED:
            return _reply(data, E_CANCELLED)
        if not _amount_ok(data, order):
            return _reply(data, E_AMOUNT)
        if data["error"] not in ("", "0"):  # Click to'lovni amalga oshira olmadi (karta xatosi, foydalanuvchi bekor qildi...)
            pay.mark_failed(p, -1)
            return _reply(data, E_CANCELLED)
        pay.mark_paid(p)
    return _reply(data, OK, {"merchant_confirm_id": p.pk})
