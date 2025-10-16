import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ===== إعدادات البوت =====
BOT_TOKEN = "ضع_توكن_البوت_بتاعك_هنا"
ADMIN_ID = 7378889303  # رقم الأدمن

# ===== تهيئة البوت =====
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== الحالات =====
class AdminStates(StatesGroup):
    waiting_broadcast_all = State()
    waiting_broadcast_subs = State()
    waiting_trade_manual = State()

# ===== لوحة الأدمن =====
def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📢 رسالة لكل المستخدمين")],
            [KeyboardButton(text="✅ رسالة للمشتركين")],
            [KeyboardButton(text="💹 إرسال صفقة يدوياً")],
            [KeyboardButton(text="🔙 رجوع للمستخدم")]
        ],
        resize_keyboard=True
    )

# ===== لوحة المستخدم =====
def user_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 جدول اليوم"), KeyboardButton(text="📈 آخر الصفقات")],
            [KeyboardButton(text="📞 الدعم الفني")]
        ],
        resize_keyboard=True
    )

# ===== رسالة ترحيبية =====
@dp.message(Command("start"))
async def start_cmd(msg: Message):
    await msg.answer(
        f"👋 أهلاً {msg.from_user.first_name}!\n"
        "مرحباً بك في بوت **AlphaTradeAI** 🤖💹\n\n"
        "اختار من القائمة اللي تحت ⬇️",
        reply_markup=user_keyboard()
    )

# ===== أمر الأدمن =====
@dp.message(Command("admin"))
async def admin_cmd(msg: Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.answer("🎛️ أهلاً بالأدمن 👑\nاختار وظيفة من القائمة:", reply_markup=admin_keyboard())
    else:
        await msg.answer("❌ الأمر ده مخصص للأدمن فقط.")

# ===== تعامل الأدمن مع الأزرار =====
@dp.message(F.text.in_({"📢 رسالة لكل المستخدمين", "✅ رسالة للمشتركين", "💹 إرسال صفقة يدوياً"}))
async def admin_actions(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer("❌ أنت مش أدمن.")
        return

    if msg.text == "📢 رسالة لكل المستخدمين":
        await msg.answer("📢 أرسل الرسالة لتصل لكل المستخدمين:")
        await state.set_state(AdminStates.waiting_broadcast_all)
    elif msg.text == "✅ رسالة للمشتركين":
        await msg.answer("✅ أرسل الرسالة لتصل للمشتركين فقط:")
        await state.set_state(AdminStates.waiting_broadcast_subs)
    elif msg.text == "💹 إرسال صفقة يدوياً":
        await msg.answer(
            "💹 أرسل الصفقة بهذا الشكل:\n\n"
            "زوج العملات 💱: EUR/USD\n"
            "النوع 📉: Sell / Buy\n"
            "دخول الصفقة 🎯: 1.0725\n"
            "وقف الخسارة ⛔: 1.0750\n"
            "الهدف 🎯: 1.0680\n"
            "نسبة الثقة 🔥: 90%\n\n"
            "📤 أرسل التفاصيل الآن."
        )
        await state.set_state(AdminStates.waiting_trade_manual)

# ===== رجوع المستخدم =====
@dp.message(F.text == "🔙 رجوع للمستخدم")
async def back_to_user(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("✅ تم الرجوع إلى وضع المستخدم.", reply_markup=user_keyboard())

# ===== أمر غير معروف =====
@dp.message()
async def unknown_command(msg: Message):
    await msg.answer("❓ أمر غير معروف أو لم يُنفذ بعد.")

# ===== تشغيل البوت =====
async def main():
    print("✅ Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
