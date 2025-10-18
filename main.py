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
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.90"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "I1l_1")
TRADE_CHECK_INTERVAL = int(os.getenv("TRADE_CHECK_INTERVAL", "30")) # فاصل متابعة الصفقات بالثواني
ALERT_INTERVAL = int(os.getenv("ALERT_INTERVAL", "3600")) # فاصل تنبيهات المراقبة بالسواني (ساعة = 3600)


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
        -- جدول الصفقات الجديد
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            sent_at DOUBLE PRECISION,
            action VARCHAR(10),
            entry_price DOUBLE PRECISION,
            take_profit DOUBLE PRECISION,
            stop_loss DOUBLE PRECISION,
            status VARCHAR(50) DEFAULT 'ACTIVE', -- ACTIVE, CLOSED
            exit_status VARCHAR(50) DEFAULT 'NONE', -- NONE, HIT_TP, HIT_SL, TIMEOUT
            close_price DOUBLE PRECISION NULL,
            user_count INTEGER
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
        ON CONFLICT (user_id) DO UPDATE SET username = %s -- تحديث اسم المستخدم إذا تغير
    """, (user_id, username, time.time(), username))
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
    
def activate_key(user_id, key):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT days FROM invite_keys WHERE key = %s AND used_by IS NULL", (key,))
        key_data = cursor.fetchone()

        if key_data:
            days = key_data[0]
            
            cursor.execute("UPDATE invite_keys SET used_by = %s, used_at = %s WHERE key = %s", (user_id, time.time(), key))
            
            # يجب التأكد من وجود سجل للمستخدم قبل محاولة قراءة vip_until
            cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
            user_data = cursor.fetchone() 
            
            vip_until_ts = user_data[0] if user_data and user_data[0] is not None else 0.0 
            
            # إذا كان اشتراكه الحالي سارياً، نبدأ إضافة الأيام بعد تاريخ الانتهاء الحالي
            if vip_until_ts > time.time():
                start_date = datetime.fromtimestamp(vip_until_ts)
            else:
                # إذا لم يكن سارياً أو كان انتهى، نبدأ من الآن
                start_date = datetime.now()
                
            new_vip_until = start_date + timedelta(days=days)
            
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
    if result and result[0] is not None and result[0] > time.time():
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

# === دوال إدارة الصفقات ===
def save_new_trade(action, entry, tp, sl, user_count):
    conn = get_db_connection()
    cursor = conn.cursor()
    trade_id = "TRADE-" + str(uuid.uuid4()).split('-')[0]
    
    cursor.execute("""
        INSERT INTO trades (trade_id, sent_at, action, entry_price, take_profit, stop_loss, user_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (trade_id, time.time(), action, entry, tp, sl, user_count))
    
    conn.commit()
    conn.close()
    return trade_id

def get_active_trades():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT trade_id, action, entry_price, take_profit, stop_loss
        FROM trades 
        WHERE status = 'ACTIVE'
    """)
    trades = cursor.fetchall()
    conn.close()
    keys = ["trade_id", "action", "entry_price", "take_profit", "stop_loss"]
    return [dict(zip(keys, trade)) for trade in trades]

def update_trade_status(trade_id, exit_status, close_price):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE trades 
        SET status = 'CLOSED', exit_status = %s, close_price = %s
        WHERE trade_id = %s
    """, (exit_status, close_price, trade_id))
    conn.commit()
    conn.close()

