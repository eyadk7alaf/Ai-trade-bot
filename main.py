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
TRADE_CHECK_INTERVAL = int(os.getenv("TRADE_CHECK_INTERVAL", "30")) # [تعديل جديد] فاصل متابعة الصفقات بالثواني

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
        -- [تعديل جديد] جدول الصفقات
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
            
            cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
            user_data = cursor.fetchone() 
            
            vip_until_ts = user_data[0] if user_data and user_data[0] is not None else 0.0 
            
            if vip_until_ts > time.time():
                start_date = datetime.fromtimestamp(vip_until_ts)
            else:
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

# === [تعديل جديد] دوال إدارة الصفقات ===
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
# ===============================================


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
            
        allowed_for_all = ["💬 تواصل مع الدعم", "ℹ️ عن AlphaTradeAI", "🔗 تفعيل مفتاح الاشتراك", "📝 حالة الاشتراك", "💰 خطة الأسعار VIP", "📈 سعر السوق الحالي"]
        
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
    
    # [تعديل جديد] حفظ الصفقة في قاعدة البيانات
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
                
# =============== القوائم المُعدَّلة ===============

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
    # [تعديل جديد] إضافة زر جرد الصفقات اليومي
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="تحليل فوري ⚡️"), KeyboardButton(text="📊 جرد الصفقات اليومي")],
            [KeyboardButton(text="📢 رسالة لكل المستخدمين"), KeyboardButton(text="🔑 إنشاء مفتاح اشتراك")],
            [KeyboardButton(text="🚫 حظر مستخدم"), KeyboardButton(text="✅ إلغاء حظر مستخدم")],
            [KeyboardButton(text="👥 عدد المستخدمين"), KeyboardButton(text
