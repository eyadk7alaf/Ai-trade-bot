import asyncio
import time
import os
import psycopg2
import pandas as pd
import yfinance as yf 
import schedule
import random
import uuid
import ccxt # <=== المكتبة الجديدة لجلب السعر الفوري

from datetime import datetime, timedelta, timezone 
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton 
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from typing import Callable, Dict, Any, Awaitable

# =============== تعريف حالات FSM المُعدَّلة ===============
class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_trade = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_key_days = State() 
    waiting_new_capital = State() 
    waiting_trade_result_input = State()
    waiting_trade_pnl = State()

class UserStates(StatesGroup):
    waiting_key_activation = State() 
    
# =============== إعداد البوت والمتغيرات (من Environment Variables) ===============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID", "0") 

# الرمز الفوري: XAU/USD (افتراضي)
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "XAU/USD") 
# المنصة لجلب البيانات الفورية (مثل binance, okx, bybit)
CCXT_EXCHANGE = os.getenv("CCXT_EXCHANGE", "binance") 

ADMIN_TRADE_SYMBOL = os.getenv("ADMIN_TRADE_SYMBOL", "XAU/USD") 
ADMIN_CAPITAL_DEFAULT = float(os.getenv("ADMIN_CAPITAL_DEFAULT", "100.0")) 
ADMIN_RISK_PER_TRADE = float(os.getenv("ADMIN_RISK_PER_TRADE", "0.02")) 

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.90"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "I1l_1")
TRADE_CHECK_INTERVAL = int(os.getenv("TRADE_CHECK_INTERVAL", "30")) 
ALERT_INTERVAL = int(os.getenv("ALERT_INTERVAL", "14400")) 

try:
    ADMIN_ID = int(ADMIN_ID_STR)
    if ADMIN_ID == 0:
        print("⚠️ ADMIN_ID هو 0. قد تكون وظائف الأدمن غير متاحة.")
except ValueError:
    print("❌ خطأ! ADMIN_ID في متغيرات البيئة ليس رقمًا صالحًا.")
    ADMIN_ID = 0 

if not BOT_TOKEN:
    raise ValueError("🚫 لم يتم العثور على متغير البيئة TELEGRAM_BOT_TOKEN. يرجى ضبطه.")

bot = Bot(token=BOT_TOKEN, 
          default=DefaultBotProperties(parse_mode="HTML")) 
          
dp = Dispatcher(storage=MemoryStorage())

# =============== قاعدة بيانات PostgreSQL ===============
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("🚫 لم يتم العثور على DATABASE_URL. يرجى التأكد من ربط PostgreSQL بـ Railway.")

def get_db_connection():
    try:
        # تحليل URL قاعدة البيانات (لضمان التوافق مع تنسيقات الاستضافة)
        url = urlparse(DATABASE_URL)
        return psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
    except Exception as e:
        print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username VARCHAR(255), joined_at DOUBLE PRECISION, is_banned INTEGER DEFAULT 0, vip_until DOUBLE PRECISION DEFAULT 0.0);
        CREATE TABLE IF NOT EXISTS invite_keys (key VARCHAR(255) PRIMARY KEY, days INTEGER, created_by BIGINT, used_by BIGINT NULL, used_at DOUBLE PRECISION NULL);
        CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY, sent_at DOUBLE PRECISION, action VARCHAR(10), entry_price DOUBLE PRECISION, take_profit DOUBLE PRECISION, stop_loss DOUBLE PRECISION, status VARCHAR(50) DEFAULT 'ACTIVE', exit_status VARCHAR(50) DEFAULT 'NONE', close_price DOUBLE PRECISION NULL, user_count INTEGER);
        
        CREATE TABLE IF NOT EXISTS admin_performance (
            id SERIAL PRIMARY KEY,
            record_type VARCHAR(50) NOT NULL,
            timestamp DOUBLE PRECISION NOT NULL,
            value_float DOUBLE PRECISION NULL, 
            trade_action VARCHAR(10) NULL,
            trade_symbol VARCHAR(50) NULL,
            lots_used DOUBLE PRECISION NULL
        );
    """)
    conn.commit()
    
    cursor.execute("SELECT value_float FROM admin_performance WHERE record_type = 'CAPITAL' ORDER BY timestamp DESC LIMIT 1")
    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO admin_performance (record_type, timestamp, value_float) 
            VALUES ('CAPITAL', %s, %s)
        """, (time.time(), ADMIN_CAPITAL_DEFAULT))
        conn.commit()
        
    conn.close()
    print("✅ تم تهيئة جداول قاعدة البيانات (PostgreSQL) بنجاح.")

# =============== دوال إدارة رأس المال والصفقات والمستخدمين (مختصرة) ===============