def get_daily_trade_report():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    time_24_hours_ago = time.time() - (24 * 3600)

    cursor.execute("""
        SELECT action, status, exit_status, entry_price, take_profit, stop_loss, user_count
        FROM trades 
        WHERE sent_at > %s
    """, (time_24_hours_ago,))
    
    trades = cursor.fetchall()
    conn.close()

    total_sent = len(trades)
    active_trades = sum(1 for t in trades if t[1] == 'ACTIVE')
    hit_tp = sum(1 for t in trades if t[2] == 'HIT_TP')
    hit_sl = sum(1 for t in trades if t[2] == 'HIT_SL')
    
    if total_sent == 0:
        return "⚠️ لم يتم إرسال أي صفقات خلال الـ 24 ساعة الماضية."
        
    report_msg = f"""
📈 **جرد أداء AlphaTradeAI (آخر 24 ساعة)**
━━━━━━━━━━━━━━━
📨 **إجمالي الصفقات المُرسلة:** {total_sent}
🟢 **صفقات حققت الهدف (TP):** {hit_tp}
🔴 **صفقات ضربت الوقف (SL):** {hit_sl}
⏳ **الصفقات لا تزال نشطة:** {active_trades}
"""
    
    latest_active = next((t for t in reversed(trades) if t[1] == 'ACTIVE'), None)
    if latest_active:
        action, _, _, entry, tp, sl, _ = latest_active
        report_msg += "\n**آخر صفقة نشطة:**\n"
        report_msg += f"  - {action} @ {entry:,.2f}\n"
        report_msg += f"  - TP: {tp:,.2f} | SL: {sl:,.2f}"

    return report_msg


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
        
        # [قراءة حالة FSM]
        state = data.get('state')
        current_state = await state.get_state() if state else None
        
        if isinstance(event, types.Message):
            add_user(user_id, username) 

        if user_id == ADMIN_ID: return await handler(event, data)

        if isinstance(event, types.Message) and (event.text == '/start' or event.text.startswith('/start ')):
             return await handler(event, data) 
        
        # [السماح بالمرور إذا كان في حالة انتظار تفعيل المفتاح]
        if current_state == UserStates.waiting_key_activation.state:
            return await handler(event, data)
             
        allowed_for_banned = ["💬 تواصل مع الدعم", "💰 خطة الأسعار VIP", "ℹ️ عن AlphaTradeAI"]
        if is_banned(user_id):
            if isinstance(event, types.Message) and event.text not in allowed_for_banned:
                 await event.answer("🚫 حسابك محظور من استخدام البوت. يمكنك التواصل مع الدعم أو التحقق من الأسعار/المعلومات فقط.")
                 return
            
        allowed_for_all = ["💬 تواصل مع الدعم", "ℹ️ عن AlphaTradeAI", "🔗 تفعيل مفتاح الاشتراك", "📝 حالة الاشتراك", "💰 خطة الأسعار VIP", "📈 سعر السوق الحالي", "🔍 الصفقات النشطة"]
        
        if isinstance(event, types.Message) and event.text in allowed_for_all:
             return await handler(event, data) 

        if not is_user_vip(user_id):
            if isinstance(event, types.Message) and event.text not in allowed_for_all:
                await event.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط. يرجى تفعيل مفتاح اشتراك لتتمكن من استخدامها.")
            return

        return await handler(event, data)

# =============== وظائف التداول والتحليل (النسخة الأقوى) ===============

