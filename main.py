import asyncio
import sqlite3
import time
import random
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
import logging
import os

# إعدادات البوت
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # توكن من المتغير البيئي
ADMIN_ID = 7378889303

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# ============= قاعدة البيانات =============
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        username TEXT,
        active_until INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE,
        days INTEGER,
        used INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()

def add_user(telegram_id, username):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)", (telegram_id, username))
    conn.commit()
    conn.close()

def activate_key(telegram_id, key):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM keys WHERE key=? AND used=0", (key,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    days = row[2]
    active_until = int(time.time()) + days * 86400
    cur.execute("UPDATE users SET active_until=? WHERE telegram_id=?", (active_until, telegram_id))
    cur.execute("UPDATE keys SET used=1 WHERE key=?", (key,))
    conn.commit()
    conn.close()
    return True

def create_key(days):
    key = "KEY" + str(random.randint(10000, 99999))
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO keys (key, days) VALUES (?, ?)", (key, days))
    conn.commit()
    conn.close()
    return key

def get_all_users(active_only=False):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    if active_only:
        cur.execute("SELECT telegram_id FROM users WHERE active_until > ?", (int(time.time()),))
    else:
        cur.execute("SELECT telegram_id FROM users")
    users = [r[0] for r in cur.fetchall()]
    conn.close()
    return users

def ban_user(uid, banned=True):
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=? WHERE telegram_id=?", (1 if banned else 0, uid))
    conn.commit()
    conn.close()

def list_keys():
    conn = sqlite3.connect("bot_data.db")
    cur = conn.cursor()
    cur.execute("SELECT key, days, used FROM keys")
    data = cur.fetchall()
    conn.close()
    return data

# ============= أوامر المستخدمين =============
@dp.message(Command("start"))
async def start_msg(msg: types.Message):
    add_user(msg.from_user.id, msg.from_user.username)
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("📊 جدول اليوم"), KeyboardButton("📈 آخر الصفقات"))
    await msg.reply(
        f"👋 أهلاً بك {msg.from_user.first_name or ''} في <b>AlphaTradeAI</b> 🤖\n\n"
        "📊 روبوت توصيات ذكي يعتمد على تحليل الأسواق العالمية بدقة عالية.\n"
        "🔐 لتفعيل اشتراكك، أرسل مفتاح التفعيل الذي حصلت عليه.\n\n"
        "🚀 استمتع بتجربة تداول احترافية مع نسبة نجاح مميزة!",
        reply_markup=keyboard
    )

# ============= لوحة الأدمن =============
@dp.message(Command("admin"))
async def admin_panel(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("🔑 إنشاء مفتاح جديد"),
        KeyboardButton("🧾 عرض المفاتيح")
    )
    kb.add(
        KeyboardButton("⛔ حظر مستخدم"),
        KeyboardButton("✅ إلغاء حظر مستخدم")
    )
    kb.add(
        KeyboardButton("📢 رسالة لكل المستخدمين"),
        KeyboardButton("💬 رسالة للمشتركين فقط")
    )
    kb.add(
        KeyboardButton("💹 إرسال صفقة يدوياً")
    )

    await msg.reply("📋 لوحة تحكم الأدمن:\nاختر العملية التي تريد تنفيذها 👇", reply_markup=kb)

# ============= تنفيذ أوامر الأدمن =============
@dp.message()
async def admin_actions(msg: types.Message):
    text = msg.text.strip()
    uid = msg.from_user.id

    # تجاهل غير الأدمن
    if uid != ADMIN_ID:
        if len(text) > 3 and " " not in text:
            ok = activate_key(uid, text)
            if ok:
                await msg.reply("✅ تم تفعيل اشتراكك بنجاح!")
            else:
                await msg.reply("❌ المفتاح غير صحيح أو مستخدم بالفعل.")
        else:
            await msg.reply("❓ أمر غير معروف. أرسل /start للبدء.")
        return

    # -------- إنشاء مفتاح جديد --------
    if text.startswith("🔑 إنشاء مفتاح"):
        await msg.reply("⏱️ أرسل عدد الأيام مثل: 30")
        dp.create_key_wait = True
        return

    if hasattr(dp, "create_key_wait") and dp.create_key_wait:
        days = int(text)
        new_key = create_key(days)
        await msg.reply(f"✅ تم إنشاء المفتاح:\n<code>{new_key}</code>\n⏳ صالح لمدة {days} يوم.")
        del dp.create_key_wait
        return

    # -------- عرض المفاتيح --------
    if text == "🧾 عرض المفاتيح":
        keys = list_keys()
        if not keys:
            await msg.reply("⚠️ لا توجد مفاتيح حالياً.")
        else:
            reply = "🔑 <b>قائمة المفاتيح:</b>\n\n"
            for k, d, u in keys:
                status = "✅ نشط" if not u else "❌ مستخدم"
                reply += f"<code>{k}</code> - {d} يوم - {status}\n"
            await msg.reply(reply)
        return

    # -------- إرسال صفقة --------
    if text == "💹 إرسال صفقة يدوياً":
        await msg.reply("📩 أرسل الصفقة بهذا الشكل:\n\n"
                        "<b>زوج:</b> EUR/USD\n<b>نوع:</b> Buy\n<b>Enter:</b> 1.0650\n"
                        "<b>TP:</b> 1.0690\n<b>SL:</b> 1.0610\n<b>نسبة النجاح:</b> 87%")
        dp.wait_trade = True
        return

    if hasattr(dp, "wait_trade") and dp.wait_trade:
        for user in get_all_users(active_only=True):
            try:
                await bot.send_message(user, f"📢 <b>صفقة جديدة</b> 🚀\n\n{text}")
            except:
                pass
        await msg.reply("✅ تم إرسال الصفقة بنجاح للمشتركين.")
        del dp.wait_trade
        return

    # -------- بث جماعي --------
    if text == "📢 رسالة لكل المستخدمين":
        await msg.reply("📝 أرسل الرسالة الآن:")
        dp.wait_all_broadcast = True
        return

    if hasattr(dp, "wait_all_broadcast") and dp.wait_all_broadcast:
        for user in get_all_users():
            try:
                await bot.send_message(user, text)
            except:
                pass
        await msg.reply("✅ تم إرسال الرسالة للجميع.")
        del dp.wait_all_broadcast
        return

    # -------- رسالة للمشتركين --------
    if text == "💬 رسالة للمشتركين فقط":
        await msg.reply("📝 أرسل الرسالة الآن:")
        dp.wait_sub_broadcast = True
        return

    if hasattr(dp, "wait_sub_broadcast") and dp.wait_sub_broadcast:
        for user in get_all_users(active_only=True):
            try:
                await bot.send_message(user, text)
            except:
                pass
        await msg.reply("✅ تم إرسال الرسالة للمشتركين فقط.")
        del dp.wait_sub_broadcast
        return

    await msg.reply("❓ أمر غير معروف.")

# ============= التشغيل =============
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("✅ قاعدة البيانات جاهزة.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