def get_capital():
    conn = get_db_connection()
    if conn is None: return ADMIN_CAPITAL_DEFAULT
    cursor = conn.cursor()
    cursor.execute("SELECT value_float FROM admin_performance WHERE record_type = 'CAPITAL' ORDER BY timestamp DESC LIMIT 1")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else ADMIN_CAPITAL_DEFAULT

def update_capital(new_capital: float, trade_action: str = None, lots: float = None):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO admin_performance (record_type, timestamp, value_float, trade_action, trade_symbol, lots_used)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, ('CAPITAL', time.time(), new_capital, trade_action, ADMIN_TRADE_SYMBOL, lots))
    conn.commit()
    conn.close()

def save_new_trade(action, entry_price, take_profit, stop_loss):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    trade_id = str(uuid.uuid4())
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0 AND vip_until > %s", (time.time(),))
    user_count = cursor.fetchone()[0]
    
    cursor.execute("""
        INSERT INTO trades (trade_id, sent_at, action, entry_price, take_profit, stop_loss, user_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (trade_id, time.time(), action, entry_price, take_profit, stop_loss, user_count))
    conn.commit()
    conn.close()

def get_active_trade():
    conn = get_db_connection()
    if conn is None: return None
    cursor = conn.cursor()
    cursor.execute("SELECT trade_id, action, entry_price, take_profit, stop_loss FROM trades WHERE status = 'ACTIVE' ORDER BY sent_at DESC LIMIT 1")
    result = cursor.fetchone()
    conn.close()
    if result:
        return {'trade_id': result[0], 'action': result[1], 'entry_price': result[2], 'take_profit': result[3], 'stop_loss': result[4]}
    return None

def close_active_trade(trade_id: str, exit_status: str, close_price: float):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE trades SET status = 'CLOSED', exit_status = %s, close_price = %s 
        WHERE trade_id = %s
    """, (exit_status, close_price, trade_id))
    conn.commit()
    conn.close()

def register_user(user_id: int, username: str):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, username, joined_at) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, username, time.time()))
    conn.commit()
    conn.close()

def get_user_status(user_id: int) -> dict:
    conn = get_db_connection()
    if conn is None: return {'is_banned': 0, 'is_vip': False, 'vip_until': 0.0}
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned, vip_until FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        vip_until = result[1]
        is_vip = vip_until > time.time()
        return {'is_banned': result[0], 'is_vip': is_vip, 'vip_until': vip_until}
    return {'is_banned': 0, 'is_vip': False, 'vip_until': 0.0}

def extend_vip_access(user_id: int, days: int):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    
    cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
    current_vip_until = cursor.fetchone()[0] if cursor.rowcount > 0 else 0.0
    
    now = time.time()
    if current_vip_until < now:
        new_vip_until = now + (days * 86400)
    else:
        new_vip_until = current_vip_until + (days * 86400)
    
    cursor.execute("UPDATE users SET vip_until = %s WHERE user_id = %s", (new_vip_until, user_id))
    conn.commit()
    conn.close()
    return new_vip_until

def generate_key(days: int, admin_id: int) -> str:
    conn = get_db_connection()
    if conn is None: return None
    cursor = conn.cursor()
    key = str(uuid.uuid4()).split('-')[0].upper() + str(random.randint(100, 999))
    try:
        cursor.execute("INSERT INTO invite_keys (key, days, created_by) VALUES (%s, %s, %s)", (key, days, admin_id))
        conn.commit()
        return key
    except Exception as e:
        print(f"خطأ في توليد المفتاح: {e}")
        return None
    finally:
        conn.close()

def activate_key(key: str, user_id: int) -> int or None:
    conn = get_db_connection()
    if conn is None: return None
    cursor = conn.cursor()
    
    cursor.execute("SELECT days FROM invite_keys WHERE key = %s AND used_by IS NULL", (key,))
    result = cursor.fetchone()
    if not result:
        conn.close()
        return -1 
        
    days = result[0]
    
    cursor.execute("UPDATE invite_keys SET used_by = %s, used_at = %s WHERE key = %s", (user_id, time.time(), key))
    
    new_vip_until = extend_vip_access(user_id, days)
    conn.commit()
    conn.close()
    return days

def ban_user(user_id: int):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()

def unban_user(user_id: int):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()

def get_all_active_users():
    conn = get_db_connection()
    if conn is None: return []
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE is_banned = 0 AND vip_until > %s", (time.time(),))
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

# ===============================================
# === دالة جلب البيانات الفورية (باستخدام ccxt) ===
# ===============================================

