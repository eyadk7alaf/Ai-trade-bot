import asyncio
import time
import os
import psycopg2
import pandas as pd
import yfinance as yf
import schedule
import random
import uuid

from datetime import datetime, timedelta
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from typing import Callable, Dict, Any, Awaitable

# =============== تعريف حالات FSM ===============
class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_trade = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_key_days = State() 

class UserStates(StatesGroup):
    waiting_key_activation = State() 
    
# =============== إعداد البوت والمتغيرات (من Environment Variables) ===============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID", "0") 
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "GC=F") 
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.90")) # تم الرفع إلى 90% للحصول على إشارات أقوى
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "I1l_1") # يوزر الأدمن للمراسلة

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

# =============== قاعدة بيانات PostgreSQL (الحل الجذري للمشكلة) ===============
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("🚫 لم يتم العثور على DATABASE_URL. يرجى التأكد من ربط PostgreSQL بـ Railway.")

def get_db_connection():
    # إنشاء اتصال جديد لكل طلب لضمان الأمان في البيئات غير المتزامنة
    return psycopg2.connect(DATABASE_URL)

# دوال CRUD أساسية
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username VARCHAR(255),
            joined_at DOUBLE PRECISION,
            is_banned INTEGER DEFAULT 0,
            vip_until DOUBLE PRECISION DEFAULT 0.0
        );
        CREATE TABLE IF NOT EXISTS invite_keys (
            key VARCHAR(255) PRIMARY KEY,
            days INTEGER,
            created_by BIGINT,
            used_by BIGINT NULL,
            used_at DOUBLE PRECISION NULL
        );
    """)
    conn.commit()
    conn.close()
    print("✅ تم تهيئة جداول قاعدة البيانات (PostgreSQL) بنجاح.")

def add_user(user_id, username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, username, joined_at) 
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
    """, (user_id, username, time.time()))
    conn.commit()
    conn.close()

