# Bozorvoy - onlayn do'kon

Django va Django REST Framework asosidagi onlayn do'kon: mijozlar uchun vitrina va boshqaruv paneli (admin).

## Imkoniyatlar

- Mahsulotlar katalogi: kategoriyalar, qidiruv, filtr, rang va o'lcham tanlash
- Ro'yxatdan o'tish va kirish (JWT)
- Savat, buyurtma berish, buyurtmalar tarixi
- Payme va Click to'lov integratsiyasi (webhooklar, lokal sandbox rejimi)
- Admin panel: buyurtmalar, mahsulotlar, kategoriyalar, mijozlar, to'lovlar, promokodlar
- Dashboard va hisobotlar (oylik daromad, eng ko'p sotilgan mahsulotlar)
- 93 ta avtomatik test

## Texnologiyalar

Python, Django, Django REST Framework, SimpleJWT, PostgreSQL / SQLite, WhiteNoise, Gunicorn.
Frontend: bitta HTML fayl (vanilla JS), Django o'zi beradi.

## Lokal ishga tushirish

    git clone https://github.com/abdullayevxusanboy119-wq/bozorvoy.git
    cd bozorvoy
    pip install -r requirements.txt
    copy .env.example .env
    python manage.py migrate
    python manage.py createsuperuser
    python manage.py runserver

`.env` faylida `DJANGO_DEBUG=True` qo'ying. Keyin brauzerda http://127.0.0.1:8000/ ni oching.

Testlarni ishga tushirish:

    python manage.py test

## Eslatma

Payme va Click lokal sandbox rejimida ishlaydi (haqiqiy merchant hisobi ulanmagan).