from django.urls import include, path
from rest_framework.routers import DefaultRouter, SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import accounts, admin_api, orders, views

router = DefaultRouter()
router.register("categories", views.CategoryViewSet, basename="category")
router.register("products", views.ProductViewSet, basename="product")

# admin panel API (faqat xodimlar): /api/admin/...
admin_router = SimpleRouter()
admin_router.register("orders", admin_api.AdminOrderViewSet, basename="admin-order")
admin_router.register("products", admin_api.AdminProductViewSet, basename="admin-product")
admin_router.register("categories", admin_api.AdminCategoryViewSet, basename="admin-category")
admin_router.register("promos", admin_api.AdminPromoViewSet, basename="admin-promo")
admin_router.register("customers", admin_api.AdminCustomerViewSet, basename="admin-customer")
admin_router.register("payments", admin_api.AdminPaymentViewSet, basename="admin-payment")

urlpatterns = [
    path('pay/', include('shop.payment_urls')),
    path("catalog-meta/", views.catalog_meta),
    path("auth/register/", accounts.RegisterView.as_view()),
    path("auth/login/", accounts.LoginView.as_view()),
    path("auth/refresh/", TokenRefreshView.as_view()),
    path("auth/me/", accounts.MeView.as_view()),
    path("auth/change-password/", accounts.ChangePasswordView.as_view()),
    path("orders/", orders.OrderListCreateView.as_view()),
    path("orders/quote/", orders.OrderQuoteView.as_view()),
    path("orders/seen/", orders.OrderSeenView.as_view()),
    path("orders/<str:number>/", orders.OrderDetailView.as_view()),
    path("admin/dashboard/", admin_api.dashboard),
    path("admin/reports/", admin_api.reports),
    path("admin/", include(admin_router.urls)),
    path("", include(router.urls)),
]