def fetch_ohlcv_data(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    """
    تجلب بيانات الشموع (OHLCV) للرمز والفاصل الزمني المحدد باستخدام ccxt.
    """
    try:
        exchange = getattr(ccxt, CCXT_EXCHANGE)()
        exchange.load_markets()

        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        
        if not ohlcv:
            # استخدام yfinance كاحتياطي فقط في حال فشل ccxt
            print(f"⚠️ فشل جلب بيانات {symbol} من {CCXT_EXCHANGE}. (التحليل لن يكون مطابقًا تمامًا لأسعار XAUUSD)")
            return pd.DataFrame()

        # تحويل بيانات ccxt إلى DataFrame
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
        
    except Exception as e:
        print(f"❌ خطأ حرج في جلب بيانات ccxt: {e}")
        # محاولة أخيرة كاحتياطي من yfinance، ولكن يرجى ملاحظة أن التحليل سيكون غير دقيق لـ XAUUSD
        try:
             if symbol == "XAU/USD" and timeframe == "5m":
                yf_symbol = "GC=F"
                data = yf.download(yf_symbol, period="7d", interval="5m", progress=False, auto_adjust=True)
                if not data.empty:
                    return data
        except:
            return pd.DataFrame()
            
        return pd.DataFrame()


def get_signal_and_confidence(symbol: str) -> tuple[str, float, str, float, float, float, float]:
    """
    تحليل ذكي باستخدام 4 فلاتر على البيانات الفورية المجلوبة بواسطة ccxt.
    """
    try:
        # جلب البيانات الفورية XAU/USD باستخدام ccxt
        data_1m = fetch_ohlcv_data(symbol, "1m", limit=200)
        data_5m = fetch_ohlcv_data(symbol, "5m", limit=200)
        
        DISPLAY_SYMBOL = "XAUUSD" 
        
        if data_1m.empty or len(data_1m) < 50 or data_5m.empty or len(data_5m) < 20: 
            return f"لا تتوفر بيانات كافية للتحليل لرمز التداول: {DISPLAY_SYMBOL}.", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0

        # 1. HTF Trend (5m)
        data_5m['EMA_10'] = data_5m['Close'].ewm(span=10, adjust=False).mean()
        data_5m['EMA_30'] = data_5m['Close'].ewm(span=30, adjust=False).mean()
        htf_trend = "صاعد (BULLISH)" if data_5m['EMA_10'].iloc[-1] > data_5m['EMA_30'].iloc[-1] else "هابط (BEARISH)"
        
        # 2. LFT Analysis (1m)
        data = data_1m 
        data['EMA_5'] = data['Close'].ewm(span=5, adjust=False).mean()
        data['EMA_20'] = data['Close'].ewm(span=20, adjust=False).mean()
        
        # 3. حساب RSI و 4. ATR
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

        latest_price = data['Close'].iloc[-1].item() 
        latest_time = data.index[-1].strftime('%Y-%m-%d %H:%M:%S')
        ema_fast_prev = data['EMA_5'].iloc[-2]
        ema_slow_prev = data['EMA_20'].iloc[-2]
        ema_fast_current = data['EMA_5'].iloc[-1]
        ema_slow_current = data['EMA_20'].iloc[-1]
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
        stop_loss_distance = 0.0 

        if current_atr < MIN_ATR_THRESHOLD:
            return f"⚠️ السوق هادئ جداً (ATR: {current_atr:.2f} < {MIN_ATR_THRESHOLD}). الإشارة HOLD.", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0
            
        # منطق تحديد الإشارة: تقاطع EMA
        is_buy_signal = (ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current)
        is_buy_trend = (ema_fast_current > ema_slow_current)
        is_sell_signal = (ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current)
        is_sell_trend = (ema_fast_current < ema_slow_current)

        if is_buy_signal or is_buy_trend:
            if htf_trend == "صاعد (BULLISH)": 
                action = "BUY"
                if data['RSI'].iloc[-1] > 50: confidence = 0.99 if is_buy_signal else 0.95
                else: confidence = 0.70 
            else: confidence = 0.50 
                
        elif is_sell_signal or is_sell_trend:
            if htf_trend == "هابط (BEARISH)": 
                action = "SELL"
                if data['RSI'].iloc[-1] < 50: confidence = 0.99 if is_sell_signal else 0.95
                else: confidence = 0.70
            else: confidence = 0.50 
        
        if action != "HOLD":
            risk_amount = max(current_atr * SL_FACTOR, MIN_SL) 

            if action == "BUY":
                stop_loss = entry_price - risk_amount 
                take_profit = entry_price + (risk_amount * TP_FACTOR) 
            
            elif action == "SELL":
                stop_loss = entry_price + risk_amount
                take_profit = entry_price - (risk_amount * TP_FACTOR)
                
            stop_loss_distance = abs(entry_price - stop_loss) 
        
        price_msg = f"📊 آخر سعر لـ <b>{DISPLAY_SYMBOL}</b> (الاتجاه الأكبر: {htf_trend}):\nالسعر: ${latest_price:,.2f}\nالوقت: {latest_time} UTC"
        
        return price_msg, confidence, action, entry_price, stop_loss, take_profit, stop_loss_distance 
        
    except Exception as e:
        return f"❌ فشل في جلب بيانات التداول لـ XAUUSD أو التحليل: {e}", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0


# =============== دالة إرسال الإشارة ===============
async def send_trade_signal(admin_triggered=False):
    
    price_info_msg_ar, confidence, action, entry_price, stop_loss, take_profit, sl_distance = get_signal_and_confidence(TRADE_SYMBOL) 
    
    confidence_percent = confidence * 100
    is_high_confidence = confidence >= CONFIDENCE_THRESHOLD

    if not is_high_confidence or action == "HOLD":
        if admin_triggered:
             await bot.send_message(ADMIN_ID, f"⚠️ **فشل إرسال الإشارة:**\nلا توجد إشارة ثقة عالية حاليًا.\n\n{price_info_msg_ar}")
        return False

    active_trade = get_active_trade()
    if active_trade:
        if admin_triggered:
            await bot.send_message(ADMIN_ID, f"⚠️ **فشل إرسال الإشارة:**\nيوجد بالفعل صفقة نشطة (<code>{active_trade['trade_id'][:8]}...</code>).", parse_mode='HTML')
        return False

    # حساب حجم اللوت بناءً على المخاطرة
    capital = get_capital()
    risk_amount = capital * ADMIN_RISK_PER_TRADE
    # قيمة النقطة للوت القياسي (1.00) في الذهب عادةً ما تكون 100 دولار للتغيرات الكاملة في الدولار (1.00)
    # لكن في الفوركس، النقطة (Pip) عادة ما تكون 0.01. نستخدم $10 للوت القياسي لكل 0.01 حركة
    point_value = 10.0 # $10 لكل 0.01 حركة (نقطة) للوت القياسي 1.00
    
    if sl_distance == 0.0:
        lots_to_use = 0.01
    else:
        # lots = (risk_amount) / (sl_distance * $100 * 0.01)
        lots_to_use = (risk_amount) / (sl_distance * point_value * 100) 
        lots_to_use = round(lots_to_use * 100) / 100 # تقريب لأقرب 0.01

    if lots_to_use < 0.01:
        lots_to_use = 0.01
        
    save_new_trade(action, entry_price, take_profit, stop_loss)
    
    # تحديد مسافة الوقف (كم نقطة)
    pips_distance = round(sl_distance * 100)
    
    signal_message = f"""
    🔥 **إشارة تداول الذهب الفورية (XAUUSD)** 🔥
    
    - **الإجراء:** <b>{action}</b>
    - **نقطة الدخول (Entry):** <code>{entry_price:,.2f}</code>
    - **جني الأرباح (TP):** <code>{take_profit:,.2f}</code>
    - **وقف الخسارة (SL):** <code>{stop_loss:,.2f}</code>
    
    ---
    
    - **الثقة:** {confidence_percent:.1f}%
    - **إدارة المخاطر:**
      - **المخاطرة/العائد (R:R):** 1 : 3
      - **حجم الوقف:** {pips_distance} نقطة
      - **حجم اللوت المقترح:** <b>{lots_to_use:.2f}</b> لوت
      - **المخاطرة:** ${risk_amount:,.2f}
      
    - **ملاحظات:** يرجى الالتزام الصارم بوقف الخسارة.
    
    """
    
    all_users = get_all_active_users()
    sent_count = 0
    
    for user_id in all_users:
        try:
            await bot.send_message(user_id, signal_message, parse_mode='HTML')
            sent_count += 1
        except Exception as e:
            print(f"❌ فشل إرسال الإشارة للمستخدم {user_id}: {e}")
            
    await bot.send_message(ADMIN_ID, f"✅ تم إرسال إشارة {action} بنجاح إلى {sent_count} مستخدم.\nحجم اللوت: {lots_to_use:.2f}", parse_mode='HTML')
    return True

# =============== وظيفة التحقق من إغلاق الصفقات (TP/SL) ===============
async def check_active_trade():
    trade = get_active_trade()
    if not trade:
        return

    # جلب السعر الحالي الفوري
    try:
        data = fetch_ohlcv_data(TRADE_SYMBOL, "1m", limit=1)
        if data.empty:
            print("❌ فشل جلب بيانات XAUUSD للتحقق من الإغلاق.")
            return
        current_price = data['Close'].iloc[-1].item()
    except Exception as e:
        print(f"❌ خطأ أثناء جلب السعر للتحقق من الإغلاق: {e}")
        return

    action = trade['action']
    entry_price = trade['entry_price']
    tp = trade['take_profit']
    sl = trade['stop_loss']
    
    exit_status = None
    
    if action == "BUY":
        if current_price >= tp:
            exit_status = "TP_HIT"
        elif current_price <= sl:
            exit_status = "SL_HIT"
            
    elif action == "SELL":
        if current_price <= tp:
            exit_status = "TP_HIT"
        elif current_price >= sl:
            exit_status = "SL_HIT"

    if exit_status:
        # حساب الربح أو الخسارة التقريبية
        lots_used = 0.01 # يجب استرجاع اللوت المستخدم فعلياً إن أمكن
        point_value = 10.0
        
        if exit_status == "TP_HIT":
            pnl_amount = abs(tp - entry_price) * point_value * 100 * lots_used
            message = f"🎉 **تم تحقيق الهدف (TP)!** 🎉\nالصفقة: <b>{action}</b> @ <code>{entry_price:,.2f}</code>\nتم الإغلاق عند <code>{tp:,.2f}</code>.\n(الربح التقريبي: +${pnl_amount:,.2f})"
        else: # SL_HIT
            pnl_amount = abs(sl - entry_price) * point_value * 100 * lots_used
            message = f"🔻 **تم ضرب وقف الخسارة (SL)!** 🔻\nالصفقة: <b>{action}</b> @ <code>{entry_price:,.2f}</code>\nتم الإغلاق عند <code>{sl:,.2f}</code>.\n(الخسارة التقريبية: -${pnl_amount:,.2f})"
        
        close_active_trade(trade['trade_id'], exit_status, current_price)
        
        # إرسال الإغلاق لجميع المستخدمين
        all_users = get_all_active_users()
        for user_id in all_users:
            try:
                await bot.send_message(user_id, message, parse_mode='HTML')
            except:
                pass 
                
        # إرسال الإغلاق للأدمن
        await bot.send_message(ADMIN_ID, f"✅ تم إغلاق الصفقة آليًا:\n{message}", parse_mode='HTML')


# =============== الأوامر الرئيسية وواجهات المستخدم ===============

# Middleware للتحقق من الحظر والاشتراك
class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.Message, Dict[str, Any]], Awaitable[Any]],
        event: types.Message,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        register_user(user_id, event.from_user.username)
        
        status = get_user_status(user_id)
        
        if status['is_banned']:
            return await event.answer("🚫 تم حظرك من استخدام هذا البوت.", show_alert=True)
            
        if not status['is_vip'] and user_id != ADMIN_ID:
            if not isinstance(event, types.Message) or event.text not in ["/start", "/key", "تفعيل مفتاح 🔑"]:
                days_left = int((status['vip_until'] - time.time()) / 86400) if status['vip_until'] > time.time() else 0
                return await event.answer(f"⚠️ اشتراكك غير مفعل أو انتهى. لتفعيل الاشتراك، استخدم زر <b>تفعيل مفتاح 🔑</b>.", parse_mode='HTML')

        return await handler(event, data)