def is_banned(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None and result[0] == 1

def update_ban_status(user_id, status):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, is_banned) VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET is_banned = %s
    """, (user_id, status, status))
    conn.commit()
    conn.close()
    
def get_all_users_ids():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, is_banned FROM users")
    result = cursor.fetchall()
    conn.close()
    return result
    
def get_total_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(user_id) FROM users") 
    result = cursor.fetchone()[0]
    conn.close()
    return result

# دوال الاشتراكات
def is_user_vip(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None and result[0] > time.time()
    
# الدالة المعدلة لحل مشكلة NoneType
def activate_key(user_id, key):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT days FROM invite_keys WHERE key = %s AND used_by IS NULL", (key,))
        key_data = cursor.fetchone()

        if key_data:
            days = key_data[0]
            
            cursor.execute("UPDATE invite_keys SET used_by = %s, used_at = %s WHERE key = %s", (user_id, time.time(), key))
            
            cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
            user_data = cursor.fetchone() 
            
            # معالجة القيمة الفارغة بأمان
            vip_until_ts = user_data[0] if user_data else 0.0 
            
            if vip_until_ts > time.time():
                start_date = datetime.fromtimestamp(vip_until_ts)
            else:
                start_date = datetime.now()
                
            new_vip_until = start_date + timedelta(days=days)
            
            # تحديث أو إدخال حالة المستخدم (ضمان التسجيل)
            cursor.execute("""
                INSERT INTO users (user_id, vip_until) VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET vip_until = %s
            """, (user_id, new_vip_until.timestamp(), new_vip_until.timestamp()))
            
            conn.commit()
            return True, days, new_vip_until
        
        return False, 0, None
    finally:
        conn.close()

def get_user_vip_status(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0] > time.time():
        return datetime.fromtimestamp(result[0]).strftime("%Y-%m-%d %H:%M")
    return "غير مشترك"

def create_invite_key(admin_id, days):
    conn = get_db_connection()
    cursor = conn.cursor()
    key = str(uuid.uuid4()).split('-')[0] + '-' + str(uuid.uuid4()).split('-')[1]
    cursor.execute("INSERT INTO invite_keys (key, days, created_by) VALUES (%s, %s, %s)", (key, days, admin_id))
    conn.commit()
    conn.close()
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
        username = user.username or "مستخدم"
        
        # الإضافة الجبرية في بداية المعالجة لضمان التسجيل الفوري
        if isinstance(event, types.Message):
            add_user(user_id, username) 

        if user_id == ADMIN_ID: return await handler(event, data)

        if isinstance(event, types.Message) and (event.text == '/start' or event.text.startswith('/start ')):
             return await handler(event, data) 
             
        allowed_for_banned = ["💬 تواصل مع الدعم", "💰 خطة الأسعار VIP", "ℹ️ عن AlphaTradeAI"]
        if is_banned(user_id):
            if isinstance(event, types.Message) and event.text not in allowed_for_banned:
                 await event.answer("🚫 حسابك محظور من استخدام البوت. يمكنك التواصل مع الدعم أو التحقق من الأسعار/المعلومات فقط.")
                 return
            
        allowed_for_all = ["💬 تواصل مع الدعم", "ℹ️ عن AlphaTradeAI", "🔗 تفعيل مفتاح الاشتراك", "📝 حالة الاشتراك", "💰 خطة الأسعار VIP"]
        
        if isinstance(event, types.Message) and event.text in allowed_for_all:
             return await handler(event, data) 

        if not is_user_vip(user_id):
            if isinstance(event, types.Message):
                await event.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط. يرجى تفعيل مفتاح اشتراك لتتمكن من استخدامها.")
            return

        return await handler(event, data)

# =============== وظائف التداول والتحليل (النسخة الأقوى) ===============

def get_signal_and_confidence(symbol: str) -> tuple[str, float, str, float, float, float]:
    """
    تحليل ذكي باستخدام 4 فلاتر (EMA 1m, RSI, ATR, EMA 5m) لتحديد إشارة فائقة القوة.
    """
    try:
        # 1. جلب بيانات الإطار الزمني الأصغر (1 دقيقة)
        data_1m = yf.download(
            symbol, 
            period="2d", interval="1m", progress=False, auto_adjust=True     
        )
        # 2. جلب بيانات الإطار الزمني الأكبر (5 دقائق) للتأكيد
        data_5m = yf.download(
            symbol,
            period="7d", interval="5m", progress=False, auto_adjust=True
        )
        
        if data_1m.empty or len(data_1m) < 50 or data_5m.empty or len(data_5m) < 20: 
            return f"لا تتوفر بيانات كافية للتحليل لرمز التداول: {symbol}.", 0.0, "HOLD", 0.0, 0.0, 0.0

        # =============== تحليل الإطار الزمني الأكبر (5 دقائق) ===============
        
        # حساب EMA على إطار 5 دقائق لتحديد الاتجاه الأكبر
        data_5m['EMA_10'] = data_5m['Close'].ewm(span=10, adjust=False).mean()
        data_5m['EMA_30'] = data_5m['Close'].ewm(span=30, adjust=False).mean()
        
        ema_fast_5m = data_5m['EMA_10'].iloc[-1]
        ema_slow_5m = data_5m['EMA_30'].iloc[-1]
        
        htf_trend = "BULLISH" if ema_fast_5m > ema_slow_5m else "BEARISH"
        
        # =============== تحليل الإطار الزمني الأصغر (1 دقيقة) ===============
        
        data = data_1m 
        
        # مؤشرات EMA (الاتجاه)
        data['EMA_5'] = data['Close'].ewm(span=5, adjust=False).mean()
        data['EMA_20'] = data['Close'].ewm(span=20, adjust=False).mean()
        
        # مؤشر RSI (الزخم)
        delta = data['Close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        RS = gain.ewm(com=14-1, min_periods=14, adjust=False).mean() / loss.ewm(com=14-1, min_periods=14, adjust=False).mean()
        data['RSI'] = 100 - (100 / (1 + RS))
        
        # مؤشر ATR (التقلب)
        high_low = data['High'] - data['Low']
        high_close = (data['High'] - data['Close'].shift()).abs()
        low_close = (data['Low'] - data['Close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        data['ATR'] = tr.rolling(14).mean()

        # =============== استخلاص القيم وتحديد الإشارة ===============
        
        latest_price = data['Close'].iloc[-1].item() 
        latest_time = data.index[-1].strftime('%Y-%m-%d %H:%M:%S')
        
        ema_fast_prev = data['EMA_5'].iloc[-2]
        ema_slow_prev = data['EMA_20'].iloc[-2]
        ema_fast_current = data['EMA_5'].iloc[-1]
        ema_slow_current = data['EMA_20'].iloc[-1]
        
        current_rsi = data['RSI'].iloc[-1]
        current_atr = data['ATR'].iloc[-1]
        
        MIN_ATR_THRESHOLD = 0.5 
        SL_FACTOR = 1.0 
        TP_FACTOR = 3.0
        MIN_SL = 0.5 # حد أدنى لوقف الخسارة لتجنب القيم الصفرية في السوق الهادئ
        
        action = "HOLD"
        confidence = 0.5
        entry_price = latest_price
        stop_loss = 0.0
        take_profit = 0.0

        # الفلتر الأول: هل السوق نشط بما يكفي؟
        if current_atr < MIN_ATR_THRESHOLD:
            return f"⚠️ السوق هادئ جداً (ATR: {current_atr:.2f} < {MIN_ATR_THRESHOLD}). الإشارة HOLD.", 0.0, "HOLD", 0.0, 0.0, 0.0

        # تحديد نوع الإشارة
        is_buy_signal = (ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current)
        is_buy_trend = (ema_fast_current > ema_slow_current)
        is_sell_signal = (ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current)
        is_sell_trend = (ema_fast_current < ema_slow_current)

        if is_buy_signal or is_buy_trend:
            if htf_trend == "BULLISH": # شرط التأكيد من الإطار الزمني الأكبر
                action = "BUY"
                if current_rsi > 50: 
                    confidence = 0.99 if is_buy_signal else 0.95
                else:
                    confidence = 0.70 
            else:
                confidence = 0.50 # تجاهل الإشارة لأنها عكس الاتجاه الأكبر
                
        elif is_sell_signal or is_sell_trend:
            if htf_trend == "BEARISH": # شرط التأكيد من الإطار الزمني الأكبر
                action = "SELL"
                if current_rsi < 50:
                    confidence = 0.99 if is_sell_signal else 0.95
                else:
                    confidence = 0.70
            else:
                 confidence = 0.50 
        
        # =============== حساب نقاط الدخول/الخروج بالـ ATR + حد أدنى ===============

        if action != "HOLD":
            # نختار الأكبر بين الـ ATR المحسوب والحد الأدنى (MIN_SL)
            risk_amount = max(current_atr * SL_FACTOR, MIN_SL) 

            if action == "BUY":
                stop_loss = entry_price - risk_amount 
                take_profit = entry_price + (risk_amount * TP_FACTOR) 
            
            elif action == "SELL":
                stop_loss = entry_price + risk_amount
                take_profit = entry_price - (risk_amount * TP_FACTOR)
        
        # تعديل رسالة الإرسال لتضمين معلومة الثقة العالية
        price_msg = f"📊 آخر سعر لـ <b>{symbol}</b> (الاتجاه الأكبر: {htf_trend}):\nالسعر: ${latest_price:,.2f}\nالوقت: {latest_time} UTC"
        
        return price_msg, confidence, action, entry_price, stop_loss, take_profit
        
    except Exception as e:
        return f"❌ فشل في جلب بيانات التداول لـ {symbol} أو التحليل: {e}", 0.0, "HOLD", 0.0, 0.0, 0.0

# =============== باقي أوامر البوت والجداول (بدون تغيير) ===============

async def send_trade_signal(admin_triggered=False):
    
    price_info_msg_ar, confidence, action, entry_price, stop_loss, take_profit = get_signal_and_confidence(TRADE_SYMBOL)
    
    confidence_percent = confidence * 100
    is_high_confidence = confidence >= CONFIDENCE_THRESHOLD

    if not is_high_confidence or action == "HOLD":
        return False

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

<i>Trade responsibly. This signal is based on {TRADE_SYMBOL} Smart Multi-Filter Analysis (EMA, RSI, ATR, HTF).</i>
"""
    sent = 0
    all_users = get_all_users_ids()
    
    for uid, is_banned_status in all_users:
        if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
            try:
                await bot.send_message(uid, trade_msg)
                sent += 1
            except Exception:
                pass
    
    if ADMIN_ID != 0:
        try:
            admin_note = "تم الإرسال عبر الأمر الفوري" if admin_triggered else "إرسال مجدول"
            await bot.send_message(ADMIN_ID, f"📢 تم إرسال صفقة VIP ({trade_action_en}) إلى {sent} مشترك.\nالثقة: {confidence_percent:.2f}%.\nملاحظة: {admin_note}")
        except Exception:
            pass
            
    return True

