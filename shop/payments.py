"""Payme va Click uchun umumiy qismlar: sozlamalar, to'lov havolalari, tranzaksiya holatlari.

Sozlamalar config/settings.py oxiriga qo'shiladi (README pastda / chatdagi izoh).
Kalitlar bo'sh bo'lsa webhook'lar hamma so'rovni rad etadi (xavfsiz default).
"""
import base64
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from .models import Order, Payment


def payme_cfg():
    return {"MERCHANT_ID": "", "KEY": "", "CHECKOUT_URL": "https://checkout.paycom.uz",
            **getattr(settings, "PAYME", {})}


def click_cfg():
    return {"SERVICE_ID": "", "MERCHANT_ID": "", "SECRET_KEY": "", **getattr(settings, "CLICK", {})}


def local_sandbox():
    """True bo'lsa to'lov havolasi haqiqiy Payme/Click'ga emas, o'zimizning test sahifamizga olib boradi."""
    return bool(getattr(settings, "PAYMENT_LOCAL_SANDBOX", False))


def now_ms():
    return int(timezone.now().timestamp() * 1000)


def return_url(order, request=None):
    base = getattr(settings, "PAYMENT_RETURN_URL", "") or (
        request.build_absolute_uri("/") if request is not None else "http://127.0.0.1:8000/")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}pay_order={order.number}"


def is_payable(order):
    """Onlayn to'lov havolasi faqat to'lanmagan Payme/Click buyurtmasiga beriladi."""
    return (order.payment_method in (Order.Method.PAYME, Order.Method.CLICK)
            and order.payment_status == Order.PayStatus.PENDING
            and order.status != Order.Status.CANCELLED)


def payme_link(order, request=None):
    cfg = payme_cfg()
    params = (f"m={cfg['MERCHANT_ID']};ac.order_id={order.number};a={order.total * 100};"
              f"l=uz;c={return_url(order, request)}")
    return f"{cfg['CHECKOUT_URL'].rstrip('/')}/{base64.b64encode(params.encode()).decode()}"


def click_link(order, request=None):
    cfg = click_cfg()
    return (f"https://my.click.uz/services/pay?service_id={cfg['SERVICE_ID']}&merchant_id={cfg['MERCHANT_ID']}"
            f"&amount={order.total}.00&transaction_param={order.number}"
            f"&return_url={quote(return_url(order, request), safe='')}")


def payment_url(order, request=None):
    if not is_payable(order):
        return ""
    if local_sandbox():
        return f"/api/pay/sandbox/{order.payment_method}/{order.number}/"
    if order.payment_method == Order.Method.PAYME:
        return payme_link(order, request) if payme_cfg()["MERCHANT_ID"] else ""
    return click_link(order, request) if click_cfg()["SERVICE_ID"] else ""


# ---------------------------------------------------------------- holat o'zgarishlari
# Hammasi chaqiruvchi `transaction.atomic()` ichida bo'lishi kerak.

def mark_paid(payment, perform_time=None):
    payment.status = Order.PayStatus.PAID
    payment.provider_state = 2
    payment.provider_perform_time = perform_time or now_ms()
    payment.save()
    Order.objects.filter(pk=payment.order_id).update(payment_status=Order.PayStatus.PAID)


def mark_failed(payment, state=-1, reason=None, cancel_time=None):
    """To'lovdan OLDIN bekor/xato: buyurtma o'zi ochiq qoladi (mijoz qayta to'lashi mumkin)."""
    payment.status = Order.PayStatus.FAILED
    payment.provider_state = state
    payment.provider_reason = reason
    payment.provider_cancel_time = cancel_time or now_ms()
    payment.save()


def can_refund(order):
    """Yetkazilayotgan/yakunlangan buyurtma to'lovini avtomatik qaytarib bo'lmaydi."""
    return order.status in (Order.Status.NEW, Order.Status.PROCESSING, Order.Status.CANCELLED)


def refund(payment, reason=None):
    """To'lovdan KEYIN bekor (Payme state -2): buyurtma bekor qilinadi, ombor qaytadi."""
    from .admin_api import _restore_stock
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    if order.status != Order.Status.CANCELLED:
        _restore_stock(order)
        order.status = Order.Status.CANCELLED
        order.status_seen = False
    order.payment_status = Order.PayStatus.FAILED
    order.save()
    mark_failed(payment, state=-2, reason=reason)


def cancel_stale_unpaid(hours=24, dry_run=False, now=None):
    """To'lanmagan Payme/Click buyurtmalarini (yangi holatda, `hours` soatdan eski) bekor qiladi:
    ombor va promo qaytadi, ochiq to'lov urinishlari yopiladi. Naqd buyurtmalarga tegmaydi.
    Bekor qilingan buyurtma raqamlari ro'yxatini qaytaradi."""
    from datetime import timedelta

    from django.db import transaction

    from .admin_api import _restore_stock

    limit = (now or timezone.now()) - timedelta(hours=hours)
    candidates = Order.objects.filter(
        payment_method__in=(Order.Method.PAYME, Order.Method.CLICK),
        payment_status=Order.PayStatus.PENDING, status=Order.Status.NEW, created_at__lt=limit,
    ).values_list("pk", flat=True)
    cancelled = []
    for pk in list(candidates):
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=pk)
            # qayta tekshiramiz: shu orada to'langan/o'zgargan bo'lishi mumkin
            if (order.payment_status != Order.PayStatus.PENDING or order.status != Order.Status.NEW
                    or order.payments.filter(provider_state=2).exists()):
                continue
            cancelled.append(order.number)
            if dry_run:
                continue
            _restore_stock(order)
            order.status = Order.Status.CANCELLED
            order.payment_status = Order.PayStatus.FAILED
            order.status_seen = False
            order.save()
            for p in order.payments.select_for_update().exclude(provider_state__in=(2, -1, -2)):
                mark_failed(p, -1, reason=4)  # 4 = Payme'da "vaqt tugadi"
    return cancelled
