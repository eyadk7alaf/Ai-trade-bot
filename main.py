import asyncio
import time
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import BOT_TOKEN, ADMIN_ID
from database import init_db, add_or_update_user, activate_user_with_key, create_key, list_keys, get_active_users

# إعدادات اللوج
logging.basicConfig(level=logging.INFO)

# إنشاء البوت والديسباتشر
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== الحالات ==========
class AdminStates(StatesGroup):
    waiting_broadcast_all = State()
    waiting_broadcast_subs = State()
    waiting_trade_manual = State()

# ========== القوائم ==========
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💹 جدول اليوم"), KeyboardButton(text="📊 آخر الصفقات")],
        [KeyboardButton(text="ℹ️ شرح الاستخدام"), KeyboardButton(text="💬 الدعم الفني")]
    ],
    resize_keyboard=True
)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🪄 إنشاء مفتاح جديد"), KeyboardButton(text="📜 عرض المفاتيح")],
        [KeyboardButton(text="🚫 حظر مستخدم"), KeyboardButton(text="✅ إلغاء الحظر")],
        [KeyboardButton(text="رسالة لكل المستخدمين 📢"), KeyboardButton(text="رسالة للمشتركين ✅")],
        [KeyboardButton(text="إرسال صفقة يدوياً 💹"), KeyboardButton(text="⬅️ رجوع")]
    ],
    resize_keyboard=True
)

# ========== رسالة الترحيب ==========
@dp.message(Command("start"))
async def start_message(msg: types.Message):
    add_or_update_user(msg.from_user.id, getattr(msg.from_user, "username", None))
    welcome_text = (
        f"👋 أهلاً {msg.from_user.first_name}!\n\n"
        f"🤖 **مرحباً بك في AlphaTradeAI** 💹\n"
        f"منصة التداول الذكي المدعومة بالذكاء الاصطناعي.\n\n"
        f"📈 توصيات دقيقة.\n"
        f"💰 إدارة ذكية للمخاطر.\n"
        f"📊 إشعارات لحظية.\n\n"
        f"للاشتراك أرسل مفتاح التفعيل الخاص بك 🔑"
    )
    await msg.answer(welcome_text, parse_mode="Markdown", reply_markup=main_menu)

