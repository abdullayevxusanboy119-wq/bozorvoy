"""Payme Merchant API (JSON-RPC) — POST /api/pay/payme/

Metodlar: CheckPerformTransaction, CreateTransaction, PerformTransaction,
CancelTransaction, CheckTransaction, GetStatement.
Summalar TIYINda keladi (1 so'm = 100 tiyin). Payme har doim HTTP 200 kutadi, xato JSON ichida.
"""
import base64
import binascii
import hmac
import json

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import payments as pay
from .models import Order, Payment

TIMEOUT_MS = 12 * 60 * 60 * 1000  # yaratilgan, lekin to'lanmagan tranzaksiya 12 soatdan keyin bekor bo'ladi

MESSAGES = {
    -32504: ("Недостаточно привилегий", "Ruxsat yo'q", "Insufficient privilege"),
    -32300: ("Метод должен быть POST", "Metod POST bo'lishi kerak", "Method must be POST"),
    -32700: ("Ошибка разбора JSON", "JSON noto'g'ri", "JSON parse error"),
    -32600: ("Неверный запрос", "So'rov noto'g'ri", "Invalid request"),
    -32601: ("Метод не найден", "Metod topilmadi", "Method not found"),
    -31001: ("Неверная сумма", "Summa noto'g'ri", "Wrong amount"),
    -31003: ("Транзакция не найдена", "Tranzaksiya topilmadi", "Transaction not found"),
    -31007: ("Заказ выполнен, отмена невозможна", "Buyurtma yetkazilgan, bekor qilib bo'lmaydi",
             "Order already fulfilled, cannot cancel"),
    -31008: ("Невозможно выполнить операцию", "Amalni bajarib bo'lmaydi", "Cannot perform operation"),
    -31050: ("Заказ не найден", "Buyurtma topilmadi", "Order not found"),
    -31051: ("Заказ не может быть оплачен", "Buyurtmani to'lab bo'lmaydi", "Order cannot be paid"),
}


class PaymeError(Exception):
    def __init__(self, code, data=None):
        self.code, self.data = code, data


def _err_body(rpc_id, code, data=None):
    ru, uz, en = MESSAGES[code]
    err = {"code": code, "message": {"ru": ru, "uz": uz, "en": en}}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": err}


def _authorized(request):
    key = pay.payme_cfg()["KEY"]
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not key or not header.startswith("Basic "):
        return False
    try:
        login, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return hmac.compare_digest(login, "Paycom") and hmac.compare_digest(password, key)


# ---------------------------------------------------------------- yordamchilar

def _find_order(params):
    account = params.get("account")
    number = account.get("order_id") if isinstance(account, dict) else None
    order = Order.objects.select_for_update().filter(number=str(number or "")).first()
    if not order:
        raise PaymeError(-31050, "order_id")
    return order


def _check_perform(order, amount):
    if (order.payment_method != Order.Method.PAYME or order.payment_status != Order.PayStatus.PENDING
            or order.status == Order.Status.CANCELLED):
        raise PaymeError(-31051, "order_id")
    if amount != order.total * 100:
        raise PaymeError(-31001)


def _get_payment(params):
    pay_obj = (Payment.objects.select_for_update()
               .filter(method=Order.Method.PAYME, provider_trans_id=str(params.get("id", ""))).first())
    if not pay_obj:
        raise PaymeError(-31003)
    return pay_obj


def _expired(p):
    return p.provider_state == 1 and pay.now_ms() - (p.provider_create_time or 0) > TIMEOUT_MS


def _info(p):
    return {"create_time": p.provider_create_time, "perform_time": p.provider_perform_time,
            "cancel_time": p.provider_cancel_time, "transaction": p.transaction_id,
            "state": p.provider_state, "reason": p.provider_reason}


# ---------------------------------------------------------------- metodlar

def check_perform_transaction(params):
    with transaction.atomic():
        order = _find_order(params)
        _check_perform(order, params.get("amount"))
    return {"allow": True}


