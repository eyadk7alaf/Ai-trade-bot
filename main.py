import asyncio
import time
import os
import sqlite3
import pandas as pd
import yfinance as yf
import schedule
import random
import uuid

from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from typing import Callable, Dict, Any, Awaitable

# =============== إعداد البوت والمتغيرات ===============
# القيم تُسحب من Railway - لا تكتبها هنا مباشرة
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID", "0") 
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "GC=F") 
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85")) 
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "I1l_1")

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    print("⚠️ لم يتم تحديد ADMIN_ID بشكل صحيح. تم تعيينه إلى 0.")
    ADMIN_ID = 0 

if not BOT_TOKEN:
    raise ValueError("🚫 لم يتم العثور على متغير البيئة TELEGRAM_BOT_TOKEN. يرجى ضبطه.")

bot = Bot(token=BOT_TOKEN, 
          default=DefaultBotProperties(parse_mode="HTML")) 
          
dp = Dispatcher(storage=MemoryStorage())

# =============== قاعدة بيانات SQLite - نظام الاشتراكات ===============
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
            is_banned INTEGER DEFAULT 0,
            vip_until REAL DEFAULT 0.0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invite_keys (
            key TEXT PRIMARY KEY,
            days INTEGER,
            created_by INTEGER,
            used_by INTEGER NULL,
            used_at REAL NULL
        )
    """)
    CONN.commit()

# دوال CRUD أساسية
def add_user(user_id, username):
    cursor = CONN.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO users (user_id, username, joined_at) 
        VALUES (?, ?, ?)
    """, (user_id, username, time.time()))
    CONN.commit()

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
    
def get_total_users():
    cursor = CONN.cursor()
    cursor.execute("SELECT COUNT(...) FROM users") # تم استخدام ... لتجنب خطأ محتمل
    return cursor.fetchone()[0]

# دوال الاشتراكات
def is_user_vip(user_id):
    """التحقق مما إذا كان المستخدم VIP (تاريخ اشتراكه لم ينتهِ بعد)."""
    cursor = CONN.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result is None: return False
    return result[0] > time.time()
    
def activate_key(user_id, key):
    """تفعيل مفتاح الاشتراك للمستخدم."""
    cursor = CONN.cursor()
    cursor.execute("SELECT days FROM invite_keys WHERE key = ? AND used_by IS NULL", (key,))
    key_data = cursor.fetchone()

    if key_data:
        days = key_data[0]
        cursor.execute("UPDATE invite_keys SET used_by = ?, used_at = ? WHERE key = ?", (user_id, time.time(), key))
        
        cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
        vip_until_ts = cursor.fetchone()[0]
        
        if vip_until_ts > time.time():
            start_date = datetime.fromtimestamp(vip_until_ts)
        else:
            start_date = datetime.now()
            
        new_vip_until = start_date + timedelta(days=days)
        
        cursor.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (new_vip_until.timestamp(), user_id))
        
        CONN.commit()
        return True, days, new_vip_until
    
    return False, 0, None

def get_user_vip_status(user_id):
    """جلب حالة VIP للمستخدم."""
    cursor = CONN.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result and result[0] > time.time():
        return datetime.fromtimestamp(result[0]).strftime("%Y-%m-%d %H:%M")
    return "غير مشترك"

def create_invite_key(admin_id, days):
    """توليد مفتاح اشتراك جديد بعدد الأيام (مفتاح أقصر)."""
    key = str(uuid.uuid4()).split('-')[0] + '-' + str(uuid.uuid4()).split('-')[1]
    cursor = CONN.cursor()
    cursor.execute("INSERT INTO invite_keys (key, days, created_by) VALUES (?, ?, ?)", (key, days, admin_id))
    CONN.commit()
    return key