dp.message.middleware(AccessMiddleware())

# ===============================================
# === أوامر المستخدم (Users Commands) ===
# ===============================================

@dp.message(Command("start"))
async def command_start(message: types.Message):
    user_id = message.from_user.id
    status = get_user_status(user_id)
    
    now = time.time()
    days_left = int((status['vip_until'] - now) / 86400) if status['vip_until'] > now else 0
    
    markup = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="حالة التداول 📈"), KeyboardButton(text="حسابي 👤")],
        [KeyboardButton(text="تفعيل مفتاح 🔑"), KeyboardButton(text=f"التواصل مع @{ADMIN_USERNAME}")]
    ], resize_keyboard=True)

    welcome_message = f"""
    👋 أهلاً بك في بوت إشارات الذهب الفورية (XAUUSD)!
    
    **حالة اشتراكك:** {'✅ مفعل VIP' if status['is_vip'] else '❌ غير مفعل'}
    **الأيام المتبقية:** {days_left} أيام
    
    للبدء، يمكنك:
    1. تفعيل مفتاح اشتراكك بالضغط على <b>تفعيل مفتاح 🔑</b>.
    2. طلب مفتاح من @{ADMIN_USERNAME}.
    """
    await message.answer(welcome_message, reply_markup=markup, parse_mode='HTML')