def create_transaction(params):
    tid, amount, err = str(params.get("id", "")), params.get("amount"), None
    if not tid or not isinstance(params.get("time"), int):
        raise PaymeError(-32600)
    with transaction.atomic():
        p = (Payment.objects.select_for_update()
             .filter(method=Order.Method.PAYME, provider_trans_id=tid).first())
        if p:  # takroriy so'rov: birinchi javob bilan bir xil bo'lishi kerak
            if _expired(p):
                pay.mark_failed(p, -1, reason=4)
                err = -31008
            elif p.provider_state != 1:
                err = -31008
            else:
                return {"create_time": p.provider_create_time, "transaction": p.transaction_id, "state": 1}
        else:
            order = _find_order(params)
            _check_perform(order, amount)
            for other in Payment.objects.select_for_update().filter(
                    order=order, method=Order.Method.PAYME, provider_state=1):
                if not _expired(other):
                    raise PaymeError(-31008)  # buyurtma uchun boshqa ochiq tranzaksiya bor
                pay.mark_failed(other, -1, reason=4)
            p = order.payments.filter(method=Order.Method.PAYME, provider_trans_id="").first() or \
                Payment(order=order, method=Order.Method.PAYME, amount=order.total)
            p.provider_trans_id, p.provider_state = tid, 1
            p.provider_time, p.provider_create_time = params["time"], pay.now_ms()
            p.status = Order.PayStatus.PENDING
            p.save()
            return {"create_time": p.provider_create_time, "transaction": p.transaction_id, "state": 1}
    raise PaymeError(err)  # atomic'dan CHIQQANDAN keyin: bekor qilish saqlanib qoladi


def perform_transaction(params):
    err = None
    with transaction.atomic():
        p = _get_payment(params)
        if p.provider_state == 1:
            if _expired(p):
                pay.mark_failed(p, -1, reason=4)
                err = -31008
            else:
                pay.mark_paid(p)
        elif p.provider_state != 2:
            err = -31008
        if err is None:
            return {"transaction": p.transaction_id, "perform_time": p.provider_perform_time, "state": 2}
    raise PaymeError(err)


def cancel_transaction(params):
    with transaction.atomic():
        p = _get_payment(params)
        reason = params.get("reason")
        if p.provider_state == 1:
            pay.mark_failed(p, -1, reason=reason)
        elif p.provider_state == 2:
            if not pay.can_refund(p.order):
                raise PaymeError(-31007)
            pay.refund(p, reason=reason)
        return {"transaction": p.transaction_id, "cancel_time": p.provider_cancel_time, "state": p.provider_state}


def check_transaction(params):
    with transaction.atomic():
        return _info(_get_payment(params))


def get_statement(params):
    qs = Payment.objects.filter(method=Order.Method.PAYME, provider_time__gte=params.get("from", 0),
                                provider_time__lte=params.get("to", 0)).select_related("order").order_by("provider_time")
    return {"transactions": [
        {"id": p.provider_trans_id, "time": p.provider_time, "amount": p.amount * 100,
         "account": {"order_id": p.order.number}, **_info(p)} for p in qs]}


METHODS = {
    "CheckPerformTransaction": check_perform_transaction,
    "CreateTransaction": create_transaction,
    "PerformTransaction": perform_transaction,
    "CancelTransaction": cancel_transaction,
    "CheckTransaction": check_transaction,
    "GetStatement": get_statement,
}


@csrf_exempt
def payme_view(request):
    def reply(body):
        return JsonResponse(body, json_dumps_params={"ensure_ascii": False})

    if request.method != "POST":
        return reply(_err_body(None, -32300))
    try:
        body = json.loads(request.body)
        rpc_id, method, params = body.get("id"), body.get("method"), body.get("params")
        assert isinstance(params, dict)
    except (ValueError, AttributeError, AssertionError):
        return reply(_err_body(None, -32700))
    if not _authorized(request):
        return reply(_err_body(rpc_id, -32504))
    handler = METHODS.get(method)
    if not handler:
        return reply(_err_body(rpc_id, -32601))
    try:
        return reply({"jsonrpc": "2.0", "id": rpc_id, "result": handler(params)})
    except PaymeError as e:
        return reply(_err_body(rpc_id, e.code, e.data))