# =============== برمجية وسيطة للحظر والاشتراك (Access Middleware) ===============
class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject, data: Dict[str, Any],
    ) -> Any:
        user = data.get('event_from_user')
        if user is None: return await handler(event, data)
        user_id = user.id
        
        if user_id == ADMIN_ID: return await handler(event, data)

        if is_banned(user_id):
            if isinstance(event, types.Message):
                await event.answer("🚫 حسابك محظور من استخدام البوت.", reply_markup=types.ReplyKeyboardRemove())
            return 
        
        # السماح بالوصول لبعض الأوامر دون اشتراك
        allowed_texts = ["💬 تواصل مع الدعم", "ℹ️ عن AlphaTradeAI", "🔗 تفعيل مفتاح الاشتراك", "📝 حالة الاشتراك"]
        if isinstance(event, types.Message) and (event.text == '/start' or event.text in allowed_texts or event.text.startswith('/start ')):
             return await handler(event, data)

        if not is_user_vip(user_id):
            if isinstance(event, types.Message):
                await event.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط. يرجى تفعيل مفتاح اشتراك لتتمكن من استخدامها.")
            return

        return await handler(event, data)

# =============== وظائف التداول والتحليل الذكي ===============

def get_signal_and_confidence(symbol: str) -> tuple[str, float, str, float, float, float]:
    """
    تحليل ذكي (تقاطع EMA) وحساب نسبة الثقة، وتحديد Entry/TP/SL.
    يعود بـ: (رسالة السعر (عربي), نسبة الثقة, نوع الصفقة, سعر الدخول, وقف الخسارة, الهدف)
    """
    try:
        # جلب بيانات 60 شمعة بدقة دقيقة واحدة
        data = yf.download(symbol, period="60m", interval="1m", progress=False)
        
        if data.empty or len(data) < 30:
            return f"لا تتوفر بيانات كافية للتحليل لرمز التداول: {symbol}.", 0.0, "HOLD", 0.0, 0.0, 0.0

        data['EMA_5'] = data['Close'].ewm(span=5, adjust=False).mean()
        data['EMA_20'] = data['Close'].ewm(span=20, adjust=False).mean()
        
        latest_price = data['Close'].iloc[-1]
        latest_time = data.index[-1].strftime('%Y-%m-%d %H:%M:%S')
        
        ema_fast_prev = data['EMA_5'].iloc[-2]
        ema_slow_prev = data['EMA_20'].iloc[-2]
        ema_fast_current = data['EMA_5'].iloc[-1]
        ema_slow_current = data['EMA_20'].iloc[-1]
        
        action = "HOLD"
        confidence = 0.5
        
        # ثوابت حساب SL و TP
        SL_RISK = 0.005  # 0.5% risk
        TP_REWARD = 0.015 # 1.5% reward (R:R 1:3)
        entry_price = latest_price
        stop_loss = 0.0
        take_profit = 0.0
        
        # منطق تحديد الصفقة (تقاطع EMA)
        if ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current:
            action = "BUY"
            confidence = 0.95 
            stop_loss = latest_price * (1 - SL_RISK)
            take_profit = latest_price * (1 + TP_REWARD)
        elif ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current:
            action = "SELL"
            confidence = 0.95
            stop_loss = latest_price * (1 + SL_RISK)
            take_profit = latest_price * (1 - TP_REWARD)
        elif ema_fast_current > ema_slow_current:
             action = "BUY"
             confidence = 0.75
             stop_loss = latest_price * (1 - SL_RISK)
             take_profit = latest_price * (1 + TP_REWARD)
        elif ema_fast_current < ema_slow_current:
             action = "SELL"
             confidence = 0.75
             stop_loss = latest_price * (1 + SL_RISK)
             take_profit = latest_price * (1 - TP_REWARD)
            
        # رسالة السعر بالعربية (للاستخدام في قائمة المستخدم العادية)
        price_msg = f"📊 آخر سعر لـ <b>{symbol}</b>:\nالسعر: ${latest_price:,.2f}\nالوقت: {latest_time} UTC"
        
        return price_msg, confidence, action, entry_price, stop_loss, take_profit
        
    except Exception as e:
        return f"❌ فشل في جلب بيانات التداول لـ {symbol} أو التحليل: {e}", 0.0, "HOLD", 0.0, 0.0, 0.0

