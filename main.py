import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from datetime import datetime, timedelta
import os
import sqlite3

# ========= الإعدادات =========
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ مفيش TELEGRAM_BOT_TOKEN في متغيرات البيئة!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ========= قاعدة البيانات =========
conn = sqlite3.connect("users.db")
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    key TEXT,
    key_expiry TEXT
)""")
conn.commit()

# ========= لوحة المستخدم =========
def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 جدول اليوم"), KeyboardButton(text="📈 آخر الصفقات")],
            [KeyboardButton(text="💼 حسابي"), KeyboardButton(text="ℹ️ الدعم الفني")]
        ],
        resize_keyboard=True
    )

# ========= أوامر =========
@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    username = msg.from_user.username or "ضيف"
    cur.execute("SELECT * FROM users WHERE id = ?", (msg.from_user.id,))
    user = cur.fetchone()

    if user:
        await msg.answer(
            f"👋 أهلاً بيك {username}!\n\n"
            f"مرحباً في بوت **AlphaTradeAI**، مساعدك الذكي لتداول العملات و الذهب.\n\n"
            f"اختر من القايمة اللي تحت 👇",
            reply_markup=user_menu()
        )
    else:
        cur.execute("INSERT INTO users (id, username) VALUES (?, ?)", (msg.from_user.id, username))
        conn.commit()
        await msg.answer(
            f"👋 أهلاً {username}!\n"
            f"مرحباً في **AlphaTradeAI** 🚀\n\n"
            f"ابدأ رحلتك الذكية في عالم التداول بالذكاء الاصطناعي.\n"
            f"اضغط أي زر للبدء 👇",
            reply_markup=user_menu()
        )

# ========= باقي الأزرار =========
@dp.message(F.text == "📊 جدول اليوم")
async def show_schedule(msg: types.Message):
    await msg.answer("📅 جدول اليوم فارغ حالياً، تابعنا لاحقاً للتحديثات.")

@dp.message(F.text == "📈 آخر الصفقات")
async def show_trades(msg: types.Message):
    await msg.answer("📊 لا توجد صفقات جديدة الآن. سيتم عرض آخر الصفقات هنا 🔔")

@dp.message(F.text == "💼 حسابي")
async def my_account(msg: types.Message):
    cur.execute("SELECT key, key_expiry FROM users WHERE id = ?", (msg.from_user.id,))
    user = cur.fetchone()
    if user and user[0]:
        expiry = user[1] or "غير محددة"
        await msg.answer(f"🔑 مفتاحك الحالي: `{user[0]}`\n📆 الصلاحية حتى: {expiry}", parse_mode="Markdown")
    else:
        await msg.answer("❌ لا يوجد مفتاح مفعل على حسابك حالياً.")

@dp.message(F.text == "ℹ️ الدعم الفني")
async def support(msg: types.Message):
    await msg.answer("📩 للتواصل مع الدعم الفني:\n@AlphaTradeAI_Support")

# ========= تشغيل البوت =========
async def main():
    print("🚀 Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
