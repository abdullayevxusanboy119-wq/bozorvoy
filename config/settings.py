"""
Django settings for config project (Bozorvoy).
Sozlamalar muhit o'zgaruvchilaridan (.env) o'qiladi.
"""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# utf-8-sig: Windows (PowerShell) yozgan .env dagi BOM belgisini ham to'g'ri o'qiydi
load_dotenv(BASE_DIR / '.env', encoding='utf-8-sig')


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name):
    return [x.strip() for x in os.environ.get(name, '').split(',') if x.strip()]


# ---------------------------------------------------------------- Asosiy
DEBUG = env_bool('DJANGO_DEBUG', False)

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-faqat-lokal-ishlab-chiqish-uchun'
    else:
        raise RuntimeError('DJANGO_SECRET_KEY o\'rnatilmagan (production uchun majburiy).')

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS')
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')

# Render domenni o'zi beradi (RENDER_EXTERNAL_HOSTNAME), uni avtomatik qo'shamiz
RENDER_HOST = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if RENDER_HOST:
    ALLOWED_HOSTS.append(RENDER_HOST)
    CSRF_TRUSTED_ORIGINS.append(f'https://{RENDER_HOST}')

if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ['127.0.0.1', 'localhost']


# ---------------------------------------------------------------- Ilovalar
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'shop',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',  # eng birinchi bo'lishi shart
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # SecurityMiddleware'dan keyin
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# ---------------------------------------------------------------- Baza
# DATABASE_URL bo'sh bo'lsa, lokalda SQLite ishlayveradi.
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
    )
}


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ---------------------------------------------------------------- Til va vaqt
LANGUAGE_CODE = 'uz'
TIME_ZONE = 'Asia/Tashkent'
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------- Statik va media
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# ---------------------------------------------------------------- Email
# Lokalda xatlar konsolga yoziladi, production'da SMTP backend (ilova hozircha email yubormaydi).
MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend' if DEBUG
                   else 'django.core.mail.backends.smtp.EmailBackend',
    },
}

AUTH_USER_MODEL = 'shop.User'


# ---------------------------------------------------------------- DRF va JWT
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 12,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_THROTTLE_RATES': {'auth': '20/min', 'order': '30/hour', 'quote': '300/hour'},  # parol tanlab urinishlarga qarshi
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}


# ---------------------------------------------------------------- CORS
# Frontend Django'ning o'zidan beriladi (same-origin), shuning uchun production'da shart emas.
# Lokalda boshqa portdan (Live Server 5500) sinash uchun qoldirilgan.
CORS_ALLOWED_ORIGINS = [
    'http://localhost:5500',
    'http://127.0.0.1:5500',
] + env_list('CORS_ALLOWED_ORIGINS')


# ---------------------------------------------------------------- Payme / Click (to'lov)
# Haqiqiy kalitlar faqat muhit o'zgaruvchilaridan olinadi (kodga yozmang, GitHub'ga yuklamang).
# PAYMENT_LOCAL_SANDBOX=True/False bilan majburan boshqarish mumkin.
# Berilmasa: DEBUG rejimida va kalitlar yo'q bo'lsa, lokal sandbox yoqiladi.
if 'PAYMENT_LOCAL_SANDBOX' in os.environ:
    PAYMENT_LOCAL_SANDBOX = env_bool('PAYMENT_LOCAL_SANDBOX')
else:
    PAYMENT_LOCAL_SANDBOX = DEBUG and not os.getenv('PAYME_MERCHANT_ID') and not os.getenv('CLICK_SERVICE_ID')

PAYMENT_RETURN_URL = os.getenv('PAYMENT_RETURN_URL', '')  # bo'sh bo'lsa so'rov manzilidan olinadi

PAYME = {
    'MERCHANT_ID': os.getenv('PAYME_MERCHANT_ID', ''),
    'KEY': os.getenv('PAYME_KEY', 'dev-payme-key' if PAYMENT_LOCAL_SANDBOX else ''),
    # sinov kabineti uchun "https://test.paycom.uz", haqiqiy uchun "https://checkout.paycom.uz"
    'CHECKOUT_URL': os.getenv('PAYME_CHECKOUT_URL', 'https://checkout.paycom.uz'),
}
CLICK = {
    'SERVICE_ID': os.getenv('CLICK_SERVICE_ID', '1' if PAYMENT_LOCAL_SANDBOX else ''),
    'MERCHANT_ID': os.getenv('CLICK_MERCHANT_ID', ''),
    'SECRET_KEY': os.getenv('CLICK_SECRET_KEY', 'dev-click-secret' if PAYMENT_LOCAL_SANDBOX else ''),
}


# ---------------------------------------------------------------- Production xavfsizligi
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', 3600))  # barqaror ishlagach 31536000 ga oshiring