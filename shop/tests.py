from django.test import TestCase

from .models import Category, Product


class CatalogApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.elec = Category.objects.create(name="Elektronika")
        cls.shoes = Category.objects.create(name="Poyabzal")
        cls.hidden = Category.objects.create(name="Yashirin", is_active=False)
        cls.p1 = Product.objects.create(name="Quloqchin", category=cls.elec, price=400, old_price=500, stock=5, sold=10)
        cls.p2 = Product.objects.create(name="Kolonka", category=cls.elec, price=300, stock=5, sold=50)
        cls.p3 = Product.objects.create(name="Krossovka", category=cls.shoes, price=900, stock=0, sold=30)
        cls.p4 = Product.objects.create(name="Off", category=cls.shoes, price=100, is_active=False)
        cls.p5 = Product.objects.create(name="Yashirin tovar", category=cls.hidden, price=100)

    def names(self, url):
        return [p["name"] for p in self.client.get(url).json()["results"]]

    def test_only_visible_products(self):
        self.assertEqual(sorted(self.names("/api/products/")), ["Kolonka", "Krossovka", "Quloqchin"])

    def test_default_sort_is_popular(self):
        self.assertEqual(self.names("/api/products/"), ["Kolonka", "Krossovka", "Quloqchin"])

    def test_sorts(self):
        self.assertEqual(self.names("/api/products/?sort=price-asc"), ["Kolonka", "Quloqchin", "Krossovka"])
        self.assertEqual(self.names("/api/products/?sort=price-desc"), ["Krossovka", "Quloqchin", "Kolonka"])
        self.assertEqual(self.names("/api/products/?sort=new")[0], "Krossovka")

    def test_filters(self):
        self.assertEqual(sorted(self.names(f"/api/products/?category={self.elec.id}")), ["Kolonka", "Quloqchin"])
        self.assertEqual(self.names("/api/products/?on_sale=true"), ["Quloqchin"])
        self.assertEqual(sorted(self.names("/api/products/?max_price=400")), ["Kolonka", "Quloqchin"])
        self.assertEqual(self.names("/api/products/?min_price=800"), ["Krossovka"])

    def test_search_by_name_and_category(self):
        self.assertEqual(self.names("/api/products/?q=kross"), ["Krossovka"])
        self.assertEqual(sorted(self.names("/api/products/?q=elektro")), ["Kolonka", "Quloqchin"])

    def test_pagination_and_page_size(self):
        data = self.client.get("/api/products/?page_size=2").json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["results"]), 2)
        self.assertIsNotNone(data["next"])

    def test_detail_related_and_hidden(self):
        d = self.client.get(f"/api/products/{self.p1.id}/").json()
        self.assertTrue(d["on_sale"])
        self.assertEqual(d["discount_percent"], 20)
        rel = self.client.get(f"/api/products/{self.p1.id}/related/").json()
        self.assertEqual([r["name"] for r in rel], ["Kolonka"])
        self.assertEqual(self.client.get(f"/api/products/{self.p4.id}/").status_code, 404)

    def test_categories_counts(self):
        cats = {c["name"]: c["products_count"] for c in self.client.get("/api/categories/").json()}
        self.assertEqual(cats, {"Elektronika": 2, "Poyabzal": 1})

    def test_catalog_meta(self):
        self.assertEqual(self.client.get("/api/catalog-meta/").json(), {"total": 3, "on_sale": 1, "max_price": 900})