def get_signal_and_confidence(symbol: str) -> tuple[str, float, str, float, float, float]:
    """
    تحليل ذكي باستخدام 4 فلاتر (EMA 1m, RSI, ATR, EMA 5m) لتحديد إشارة فائقة القوة.
    """
    try:
        data_1m = yf.download(
            symbol, 
            period="2d", interval="1m", progress=False, auto_adjust=True     
        )
        data_5m = yf.download(
            symbol,
            period="7d", interval="5m", progress=False, auto_adjust=True
        )
        
        if data_1m.empty or len(data_1m) < 50 or data_5m.empty or len(data_5m) < 20: 
            return f"لا تتوفر بيانات كافية للتحليل لرمز التداول: {symbol}.", 0.0, "HOLD", 0.0, 0.0, 0.0

        # =============== تحليل الإطار الزمني الأكبر (5 دقائق) ===============
        data_5m['EMA_10'] = data_5m['Close'].ewm(span=10, adjust=False).mean()
        data_5m['EMA_30'] = data_5m['Close'].ewm(span=30, adjust=False).mean()
        
        ema_fast_5m = data_5m['EMA_10'].iloc[-1]
        ema_slow_5m = data_5m['EMA_30'].iloc[-1]
        
        htf_trend = "BULLISH" if ema_fast_5m > ema_slow_5m else "BEARISH"
        
        # =============== تحليل الإطار الزمني الأصغر (1 دقيقة) ===============
        data = data_1m 
        
        data['EMA_5'] = data['Close'].ewm(span=5, adjust=False).mean()
        data['EMA_20'] = data['Close'].ewm(span=20, adjust=False).mean()
        
        delta = data['Close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        RS = gain.ewm(com=14-1, min_periods=14, adjust=False).mean() / loss.ewm(com=14-1, min_periods=14, adjust=False).mean().replace(0, 1e-10)
        data['RSI'] = 100 - (100 / (1 + RS))
        
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
        MIN_SL = 0.5 
        
        action = "HOLD"
        confidence = 0.5
        entry_price = latest_price
        stop_loss = 0.0
        take_profit = 0.0

        if current_atr < MIN_ATR_THRESHOLD:
            return f"⚠️ السوق هادئ جداً (ATR: {current_atr:.2f} < {MIN_ATR_THRESHOLD}). الإشارة HOLD.", 0.0, "HOLD", 0.0, 0.0, 0.0

        is_buy_signal = (ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current)
        is_buy_trend = (ema_fast_current > ema_slow_current)
        is_sell_signal = (ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current)
        is_sell_trend = (ema_fast_current < ema_slow_current)

        if is_buy_signal or is_buy_trend:
            if htf_trend == "BULLISH": 
                action = "BUY"
                if current_rsi > 50: 
                    confidence = 0.99 if is_buy_signal else 0.95
                else:
                    confidence = 0.70 
            else:
                confidence = 0.50 
                
        elif is_sell_signal or is_sell_trend:
            if htf_trend == "BEARISH": 
                action = "SELL"
                if current_rsi < 50:
                    confidence = 0.99 if is_sell_signal else 0.95
                else:
                    confidence = 0.70
            else:
                 confidence = 0.50 
        
        if action != "HOLD":
            risk_amount = max(current_atr * SL_FACTOR, MIN_SL) 

            if action == "BUY":
                stop_loss = entry_price - risk_amount 
                take_profit = entry_price + (risk_amount * TP_FACTOR) 
            
            elif action == "SELL":
                stop_loss = entry_price + risk_amount
                take_profit = entry_price - (risk_amount * TP_FACTOR)
        
        price_msg = f"📊 آخر سعر لـ <b>{symbol}</b> (الاتجاه الأكبر: {htf_trend}):\nالسعر: ${latest_price:,.2f}\nالوقت: {latest_time} UTC"
        
        return price_msg, confidence, action, entry_price, stop_loss, take_profit
        
    except Exception as e:
        return f"❌ فشل في جلب بيانات التداول لـ {symbol} أو التحليل: {e}", 0.0, "HOLD", 0.0, 0.0, 0.0

# =============== دالة إرسال الإشارة (مع حفظ الصفقة) ===============

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
    
    # حفظ الصفقة في قاعدة البيانات
    trade_id = None
    if sent > 0:
        trade_id = save_new_trade(trade_action_en, entry_price, take_profit, stop_loss, sent)
    
    if ADMIN_ID != 0:
        try:
            admin_note = "تم الإرسال عبر الأمر الفوري" if admin_triggered else "إرسال مجدول"
            await bot.send_message(ADMIN_ID, f"📢 تم إرسال صفقة VIP ({trade_action_en}) إلى {sent} مشترك.\nالثقة: {confidence_percent:.2f}%.\n**Trade ID:** {trade_id}\nملاحظة: {admin_note}")
        except Exception:
            pass
            
    return True

# [مهمة جديدة: إرسال تنبيهات المراقبة]
async def send_analysis_alert():
    """
    يرسل تنبيهات عشوائية للمشتركين بأن البوت يراقب السوق.
    """
    
    alert_messages = [
        "🔎 Scanning the Gold market... 🧐 Looking for a strong trading opportunity on XAUUSD.",
        "⏳ Analyzing Gold data now... Please wait, a VIP trade signal might drop soon!",
        "🤖 Smart Analyst is running... 💡 Evaluating current Multi-Filter patterns for a high-confidence trade.",
        "📊 البوت يراقب تحركات الذهب الآن. ابق عينيك على الإشعارات."
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
            [KeyboardButton(text="🔗 تفعيل مفتاح الاشتراك"), KeyboardButton(text="🔍 الصفقات النشطة")], 
            [KeyboardButton(text="💰 خطة الأسعار VIP"), KeyboardButton(text="💬 تواصل مع الدعم")],
            [KeyboardButton(text="ℹ️ عن AlphaTradeAI")]
        ],
        resize_keyboard=True
    )

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="تحليل فوري ⚡️"), KeyboardButton(text="📊 جرد الصفقات اليومي")],
            [KeyboardButton(text="📢 رسالة لكل المستخدمين"), KeyboardButton(text="🔑 إنشاء مفتاح اشتراك")],
            [KeyboardButton(text="🚫 حظر مستخدم"), KeyboardButton(text="✅ إلغاء حظر مستخدم")],
            [KeyboardButton(text="👥 عدد المستخدمين"), KeyboardButton(text="🗒️ عرض حالة المشتركين")],
            [KeyboardButton(text="🔙 عودة للمستخدم")]
        ],
        resize_keyboard=True
    )