@dp.message(F.text == "حسابي 👤")
async def handle_account_status(message: types.Message):
    status = get_user_status(message.from_user.id)
    
    now = time.time()
    days_left = int((status['vip_until'] - now) / 86400) if status['vip_until'] > now else 0
    
    vip_end_date = datetime.fromtimestamp(status['vip_until'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if status['vip_until'] > 0 else 'غير محدد'
    
    active_trade = get_active_trade()
    trade_info = f"<code>{active_trade['trade_id'][:8]}...</code> - {active_trade['action']} @ {active_trade['entry_price']:,.2f}" if active_trade else "لا يوجد"

    response = f"""
    **حالة حسابك:**
    
    - **مُعرّف الحساب:** <code>{message.from_user.id}</code>
    - **حالة الاشتراك:** {'✅ مفعل VIP' if status['is_vip'] else '❌ غير مفعل'}
    - **الأيام المتبقية:** <b>{days_left}</b> أيام
    - **ينتهي في:** {vip_end_date}
    - **الصفقة النشطة:** {trade_info}
    """
    await message.answer(response, parse_mode='HTML')

@dp.message(F.text == "حالة التداول 📈")
async def handle_trade_status(message: types.Message):
    active_trade = get_active_trade()
    
    if active_trade:
        response = f"""
        🔥 **الصفقة النشطة حاليًا (XAUUSD):**
        
        - **الإجراء:** <b>{active_trade['action']}</b>
        - **نقطة الدخول (Entry):** <code>{active_trade['entry_price']:,.2f}</code>
        - **جني الأرباح (TP):** <code>{active_trade['take_profit']:,.2f}</code>
        - **وقف الخسارة (SL):** <code>{active_trade['stop_loss']:,.2f}</code>
        """
    else:
        # عرض آخر سعر متاح
        price_info_msg_ar, confidence, action, entry_price, stop_loss, take_profit, sl_distance = get_signal_and_confidence(TRADE_SYMBOL) 
        
        response = f"""
        **لا توجد صفقات نشطة حاليًا.**
        
        {price_info_msg_ar}
        
        ننتظر إشارة ثقة عالية جديدة.
        """
    
    await message.answer(response, parse_mode='HTML')


@dp.message(F.text.in_({"تفعيل مفتاح 🔑", "/key"}))
async def handle_key_activation_request(message: types.Message, state: FSMContext):
    await state.set_state(UserStates.waiting_key_activation)
    await message.answer("🔑 **الرجاء إدخال مفتاح التفعيل:**", parse_mode='HTML')

@dp.message(UserStates.waiting_key_activation)
async def handle_key_activation_process(message: types.Message, state: FSMContext):
    key = message.text.strip().upper()
    user_id = message.from_user.id
    
    days = activate_key(key, user_id)
    
    if days > 0:
        await message.answer(f"✅ **تم التفعيل بنجاح!**\nتم إضافة <b>{days}</b> أيام إلى اشتراكك. يمكنك الآن الاستمتاع بجميع الإشارات.", parse_mode='HTML')
    elif days == -1:
        await message.answer("❌ **فشل التفعيل!**\nالمفتاح غير صالح أو تم استخدامه مسبقًا. يرجى التحقق من المفتاح والمحاولة مرة أخرى.")
    else:
        await message.answer("❌ **حدث خطأ غير متوقع** أثناء معالجة التفعيل. يرجى المحاولة لاحقًا.")
        
    await state.clear() 

# ===============================================
# === أوامر المشرف (Admin Commands) ===
# ===============================================

@dp.message(F.text == f"التواصل مع @{ADMIN_USERNAME}")
async def handle_contact_admin(message: types.Message):
    await message.answer(f"للتواصل مع المشرف، يمكنك مراسلة الحساب:\n@{ADMIN_USERNAME}")

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("🚫 ليس لديك صلاحية الوصول لهذه اللوحة.")

    capital = get_capital()
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 الأداء والتحكم المالي", callback_data="admin_perf")],
        [InlineKeyboardButton(text="🔑 توليد مفتاح اشتراك", callback_data="admin_key")],
        [InlineKeyboardButton(text="📢 إرسال رسالة جماعية", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="❌ حظر مستخدم", callback_data="admin_ban"), InlineKeyboardButton(text="✅ إلغاء حظر", callback_data="admin_unban")],
        [InlineKeyboardButton(text="⚠️ إرسال إشارة يدوية", callback_data="admin_send_signal")],
        [InlineKeyboardButton(text="📉 إغلاق صفقة نشطة", callback_data="admin_close_trade")]
    ])
    
    active_trade = get_active_trade()
    trade_status = "✅ لا يوجد صفقة نشطة" if not active_trade else f"🔥 صفقة نشطة: {active_trade['action']} @ {active_trade['entry_price']:,.2f}"

    await message.answer(f"**لوحة تحكم المشرف**\n\n**رأس المال المسجل:** ${capital:,.2f}\n**حالة التداول:** {trade_status}", reply_markup=markup)