# ========== لوحة الأدمن ==========
@dp.message(Command("admin"))
async def admin_panel(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.answer("⚙️ مرحباً أيها الأدمن، اختر من القائمة:", reply_markup=admin_menu)
    else:
        await msg.answer("🚫 هذا الأمر مخصص للأدمن فقط.")

# ========== أوامر المستخدم ==========
@dp.message(lambda msg: msg.text == "ℹ️ شرح الاستخدام")
async def usage_info(msg: types.Message):
    text = (
        "📘 **طريقة الاستخدام:**\n"
        "1️⃣ أرسل مفتاح التفعيل لتفعيل اشتراكك.\n"
        "2️⃣ استقبل إشارات التداول اليومية.\n"
        "3️⃣ راقب أداء الصفقات من قسم *آخر الصفقات*.\n"
        "4️⃣ احصل على تحليلات دقيقة مباشرة من AlphaTradeAI 🤖"
    )
    await msg.reply(text, parse_mode="Markdown")

@dp.message(lambda msg: msg.text == "💬 الدعم الفني")
async def support(msg: types.Message):
    await msg.reply("📞 راسل الدعم الفني عبر: @AlphaTradeAI_Support")

@dp.message(lambda msg: msg.text == "📊 آخر الصفقات")
async def last_trades(msg: types.Message):
    await msg.reply("📈 لا توجد صفقات سابقة حالياً. سيتم عرضها هنا قريباً.")

@dp.message(lambda msg: msg.text == "💹 جدول اليوم")
async def today_trades(msg: types.Message):
    await msg.reply("📊 يتم حالياً إعداد صفقات اليوم من التحليل الآلي...")

# ========== أوامر الأدمن ==========
@dp.message(lambda msg: msg.text == "⬅️ رجوع")
async def back_to_main(msg: types.Message):
    await msg.answer("🏠 تم الرجوع إلى القائمة الرئيسية", reply_markup=main_menu)

@dp.message(lambda msg: msg.text == "🪄 إنشاء مفتاح جديد")
async def create_key_cmd(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    await msg.reply("🔑 أرسل الكود وعدد الأيام، مثال:\n`ABC123 7`", parse_mode="Markdown")

@dp.message(lambda msg: msg.text == "📜 عرض المفاتيح")
async def show_keys_cmd(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    keys = list_keys()
    if not keys:
        await msg.reply("❌ لا توجد مفاتيح حالياً.")
        return
    text = "🔐 **قائمة المفاتيح:**\n"
    for k in keys:
        used = "✅ مستخدم" if k["used_by"] else "❌ غير مستخدم"
        text += f"\n• {k['key_code']} | {k['duration_days']} يوم | {used}"
    await msg.reply(text, parse_mode="Markdown")

@dp.message(lambda msg: msg.text == "رسالة لكل المستخدمين 📢")
async def broadcast_all(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    await msg.reply("📢 أرسل الرسالة لتصل لكل المستخدمين:")
    await state.set_state(AdminStates.waiting_broadcast_all)

@dp.message(lambda msg: msg.text == "رسالة للمشتركين ✅")
async def broadcast_subs(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    await msg.reply("✅ أرسل الرسالة لتصل للمشتركين فقط:")
    await state.set_state(AdminStates.waiting_broadcast_subs)

@dp.message(lambda msg: msg.text == "إرسال صفقة يدوياً 💹")
async def send_trade_manual(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    await msg.reply("💹 أرسل الصفقة بهذا الشكل:\n\n"
                    "زوج العملة، نوع الصفقة (Buy/Sell)\n"
                    "سعر الدخول، TP، SL، نسبة النجاح (%)",
                    parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_trade_manual)

# ========== تفعيل المفاتيح ==========
@dp.message()
async def message_handler(msg: types.Message, state: FSMContext):
    user_id = msg.from_user.id
    text = msg.text.strip()

    # لو المستخدم أدمن
    if user_id == ADMIN_ID:
        current = await state.get_state()

        # إرسال الصفقة اليدوية
        if current == AdminStates.waiting_trade_manual.state:
            await msg.reply("✅ تم إرسال الصفقة للمشتركين.")
            await state.clear()
            trade_text = (
                f"💹 **صفقة جديدة من AlphaTradeAI** 💎\n\n"
                f"زوج العملة: EUR/USD\n"
                f"النوع: 🟢 Buy\n"
                f"الدخول: 1.08500\n"
                f"الهدف (TP): 1.08800 🎯\n"
                f"وقف الخسارة (SL): 1.08300 ❌\n"
                f"نسبة النجاح المتوقعة: 88% ✅"
            )
            users = get_active_users()
            for u in users:
                try:
                    await bot.send_message(u["telegram_id"], trade_text, parse_mode="Markdown")
                except:
                    continue
            return

    # تفعيل المفتاح
    if len(text) > 3 and " " not in text:
        ok, info = activate_user_with_key(user_id, text)
        if ok:
            await msg.reply(f"✅ تم تفعيل اشتراكك حتى: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(info))}")
        else:
            if info == "invalid":
                await msg.reply("❌ المفتاح غير صحيح.")
            elif info == "used":
                await msg.reply("⚠️ المفتاح مستخدم بالفعل.")
            else:
                await msg.reply("❌ حدث خطأ أثناء التفعيل.")
        return

    await msg.reply("❓ أمر غير معروف أو لم يُنفذ بعد.")

# ================= تشغيل البوت =================
async def main():
    init_db()
    print("✅ قاعدة البيانات جاهزة")
    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        print("🛑 تم إيقاف البوت")

if __name__ == "__main__":
    asyncio.run(main())
