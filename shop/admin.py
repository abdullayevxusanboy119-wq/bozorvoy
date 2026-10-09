from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Category, Customer, Order, OrderItem, Payment, Product, PromoCode, User


@admin.register(User)
class BozorUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Aloqa", {"fields": ("phone",)}),)
    list_display = ("username", "first_name", "phone", "email", "is_staff")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "icon", "is_active")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "old_price", "stock", "sold", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name",)


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "percent", "used", "expires", "is_active")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "user", "created_at")
    search_fields = ("name", "phone")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product_name", "price")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "total", "payment_method", "payment_status", "status", "created_at")
    list_filter = ("status", "payment_status", "payment_method")
    search_fields = ("number", "name", "phone")
    inlines = [OrderItemInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("transaction_id", "order", "method", "amount", "status", "created_at")
    list_filter = ("method", "status")
