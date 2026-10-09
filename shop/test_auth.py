from django.core.cache import cache
from django.test import TestCase

from .accounts import normalize_phone
from .models import User

STRONG = "Sinov-parol-482"


class AuthApiTests(TestCase):
    def setUp(self):
        cache.clear()  # throttle hisoblagichini tozalaymiz

    def register(self, **kw):
        data = {"name": "Aziza", "contact": "+998 90 111 22 33", "password": STRONG}
        data.update(kw)
        return self.client.post("/api/auth/register/", data, content_type="application/json")

    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("+998 90 111 22 33"), "+998901112233")
        self.assertEqual(normalize_phone("901112233"), "+998901112233")
        self.assertIsNone(normalize_phone("12345"))

    def test_register_with_phone_returns_tokens(self):
        r = self.register()
        self.assertEqual(r.status_code, 201)
        d = r.json()
        self.assertEqual(d["user"]["phone"], "+998901112233")
        self.assertEqual(d["user"]["name"], "Aziza")
        self.assertFalse(d["user"]["is_admin"])
        self.assertIn("access", d)
        self.assertIn("refresh", d)
        self.assertNotIn("password", d["user"])

    def test_register_with_email_lowercases(self):
        r = self.register(contact="Aziza@Mail.UZ")
        self.assertEqual(r.json()["user"]["email"], "aziza@mail.uz")

    def test_register_duplicate_contact_in_any_format(self):
        self.register()
        self.assertEqual(self.register(contact="901112233").status_code, 400)

    def test_register_validation(self):
        self.assertEqual(self.register(contact="123").status_code, 400)
        self.assertEqual(self.register(contact="bad@mail").status_code, 400)
        self.assertEqual(self.register(password="1234").status_code, 400)
        self.assertEqual(self.register(name="  ").status_code, 400)

    def test_password_is_hashed(self):
        self.register()
        u = User.objects.get(phone="+998901112233")
        self.assertNotEqual(u.password, STRONG)
        self.assertTrue(u.check_password(STRONG))

    def test_login_by_phone_and_email(self):
        self.register()
        self.register(name="B", contact="b@mail.uz")
        for contact in ("+998 90 111 22 33", "901112233", "B@mail.uz"):
            r = self.client.post("/api/auth/login/", {"contact": contact, "password": STRONG}, content_type="application/json")
            self.assertEqual(r.status_code, 200, contact)
            self.assertIn("access", r.json())

    def test_login_failures_share_one_message(self):
        self.register()
        wrong = self.client.post("/api/auth/login/", {"contact": "901112233", "password": "xato"}, content_type="application/json")
        nouser = self.client.post("/api/auth/login/", {"contact": "907776655", "password": STRONG}, content_type="application/json")
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.json(), nouser.json())

    def test_me_requires_token_and_works_with_it(self):
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        access = self.register().json()["access"]
        r = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(r.json()["name"], "Aziza")

    def test_update_profile_and_conflicts(self):
        self.register(contact="b@mail.uz", name="B")
        access = self.register().json()["access"]
        h = {"HTTP_AUTHORIZATION": f"Bearer {access}"}
        ok = self.client.patch("/api/auth/me/", {"name": "Aziza K", "email": "aziza@mail.uz"}, content_type="application/json", **h)
        self.assertEqual(ok.json()["email"], "aziza@mail.uz")
        taken = self.client.patch("/api/auth/me/", {"email": "b@mail.uz"}, content_type="application/json", **h)
        self.assertEqual(taken.status_code, 400)

    def test_cannot_remove_last_contact(self):
        access = self.register().json()["access"]
        r = self.client.patch("/api/auth/me/", {"phone": ""}, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(r.status_code, 400)

    def test_refresh_token(self):
        refresh = self.register().json()["refresh"]
        r = self.client.post("/api/auth/refresh/", {"refresh": refresh}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.json())

    def test_change_password(self):
        access = self.register().json()["access"]
        h = {"HTTP_AUTHORIZATION": f"Bearer {access}"}
        bad = self.client.post("/api/auth/change-password/", {"old_password": "x", "new_password": "Yangi-parol-771"}, content_type="application/json", **h)
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/auth/change-password/", {"old_password": STRONG, "new_password": "Yangi-parol-771"}, content_type="application/json", **h)
        self.assertEqual(ok.status_code, 200)
        login = self.client.post("/api/auth/login/", {"contact": "901112233", "password": "Yangi-parol-771"}, content_type="application/json")
        self.assertEqual(login.status_code, 200)

    def test_login_is_throttled(self):
        for _ in range(20):
            self.client.post("/api/auth/login/", {"contact": "901112233", "password": "x"}, content_type="application/json")
        r = self.client.post("/api/auth/login/", {"contact": "901112233", "password": "x"}, content_type="application/json")
        self.assertEqual(r.status_code, 429)

    def test_staff_flag_exposed(self):
        User.objects.create_user("adm", password=STRONG, email="a@a.uz", is_staff=True, first_name="Admin")
        r = self.client.post("/api/auth/login/", {"contact": "a@a.uz", "password": STRONG}, content_type="application/json")
        self.assertTrue(r.json()["user"]["is_admin"])
