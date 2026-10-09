from django.core.management.base import BaseCommand

from shop.payments import cancel_stale_unpaid


class Command(BaseCommand):
    help = "To'lanmagan Payme/Click buyurtmalarini bekor qiladi va ombor qoldig'ini qaytaradi."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24, help="shu soatdan eski buyurtmalar (default 24)")
        parser.add_argument("--dry-run", action="store_true", help="hech narsa o'zgartirmasdan ro'yxatni ko'rsatadi")

    def handle(self, *args, hours, dry_run, **opts):
        numbers = cancel_stale_unpaid(hours=hours, dry_run=dry_run)
        verb = "Bekor qilinadi (dry-run)" if dry_run else "Bekor qilindi"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {len(numbers)} ta" + (f" — {', '.join(numbers)}" if numbers else "")))
