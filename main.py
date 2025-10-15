import logging
import sqlite3
import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import time

# ===== إعداد البوت =====
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7378889303"))

if not TOKEN:
    raise ValueError("❌ متغير البيئة TELEGRAM_BOT_TOKEN غير موجود!")

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# ===== قاعدة البيانات =====
DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # جدول المستخدمين
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        active INTEGER DEFAULT 0,
        expiry INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0
    )''')
    # جدول المفاتيح
    c.execute('''CREATE TABLE IF NOT EXISTS keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_code TEXT UNIQUE,
        duration_days INTEGER,
        used_by INTEGER,
        created_at INTEGER,
        expiry INTEGER
    )''')
    conn.commit()
    conn.close()

# ===== دوال قاعدة البيانات =====
def add_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def activate_user_with_key(user_id, key_code):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM keys WHERE key_code=?", (key_code,))
    k = c.fetchone()
    if not k:
        conn.close()
        return False, 'invalid'
    if k[3]:  # used_by
        conn.close()
        return False, 'used'
    now = int(time.time())
    duration = k[2]  # duration_days
    expiry = now + duration*24*3600
    c.execute("UPDATE keys SET used_by=?, expiry=? WHERE key_code=?", (user_id, expiry, key_code))
    c.execute("UPDATE users SET active=1, expiry=?, banned=0 WHERE user_id=?", (expiry, user_id))
    conn.commit()
    conn.close()
    return True, expiry

def create_key(key_code, duration_days):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = int(time.time())
    c.execute("INSERT OR IGNORE INTO keys (key_code, duration_days, created_at) VALUES (?,?,?)", 
              (key_code, duration_days, now))
    conn.commit()
    conn.close()

def list_keys():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM keys ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def get_active_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, active, expiry, banned FROM users")
    rows = c.fetchall()
    conn.close()
    return rows

def ban_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET banned=1, active=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET banned=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

# ===== دالة لتأمين النصوص من HTML =====
def escape_html(text: str) -> str:
    if not text:
        return ""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))

# ===== رسالة الترحيب =====
@dp.message(Command("start"))
async def start_cmd(message: Message):
    user_id = message.from_user.id
    add_user(user_id)
    welcome_text = (
        f"👋 أهلاً {escape_html(message.from_user.first_name)}!\n"
        f"🤖 أنا بوت توصيات التداول الذكي.\n\n"
        f"🔑 لو معاك مفتاح تفعيل، ابعته هنا علشان تبدأ.\n"
        f"💬 لأي استفسار، ابعت رسالتك وهساعدك فوراً."
    )
    await message.answer(welcome_text)

# ===== أوامر الأدمن - قائمة ذكية =====
@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.answer("🚫 أنت لست الأدمن.")
        return

    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🔑 إنشاء مفتاح جديد"), types.KeyboardButton(text="🗝️ عرض المفاتيح")],
            [types.KeyboardButton(text="📋 عرض المستخدمين"), types.KeyboardButton(text="🚫 حظر/فك حظر مستخدم")]
        ],
        resize_keyboard=True
    )
    await message.answer("👑 مرحباً أدمن! هذه قائمة الأوامر المتاحة:", reply_markup=keyboard)

# ===== التعامل مع رسائل الأدمن =====
@dp.message()
async def handle_admin_panel(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    if user_id != ADMIN_ID:
        # مستخدم عادي يرسل مفتاح
        if len(text) > 3:
            ok, info = activate_user_with_key(user_id, text)
            if ok:
                await message.reply(f"✅ تم تفعيل اشتراكك حتى: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(info))}")
            else:
                if info == 'invalid':
                    await message.reply("❌ المفتاح غير صحيح.")
                elif info == 'used':
                    await message.reply("⚠️ المفتاح مستخدم بالفعل.")
            return
        await message.reply("❓ أمر غير معروف.")
        return

    # ==== أوامر الأدمن ====
    if text == "🔑 إنشاء مفتاح جديد":
        await message.reply("✍️ ابعت المفتاح الجديد وعدد الأيام مفصول بمسافة مثال:\n`MYKEY123 7`", parse_mode="Markdown")

    elif " " in text and text.split()[1].isdigit():
        parts = text.split()
        k, dur = parts[0], int(parts[1])
        create_key(k, dur)
        await message.reply(f"✅ تم إنشاء المفتاح `{k}` لمدة {dur} يوم.", parse_mode="Markdown")

    elif text == "🗝️ عرض المفاتيح":
        keys = list_keys()
        if not keys:
            await message.reply("🚫 لا توجد مفاتيح حتى الآن.")
            return
        msg = "🗝️ <b>المفاتيح النشطة:</b>\n\n"
        for k in keys:
            used = k[3] if k[3] else "متاح"
            expiry = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(k[5])) if k[5] else "غير محدد"
            msg += f"{k[1]} - {k[2]} يوم - مستخدم: {used} - انتهاء: {expiry}\n"
        await message.reply(msg)

    elif text == "📋 عرض المستخدمين":
        users = get_active_users()
        if not users:
            await message.reply("🚫 لا يوجد مستخدمين.")
            return
        msg = "👥 <b>المستخدمين:</b>\n\n"
        for u in users:
            status = "✅ مفعل" if u[1] and not u[4] else "❌ غير مفعل/محظور"
            expiry = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(u[2])) if u[2] else "غير محدد"
            msg += f"🆔 {u[0]} - {status} - انتهاء: {expiry}\n"
        await message.reply(msg)

    elif text == "🚫 حظر/فك حظر مستخدم":
        await message.reply("✍️ ابعت رقم المستخدم لحظره أو فك الحظر.")

    elif text.isdigit():
        target_id = int(text)
        users = get_active_users()
        user_ids = [u[0] for u in users]
        if target_id in user_ids:
            ban_user(target_id)
            await message.reply(f"🚫 تم حظر المستخدم {target_id}")
        else:
            unban_user(target_id)
            await message.reply(f"✅ تم فك الحظر عن المستخدم {target_id}")

    else:
        await message.reply("ℹ️ استخدم الأزرار لاختيار أمر من قايمة الأدمن.")

# ===== تشغيل البوت =====
async def main():
    init_db()
    print("🚀 البوت شغال تمام يا روي...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
