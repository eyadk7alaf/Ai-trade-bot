توصيل وتشغيل بوت 'توصيات AI' - إصدار ثابت
----------------------------------------
خطوات سريعة:
1) اضبط متغيرات البيئة في Railway (أنصح بنقل BOT_TOKEN و ADMIN_ID إلى ENV vars):
   - BOT_TOKEN
   - ADMIN_ID
2) أضف repo إلى Railway واختر Start command: python main.py
3) تأكد من وجود runtime.txt (python-3.10.12) وrequirements.txt مثبّتة.
4) تشغيل: Railway سيقوم بعمل Build وتثبيت المتطلبات ثم تشغيل البوت.