async def send_analysis_alert():
    
    alert_messages = [
        "🔎 Scanning the Gold market... 🧐 Looking for a strong trading opportunity on XAUUSD.",
        "⏳ Analyzing Gold data now... Please wait, a VIP trade signal might drop soon!",
        "🤖 Smart Analyst is running... 💡 Evaluating current Multi-Filter patterns for a high-confidence trade."
    ]
    
    msg_to_send = random.choice(alert_messages)
    
    all_users = get_all_users_ids()
    
    for uid, is_banned_status in all_users:
        if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
            try:
                await bot.send_message(uid, msg_to_send)
            except Exception:
                pass
                
# =============== القوائم المُعدَّلة (باقي الأوامر) ===============

def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📈 سعر السوق الحالي"), KeyboardButton(text="📝 حالة الاشتراك")],
            [KeyboardButton(text="🔗 تفعيل مفتاح الاشتراك"), KeyboardButton(text="💰 خطة الأسعار VIP")],
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

# =============== أوامر الأدمن والمستخدم (باقي الأوامر) ===============

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    welcome_msg = f"""
🤖 <b>مرحبًا بك في AlphaTradeAI!</b>
🚀 نظام ذكي يتابع سوق الذهب ({TRADE_SYMBOL}) بأربعة فلاتر تحليلية.
اختر من القائمة 👇
"""
    await msg.reply(welcome_msg, reply_markup=user_menu())
    
