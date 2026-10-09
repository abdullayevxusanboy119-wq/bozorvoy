from django.urls import path

from .click import complete_view, prepare_view
from .payme import payme_view
from .sandbox import sandbox_view

# shop/urls.py ichida:  path("pay/", include("shop.payment_urls")),
urlpatterns = [
    path("payme/", payme_view),
    path("click/prepare/", prepare_view),
    path("click/complete/", complete_view),
    path("sandbox/<str:method>/<str:number>/", sandbox_view),
]