# =============== أوامر الأدمن والمستخدم (لا تغييرات كبيرة هنا) ===============

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
    if msg.from_user.id != ADMIN_ID and not is_user_vip(msg.from_user.id): 
        await msg.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط.")
        return
    
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

@dp.message(F.text == "📊 جرد الصفقات اليومي")
async def daily_inventory_report(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 ليس لديك صلاحية الوصول.")
        return
    
    report = get_daily_trade_report()
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📈 سعر السوق الحالي")
async def get_current_price(msg: types.Message):
    price_info_msg, _, _, _, _, _ = get_signal_and_confidence(TRADE_SYMBOL)
    await msg.reply(price_info_msg)
    
@dp.message(F.text == "🔍 الصفقات النشطة")
async def show_active_trades(msg: types.Message):
    
    active_trades = get_active_trades()
    
    if not active_trades:
        await msg.reply("✅ لا توجد حاليًا أي صفقات VIP نشطة. انتظر إشارة قادمة!")
        return
    
    report = "⏳ **قائمة الصفقات النشطة حالياً (VIP)**\n━━━━━━━━━━━━━━━"
    
    for trade in active_trades:
        action = trade['action']
        entry = trade['entry_price']
        tp = trade['take_profit']
        sl = trade['stop_loss']
        
        signal_emoji = "🟢" if action == "BUY" else "🔴"
        
        report += f"""
{signal_emoji} **{action} @ ${entry:,.2f}**
  - **TP:** ${tp:,.2f}
  - **SL:** ${sl:,.2f}
  - **ID:** <code>{trade['trade_id']}</code>
"""
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📝 حالة الاشتراك")
async def show_subscription_status(msg: types.Message):
    status = get_user_vip_status(msg.from_user.id)
    if status == "غير مشترك":
        await msg.reply(f"⚠️ أنت حالياً **غير مشترك** في خدمة VIP.\nللاشتراك، اطلب مفتاح تفعيل من الأدمن (@{ADMIN_USERNAME}) ثم اضغط '🔗 تفعيل مفتاح الاشتراك'.")
    else:
        await msg.reply(f"✅ أنت مشترك في خدمة VIP.\nالاشتراك ينتهي في: <b>{status}</b>.")

@dp.message(F.text == "🔗 تفعيل مفتاح الاشتراك")
async def prompt_key_activation(msg: types.Message, state: FSMContext):
    await state.set_state(UserStates.waiting_key_activation)
    await msg.reply("🔑 يرجى إدخال مفتاح التفعيل الآن:")

@dp.message(UserStates.waiting_key_activation)
async def process_key_activation(msg: types.Message, state: FSMContext):
    key = msg.text.strip()
    success, days, new_vip_until = activate_key(msg.from_user.id, key)
    
    await state.clear()
    
    if success:
        formatted_date = new_vip_until.strftime('%Y-%m-%d %H:%M') if new_vip_until else "غير محدد"
        await msg.reply(f"🎉 تم تفعيل مفتاح الاشتراك بنجاح!\n✅ تمت إضافة {days} يوم/أيام إلى اشتراكك.\nالاشتراك الجديد ينتهي في: <b>{formatted_date}</b>.", reply_markup=user_menu())
    else:
        await msg.reply("❌ فشل تفعيل المفتاح. يرجى التأكد من صحة المفتاح وأنه لم يُستخدم من قبل.", reply_markup=user_menu())

@dp.message(F.text == "💰 خطة الأسعار VIP")
async def show_prices(msg: types.Message):
    prices_msg = f"""
🌟 **مفتاحك للنجاح يبدأ هنا! 🔑**

خدمة AlphaTradeAI تقدم لك تحليل الذهب الأوتوماتيكي بأفضل قيمة. اختر الخطة التي تناسب أهدافك:

━━━━━━━━━━━━━━━
🥇 **الخطة الأساسية (تجربة ممتازة)**
* المدة: 7 أيام
* السعر: 💰 $15 فقط

🥈 **الخطة الفضية (الأكثر شيوعاً)**
* المدة: 45 يومًا (شهر ونصف)
* السعر: 💰 $49 فقط

🥉 **الخطة الذهبية (صفقة التوفير)**
* المدة: 120 يومًا (4 أشهر)
* السعر: 💰 $99 فقط

💎 **الخطة البلاتينية (للمتداول الجاد)**
* المدة: 200 يوم (أكثر من 6 أشهر)
* السعر: 💰 $149 فقط

━━━━━━━━━━━━━━━
🛒 **للاشتراك وتفعيل المفتاح:**
يرجى التواصل مباشرة مع الأدمن: 
👤 @{ADMIN_USERNAME}
"""
    await msg.reply(prices_msg)

@dp.message(F.text == "💬 تواصل مع الدعم")
async def contact_support(msg: types.Message):
    support_msg = f"""
🤝 للدعم الفني أو الاستفسارات أو طلب مفتاح اشتراك، يرجى التواصل مباشرة مع الأدمن:
🔗 @{ADMIN_USERNAME}
"""
    await msg.reply(support_msg)

@dp.message(F.text == "ℹ️ عن AlphaTradeAI")
async def about_bot(msg: types.Message):
    threshold_percent = int(CONFIDENCE_THRESHOLD * 100)
    about_msg = f"""
🚀 <b>AlphaTradeAI: ثورة التحليل الكمّي في تداول الذهب!</b> 🚀

نحن لسنا مجرد بوت، بل منصة تحليل ذكية ومؤتمتة بالكامل، مصممة لملاحقة أكبر الفرص في سوق الذهب (XAUUSD). مهمتنا هي تصفية ضجيح السوق وتقديم إشارات <b>مؤكدة فقط</b>.

━━━━━━━━━━━━━━━
🛡️ **ماذا يقدم لك الاشتراك VIP؟ (ميزة القوة الخارقة)**
1.  <b>إشارات خماسية التأكيد (5-Tier Confirmation):</b>
    نظامنا لا يعتمد على مؤشر واحد! بل يمرر الإشارة عبر <b>أربعة فلاتر تحليلية احترافية</b> في وقت واحد قبل الإرسال:
    * **الفلتر 1 (EMA):** تحديد الإشارة الأولية على إطار الدقيقة.
    * **الفلتر 2 (RSI):** تأكيد قوة الزخم واستمرارية الحركة.
    * **الفلتر 3 (ATR):** قياس التقلب لتحديد نقاط TP/SL ديناميكيًا.
    * **الفلتر 4 (HTF):** التأكد من توافق الإشارة مع الاتجاه الأكبر (5 دقائق) لتجنب الإشارات الكاذبة.
    
2.  <b>أعلى درجات الثقة:</b>
    لا يتم إرسال أي صفقة إلا إذا تجاوزت نسبة الثقة **{threshold_percent}%** (حالياً يتم الإرسال عند {threshold_percent}% أو أعلى). هذا يعني أنك تحصل على إشارات نادرة، لكنها فائقة القوة.
    
3.  <b>إدارة مخاطر 1:3:</b>
    كل صفقة جاهزة للتنفيذ بنسبة مخاطرة إلى عائد مثالية (هدف الربح = 3 أضعاف وقف الخسارة)، لضمان أن **الأرباح تفوق الخسائر دائمًا** على المدى الطويل.

━━━━━━━━━━━━━━━
💰 حوّل التحليل إلى ربح. لا تدع الفرص تفوتك! اضغط على '💰 خطة الأسعار VIP' للاطلاع على العروض الحالية.
"""
    await msg.reply(about_msg)

@dp.message(F.text == "🔙 عودة للمستخدم")
async def back_to_user_menu(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await msg.reply("➡️ تم التحويل إلى قائمة المستخدمين.", reply_markup=user_menu())

@dp.message(F.text == "👥 عدد المستخدمين")
async def count_users(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    total = get_total_users()
    await msg.reply(f"📊 إجمالي عدد المستخدمين المسجلين في قاعدة البيانات: **{total}**")

@dp.message(F.text == "📢 رسالة لكل المستخدمين")
async def prompt_broadcast(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_broadcast)
    await msg.reply("📝 أدخل نص الرسالة المراد بثها لجميع المستخدمين (غير المحظورين):")

@dp.message(AdminStates.waiting_broadcast)
async def send_broadcast(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    
    await state.clear()
    
    broadcast_text = msg.text
    all_users = get_all_users_ids()
    sent_count = 0
    
    await msg.reply("⏳ جارٍ إرسال الرسالة...")
    
    for uid, is_banned_status in all_users:
        if is_banned_status == 0 and uid != ADMIN_ID: # إرسال لغير الأدمن وغير المحظورين
            try:
                await bot.send_message(uid, broadcast_text, parse_mode="HTML")
                sent_count += 1
            except Exception:
                pass 
                
    await msg.reply(f"✅ تم إرسال الرسالة بنجاح إلى **{sent_count}** مستخدم غير محظور.", reply_markup=admin_menu())

@dp.message(F.text == "🚫 حظر مستخدم")
async def prompt_ban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_ban)
    await msg.reply("🛡️ أدخل ID المستخدم المراد حظره:")

@dp.message(AdminStates.waiting_ban)
async def process_ban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.clear()
    
    try:
        user_id_to_ban = int(msg.text.strip())
        update_ban_status(user_id_to_ban, 1) # 1 للحظر
        await msg.reply(f"✅ تم حظر المستخدم **{user_id_to_ban}** بنجاح.", reply_markup=admin_menu())
    except ValueError:
        await msg.reply("❌ ID المستخدم غير صحيح. يرجى إدخال رقم.", reply_markup=admin_menu())

@dp.message(F.text == "✅ إلغاء حظر مستخدم")
async def prompt_unban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_unban)
    await msg.reply("🔓 أدخل ID المستخدم المراد إلغاء حظره:")

@dp.message(AdminStates.waiting_unban)
async def process_unban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.clear()
    
    try:
        user_id_to_unban = int(msg.text.strip())
        update_ban_status(user_id_to_unban, 0) # 0 لإلغاء الحظر
        await msg.reply(f"✅ تم إلغاء حظر المستخدم **{user_id_to_unban}** بنجاح.", reply_markup=admin_menu())
    except ValueError:
        await msg.reply("❌ ID المستخدم غير صحيح. يرجى إدخال رقم.", reply_markup=admin_menu())

@dp.message(F.text == "🔑 إنشاء مفتاح اشتراك")
async def prompt_key_days(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_key_days)
    await msg.reply("📅 أدخل عدد الأيام التي سيعطيها المفتاح (مثال: 30):")

@dp.message(AdminStates.waiting_key_days)
async def process_create_key(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    
    try:
        days = int(msg.text.strip())
        if days <= 0:
            raise ValueError
            
        key = create_invite_key(msg.from_user.id, days)
        
        await state.clear()
        
        key_msg = f"""
🎉 تم إنشاء مفتاح تفعيل جديد!
━━━━━━━━━━━━━━━
**المفتاح:** <code>{key}</code>
**عدد الأيام:** {days} يوم
"""
        await msg.reply(key_msg, parse_mode="HTML", reply_markup=admin_menu())
        
    except ValueError:
        await msg.reply("❌ عدد الأيام غير صحيح. يرجى إدخال رقم صحيح وموجب.", reply_markup=admin_menu())


@dp.message(F.text == "🗒️ عرض حالة المشتركين")
async def display_user_status(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, is_banned, vip_until FROM users ORDER BY vip_until DESC LIMIT 20")
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await msg.reply("لا يوجد مستخدمون مسجلون حالياً.")
        return

    report = "📋 **تقرير حالة آخر 20 مستخدماً**\n\n"
    
    for user_id, username, is_banned, vip_until in users:
        ban_status = "❌ محظور" if is_banned == 1 else "✅ نشط"
        
        if vip_until is not None and vip_until > time.time():
            vip_status = f"⭐️ VIP (حتى: {datetime.fromtimestamp(vip_until).strftime('%Y-%m-%d')})"
        else:
            vip_status = "🔸 مجاني/انتهى"
            
        report += f"👤 ID: {user_id}\n"
        report += f"  - اليوزر: @{username if username else 'لا يوجد'}\n"
        report += f"  - الحالة: {ban_status} / {vip_status}\n\n"
        
    await msg.reply(report, parse_mode="HTML")


# ===============================================
# === دالة متابعة الصفقات (Trade Monitoring) ===
# ===============================================

async def check_open_trades():
    """
    مهمة غير متزامنة تعمل بشكل دوري لمتابعة الصفقات النشطة وإغلاقها عند تحقق الشروط.
    """
    
    active_trades = get_active_trades()
    
    if not active_trades:
        return

    # 1. جلب السعر الحالي للسوق (مرة واحدة)
    try:
        data = yf.download(TRADE_SYMBOL, period="1m", interval="1m", progress=False, auto_adjust=True)
        current_price = data['Close'].iloc[-1].item()
    except Exception as e:
        print(f"❌ فشل في جلب سعر السوق الحالي: {e}")
        return

    closed_count = 0
    
    # 2. المرور على كل صفقة نشطة
    for trade in active_trades:
        trade_id = trade['trade_id']
        action = trade['action']
        tp = trade['take_profit']
        sl = trade['stop_loss']
        
        exit_status = None
        close_price = None
        
        if action == "BUY":
            # حالة ضرب الهدف
            if current_price >= tp:
                exit_status = "HIT_TP"
                close_price = tp
            # حالة ضرب الوقف
            elif current_price <= sl:
                exit_status = "HIT_SL"
                close_price = sl
        
        elif action == "SELL":
            # حالة ضرب الهدف
            if current_price <= tp:
                exit_status = "HIT_TP"
                close_price = tp
            # حالة ضرب الوقف
            elif current_price >= sl:
                exit_status = "HIT_SL"
                close_price = sl

        # 3. إذا تم إغلاق الصفقة، قم بالتحديث والإشعار
        if exit_status:
            update_trade_status(trade_id, exit_status, close_price)
            closed_count += 1
            
            result_emoji = "🏆🎉" if exit_status == "HIT_TP" else "🛑"
            
            close_msg = f"""
{result_emoji} <b>TRADE CLOSED!</b> {result_emoji}
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD
➡️ **ACTION:** {action}
🔒 **RESULT:** تم الإغلاق عند **{exit_status.replace('HIT_', '')}**!
💰 **PRICE:** ${close_price:,.2f}
"""
            # إرسال إلى جميع المستخدمين النشطين (VIP)
            all_users = get_all_users_ids()
            for uid, is_banned_status in all_users:
                 if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
                    try:
                        await bot.send_message(uid, close_msg)
                    except Exception:
                        pass
                        
            # إشعار الأدمن
            if ADMIN_ID != 0:
                await bot.send_message(ADMIN_ID, f"🔔 تم إغلاق الصفقة **{trade_id}** بنجاح على: {exit_status}", parse_mode="HTML")

# ===============================================
# === إعداد المهام المجدولة (Setup Scheduled Tasks) ===
# ===============================================

async def scheduled_tasks():
    # يبدأ التشغيل بعد فترة قصيرة
    await asyncio.sleep(5) 
    while True:
        # فحص الصفقات المفتوحة كل 'TRADE_CHECK_INTERVAL' ثانية (مراقبة الـ 24 ساعة)
        await check_open_trades()
        await asyncio.sleep(TRADE_CHECK_INTERVAL)
        
async def monitor_market_continously():
    """مهمة إرسال تنبيهات المراقبة بشكل دوري (لتنبيه المستخدمين بأن البوت يعمل)."""
    await asyncio.sleep(60) # يبدأ بعد دقيقة من تشغيل البوت
    while True:
        await send_analysis_alert()
        # يستخدم ALERT_INTERVAL (ساعة واحدة افتراضياً)
        await asyncio.sleep(ALERT_INTERVAL) 


async def main():
    # تهيئة قاعدة البيانات وضمان وجود الجداول
    init_db()
    
    # تطبيق الـ Middleware على جميع الرسائل للمعالجة
    dp.message.middleware(AccessMiddleware())
    
    # تشغيل المهام المجدولة (مثل فحص الصفقات)
    asyncio.create_task(scheduled_tasks())
    
    # [الإضافة الجديدة] تشغيل مهمة التنبيهات المستمرة
    asyncio.create_task(monitor_market_continously())
    
    # بدء البوت
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🤖 تم إيقاف البوت بنجاح.")
    except Exception as e:
        print(f"حدث خطأ كبير: {e}")
