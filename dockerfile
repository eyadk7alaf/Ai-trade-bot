# استخدام صورة أساسية (Base Image) مناسبة
FROM python:3.10-slim

# تثبيت التبعيات الأساسية المطلوبة لـ psycopg2 (PostgreSQL)
# هذا هو الإجراء الذي كان يفتقده البناء السابق، وهو يضمن عمل PostgreSQL
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# تعيين دليل العمل
WORKDIR /app

# نسخ الملفات
COPY . /app

# تثبيت المكتبات من requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# أمر التشغيل النهائي (يحل محل Procfile في هذا السياق)
CMD ["python", "main.py"]