@dp.callback_query(lambda c: c.data.startswith('admin_'))
async def handle_admin_callbacks(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    data = callback_query.data
    
    if data == "admin_perf":
        capital = get_capital()
        await callback_query.message.answer(f"**إدارة الأداء:**\nرأس المال الحالي: ${capital:,.2f}\n\nاختر الإجراء:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="تحديث رأس المال", callback_data="admin_update_capital")]
        ]))
        
    elif data == "admin_update_capital":
        await state.set_state(AdminStates.waiting_new_capital)
        await callback_query.message.answer("💰 **أدخل رأس المال الجديد** (كرقم فقط):")
        
    elif data == "admin_key":
        await state.set_state(AdminStates.waiting_key_days)
        await callback_query.message.answer("🔑 **كم يومًا تريد أن يكون صالحًا للمفتاح؟** (كرقم صحيح):")
        
    elif data == "admin_broadcast":
        await state.set_state(AdminStates.waiting_broadcast)
        await callback_query.message.answer("📢 **أرسل الرسالة التي تريد بثها** لجميع المستخدمين VIP:")
        
    elif data == "admin_ban":
        await state.set_state(AdminStates.waiting_ban)
        await callback_query.message.answer("❌ **أدخل مُعرّف (ID) المستخدم الذي تريد حظره:**")
        
    elif data == "admin_unban":
        await state.set_state(AdminStates.waiting_unban)
        await callback_query.message.answer("✅ **أدخل مُعرّف (ID) المستخدم الذي تريد إلغاء حظره:**")

    elif data == "admin_send_signal":
        await send_trade_signal(admin_triggered=True)
        
    elif data == "admin_close_trade":
        active_trade = get_active_trade()
        if not active_trade:
            return await callback_query.message.answer("⚠️ لا توجد صفقة نشطة حاليًا لإغلاقها.")

        await state.update_data(trade_id=active_trade['trade_id'])
        
        await callback_query.message.answer(f"**إغلاق الصفقة النشطة:**\n{active_trade['action']} @ {active_trade['entry_price']:,.2f}\n\nاختر حالة الإغلاق:", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="TP (جني أرباح) ✅", callback_data="close_tp"), InlineKeyboardButton(text="SL (وقف خسارة) ❌", callback_data="close_sl")],
                [InlineKeyboardButton(text="إغلاق يدوي (Manual) 💼", callback_data="close_manual")]
            ])
        )