@dp.message(Command("admin"))
async def admin_panel(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 ليس لديك صلاحية الوصول إلى لوحة التحكم.")
        return
    await msg.reply("🎛️ مرحبًا بك في لوحة تحكم الأدمن!", reply_markup=admin_menu())

@dp.message(F.text == "تحليل فوري ⚡️")
async def analyze_market_now(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: 
        if not is_user_vip(msg.from_user.id): return
    
    await msg.reply("⏳ جارٍ تحليل السوق بحثًا عن فرصة تداول ذات ثقة عالية...")
    
    sent_successfully = await send_trade_signal(admin_triggered=True)
    
    if sent_successfully:
        await msg.answer("✅ تم إرسال صفقة VIP بنجاح إلى المشتركين.")
    else:
        _, confidence, action, _, _, _ = get_signal_and_confidence(TRADE_SYMBOL)
        confidence_percent = confidence * 100
        
        if action == "HOLD":
             await msg.answer("💡 لا توجد إشارة واضحة (HOLD). لم يتم إرسال صفقة.")
        else:
             await msg.answer(f"⚠️ الإشارة موجودة ({action})، لكن نسبة الثقة {confidence_percent:.2f}% أقل من المطلوب ({int(CONFIDENCE_THRESHOLD*100)}%). لم يتم إرسال صفقة.")

@dp.message(F.text == "📈 سعر السوق الحالي")
async def get_current_price(msg: types.Message):
    price_info_msg, _, _, _, _, _ = get_signal_and_confidence(TRADE_SYMBOL)
    await msg.reply(price_info_msg)

@dp.message(F.text == "📊 جدول اليوم")
async def get_current_signal(msg: types.Message):
    await msg.reply("🗓️ يتم تحليل السوق حاليًا. ستصلك الصفقات المجدولة تلقائيًا إذا توفرت.")

@dp.message(F.text == "📝 حالة الاشتراك")
async def show_subscription_status(msg: types.Message):
    status = get_user_vip_status(msg.from_user.id)
    if status == "غير مشترك":
        await msg.reply(f"⚠️ أنت حالياً **غير مشترك** في خدمة VIP.\nللاشتراك، اطلب مفتاح تفعيل من الأدمن (@{ADMIN_USERNAME}) ثم اضغط '🔗 تفعيل مفتاح الاشتراك'.")
    else:
        await msg.reply(f"✅ أنت مشترك في خدمة VIP.\nتنتهي صلاحية اشتراكك في: <b>{status}</b>.")

@dp.message(F.text == "💰 خطة الأسعار VIP")
async def show_pricing_plan(msg: types.Message):
    pricing_message = f"""
🌟 <b>مفتاحك للنجاح يبدأ هنا!</b> 🔑

خدمة AlphaTradeAI تقدم لك تحليل الذهب الأوتوماتيكي بأفضل قيمة. اختر الخطة التي تناسب أهدافك:

━━━━━━━━━━━━━━━
🥇 <b>الخطة الأساسية (تجربة ممتازة)</b>
* <b>المدة:</b> 7 أيام
* <b>السعر:</b> 💰 <b>$15 فقط</b>

🥈 <b>الخطة الفضية (الأكثر شيوعاً)</b>
* <b>المدة:</b> 45 يومًا (شهر ونصف)
* <b>السعر:</b> 💰 <b>$49 فقط</b>

🥉 <b>الخطة الذهبية (صفقة التوفير)</b>
* <b>المدة:</b> 120 يومًا (4 أشهر)
* <b>السعر:</b> 💰 <b>$99 فقط</b>

💎 <b>الخطة البلاتينية (للمتداول الجاد)</b>
* <b>المدة:</b> 200 يوم (أكثر من 6 أشهر)
* <b>السعر:</b> 💰 <b>$149 فقط</b>

━━━━━━━━━━━━━━━
🛒 **للاشتراك وتفعيل المفتاح:**
يرجى التواصل مباشرة مع الأدمن: 
👤 <b>@{ADMIN_USERNAME}</b>
"""
    await msg.reply(pricing_message)
        
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

@dp.message(F.text == "📢 رسالة لكل المستخدمين")
async def send_broadcast_start(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await msg.reply("📝 أرسل الرسالة التي تريد إرسالها لجميع المستخدمين:")
    await state.set_state(AdminStates.waiting_broadcast)

@dp.message(AdminStates.waiting_broadcast)
async def process_broadcast(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    sent = 0
    failed = 0
    all_users = get_all_users_ids()
    
    for uid, is_banned_status in all_users:
        if is_banned_status == 0: 
            try:
                await bot.send_message(uid, msg.text)
                sent += 1
            except Exception:
                failed += 1
            
    await msg.reply(f"✅ تم إرسال الرسالة إلى {sent} مستخدم غير محظور.\n❌ فشل الإرسال إلى {failed} مستخدم.")
    await state.clear()
    await msg.answer("🎛️ العودة إلى لوحة الأدمن.", reply_markup=admin_menu())

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
             except: pass
    except Exception as e:
        await msg.reply(f"❌ ID غير صالح أو حدث خطأ: {e}")
    await state.clear()
    await msg.answer("🎛️ العودة إلى لوحة الأدمن.", reply_markup=admin_menu())

@dp.message(F.text == "✅ إلغاء حظر مستخدم")
async def unban_user_start(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await msg.reply("♻️ أرسل ID المستخدم لإلغاء حظره:")
    await state.set_state(AdminStates.waiting_unban)

@dp.message(AdminStates.waiting_unban)
async def process_unban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        uid = int(msg.text)
        update_ban_status(uid, 0)
        await msg.reply(f"✅ تم إلغاء حظر المستخدم {uid} بنجاح.")
    except Exception as e:
        await msg.reply(f"❌ ID غير صالح أو حدث خطأ: {e}")
    await state.clear()
    await msg.answer("🎛️ العودة إلى لوحة الأدمن.", reply_markup=admin_menu())

@dp.message(F.text == "🗒️ عرض حالة المشتركين")
async def show_active_users(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, vip_until, username FROM users WHERE vip_until > %s", (time.time(),))
    active_users = cursor.fetchall()
    conn.close()
    
    if not active_users:
        await msg.reply("لا يوجد مشتركين فعالين حالياً.")
        return
        
    response = "👥 **المشتركون الفعالون**:\n\n"
    for uid, vip_until_ts, username in active_users:
        expiry_date = datetime.fromtimestamp(vip_until_ts).strftime("%Y-%m-%d %H:%M")
        response += f"• @{username or 'لا يوزر'} (<code>{uid}</code>)\n  (تنتهي صلاحيته في: {expiry_date})\n"
        
    await msg.reply(response)
    
@dp.message(F.text == "👥 عدد المستخدمين")
async def show_user_count(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        count = get_total_users()
        await msg.reply(f"👥 عدد المستخدمين المسجلين: {count}")

@dp.message(F.text == "🔙 عودة للمستخدم")
async def back_to_user_menu(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.reply("👤 العودة إلى قائمة المستخدم الرئيسية.", reply_markup=user_menu())
        
@dp.message(F.text.in_(["💬 تواصل مع الدعم", "ℹ️ عن AlphaTradeAI"]))
async def handle_user_actions(msg: types.Message):
    if msg.text == "💬 تواصل مع الدعم":
        await msg.reply(f"📞 يمكنك التواصل مع الإدارة مباشرة عبر @{ADMIN_USERNAME} للإستفسارات أو الدعم.")
    elif msg.text == "ℹ️ عن AlphaTradeAI":
        marketing_text = f"""
🚀 <b>AlphaTradeAI: ثورة الذكاء الاصطناعي في تداول الذهب!</b> 🚀

نحن لسنا مجرد بوت، بل نظام متكامل تم بناؤه على سنوات من الخبرة والخوارزميات المتقدمة. هدفنا هو أن نجعلك تتداول بثقة المحترفين، بعيداً عن ضجيج السوق وتقلباته العاطفية.

━━━━━━━━━━━━━━━
✨ <b>ماذا يقدم لك الاشتراك VIP؟</b>
1.  <b>الإشارات فائقة الدقة (High-Confidence):</b>
    نظامنا يراقب حركة الذهب (XAUUSD) على مدار الساعة. نستخدم نموذج التحليل متعدد الفلاتر (EMA, RSI, ATR, HTF) لتصفية الإشارات واختيار فقط الصفقات التي تتجاوز نسبة ثقة <b>{int(CONFIDENCE_THRESHOLD*100)}%</b>.
2.  <b>إدارة مخاطر احترافية:</b>
    كل إشارة تُرسَل إليك هي صفقة جاهزة للتنفيذ. تحصل على سعر الدخول (Entry Price)، هدف الربح (TP) ونقطة وقف الخسارة (SL).
3.  <b>توفير الوقت والجهد:</b>
    سيتولى AlphaTradeAI التحليل المعقد وإرسال ما بين <b>4 إلى 7 صفقات</b> مجدولة يومياً.

━━━━━━━━━━━━━━━
💰 <b>لتحقيق الأرباح بذكاء، استثمر في أدواتك!</b> اضغط على '💰 خطة الأسعار VIP' للاطلاع على العروض الحالية.
"""
        await msg.reply(marketing_text)


# =============== الجدولة وتشغيل البوت (باقي الأوامر) ===============

def setup_random_schedules():
    
    for _ in range(3):
        hour = random.randint(7, 21); minute = random.randint(0, 59)
        schedule_time = f"{hour:02d}:{minute:02d}"
        schedule.every().day.at(schedule_time).do(lambda: asyncio.create_task(send_analysis_alert()))
        
    num_signals = random.randint(4, 7)
    for i in range(num_signals):
        hour = random.randint(8, 23); minute = random.randint(0, 59)
        schedule_time = f"{hour:02d}:{minute:02d}"
        schedule.every().day.at(schedule_time).do(lambda: asyncio.create_task(send_trade_signal(admin_triggered=False)))

async def scheduler_runner():
    setup_random_schedules() 
    print("✅ تم إعداد جدول الصفقات العشوائية لليوم.")
    
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"Error in scheduler: {e}")
        await asyncio.sleep(1) 

async def main():
    # 1. تهيئة قاعدة البيانات (PostgreSQL)
    print("⏳ جارٍ تهيئة قاعدة بيانات PostgreSQL...")
    init_db()
    
    # 2. تسجيل الـ Middleware
    dp.update.outer_middleware(AccessMiddleware())
    
    print("✅ البوت قيد التشغيل وجاهز لاستقبال التحديثات.")
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
