# main.py
import os
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

# ⛔️ المتغيرات من البيئة (ENV)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7378889303"))

# ✅ تهيئة البوت والديسباتشر
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 🧱 إنشاء قاعدة البيانات
conn = sqlite3.connect("users.db")
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT
)
""")
conn.commit()

# 🧩 دالة لإضافة أو تحديث المستخدم
def add_or_update_user(user_id, first_name, username):
    cur.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO users (id, first_name, username) VALUES (?, ?, ?)",
            (user_id, first_name, username),
        )
        conn.commit()

# 🔘 لوحة المستخدم
def user_keyboard():
    keyboard = [
        [KeyboardButton(text="🔹 تواصل معانا"), KeyboardButton(text="ℹ️ عن البوت")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# 🔧 لوحة الأدمن
def admin_keyboard():
    keyboard = [
        [KeyboardButton(text="👥 عدد المستخدمين")],
        [KeyboardButton(text="📢 إرسال رسالة للجميع")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# 🎯 أمر /start
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username

    add_or_update_user(user_id, first_name, username)

    if user_id == ADMIN_ID:
        await message.answer(
            f"👋 أهلاً وسهلاً يا أدمن {first_name}.\n"
            f"اختر من القائمة أدناه لإدارة البوت 👇",
            reply_markup=admin_keyboard()
        )
    else:
        await message.answer(
            f"أهلاً بيك يا {first_name} 🙌\n"
            "أنا بوت مساعد بسيط هيساعدك في التعامل بسهولة ❤️",
            reply_markup=user_keyboard()
        )

# 👑 أمر /admin
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ هذا الأمر للأدمن فقط.")
    await message.answer("مرحباً بك يا أدمن 👑", reply_markup=admin_keyboard())

# 👥 عدد المستخدمين
@dp.message(F.text == "👥 عدد المستخدمين")
async def get_users_count(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    await message.answer(f"📊 عدد المستخدمين الحاليين: {count}")

# 📢 إرسال رسالة جماعية
@dp.message(F.text == "📢 إرسال رسالة للجميع")
async def broadcast_message(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("✍️ أرسل الرسالة التي تريد إرسالها لكل المستخدمين:")

    @dp.message(F.text)
    async def handle_broadcast(msg: Message):
        if msg.from_user.id != ADMIN_ID:
            return
        cur.execute("SELECT id FROM users")
        users = cur.fetchall()
        sent, failed = 0, 0
        for user in users:
            try:
                await bot.send_message(user[0], msg.text)
                sent += 1
            except:
                failed += 1
        await msg.answer(f"✅ تم إرسال الرسالة إلى {sent} مستخدم، وفشل {failed}.")

# 🔹 عن البوت
@dp.message(F.text == "ℹ️ عن البوت")
async def about_bot(message: Message):
    await message.answer("🤖 هذا البوت تم تطويره لتجربة استخدام بسيطة وسلسة.\n💡 الإصدار: V3")

# 🔹 تواصل معانا
@dp.message(F.text == "🔹 تواصل معانا")
async def contact_us(message: Message):
    await message.answer("📩 يمكنك التواصل معنا عبر المعرف: @YourSupport")

# 🚀 التشغيل
async def main():
    print("✅ البوت شغال بنجاح...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
