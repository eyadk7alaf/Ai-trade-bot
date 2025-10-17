import asyncio
import time
import os
import sqlite3
import pandas as pd
import yfinance as yf
import schedule

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
# تم استبدال Message, TelegramObject باستيراد شامل لـ types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from typing import Callable, Dict, Any, Awaitable

# =============== إعداد البوت والمتغيرات ===============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID", "0") 
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "AAPL") 

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    print("⚠️ لم يتم تحديد ADMIN_ID بشكل صحيح. تم تعيينه إلى 0.")
    ADMIN_ID = 0 

if not BOT_TOKEN:
    raise ValueError("🚫 لم يتم العثور على متغير البيئة TELEGRAM_BOT_TOKEN.")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())

# =============== قاعدة بيانات SQLite الدائمة (بدون تغيير) ===============
DB_NAME = 'alpha_trade_ai.db'
CONN = None

def init_db():
    global CONN
    CONN = sqlite3.connect(DB_NAME)
    cursor = CONN.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            joined_at REAL,
            is_banned INTEGER DEFAULT 0
        )
    """)
    CONN.commit()

def add_user(user_id, username):
    cursor = CONN.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO users (user_id, username, joined_at) 
        VALUES (?, ?, ?)
    """, (user_id, username, time.time()))
    CONN.commit()

def get_total_users():
    cursor = CONN.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    return cursor.fetchone()[0]

def is_banned(user_id):
    cursor = CONN.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result is not None and result[0] == 1

def update_ban_status(user_id, status):
    cursor = CONN.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (status, user_id))
    CONN.commit()
    
def get_all_users_ids():
    cursor = CONN.cursor()
    cursor.execute("SELECT user_id, is_banned FROM users")
    return cursor.fetchall()

# =============== برمجية وسيطة للحظر (Middleware) - تم الإصلاح ✅ ===============
class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # 1. استخدام event.event_from_user للوصول الآمن إلى المستخدم المرسل
        user = data.get('event_from_user')
        
        # إذا لم يكن هناك مستخدم مرتبط بالحدث (مثل تحديث قناة)، نستمر
        if user is None:
            return await handler(event, data)

        user_id = user.id
        
        # استثناء الأدمن
        if user_id == ADMIN_ID:
            return await handler(event, data)

        # 🚫 منع معالجة التحديث إذا كان المستخدم محظورًا
        if is_banned(user_id):
            # محاولة إرسال رسالة تنبيه للمستخدم المحظور فقط إذا كان الحدث رسالة
            if isinstance(event, types.Message):
                try:
                    await event.answer("🚫 حسابك محظور من استخدام البوت.", reply_markup=types.ReplyKeyboardRemove())
                except:
                    pass
            return # إيقاف تمرير التحديث للمعالجات الأخرى

        # ✅ السماح بالمرور
        return await handler(event, data)


# =============== وظائف التداول وتحليل البيانات (yfinance و pandas) ===============

def fetch_latest_price(symbol: str) -> str:
    """جلب آخر سعر إغلاق لرمز تداول محدد."""
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1d", interval="1m")
        
        if data.empty:
            return "لا تتوفر بيانات حديثة."

        latest_price = data['Close'].iloc[-1]
        latest_time = data.index[-1].strftime('%Y-%m-%d %H:%M:%S')
        
        return f"📊 آخر سعر لـ <b>{symbol}</b>:\nالسعر: ${latest_price:,.2f}\nالوقت: {latest_time} UTC"
        
    except Exception as e:
        return f"❌ فشل في جلب بيانات التداول لـ {symbol}: {e}"

# تم تعديلها لتكون دالة غير متزامنة ليتم استدعاؤها في الـ Scheduler
async def send_daily_trade_signal():
    """وظيفة إرسال إشارة تداول مجدولة لجميع المستخدمين."""
    # يجب أن يتم تنفيذ هذه الوظيفة في حلقة الـ asyncio
    price_info = fetch_latest_price(TRADE_SYMBOL)
    
    trade_msg = f"""
🚨 <b>إشارة تداول يومية (آلية)</b> 🚨
━━━━━━━━━━━━━━━
📈 <b>رمز التداول:</b> {TRADE_SYMBOL}
{price_info}
💡 <b>تحليل:</b> فرصة شراء محتملة.
⚠️ <b>تذكير:</b> تداول بمسؤولية.
━━━━━━━━━━━━━━━
"""
    sent = 0
    all_users = get_all_users_ids()
    
    for uid, is_banned_status in all_users:
        if is_banned_status == 0 and uid != ADMIN_ID:
            try:
                await bot.send_message(uid, trade_msg)
                sent += 1
            except Exception:
                pass
                
    if ADMIN_ID != 0:
        try:
            await bot.send_message(ADMIN_ID, f"📢 تم إرسال الإشارة التداولية الآلية بنجاح إلى {sent} مستخدم.\n{price_info}")
        except Exception:
            pass