@dp.callback_query(lambda c: c.data.startswith('close_'))
async def handle_close_trade_select(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    action = callback_query.data
    data = await state.get_data()
    trade_id = data.get('trade_id')
    active_trade = get_active_trade()
    
    if not active_trade or active_trade['trade_id'] != trade_id:
        return await callback_query.message.answer("⚠️ حدث خطأ أو الصفقة مغلقة بالفعل.")

    close_price = 0.0
    exit_status = "MANUAL"
    
    if action == "close_tp":
        close_price = active_trade['take_profit']
        exit_status = "TP_HIT"
    elif action == "close_sl":
        close_price = active_trade['stop_loss']
        exit_status = "SL_HIT"
    elif action == "close_manual":
        await state.set_state(AdminStates.waiting_trade_pnl)
        return await callback_query.message.answer("💼 **أدخل الربح/الخسارة الصافي بالدولار** (مثال: `+50.5` أو `-25.0`):")

    # إكمال الإغلاق (لـ TP أو SL)
    if close_price > 0.0:
        close_active_trade(trade_id, exit_status, close_price)
        pnl_amount = abs(close_price - active_trade['entry_price']) * 100 * 10 # قيمة تقديرية للربح
        
        if exit_status == "TP_HIT":
            message = f"🎉 **تم إغلاق الصفقة (TP)**:\nتم الإغلاق عند <code>{close_price:,.2f}</code>."
        else:
            message = f"🔻 **تم إغلاق الصفقة (SL)**:\nتم الإغلاق عند <code>{close_price:,.2f}</code>."
        
        all_users = get_all_active_users()
        for user_id in all_users:
            try:
                await bot.send_message(user_id, message, parse_mode='HTML')
            except:
                pass 
        
        await callback_query.message.answer(f"✅ تم إغلاق الصفقة <code>{trade_id[:8]}...</code> بنجاح.")
        await state.clear()


@dp.message(AdminStates.waiting_trade_pnl)
async def handle_manual_close_pnl(message: types.Message, state: FSMContext):
    pnl_input = message.text.strip()
    data = await state.get_data()
    trade_id = data.get('trade_id')
    active_trade = get_active_trade()
    
    if not active_trade or active_trade['trade_id'] != trade_id:
        await state.clear()
        return await message.answer("⚠️ حدث خطأ أو الصفقة مغلقة بالفعل.")

    try:
        pnl = float(pnl_input)
        exit_status = "MANUAL_PROFIT" if pnl >= 0 else "MANUAL_LOSS"
        
        close_active_trade(trade_id, exit_status, active_trade['entry_price']) 
        
        current_capital = get_capital()
        new_capital = current_capital + pnl
        update_capital(new_capital, trade_action=active_trade['action'])

        message_to_users = f"💼 **تم إغلاق الصفقة يدويًا.**\nالصفقة: <b>{active_trade['action']}</b> @ <code>{active_trade['entry_price']:,.2f}</code>\nنتيجة الإغلاق: {pnl_input} $"

        all_users = get_all_active_users()
        for user_id in all_users:
            try:
                await bot.send_message(user_id, message_to_users, parse_mode='HTML')
            except:
                pass 
        
        await message.answer(f"✅ تم إغلاق الصفقة يدويًا وتحديث رأس المال.\nرأس المال الجديد: ${new_capital:,.2f}")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ **قيمة غير صالحة!** يرجى إدخال الربح/الخسارة كـ رقم (مثال: `50.5` أو `-25.0`).")


@dp.message(AdminStates.waiting_new_capital)
async def handle_new_capital(message: types.Message, state: FSMContext):
    try:
        new_capital = float(message.text.strip())
        update_capital(new_capital)
        await message.answer(f"✅ **تم تحديث رأس المال بنجاح!**\nرأس المال الجديد هو: ${new_capital:,.2f}")
    except ValueError:
        await message.answer("❌ **قيمة غير صالحة!** يرجى إدخال رأس المال كرقم صحيح أو عشري.")
    finally:
        await state.clear()

@dp.message(AdminStates.waiting_key_days)
async def handle_key_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text.strip())
        if days <= 0:
             return await message.answer("❌ يجب أن تكون الأيام رقمًا صحيحًا وموجبًا.")
        
        key = generate_key(days, message.from_user.id)
        if key:
            await message.answer(f"🎉 **تم توليد المفتاح بنجاح!**\nالمفتاح: <code>{key}</code>\nالصلاحية: {days} أيام", parse_mode='HTML')
        else:
            await message.answer("❌ فشل توليد المفتاح. يرجى المحاولة مرة أخرى.")
    except ValueError:
        await message.answer("❌ **قيمة غير صالحة!** يرجى إدخال الأيام كرقم صحيح.")
    finally:
        await state.clear()

