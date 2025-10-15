import asyncio
import sqlite3
import time
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import os

# =================== الإعدادات ===================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = 7378889303
DB_PATH = "bot_data.db"

# =================== إعداد اللوج ===================
logging.basicConfig(level=logging.INFO)

# =================== قاعدة البيانات ===================
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            active INTEGER DEFAULT 0,
            expiry INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT UNIQUE,
            duration_days INTEGER,
            used_by INTEGER,
            created_at INTEGER,
            expiry INTEGER
        )
    """)
    conn.commit()
    conn.close()

def add_or_update_user(telegram_id, username=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE users SET username=? WHERE telegram_id=?", (username, telegram_id))
    else:
        cur.execute("INSERT INTO users (telegram_id, username) VALUES (?,?)", (telegram_id, username))
    conn.commit()
    conn.close()

def activate_user_with_key(telegram_id, key_code):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM keys WHERE key_code=?", (key_code,))
    k = cur.fetchone()
    if not k:
        conn.close()
        return False, 'invalid'
    if k['used_by'] is not None:
        conn.close()
        return False, 'used'
    now = int(time.time())
    expiry = now + k['duration_days']*24*3600
    cur.execute("UPDATE keys SET used_by=?, expiry=? WHERE key_code=?", (telegram_id, expiry, key_code))
    cur.execute("UPDATE users SET active=1, expiry=? WHERE telegram_id=?", (expiry, telegram_id))
    conn.commit()
    conn.close()
    return True, expiry

def create_key(key_code, duration_days):
    conn = get_conn()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("INSERT INTO keys (key_code, duration_days, created_at) VALUES (?,?,?)", (key_code, duration_days, now))
    conn.commit()
    conn.close()

def list_keys():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM keys ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_active_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id, username, expiry FROM users WHERE active=1 AND banned=0")
    rows = cur.fetchall()
    conn.close()
    return rows

def ban_user(telegram_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (telegram_id,))
    conn.commit()
    conn.close()

def unban_user(telegram_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=0 WHERE telegram_id=?", (telegram_id,))
    conn.commit()
    conn.close()

# =================== إعداد البوت ===================
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

def format_expiry(ts):
    import datetime
    if not ts:
        return 'غير محدد'
    return datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S UTC')

# =================== أوامر البوت ===================
@dp.message(Command("start"))
async def start(msg: types.Message):
    add_or_update_user(msg.from_user.id, getattr(msg.from_user, 'username', None))
    await msg.answer("👋 أهلاً بك في بوت <b>Black Web 💲</b>\n"
                     "للاشتراك أرسل مفتاح التفعيل 🔑")

@dp.message(Command("admin"))
async def admin_menu(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("❌ غير مسموح بالدخول هنا.")
        return

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    keyboard.add(
        types.KeyboardButton("إنشاء مفتاح 🔑"),
        types.KeyboardButton("عرض المفاتيح 📜")
    )
    keyboard.add(
        types.KeyboardButton("رسالة لكل المستخدمين 📢"),
        types.KeyboardButton("رسالة للمشتركين ✅")
    )
    keyboard.add(
        types.KeyboardButton("حظر مستخدم ❌"),
        types.KeyboardButton("إلغاء حظر مستخدم ✅")
    )
    await msg.reply("📋 قائمة أوامر الأدمن:", reply_markup=keyboard)

# =================== التعامل مع نصوص المستخدم ===================
@dp.message()
async def handle_text(msg: types.Message):
    text = msg.text.strip()

    # =================== أوامر الأدمن ===================
    if msg.from_user.id == ADMIN_ID:
        if text.startswith("/createkey") or text.startswith("إنشاء مفتاح 🔑"):
            await msg.reply("🪄 استخدم الأمر بالشكل التالي:\n/createkey `كود_المفتاح` `المدة_بالأيام`")
            return
        elif text.startswith("/listkeys") or text.startswith("عرض المفاتيح 📜"):
            rows = list_keys()
            if not rows:
                await msg.reply("❌ لا توجد مفاتيح بعد.")
                return
            reply = "📜 <b>قائمة المفاتيح:</b>\n"
            for r in rows:
                used = "✅ مستخدم" if r['used_by'] else "🟢 متاح"
                reply += f"🔑 <code>{r['key_code']}</code> - {r['duration_days']} يوم - {used}\n"
            await msg.reply(reply)
            return
        elif text.startswith("حظر مستخدم ❌") or text.startswith("/ban"):
            await msg.reply("🛑 أرسل أيدي المستخدم لحظره:")
            return
        elif text.startswith("إلغاء حظر مستخدم ✅") or text.startswith("/unban"):
            await msg.reply("✅ أرسل أيدي المستخدم لإلغاء الحظر:")
            return
        elif text.startswith("رسالة لكل المستخدمين 📢") or text.startswith("/msgall"):
            await msg.reply("📢 أرسل الرسالة لتصل لكل المستخدمين:")
            return
        elif text.startswith("رسالة للمشتركين ✅") or text.startswith("/msgsub"):
            await msg.reply("✅ أرسل الرسالة لتصل لكل المشتركين:")
            return

    # =================== تفعيل الاشتراك ===================
    if len(text) > 3:
        ok, info = activate_user_with_key(msg.from_user.id, text)
        if ok:
            await msg.reply(f"✅ تم تفعيل اشتراكك حتى: {format_expiry(info)}")
        else:
            if info == "invalid":
                await msg.reply("❌ المفتاح غير صحيح.")
            elif info == "used":
                await msg.reply("⚠️ المفتاح مستخدم بالفعل.")
            else:
                await msg.reply("حدث خطأ أثناء التفعيل.")
        return

    await msg.reply("❓ أمر غير معروف.")

# =================== تشغيل البوت ===================
async def main():
    init_db()
    print("✅ قاعدة البيانات جاهزة")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