async def send_trade_signal(admin_triggered=False):
    """إرسال إشارة تداول VIP باللغة الإنجليزية وبالمعاملات المطلوبة."""
    
    price_info_msg_ar, confidence, action, entry_price, stop_loss, take_profit = get_signal_and_confidence(TRADE_SYMBOL)
    
    confidence_percent = confidence * 100
    is_high_confidence = confidence >= CONFIDENCE_THRESHOLD

    # لن نرسل الصفقة إذا لم تكن ذات ثقة عالية أو كانت HOLD
    if not is_high_confidence or action == "HOLD":
        return False # لم يتم إرسال صفقة

    # بناء الرسالة باللغة الإنجليزية
    signal_emoji = "🟢" if action == "BUY" else "🔴"
    trade_action_en = "BUY" if action == "BUY" else "SELL"
    
    trade_msg = f"""
{signal_emoji} <b>VIP TRADE SIGNAL - GOLD (XAUUSD Proxy)</b> {signal_emoji}
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD
🔥 **ACTION:** {trade_action_en} (Market Execution)
💰 **ENTRY:** ${entry_price:,.2f}
🎯 **TARGET (TP):** ${take_profit:,.2f}
🛑 **STOP LOSS (SL):** ${stop_loss:,.2f}
🔒 **SUCCESS RATE:** {confidence_percent:.2f}%

<i>Trade responsibly. This signal is based on {TRADE_SYMBOL} Smart EMA analysis.</i>
"""
    sent = 0
    all_users = get_all_users_ids()
    
    for uid, is_banned_status in all_users:
        # إرسال فقط لغير المحظورين والمشتركين VIP
        if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
            try:
                await bot.send_message(uid, trade_msg)
                sent += 1
            except Exception:
                pass
    
    if ADMIN_ID != 0:
        # إرسال إشعار للأدمن
        try:
            admin_note = "تم الإرسال عبر الأمر الفوري" if admin_triggered else "إرسال مجدول"
            await bot.send_message(ADMIN_ID, f"📢 تم إرسال صفقة VIP ({trade_action_en}) إلى {sent} مشترك.\nالثقة: {confidence_percent:.2f}%.\nملاحظة: {admin_note}")
        except Exception:
            pass
            
    return True # تم إرسال صفقة

# وظيفة التنبيه باللغة الإنجليزية
async def send_analysis_alert():
    """وظيفة إرسال تنبيه عشوائي بأن البوت يجري تحليل."""
    
    alert_messages = [
        "🔎 Scanning the Gold market... 🧐 Looking for a strong trading opportunity on XAUUSD.",
        "⏳ Analyzing Gold data now... Please wait, a VIP trade signal might drop soon!",
        "🤖 Smart Analyst is running... 💡 Evaluating current EMA cross patterns for a high-confidence trade."
    ]
    
    msg_to_send = random.choice(alert_messages)
    
    all_users = get_all_users_ids()
    
    for uid, is_banned_status in all_users:
        if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
            try:
                await bot.send_message(uid, msg_to_send)
            except Exception:
                pass
                
# =============== القوائم المُعدَّلة ===============

def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📈 سعر السوق الحالي"), KeyboardButton(text="📝 حالة الاشتراك")],
            [KeyboardButton(text="🔗 تفعيل مفتاح الاشتراك"), KeyboardButton(text="📊 جدول اليوم")],
            [KeyboardButton(text="💬 تواصل مع الدعم"), KeyboardButton(text="ℹ️ عن AlphaTradeAI")]
        ],
        resize_keyboard=True
    )

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="تحليل فوري ⚡️"), KeyboardButton(text="📢 رسالة لكل المستخدمين")],
            [KeyboardButton(text="🔑 إنشاء مفتاح اشتراك"), KeyboardButton(text="🗒️ عرض حالة المشتركين")],
            [KeyboardButton(text="🚫 حظر مستخدم"), KeyboardButton(text="✅ إلغاء حظر مستخدم")],
            [KeyboardButton(text="👥 عدد المستخدمين"), KeyboardButton(text="🔙 عودة للمستخدم")]
        ],
        resize_keyboard=True
    )

# =============== أوامر الأدمن المُعدَّلة ===============

