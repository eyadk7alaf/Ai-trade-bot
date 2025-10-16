import os
import sqlite3
import logging
import time
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor

# ================= إعداد =================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = 7378889303  # رقم الأدمن

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ================= قاعدة البيانات =================
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        active_until REAL DEFAULT 0,
        banned INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS keys (
        key TEXT PRIMARY KEY,
        expires_in INTEGER,
        used INTEGER DEFAULT 0,
        user_id INTEGER
    )""")
    conn.commit()
    conn.close()

def add_or_update_user(user_id, username):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?,?)", (user_id, username))
    conn.commit()
    conn.close()

def create_key(key, days):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO keys (key, expires_in, used) VALUES (?, ?, 0)", (key, days))
    conn.commit()
    conn.close()

def activate_user_with_key(user_id, key):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM keys WHERE key=? AND used=0", (key,))
    data = cur.fetchone()
    if not data:
        conn.close()
        return False, "invalid"
    days = data[1]
    expiry = time.time() + days * 86400
    cur.execute("UPDATE users SET active_until=? WHERE telegram_id=?", (expiry, user_id))
    cur.execute("UPDATE keys SET used=1, user_id=? WHERE key=?", (user_id, key))
    conn.commit()
    conn.close()
    return True, expiry

def get_all_users(active_only=False):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    if active_only:
        cur.execute("SELECT telegram_id FROM users WHERE active_until > ? AND banned=0", (time.time(),))
    else:
        cur.execute("SELECT telegram_id FROM users WHERE banned=0")
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users

def ban_user(user_id, banned=True):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=? WHERE telegram_id=?", (1 if banned else 0, user_id))
    conn.commit()
    conn.close()

# ================= الكيبوردات =================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📊 جدول اليوم"), KeyboardButton("📈 آخر الصفقات"))
    kb.row(KeyboardButton("🎟️ تفعيل مفتاح الاشتراك"))
    kb.row(KeyboardButton("ℹ️ معلومات عن البوت"))
    return kb

def admin_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("إنشاء مفتاح جديد 🔑"), KeyboardButton("عرض المفاتيح 🧾"))
    kb.row(KeyboardButton("حظر مستخدم ❌"), KeyboardButton("إلغاء حظر مستخدم ✅"))
    kb.row(KeyboardButton("رسالة لكل المستخدمين 📢"), KeyboardButton("رسالة للمشتركين ✅"))
    kb.row(KeyboardButton("إرسال صفقة يدوياً 💹"), KeyboardButton("رجوع ↩️"))
    return kb

# ================= الأوامر =================
@dp.message_handler(commands=["start"])
async def start_cmd(msg: types.Message):
    user_id = msg.from_user.id
    add_or_update_user(user_id, msg.from_user.username)
    text = (
        f"👋 أهلاً بك {msg.from_user.first_name or 'عزيزي'} في بوت AlphaTradeAI 💹\n\n"
        "🤖 هذا البوت يقدم إشارات تداول عالية الدقة اعتمادًا على تحليل السوق بالذكاء الصناعي.\n\n"
        "🎯 استخدم الأزرار بالأسفل للتفاعل مع البوت."
    )
    await msg.answer(text, reply_markup=main_menu_kb())

@dp.message_handler(commands=["admin"])
async def admin_cmd(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return await msg.reply("❌ غير مصرح لك بالدخول.")
    await msg.reply("⚙️ مرحبًا بك في لوحة تحكم الأدمن:", reply_markup=admin_menu_kb())

# ================= معالجة الأزرار =================
@dp.message_handler(lambda msg: msg.text)
async def handle_text(msg: types.Message):
    user_id = msg.from_user.id
    text = msg.text.strip()

    if user_id == ADMIN_ID:
        # === قائمة الأدمن ===
        if text == "رجوع ↩️":
            await msg.reply("✅ تم الرجوع للقائمة الرئيسية.", reply_markup=main_menu_kb())
            return
        elif text == "إنشاء مفتاح جديد 🔑":
            await msg.reply("🧩 أرسل المفتاح وعدد الأيام بهذا الشكل:\n\n`KEY123 7`")
            return
        elif " " in text and text.split()[0].isalnum():
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                create_key(parts[0], int(parts[1]))
                await msg.reply(f"✅ تم إنشاء المفتاح `{parts[0]}` لمدة {parts[1]} يومًا.")
                return
        elif text == "عرض المفاتيح 🧾":
            await msg.reply("📋 سيتم قريبًا عرض المفاتيح المسجلة في قاعدة البيانات.")
            return
        elif text == "حظر مستخدم ❌":
            await msg.reply("📛 أرسل رقم المستخدم لحظره.")
            return
        elif text == "إلغاء حظر مستخدم ✅":
            await msg.reply("✅ أرسل رقم المستخدم لإلغاء حظره.")
            return
        elif text == "رسالة لكل المستخدمين 📢":
            await msg.reply("📝 أرسل الرسالة ليتم إرسالها لجميع المستخدمين.")
            return
        elif text == "رسالة للمشتركين ✅":
            await msg.reply("📨 أرسل الرسالة ليتم إرسالها فقط للمشتركين.")
            return
        elif text == "إرسال صفقة يدوياً 💹":
            await msg.reply("💹 أرسل الصفقة بهذا الشكل:\n\nEUR/USD BUY\nEnter: 1.0780\nSL: 1.0745\nTP: 1.0850\nTrust: 90%")
            return

    # === تفعيل المفتاح ===
    if len(text) > 3 and " " not in text:
        ok, info = activate_user_with_key(user_id, text)
        if ok:
            await msg.reply(f"✅ تم تفعيل اشتراكك حتى {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(info))}")
        else:
            await msg.reply("❌ المفتاح غير صحيح أو مستخدم مسبقاً.")
        return

    await msg.reply("❓ أمر غير معروف.")

# ================= تشغيل البوت =================
if __name__ == "__main__":
    init_db()
    print("✅ قاعدة البيانات جاهزة")
    executor.start_polling(dp, skip_updates=True)
