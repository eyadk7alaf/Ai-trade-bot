import asyncio
import time
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# =============== إعداد البوت ===============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = 7378889303  # رقم الأدمن
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# =============== قاعدة بيانات بسيطة ===============
users_db = {}
banned_users = set()

def add_user(user_id, username):
    users_db[user_id] = {"username": username, "joined": time.time()}

def get_total_users():
    return len(users_db)

def is_banned(user_id):
    return user_id in banned_users

# =============== لوحة المستخدم ===============
def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📈 آخر الصفقات"), KeyboardButton("📊 جدول اليوم")],
            [KeyboardButton("💬 تواصل مع الدعم"), KeyboardButton("ℹ️ عن AlphaTradeAI")]
        ],
        resize_keyboard=True
    )

# =============== لوحة الأدمن ===============
def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📢 رسالة لكل المستخدمين"), KeyboardButton("💹 إرسال صفقة")],
            [KeyboardButton("🚫 حظر مستخدم"), KeyboardButton("✅ إلغاء حظر مستخدم")],
            [KeyboardButton("👥 عدد المستخدمين"), KeyboardButton("🔙 عودة للمستخدم")]
        ],
        resize_keyboard=True
    )

# =============== الحالات ===============
class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_trade = State()
    waiting_ban = State()
    waiting_unban = State()

# =============== أوامر الأدمن ===============
@dp.message(Command("admin"))
async def admin_panel(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 ليس لديك صلاحية الوصول إلى لوحة التحكم.")
        return
    await msg.reply("🎛️ مرحبًا بك في لوحة تحكم الأدمن!", reply_markup=admin_menu())

@dp.message(F.text == "👥 عدد المستخدمين")
async def show_user_count(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.reply(f"👥 عدد المستخدمين المسجلين: {get_total_users()}")

@dp.message(F.text == "🚫 حظر مستخدم")
async def ban_user(msg: types.Message, state: FSMContext):
    await msg.reply("📛 أرسل ID المستخدم المراد حظره:")
    await state.set_state(AdminStates.waiting_ban)

@dp.message(AdminStates.waiting_ban)
async def process_ban(msg: types.Message, state: FSMContext):
    try:
        uid = int(msg.text)
        banned_users.add(uid)
        await msg.reply(f"🚫 تم حظر المستخدم {uid}")
    except:
        await msg.reply("❌ ID غير صالح.")
    await state.clear()

@dp.message(F.text == "✅ إلغاء حظر مستخدم")
async def unban_user(msg: types.Message, state: FSMContext):
    await msg.reply("♻️ أرسل ID المستخدم لإلغاء حظره:")
    await state.set_state(AdminStates.waiting_unban)

@dp.message(AdminStates.waiting_unban)
async def process_unban(msg: types.Message, state: FSMContext):
    try:
        uid = int(msg.text)
        banned_users.discard(uid)
        await msg.reply(f"✅ تم إلغاء حظر المستخدم {uid}")
    except:
        await msg.reply("❌ ID غير صالح.")
    await state.clear()

@dp.message(F.text == "📢 رسالة لكل المستخدمين")
async def send_broadcast(msg: types.Message, state: FSMContext):
    await msg.reply("📝 أرسل الرسالة التي تريد إرسالها لجميع المستخدمين:")
    await state.set_state(AdminStates.waiting_broadcast)

@dp.message(AdminStates.waiting_broadcast)
async def process_broadcast(msg: types.Message, state: FSMContext):
    sent = 0
    for uid in users_db.keys():
        try:
            await bot.send_message(uid, msg.text)
            sent += 1
        except:
            pass
    await msg.reply(f"✅ تم إرسال الرسالة إلى {sent} مستخدم.")
    await state.clear()

@dp.message(F.text == "💹 إرسال صفقة")
async def send_trade(msg: types.Message, state: FSMContext):
    await msg.reply("💹 أرسل الصفقة بالشكل التالي:\n\nزوج العملة | نوع الصفقة | سعر الدخول | TP | SL | نسبة الثقة")
    await state.set_state(AdminStates.waiting_trade)

@dp.message(AdminStates.waiting_trade)
async def process_trade(msg: types.Message, state: FSMContext):
    parts = msg.text.split("|")
    if len(parts) < 6:
        await msg.reply("❌ تأكد من إدخال كل البيانات.")
        return
    pair, ttype, entry, tp, sl, trust = [p.strip() for p in parts]
    trade_msg = f"""
📊 <b>صفقة جديدة!</b>
━━━━━━━━━━━━━━━
💱 <b>الزوج:</b> {pair}
📈 <b>النوع:</b> {ttype}
🎯 <b>دخول:</b> {entry}
🎯 <b>TP:</b> {tp}
🛑 <b>SL:</b> {sl}
🔥 <b>نسبة الثقة:</b> {trust}%
━━━━━━━━━━━━━━━
🤖 <b>AlphaTradeAI</b>
"""
    for uid in users_db.keys():
        if not is_banned(uid):
            try:
                await bot.send_message(uid, trade_msg, parse_mode="HTML")
            except:
                pass
    await msg.reply("✅ تم إرسال الصفقة لكل المستخدمين.")
    await state.clear()

# =============== المستخدم ===============
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    user_id = msg.from_user.id
    username = msg.from_user.username or "مستخدم"
    if is_banned(user_id):
        await msg.reply("🚫 حسابك محظور من استخدام البوت.")
        return
    if user_id not in users_db:
        add_user(user_id, username)

    welcome_msg = f"""
🤖 <b>مرحبًا بك في AlphaTradeAI!</b>

💬 نظام ذكي يقدم لك إشعارات تداول دقيقة وسريعة.
🚀 تابع أحدث الصفقات والفرص اليومية.
📊 كل ما تحتاجه في عالم التداول في مكان واحد.

اختر من القائمة أدناه 👇
"""
    await msg.reply(welcome_msg, parse_mode="HTML", reply_markup=user_menu())

# =============== تشغيل البوت ===============
async def main():
    print("✅ Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
