"""Autentifikatsiya: ro'yxatdan o'tish, kirish (telefon YOKI email), profil, parol almashtirish."""
import re
import uuid

from django.contrib.auth import password_validation
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_phone(value):
    """'+998 90 123 45 67' yoki '901234567' -> '+998901234567'. Noto'g'ri bo'lsa None."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("998"):
        digits = digits[3:]
    if len(digits) != 9:
        return None
    return "+998" + digits


def parse_contact(value):
    """Kontaktni ('email'|'phone', normallashgan qiymat) ko'rinishiga keltiradi."""
    value = (value or "").strip()
    if "@" in value:
        if not EMAIL_RE.match(value):
            raise serializers.ValidationError("Email manzili noto'g'ri")
        return "email", value.lower()
    phone = normalize_phone(value)
    if not phone:
        raise serializers.ValidationError("Telefon raqami noto'g'ri (masalan: +998 90 123 45 67)")
    return "phone", phone


def user_payload(user):
    return {
        "id": user.id,
        "name": user.first_name,
        "email": user.email or "",
        "phone": user.phone or "",
        "is_admin": user.is_staff,
    }


def auth_response(user, http_status=status.HTTP_200_OK):
    refresh = RefreshToken.for_user(user)
    return Response(
        {"user": user_payload(user), "access": str(refresh.access_token), "refresh": str(refresh)},
        status=http_status,
    )


class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    contact = serializers.CharField(help_text="telefon yoki email")
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Ismingizni kiriting")
        return value

    def validate(self, attrs):
        kind, contact = parse_contact(attrs["contact"])
        if User.objects.filter(**{kind: contact}).exists():
            label = "email" if kind == "email" else "telefon"
            raise serializers.ValidationError({"contact": f"Bu {label} allaqachon ro'yxatdan o'tgan. Kirish orqali davom eting."})
        password_validation.validate_password(attrs["password"])
        attrs["kind"], attrs["contact"] = kind, contact
        return attrs

    def create(self, data):
        return User.objects.create_user(
            username="u" + uuid.uuid4().hex[:12],
            password=data["password"],
            first_name=data["name"],
            **{data["kind"]: data["contact"]},
        )


class LoginSerializer(serializers.Serializer):
    contact = serializers.CharField(help_text="telefon yoki email")
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class RegisterView(GenericAPIView):
    """POST /api/auth/register/  {name, contact, password} -> {user, access, refresh}"""
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        return auth_response(s.save(), status.HTTP_201_CREATED)


class LoginView(GenericAPIView):
    """POST /api/auth/login/  {contact, password} -> {user, access, refresh}"""
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            kind, contact = parse_contact(s.validated_data["contact"])
        except serializers.ValidationError:
            kind = contact = None
        user = User.objects.filter(**{kind: contact}).first() if kind else None
        # Bir xil xabar: hisob bor-yo'qligini boshqalarga bildirmaymiz
        if not user or not user.is_active or not user.check_password(s.validated_data["password"]):
            return Response({"detail": "Kontakt yoki parol noto'g'ri"}, status=status.HTTP_400_BAD_REQUEST)
        return auth_response(user)


class ProfileSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    email = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value):
        if not value.strip():
            return None
        if not EMAIL_RE.match(value.strip()):
            raise serializers.ValidationError("Email manzili noto'g'ri")
        return value.strip().lower()

    def validate_phone(self, value):
        if not value.strip():
            return None
        phone = normalize_phone(value)
        if not phone:
            raise serializers.ValidationError("Telefon raqami noto'g'ri")
        return phone

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("Ismni kiriting")
        return value.strip()

    def validate(self, attrs):
        user = self.context["request"].user
        for field in ("email", "phone"):
            v = attrs.get(field)
            if v and User.objects.filter(**{field: v}).exclude(pk=user.pk).exists():
                raise serializers.ValidationError({field: "Bu qiymat boshqa hisobda ishlatilgan"})
        return attrs


class MeView(GenericAPIView):
    """GET /api/auth/me/ — profil.  PATCH — {name, email, phone} ni yangilash."""
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(user_payload(request.user))

    def patch(self, request):
        s = self.get_serializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        user, d = request.user, s.validated_data
        if "name" in d:
            user.first_name = d["name"]
        if "email" in d:
            user.email = d["email"]
        if "phone" in d:
            user.phone = d["phone"]
        if not (user.email or user.phone):
            return Response({"detail": "Email yoki telefondan kamida bittasi bo'lishi kerak"}, status=status.HTTP_400_BAD_REQUEST)
        user.save()
        return Response(user_payload(user))


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})


class ChangePasswordView(GenericAPIView):
    """POST /api/auth/change-password/  {old_password, new_password}"""
    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(s.validated_data["old_password"]):
            return Response({"old_password": "Joriy parol noto'g'ri"}, status=status.HTTP_400_BAD_REQUEST)
        password_validation.validate_password(s.validated_data["new_password"], user)
        user.set_password(s.validated_data["new_password"])
        user.save()
        return Response({"detail": "Parol yangilandi"})
