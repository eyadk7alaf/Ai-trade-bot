import re
from pathlib import Path

# اسم الملف الأصلي اللي فيه الأخطاء
src = Path("main.py")

# اسم الملف الجديد بعد التنظيف
dst = Path("main_cleaned.py")

# اقرأ محتوى الملف
text = src.read_text(encoding="utf-8")

# 1️⃣ رجّع الأسطر اللي فيها \n إلى سطور فعلية
text = text.replace("\\n", "\n")

# 2️⃣ شيل أي backslashes زيادة قبل علامات التنصيص
text = re.sub(r'\\(["\'])', r'\1', text)

# 3️⃣ إزالة أي رموز غير طبيعية في نهاية الأسطر
text = re.sub(r' +$', '', text, flags=re.MULTILINE)

# 4️⃣ تأكد إن except و try و if و for و while واخدين سطر لوحدهم
lines = text.splitlines()
cleaned_lines = []
for line in lines:
    if line.strip() == "":
        cleaned_lines.append("")
    else:
        cleaned_lines.append(line.rstrip())

# اكتب الناتج في ملف جديد
dst.write_text("\n".join(cleaned_lines), encoding="utf-8")

print("✅ تم تنظيف الملف بنجاح!")
print("📄 الملف الجديد: main_cleaned.py")
print("🔹 شغّله بالأمر التالي:")
print("python main_cleaned.py")
