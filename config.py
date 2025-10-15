# config.py - إعدادات البوت

import os

# 🔹 بنقرأ التوكن من متغيرات البيئة (أمان أكتر)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or ""

# 🔹 آي دي الأدمن
ADMIN_ID = int(os.getenv("ADMIN_ID", "7378889303"))

# 🔹 رابط API لجلب بيانات الذهب (XAUUSD)
API_URL = "https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X"

# 🔹 فحص المفاتيح المنتهية
CHECK_EXPIRE_HOURS = 1  # كل ساعة

# 🔹 إرسال تنبيه قبل الانتهاء
NOTIFY_BEFORE_HOURS = 6  # قبل الانتهاء بـ 6 ساعات