@dp.message(AdminStates.waiting_broadcast)
async def handle_broadcast_message(message: types.Message, state: FSMContext):
    broadcast_text = message.text
    all_users = get_all_active_users()
    sent_count = 0
    
    for user_id in all_users:
        try:
            await bot.send_message(user_id, broadcast_text, parse_mode='HTML')
            sent_count += 1
        except Exception as e:
            print(f"❌ فشل إرسال الرسالة للمستخدم {user_id}: {e}")
            
    await message.answer(f"✅ تم إرسال الرسالة إلى {sent_count} مستخدم VIP.")
    await state.clear()

@dp.message(AdminStates.waiting_ban)
async def handle_ban_user(message: types.Message, state: FSMContext):
    try:
        user_id_to_ban = int(message.text.strip())
        ban_user(user_id_to_ban)
        await message.answer(f"✅ **تم حظر المستخدم** ذو المُعرّف <code>{user_id_to_ban}</code>.", parse_mode='HTML')
        await bot.send_message(user_id_to_ban, "🚫 تم حظرك من استخدام هذا البوت.", show_alert=True)
    except ValueError:
        await message.answer("❌ **قيمة غير صالحة!** يرجى إدخال مُعرّف (ID) رقمي.")
    except Exception as e:
        await message.answer(f"❌ حدث خطأ: {e}")
    finally:
        await state.clear()

@dp.message(AdminStates.waiting_unban)
async def handle_unban_user(message: types.Message, state: FSMContext):
    try:
        user_id_to_unban = int(message.text.strip())
        unban_user(user_id_to_unban)
        await message.answer(f"✅ **تم إلغاء حظر المستخدم** ذو المُعرّف <code>{user_id_to_unban}</code>.", parse_mode='HTML')
        await bot.send_message(user_id_to_unban, "✅ تم إلغاء حظرك. يمكنك استخدام البوت الآن.", show_alert=True)
    except ValueError:
        await message.answer("❌ **قيمة غير صالحة!** يرجى إدخال مُعرّف (ID) رقمي.")
    except Exception as e:
        await message.answer(f"❌ حدث خطأ: {e}")
    finally:
        await state.clear()

# =============== تشغيل المهام المجدولة (Background Tasks) ===============

async def scheduler_loop():
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

def setup_scheduler():
    # التحقق من إرسال إشارة كل X دقيقة (مثلاً: كل 30 دقيقة)
    schedule.every(TRADE_CHECK_INTERVAL).minutes.do(lambda: asyncio.create_task(send_trade_signal()))
    # التحقق من إغلاق الصفقات كل دقيقة
    schedule.every(1).minute.do(lambda: asyncio.create_task(check_active_trade()))
    
# =============== وظيفة التشغيل الرئيسية ===============

async def main():
    init_db()
    setup_scheduler()
    
    # تشغيل المهام المجدولة في الخلفية
    asyncio.create_task(scheduler_loop()) 
    
    # تشغيل البوت
    print("🚀 بدء تشغيل البوت...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 تم إيقاف البوت يدوياً.")
    except Exception as e:
        print(f"❌ خطأ حرج في التشغيل: {e}")