# =============== لوحة المستخدم/الأدمن و FSM (بدون تغيير) ===============
def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📈 سعر السوق الحالي"), KeyboardButton("📊 جدول اليوم")],
            [KeyboardButton("💬 تواصل مع الدعم"), KeyboardButton("ℹ️ عن AlphaTradeAI")]
        ],
        resize_keyboard=True
    )

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📢 رسالة لكل المستخدمين"), KeyboardButton("💹 إرسال صفقة يدوية")],
            [KeyboardButton("🚫 حظر مستخدم"), KeyboardButton("✅ إلغاء حظر مستخدم")],
            [KeyboardButton("👥 عدد المستخدمين"), KeyboardButton("🔙 عودة للمستخدم")]
        ],
        resize_keyboard=True
    )

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

@dp.message(F.text == "🔙 عودة للمستخدم")
async def back_to_user_menu(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.reply("👤 العودة إلى قائمة المستخدم الرئيسية.", reply_markup=user_menu())
        
@dp.message(F.text == "👥 عدد المستخدمين")
async def show_user_count(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        count = get_total_users()
        await msg.reply(f"👥 عدد المستخدمين المسجلين: {count}")

@dp.message(F.text == "🚫 حظر مستخدم")
async def ban_user_start(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await msg.reply("📛 أرسل ID المستخدم المراد حظره:")
    await state.set_state(AdminStates.waiting_ban)

@dp.message(AdminStates.waiting_ban)
async def process_ban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        uid = int(msg.text)
        update_ban_status(uid, 1) 
        await msg.reply(f"🚫 تم حظر المستخدم {uid} بنجاح.")
        if uid != ADMIN_ID:
             try:
                 await bot.send_message(uid, "🚫 تم حظر حسابك من قبل الإدارة.")
             except:
                 pass
    except Exception as e:
        await msg.reply(f"❌ ID غير صالح أو حدث خطأ: {e}")
    await state.clear()
    await msg.answer("🎛️ العودة إلى لوحة الأدمن.", reply_markup=admin_menu())

# (بقية دوال الأدمن الأخرى مثل Unban و Broadcast و Trade Manual)
# ...

# =============== أوامر المستخدم (تم إصلاحها لـ F.text) ===============
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    user_id = msg.from_user.id
    username = msg.from_user.username or "مستخدم"
    
    add_user(user_id, username)

    welcome_msg = f"""
🤖 <b>مرحبًا بك في AlphaTradeAI!</b>
🚀 نظام ذكي يتابع السوق ({TRADE_SYMBOL}).
اختر من القائمة 👇
"""
    await msg.reply(welcome_msg, reply_markup=user_menu())

@dp.message(F.text == "📈 سعر السوق الحالي")
async def get_current_price(msg: types.Message):
    price_info = fetch_latest_price(TRADE_SYMBOL)
    await msg.reply(price_info)
    
@dp.message(F.text.in_(["📊 جدول اليوم", "💬 تواصل مع الدعم", "ℹ️ عن AlphaTradeAI"]))
async def handle_user_actions(msg: types.Message):
    if msg.text == "📊 جدول اليوم":
        await msg.reply("🗓️ يتم عرض آخر إشارة تداول آلية:")
        # تشغيل الإشارة في الـ Event Loop الحالي
        await send_daily_trade_signal() 
    elif msg.text == "💬 تواصل مع الدعم":
        await msg.reply(f"📞 يمكنك التواصل مع الإدارة مباشرة عبر @Admin_Username (يرجى استبدالها) أو الإبلاغ عن مشكلة.")
    elif msg.text == "ℹ️ عن AlphaTradeAI":
        await msg.reply("🌟 نحن نقدم تحليلات تداول تعتمد على الذكاء الاصطناعي لمساعدتك في اتخاذ قرارات أفضل في السوق.")


# =============== حلقة التشغيل المجدولة (Schedule Runner) ===============
async def scheduler_runner():
    """تشغيل المهام المجدولة بشكل غير متزامن."""
    # مثال: جدول إرسال الإشارة كل 60 دقيقة
    # ملاحظة: يجب أن تكون الدالة المجدولة بسيطة وتستدعي دالة async عبر asyncio.create_task
    schedule.every(60).minutes.do(lambda: asyncio.create_task(send_daily_trade_signal()))
    
    while True:
        try:
            # تشغيل جميع المهام المجدولة المعلقة
            schedule.run_pending()
        except Exception as e:
            print(f"Error in scheduler: {e}")
        await asyncio.sleep(1) # تحقق كل ثانية

# =============== تشغيل البوت (Main Function) ===============
async def main():
    # 1. تهيئة قاعدة البيانات
    init_db()
    
    # 2. تسجيل الـ Middleware (تأكد من أنه أول ما يسجل)
    dp.update.outer_middleware(BanMiddleware())
    
    print("✅ Bot is running and ready for polling.")
    print(f"👤 Admin ID: {ADMIN_ID} | Trade Symbol: {TRADE_SYMBOL}")
    
    # 3. تشغيل البوت وحلقة الجدولة في نفس الوقت
    await asyncio.gather(
        dp.start_polling(bot),
        scheduler_runner()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped manually.")
    except Exception as e:
        print(f"An error occurred during runtime: {e}")