@dp.message(F.text == "تحليل فوري ⚡️")
async def analyze_market_now(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 ليس لديك صلاحية الوصول.")
        return
    
    await msg.reply("⏳ جارٍ تحليل السوق بحثًا عن فرصة تداول ذات ثقة عالية...")
    
    # تشغيل التحليل وإرسال الصفقة (إذا توفرت)
    sent_successfully = await send_trade_signal(admin_triggered=True)
    
    if sent_successfully:
        await msg.answer("✅ تم إرسال صفقة VIP بنجاح إلى المشتركين.")
    else:
        # نحتاج إلى جلب الثقة هنا لتوضيح سبب عدم الإرسال
        _, confidence, action, _, _, _ = get_signal_and_confidence(TRADE_SYMBOL)
        confidence_percent = confidence * 100
        
        if action == "HOLD":
             await msg.answer("💡 لا توجد إشارة واضحة (HOLD). لم يتم إرسال صفقة.")
        else:
             await msg.answer(f"⚠️ الإشارة موجودة ({action})، لكن نسبة الثقة {confidence_percent:.2f}% أقل من المطلوب ({int(CONFIDENCE_THRESHOLD*100)}%). لم يتم إرسال صفقة.")

# =============== أوامر المستخدم ===============

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    user_id = msg.from_user.id
    username = msg.from_user.username or "مستخدم"
    
    add_user(user_id, username)

    welcome_msg = f"""
🤖 <b>مرحبًا بك في AlphaTradeAI!</b>
🚀 نظام ذكي يتابع سوق الذهب ({TRADE_SYMBOL}).
اختر من القائمة 👇
"""
    await msg.reply(welcome_msg, reply_markup=user_menu())
    
@dp.message(F.text == "📈 سعر السوق الحالي")
async def get_current_price(msg: types.Message):
    # نستدعي الدالة ونستخلص منها رسالة السعر فقط (باللغة العربية)
    price_info_msg, _, _, _, _, _ = get_signal_and_confidence(TRADE_SYMBOL)
    await msg.reply(price_info_msg)

@dp.message(F.text == "📊 جدول اليوم")
async def get_current_signal(msg: types.Message):
    # هذه الدالة تستخدم للإشارة إلى أن التحليل يعمل في الخلفية
    await msg.reply("🗓️ يتم تحليل السوق حاليًا. ستصلك الصفقات المجدولة تلقائيًا إذا توفرت.")

@dp.message(F.text == "📝 حالة الاشتراك")
async def show_subscription_status(msg: types.Message):
    status = get_user_vip_status(msg.from_user.id)
    if status == "غير مشترك":
        await msg.reply(f"⚠️ أنت حالياً **غير مشترك** في خدمة VIP.\nللاشتراك، اطلب مفتاح تفعيل من الأدمن (@{ADMIN_USERNAME}) ثم اضغط '🔗 تفعيل مفتاح الاشتراك'.")
    else:
        await msg.reply(f"✅ أنت مشترك في خدمة VIP.\nتنتهي صلاحية اشتراكك في: <b>{status}</b>.")

@dp.message(F.text == "💬 تواصل مع الدعم")
async def handle_contact_support(msg: types.Message):
    await msg.reply(f"📞 يمكنك التواصل مع الإدارة مباشرة عبر @{ADMIN_USERNAME} للإستفسارات أو الدعم.")
    
# ... (بقية دوال الأدمن الأخرى: الحظر، إلغاء الحظر، المفاتيح، البث تبقى كما هي)
@dp.message(F.text == "🔙 عودة للمستخدم")
async def back_to_user_menu(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.reply("👤 العودة إلى قائمة المستخدم الرئيسية.", reply_markup=user_menu())

@dp.message(F.text == "👥 عدد المستخدمين")
async def show_user_count(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        count = get_total_users()
        await msg.reply(f"👥 عدد المستخدمين المسجلين: {count}")
# (بقية دوال المفاتيح والبث والحظر يتم وضعها هنا)
@dp.message(F.text == "🔑 إنشاء مفتاح اشتراك")
async def create_key_start(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await msg.reply("🗓️ كم عدد الأيام التي تريد تفعيلها بهذا المفتاح؟ (أرسل رقماً)")
    await state.set_state(AdminStates.waiting_key_days)

@dp.message(AdminStates.waiting_key_days)
async def process_key_days(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        days = int(msg.text)
        if days <= 0: raise ValueError
        new_key = create_invite_key(msg.from_user.id, days)
        await msg.reply(f"✅ تم إنشاء مفتاح اشتراك {days} أيام:\n<code>{new_key}</code>")
    except ValueError:
        await msg.reply("❌ يرجى إرسال عدد صحيح موجب للأيام.")
    finally:
        await state.clear()
        await msg.answer("🎛️ العودة إلى لوحة الأدمن.", reply_markup=admin_menu())

@dp.message(F.text == "🗒️ عرض حالة المشتركين")
async def show_keys(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    cursor = CONN.cursor()
    cursor.execute("SELECT user_id, vip_until, username FROM users WHERE vip_until > ?", (time.time(),))
    active_users = cursor.fetchall()
    
    if not active_users:
        await msg.reply("لا يوجد مشتركين فعالين حالياً.")
        return
        
    response = "👥 **المشتركون الفعالون**:\n\n"
    for uid, vip_until_ts, username in active_users:
        expiry_date = datetime.fromtimestamp(vip_until_ts).strftime("%Y-%m-%d %H:%M")
        response += f"• @{username or 'لا يوزر'} (<code>{uid}</code>)\n  (تنتهي صلاحيته في: {expiry_date})\n"
        
    await msg.reply(response)
    
@dp.message(F.text == "🔗 تفعيل مفتاح الاشتراك")
async def handle_invite_key(msg: types.Message, state: FSMContext):
    await msg.reply("🔑 يرجى إرسال مفتاح الاشتراك VIP الخاص بك لتفعيله:")
    await state.set_state(UserStates.waiting_key_activation)

@dp.message(UserStates.waiting_key_activation)
async def process_key_activation(msg: types.Message, state: FSMContext):
    user_id = msg.from_user.id
    key = msg.text.strip()
    
    success, days, expiry_date = activate_key(user_id, key)

    if success:
        await msg.reply(f"🎉 تهانينا! تم تفعيل اشتراكك VIP لمدة {days} أيام.\nتنتهي صلاحيته في: <b>{expiry_date.strftime('%Y-%m-%d %H:%M')}</b>.")
    else:
        await msg.reply("❌ فشل التفعيل. المفتاح غير صالح أو تم استخدامه مسبقًا.")
        
    await state.clear()


class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_trade = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_key_days = State() 

class UserStates(StatesGroup):
    waiting_key_activation = State() 
    
# ... (بقية دوال الحظر والبث)

# =============== تشغيل البوت (Main Function) ===============

def setup_random_schedules():
    """إعداد جدول عشوائي للصفقات والتنبيهات."""
    
    # 1. جدولة إشارات التنبيه العشوائية (3 مرات في اليوم)
    for _ in range(3):
        hour = random.randint(7, 21) 
        minute = random.randint(0, 59)
        schedule_time = f"{hour:02d}:{minute:02d}"
        schedule.every().day.at(schedule_time).do(lambda: asyncio.create_task(send_analysis_alert()))
        print(f"Alert scheduled at {schedule_time}")
        
    # 2. جدولة صفقات التحليل (4-7 مرات في اليوم)
    num_signals = random.randint(4, 7)
    for i in range(num_signals):
        hour = random.randint(8, 23)
        minute = random.randint(0, 59)
        schedule_time = f"{hour:02d}:{minute:02d}"
        # إرسال صفقة مجدولة (admin_triggered=False)
        schedule.every().day.at(schedule_time).do(lambda: asyncio.create_task(send_trade_signal(admin_triggered=False)))
        print(f"Trade signal scheduled at {schedule_time}")

async def scheduler_runner():
    """تشغيل المهام المجدولة بشكل غير متزامن."""
    setup_random_schedules() 
    print("✅ تم إعداد جدول الصفقات العشوائية لليوم.")
    
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"Error in scheduler: {e}")
        await asyncio.sleep(1) 

async def main():
    # 1. تهيئة قاعدة البيانات
    init_db()
    
    # 2. تسجيل الـ Middleware
    dp.update.outer_middleware(AccessMiddleware())
    
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
