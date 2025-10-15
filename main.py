import logging
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import asyncio
import os

# ===== إعداد البوت =====
TOKEN = os.getenv("BOT_TOKEN", "هنا_توكن_البوت")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7378889303"))

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# ===== قاعدة البيانات =====
def init_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        user_id INTEGER UNIQUE,
        active INTEGER DEFAULT 0,
        key TEXT
    )''')
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def set_active(user_id, status):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("UPDATE users SET active = ? WHERE user_id = ?", (status, user_id))
    conn.commit()
    conn.close()

def is_admin(user_id):
    return user_id == ADMIN_ID

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
    await message.answer(welcome_text, parse_mode="HTML")

# ===== أمر الأدمن =====
@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("🚫 الأمر ده مش متاح ليك.")
        return

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("📋 عرض المستخدمين", "🔑 تفعيل مستخدم")
    await message.answer("👑 مرحباً أدمن! اختر أمر من القايمة:", reply_markup=keyboard)

# ===== الأوامر داخل قايمة الأدمن =====
@dp.message()
async def handle_admin_panel(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    if is_admin(user_id):
        if text == "📋 عرض المستخدمين":
            conn = sqlite3.connect("users.db")
            c = conn.cursor()
            c.execute("SELECT user_id, active FROM users")
            users = c.fetchall()
            conn.close()

            if not users:
                await message.answer("🚫 مفيش مستخدمين لسه.")
                return

            msg = "👥 <b>قائمة المستخدمين:</b>\n\n"
            for u in users:
                status = "✅ مفعل" if u[1] else "❌ غير مفعل"
                msg += f"🆔 {u[0]} - {status}\n"
            await message.answer(msg, parse_mode="HTML")

        elif text == "🔑 تفعيل مستخدم":
            await message.answer("✍️ ابعت رقم المستخدم اللي عايز تفعل حسابه بعديها على طول.")

        elif text.isdigit():
            target_id = int(text)
            set_active(target_id, 1)
            await message.answer(f"✅ تم تفعيل المستخدم {target_id}")

        else:
            await message.answer("ℹ️ استخدم الأزرار لاختيار أمر من قايمة الأدمن.")

    else:
        await message.answer("💬 رسالتك وصلت! الدعم هيرد عليك قريب ❤️")

# ===== تشغيل البوت =====
async def main():
    init_db()
    print("🚀 البوت شغال تمام يا روي...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
