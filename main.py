import asyncio
import time
import os
import psycopg2
import pandas as pd
import schedule
import random
import uuid
import ccxt 
import numpy as np 
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
# 🚨🚨🚨 التعديل الحاسم: تم استبدال السطر القديم الذي يسبب المشكلة
# من: from aiogram.utils.markdown import h 
# أو: from aiogram.utils import html as h
# إلى الاستيراد الأكثر توافقاً escape_html مع تعريف h كدالة لتنظيف النص.
from aiogram.utils.markdown import escape_html 

# تعريف دالة h لتكون هي دالة تنظيف الـ HTML (escape_html)
# هذا يحل مشكلة الاستيراد ويضمن أن النص آمن عند عرضه
def h(text):
    return escape_html(str(text)) 


# =============== تعريف حالات FSM المُعدَّلة والمُضافة ===============
class AdminStates(StatesGroup):
    waiting_broadcast_target = State() 
    waiting_broadcast_text = State()   
    waiting_trade = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_key_days = State() 

class UserStates(StatesGroup):
    waiting_key_activation = State() 
    
# =============== إعداد البوت والمتغيرات ===============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID", "0") 
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "XAUT/USDT") 
CCXT_EXCHANGE = os.getenv("CCXT_EXCHANGE", "bybit") 
ADMIN_TRADE_SYMBOL = os.getenv("ADMIN_TRADE_SYMBOL", "XAUT/USDT") 

# ⚠️ متغيرات الثقة (تم تحديث 85% لتصبح 90%)
REQUIRED_MANUAL_CONFIDENCE = float(os.getenv("REQUIRED_MANUAL_CONFIDENCE", "0.90")) 
CONFIDENCE_THRESHOLD_98 = float(os.getenv("CONFIDENCE_THRESHOLD_98", "0.98")) 
CONFIDENCE_THRESHOLD_85 = float(os.getenv("CONFIDENCE_THRESHOLD_85", "0.90")) 

# ⚠️ متغيرات الجدولة 
TRADE_CHECK_INTERVAL = int(os.getenv("TRADE_CHECK_INTERVAL", "30"))             
TRADE_ANALYSIS_INTERVAL_98 = 3 * 60   # توحيد التردد: كل 3 دقائق                                       
TRADE_ANALYSIS_INTERVAL_85 = 3 * 60   # توحيد التردد: كل 3 دقائق                                       
ACTIVITY_ALERT_INTERVAL = 6 * 3600    # 🌟 تم تعديله ليصبح 6 ساعات (6 * 3600 ثانية)                                        

# 🌟🌟🌟 المتغيرات المُعدَّلة للمخاطرة المنخفضة 🌟🌟🌟
SL_FACTOR = 3.0           
SCALPING_RR_FACTOR = 1.5  
LONGTERM_RR_FACTOR = 1.5  
MAX_SL_DISTANCE = 7.0     
MIN_SL_DISTANCE = 1.5     

# ⚠️ فلاتر ADX الجديدة 
ADX_SCALPING_MIN = 15 
ADX_LONGTERM_MIN = 12 
BB_PROXIMITY_THRESHOLD = 0.5 

# ⚠️ الحد الأدنى لعدد الفلاتر المارة
MIN_FILTERS_FOR_98 = 7 
MIN_FILTERS_FOR_85 = 6    

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "I1l_1")

try:
    ADMIN_ID = int(ADMIN_ID_STR)
    if ADMIN_ID == 0:
        print("⚠️ ADMIN_ID هو 0. قد تكون وظائف الأدمن غير متاحة.")
except ValueError:
    print("❌ خطأ! ADMIN_ID في متغيرات البيئة ليس رقمًا صالحًا.")
    ADMIN_ID = 0 

if not BOT_TOKEN:
    raise ValueError("🚫 لم يتم العثور على متغير البيئة TELEGRAM_BOT_TOKEN. يرجح ضبطه.")

# 💡 تم تعيين parse_mode="HTML" كإعداد افتراضي
bot = Bot(token=BOT_TOKEN, 
          default=DefaultBotProperties(parse_mode="HTML")) 
          
dp = Dispatcher(storage=MemoryStorage())

# =============== قاعدة بيانات PostgreSQL ===============

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("🚫 لم يتم العثور على DATABASE_URL. يرجى التأكد من ربط PostgreSQL بـ Railway.")

def get_db_connection():
    try:
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
        CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY, sent_at DOUBLE PRECISION, action VARCHAR(10), entry_price DOUBLE PRECISION, take_profit DOUBLE PRECISION, stop_loss DOUBLE PRECISION, status VARCHAR(50) DEFAULT 'ACTIVE', exit_status VARCHAR(50) DEFAULT 'NONE', close_price DOUBLE PRECISION NULL, user_count INTEGER, trade_type VARCHAR(50) DEFAULT 'SCALPING');
        CREATE TABLE IF NOT EXISTS auto_trades_log (
            id SERIAL PRIMARY KEY,
            trade_id TEXT NOT NULL,
            sent_at DOUBLE PRECISION NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            action VARCHAR(10) NOT NULL,
            trade_type VARCHAR(50) NOT NULL
        );
    """)
    conn.commit()
    
    try:
        # هذا الأمر يتجنب الخطأ الذي ظهر في السجلات
        cursor.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS trade_type VARCHAR(50) DEFAULT 'SCALPING'")
        conn.commit()
    except Exception:
        conn.rollback()
        
    conn.close()
    print("✅ تم تهيئة جداول قاعدة البيانات (PostgreSQL) بنجاح.")

def generate_weekly_trade_summary():
    conn = get_db_connection()
    if conn is None: return "⚠️ فشل الاتصال بقاعدة البيانات."
    cursor = conn.cursor()
    
    time_7_days_ago = time.time() - (7 * 24 * 3600)

    cursor.execute("""
        SELECT exit_status, status
        FROM trades 
        WHERE sent_at > %s
    """, (time_7_days_ago,))
    
    trades = cursor.fetchall()
    conn.close()

    total_sent = len(trades)
    
    closed_trades = [t for t in trades if t[1] == 'CLOSED']
    total_closed = len(closed_trades)
    
    hit_tp = sum(1 for t in closed_trades if t[0] == 'HIT_TP')
    hit_sl = sum(1 for t in closed_trades if t[0] == 'HIT_SL')
    active_trades = sum(1 for t in trades if t[1] == 'ACTIVE')

    if total_closed == 0:
        success_rate = 0.0
    else:
        success_rate = (hit_tp / total_closed) * 100
        
    report_msg = f"""
<b>📈 جرد أداء صفقات AlphaTradeAI (آخر 7 أيام)</b>
━━━━━━━━━━━━━━━
📨 <b>إجمالي الصفقات المُرسلة:</b> {total_sent}
🔒 <b>إجمالي الصفقات المغلقة:</b> {total_closed}
🟢 <b>صفقات حققت الهدف (TP):</b> {hit_tp}
🔴 <b>صفقات ضربت الوقف (SL):</b> {hit_sl}
⏳ <b>الصفقات لا تزال نشطة:</b> {active_trades}
📊 <b>نسبة نجاح الصفقات المغلقة:</b> <b>{success_rate:.2f}%</b>
"""
    
    if total_sent == 0:
        report_msg = "⚠️ لم يتم إرسال أي صفقات خلال الـ 7 أيام الماضية."

    return report_msg

def log_auto_trade_sent(trade_id: str, confidence: float, action: str, trade_type: str):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO auto_trades_log (trade_id, sent_at, confidence, action, trade_type)
        VALUES (%s, %s, %s, %s, %s)
    """, (trade_id, time.time(), confidence, action, trade_type))
    conn.commit()
    conn.close()

def get_last_auto_trades(limit: int = 5):
    conn = get_db_connection()
    if conn is None: return []
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sent_at, confidence, action, trade_type 
        FROM auto_trades_log 
        ORDER BY sent_at DESC 
        LIMIT %s
    """, (limit,))
    trades = cursor.fetchall()
    conn.close()
    return trades

def add_user(user_id, username):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, username, joined_at) 
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET username = %s 
    """, (user_id, username, time.time(), username))
    conn.commit()
    conn.close()

def is_banned(user_id):
    conn = get_db_connection()
    if conn is None: return False
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None and result[0] == 1

def update_ban_status(user_id, status):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, is_banned) VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET is_banned = %s
    """, (user_id, status, status))
    conn.commit()
    conn.close()
    
def get_all_users_ids():
    conn = get_db_connection()
    if conn is None: return []
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, is_banned FROM users")
    result = cursor.fetchall()
    conn.close()
    return result
    
def get_total_users():
    conn = get_db_connection()
    if conn is None: return 0
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(user_id) FROM users") 
    result = cursor.fetchone()[0]
    conn.close()
    return result

def is_user_vip(user_id):
    conn = get_db_connection()
    if conn is None: return False
    cursor = conn.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None and result[0] is not None and result[0] > time.time()
    
def activate_key(user_id, key):
    conn = get_db_connection()
    if conn is None: return False, 0, None
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
    if conn is None: return "خطأ في الاتصال"
    cursor = conn.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0] is not None and result[0] > time.time():
        return datetime.fromtimestamp(result[0]).strftime("%Y-%m-%d %H:%M")
    return "غير مشترك"

def create_invite_key(admin_id, days):
    conn = get_db_connection()
    if conn is None: return None
    cursor = conn.cursor()
    key = str(uuid.uuid4()).split('-')[0] + '-' + str(uuid.uuid4()).split('-')[1]
    cursor.execute("INSERT INTO invite_keys (key, days, created_by) VALUES (%s, %s, %s)", (key, days, admin_id))
    conn.commit()
    conn.close()
    return key
    
def save_new_trade(action, entry, tp, sl, user_count, trade_type):
    conn = get_db_connection()
    if conn is None: return None
    cursor = conn.cursor()
    trade_id = "TRADE-" + str(uuid.uuid4()).split('-')[0]
    
    try:
        entry_f = float(entry) 
        tp_f = float(tp)
        sl_f = float(sl)
    except Exception as e:
        print(f"❌ فشل تحويل أنواع البيانات إلى float: {e}")
        return None 
        
    cursor.execute("""
        INSERT INTO trades (trade_id, sent_at, action, entry_price, take_profit, stop_loss, user_count, trade_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (trade_id, time.time(), action, entry_f, tp_f, sl_f, user_count, trade_type))
    
    conn.commit()
    conn.close()
    return trade_id

def get_active_trades():
    conn = get_db_connection()
    if conn is None: return []
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT trade_id, action, entry_price, take_profit, stop_loss, trade_type
        FROM trades 
        WHERE status = 'ACTIVE'
    """)
    trades = cursor.fetchall()
    conn.close()
    
    keys = ["trade_id", "action", "entry_price", "take_profit", "stop_loss", "trade_type"]
    
    trades_list = []
    for trade in trades:
        trades_list.append(dict(zip(keys, trade)))
        
    return trades_list

def update_trade_status(trade_id, exit_status, close_price):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    close_price_f = float(close_price) 
    cursor.execute("""
        UPDATE trades 
        SET status = 'CLOSED', exit_status = %s, close_price = %s
        WHERE trade_id = %s
    """, (exit_status, close_price_f, trade_id)) 
    conn.commit()
    conn.close()

def get_daily_trade_report():
    conn = get_db_connection()
    if conn is None: return "⚠️ فشل الاتصال بقاعدة البيانات."
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
<b>📈 جرد أداء AlphaTradeAI (آخر 24 ساعة)</b>
━━━━━━━━━━━━━━━
📨 <b>إجمالي الصفقات المُرسلة:</b> {total_sent}
🟢 <b>صفقات حققت الهدف (TP):</b> {hit_tp}
🔴 <b>صفقات ضربت الوقف (SL):</b> {hit_sl}
⏳ <b>الصفقات لا تزال نشطة:</b> {active_trades}
"""
    
    latest_active = next((t for t in reversed(trades) if t[1] == 'ACTIVE'), None)
    if latest_active:
        action, _, _, entry, tp, sl, _ = latest_active
        report_msg += "\n<b>آخر صفقة نشطة:</b>\n"
        report_msg += f"  - {action} @ {entry:,.2f}\n"
        report_msg += f"  - TP: {tp:,.2f} | SL: {sl:,.2f}"

    return report_msg

def fetch_ohlcv_data(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    try:
        api_key = os.getenv("BYBIT_API_KEY", "")
        secret = os.getenv("BYBIT_SECRET", "")
        
        exchange_class = getattr(ccxt, CCXT_EXCHANGE)
        
        exchange_config = {}
        if CCXT_EXCHANGE.lower() == 'bybit' and api_key and secret:
             exchange_config = {'apiKey': api_key, 'secret': secret}
             
        exchange = exchange_class(exchange_config)
        
        exchange.load_markets()
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        
        if ohlcv and len(ohlcv) >= limit: 
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            return df
        
        return pd.DataFrame()

    except Exception as e:
        raise Exception(f"فشل جلب بيانات OHLCV من CCXT ({CCXT_EXCHANGE}): {e}")

def fetch_current_price_ccxt(symbol: str) -> float or None:
    try:
        api_key = os.getenv("BYBIT_API_KEY", "")
        secret = os.getenv("BYBIT_SECRET", "")
        
        exchange_class = getattr(ccxt, CCXT_EXCHANGE)

        exchange_config = {}
        if CCXT_EXCHANGE.lower() == 'bybit' and api_key and secret:
             exchange_config = {'apiKey': api_key, 'secret': secret}
             
        exchange = exchange_class(exchange_config)
             
        exchange.load_markets()
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['ask']) if 'ask' in ticker and ticker['ask'] is not None else float(ticker['last'])
        
    except Exception as e:
        print(f"❌ فشل جلب السعر اللحظي من CCXT ({CCXT_EXCHANGE}): {e}.")
        return None

# =============== وظيفة التحقق من عطلة نهاية الأسبوع (توقف الرسائل الدورية) ===============

def is_weekend_closure():
    """التحقق مما إذا كان إغلاق عطلة نهاية الأسبوع (الجمعة 21:00 UTC إلى الأحد 21:00 UTC)."""
    now_utc = datetime.now(timezone.utc) 
    weekday = now_utc.weekday() 
    
    # السبت (يوم 5) أو الأحد (يوم 6) قبل الساعة 21:00
    if weekday == 5 or (weekday == 6 and now_utc.hour < 21): 
        return True
    
    # الجمعة (يوم 4) بعد الساعة 21:00
    if weekday == 4 and now_utc.hour >= 21:
        return True

    return False 

def calculate_adx(df, window=14):
    df['tr'] = pd.concat([df['High'] - df['Low'], (df['High'] - df['Close'].shift()).abs(), (df['Low'] - df['Close'].shift()).abs()], axis=1).max(axis=1)
    df['up'] = df['High'] - df['High'].shift()
    df['down'] = df['Low'].shift() - df['Low']
    df['+DM'] = df['up'].where((df['up'] > 0) & (df['up'] > df['down']), 0).fillna(0) 
    df['-DM'] = df['down'].where((df['down'] > 0) & (df['down'] > df['up']), 0).fillna(0) 
    
    def smooth(series, periods):
        return series.ewm(com=periods - 1, adjust=False).mean()
        
    df['+DMS'] = smooth(df['+DM'], window)
    df['-DMS'] = smooth(df['-DM'], window)
    df['TRS'] = smooth(df['tr'], window)
    
    df['+DI'] = (df['+DMS'] / df['TRS'].replace(0, 1e-10)).fillna(0) * 100
    df['-DI'] = (df['-DMS'] / df['TRS'].replace(0, 1e-10)).fillna(0) * 100
    
    df['DX'] = (abs(df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI']).replace(0, 1e-10)).fillna(0) * 100
    df['ADX'] = smooth(df['DX'], window)
    
    return df

def get_signal_and_confidence(symbol: str, is_admin_manual: bool) -> tuple[str, float, str, float, float, float, float, str]:
    
    global SL_FACTOR, SCALPING_RR_FACTOR, LONGTERM_RR_FACTOR, ADX_SCALPING_MIN, ADX_LONGTERM_MIN, BB_PROXIMITY_THRESHOLD, MIN_FILTERS_FOR_98, MAX_SL_DISTANCE, MIN_SL_DISTANCE, MIN_FILTERS_FOR_85
    
    best_action = "HOLD"
    best_confidence = 0.0
    best_entry = 0.0
    best_sl = 0.0
    best_tp = 0.0
    best_sl_distance = 0.0
    best_trade_type = "NONE"

    DISPLAY_SYMBOL = "XAUUSD" 
    price_info_msg = ""
    
    try:
        data_3m = fetch_ohlcv_data(symbol, "3m", limit=200)   
        data_5m = fetch_ohlcv_data(symbol, "5m", limit=200)
        data_15m = fetch_ohlcv_data(symbol, "15m", limit=200)
        data_30m = fetch_ohlcv_data(symbol, "30m", limit=200)
        data_1h = fetch_ohlcv_data(symbol, "1h", limit=200) 
        
        if data_3m.empty or data_5m.empty or data_15m.empty or data_30m.empty or data_1h.empty: 
            return f"❌ لا تتوفر بيانات كافية للتحليل لرمز التداول: <b>{DISPLAY_SYMBOL}</b>.", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE"

        current_spot_price = fetch_current_price_ccxt(symbol)
        price_source = CCXT_EXCHANGE
        
        if current_spot_price is None:
            current_spot_price = float(data_3m['Close'].iloc[-1]) 
            price_source = f"تحليل ({CCXT_EXCHANGE})" 
            
        entry_price = current_spot_price 
        latest_time = data_3m.index[-1].strftime('%Y-%m-%d %H:%M:%S') 

        # === حساب المؤشرات على 3m
        data_3m['EMA_5'] = data_3m['Close'].ewm(span=5, adjust=False).mean()
        data_3m['EMA_20'] = data_3m['Close'].ewm(span=20, adjust=False).mean()
        
        delta_3m = data_3m['Close'].diff()
        gain_3m = delta_3m.where(delta_3m > 0, 0)
        loss_3m = -delta_3m.where(delta_3m < 0, 0)
        RS_3m = gain_3m.ewm(com=14-1, min_periods=14, adjust=False).mean() / loss_3m.ewm(com=14-1, min_periods=14, adjust=False).mean().replace(0, 1e-10)
        data_3m['RSI'] = 100 - (100 / (1 + RS_3m))
        
        high_low = data_3m['High'] - data_3m['Low']
        high_close = (data_3m['High'] - data_3m['Close'].shift()).abs()
        low_close = (data_3m['Low'] - data_3m['Close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        data_3m['ATR'] = tr.rolling(14).mean()
        
        current_atr = float(data_3m['ATR'].iloc[-1])
        current_rsi = float(data_3m['RSI'].iloc[-1])
        
        MIN_ATR_THRESHOLD = 1.2  
        
        if current_atr < MIN_ATR_THRESHOLD:
            price_info_msg = f"""
📊 آخر سعر لـ <b>{DISPLAY_SYMBOL}</b> (المصدر: {price_source})
السعر: ${entry_price:,.2f} | الوقت: {latest_time} UTC

⚠️ <b>تحذير:</b> السوق هادئ جداً (ATR: {current_atr:.2f} &lt; {MIN_ATR_THRESHOLD}).
"""
            return price_info_msg, 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE"


        # === حساب المؤشرات على 5m 
        data_5m = calculate_adx(data_5m)
        current_adx_5m = float(data_5m['ADX'].iloc[-1])
        data_5m['EMA_10'] = data_5m['Close'].ewm(span=10, adjust=False).mean()
        data_5m['EMA_30'] = data_5m['Close'].ewm(span=30, adjust=False).mean()
        htf_trend_5m = "BULLISH" if data_5m['EMA_10'].iloc[-1] > data_5m['EMA_30'].iloc[-1] else "BEARISH"
        data_5m['SMA_200'] = data_5m['Close'].rolling(window=200).mean()
        latest_sma_200_5m = float(data_5m['SMA_200'].iloc[-1])
        data_5m['BB_MA'] = data_5m['Close'].rolling(window=20).mean()
        data_5m['BB_STD'] = data_5m['Close'].rolling(window=20).std()
        data_5m['BB_UPPER'] = data_5m['BB_MA'] + (data_5m['BB_STD'] * 2)
        data_5m['BB_LOWER'] = data_5m['BB_MA'] - (data_5m['BB_STD'] * 2)
        latest_bb_lower_5m = float(data_5m['BB_LOWER'].iloc[-1])
        latest_bb_upper_5m = float(data_5m['BB_UPPER'].iloc[-1])
        
        # === حساب المؤشرات على 15m 
        data_15m = calculate_adx(data_15m)
        current_adx_15m = float(data_15m['ADX'].iloc[-1])
        data_15m['EMA_10'] = data_15m['Close'].ewm(span=10, adjust=False).mean()
        data_15m['EMA_30'] = data_15m['Close'].ewm(span=30, adjust=False).mean()
        htf_trend_15m = "BULLISH" if data_15m['EMA_10'].iloc[-1] > data_15m['EMA_30'].iloc[-1] else "BEARISH"
        
        # === حساب المؤشرات على 30m
        data_30m['EMA_10'] = data_30m['Close'].ewm(span=10, adjust=False).mean()
        data_30m['EMA_30'] = data_30m['Close'].ewm(span=30, adjust=False).mean()
        htf_trend_30m = "BULLISH" if data_30m['EMA_10'].iloc[-1] > data_30m['EMA_30'].iloc[-1] else "BEARISH"
        data_30m['SMA_200'] = data_30m['Close'].rolling(window=200).mean() 
        latest_sma_200_30m = float(data_30m['SMA_200'].iloc[-1])
        
        # === حساب المؤشرات على 1h
        data_1h['EMA_10'] = data_1h['Close'].ewm(span=10, adjust=False).mean()
        data_1h['EMA_30'] = data_1h['Close'].ewm(span=30, adjust=False).mean()
        htf_trend_1h = "BULLISH" if data_1h['EMA_10'].iloc[-1] > data_1h['EMA_30'].iloc[-1] else "BEARISH"
        
        price_info_msg = f"""
📊 آخر سعر لـ <b>{DISPLAY_SYMBOL}</b> (المصدر: {price_source})
السعر: ${entry_price:,.2f} | الوقت: {latest_time} UTC

<b>تحليل المؤشرات:</b>
- <b>RSI (3m):</b> {current_rsi:.2f} 
- <b>ATR (3m):</b> {current_atr:.2f} 
- <b>ADX (5m):</b> {current_adx_5m:.2f} 
- <b>ADX (15m):</b> {current_adx_15m:.2f} 
- <b>SMA 200 (5m):</b> {latest_sma_200_5m:,.2f}
- <b>الاتجاهات (5m/15m/30m/1h):</b> {htf_trend_5m[0]}/{htf_trend_15m[0]}/{htf_trend_30m[0]}/{htf_trend_1h[0]}
"""
        
        # ===============================================
        # === 1. محاولة استخراج إشارة LONG-TERM (الأولوية) ===
        # ===============================================
        
        passed_filters_lt = 0
        
        is_buy_signal_15m = (data_15m['EMA_10'].iloc[-2] <= data_15m['EMA_30'].iloc[-2] and data_15m['EMA_10'].iloc[-1] > data_15m['EMA_30'].iloc[-1])
        is_sell_signal_15m = (data_15m['EMA_10'].iloc[-2] >= data_15m['EMA_30'].iloc[-2] and data_15m['EMA_10'].iloc[-1] < data_15m['EMA_30'].iloc[-1])

        if is_buy_signal_15m or is_sell_signal_15m:
            
            # فلتر 1: EMA Crossover 15m (EMA)
            passed_filters_lt += 1
            
            # فلتر 2: الاتجاه قوي على 15m (ADX)
            if current_adx_15m >= ADX_LONGTERM_MIN:
                passed_filters_lt += 1
                
            # فلتر 3: توافق 30m (HTF)
            if (htf_trend_30m == "BULLISH" and is_buy_signal_15m) or (htf_trend_30m == "BEARISH" and is_sell_signal_15m):
                passed_filters_lt += 1
                
            # فلتر 4: توافق 1h (HTF)
            if (htf_trend_1h == "BULLISH" and is_buy_signal_15m) or (htf_trend_1h == "BEARISH" and is_sell_signal_15m):
                passed_filters_lt += 1
                
            # فلتر 5: SMA 200 (30m)
            if (entry_price > latest_sma_200_30m and is_buy_signal_15m) or (entry_price < latest_sma_200_30m and is_sell_signal_15m):
                passed_filters_lt += 1
                
            # فلتر 6: DI Crossover (15m)
            di_pass = (data_15m['+DI'].iloc[-1] > data_15m['-DI'].iloc[-1] and is_buy_signal_15m) or (data_15m['+DI'].iloc[-1] < data_15m['-DI'].iloc[-1] and is_sell_signal_15m)
            if di_pass:
                passed_filters_lt += 1
                
            # فلتر 7: RSI (5m) ليس في منطقة التشبع الشديد (RSI)
            rsi_pass = (current_rsi < 85 and is_buy_signal_15m) or (current_rsi > 15 and is_sell_signal_15m)
            if rsi_pass:
                 passed_filters_lt += 1
                 
            calculated_confidence_lt = passed_filters_lt / MIN_FILTERS_FOR_98 

            if calculated_confidence_lt >= best_confidence:
                
                best_confidence = calculated_confidence_lt
                best_action = "BUY" if is_buy_signal_15m else "SELL"
                best_trade_type = "LONG_TERM"
                
                confidence_perc = best_confidence * 100 
                
                if confidence_perc >= 90.0:
                    dynamic_sl_factor = 3.0 - (confidence_perc - 90.0) * ((3.0 - 1.5) / (98.0 - 90.0))
                else:
                    dynamic_sl_factor = 3.0 
                    
                risk_amount_unlimited = current_atr * dynamic_sl_factor 
                
                risk_amount = max(MIN_SL_DISTANCE, min(risk_amount_unlimited, MAX_SL_DISTANCE))
                
                best_sl_distance = risk_amount
                rr_factor = LONGTERM_RR_FACTOR
                
                if best_action == "BUY":
                    best_sl = entry_price - risk_amount 
                    best_tp = entry_price + (risk_amount * rr_factor) 
                else: # SELL
                    best_sl = entry_price + risk_amount
                    best_tp = entry_price - (risk_amount * rr_factor)
                
                best_entry = entry_price
        
        # ===============================================
        # === 2. محاولة استخراج إشارة SCALPING (الخيار الثاني) ===
        # ===============================================
        
        passed_filters_sc = 0
            
        ema_fast_prev = data_3m['EMA_5'].iloc[-2] 
        ema_slow_prev = data_3m['EMA_20'].iloc[-2] 
        ema_fast_current = data_3m['EMA_5'].iloc[-1] 
        ema_slow_current = data_3m['EMA_20'].iloc[-1] 
            
        is_buy_signal_1m = (ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current)
        is_sell_signal_1m = (ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current)

        if is_buy_signal_1m or is_sell_signal_1m: 
                
            # فلتر 1: EMA Crossover 3m (EMA)
            passed_filters_sc += 1
                
            # فلتر 2: الاتجاه قوي على 5m (ADX)
            if current_adx_5m >= ADX_SCALPING_MIN:
                passed_filters_sc += 1
                    
            # فلتر 3: توافق 15m (HTF)
            if (htf_trend_15m == "BULLISH" and is_buy_signal_1m) or (htf_trend_15m == "BEARISH" and is_sell_signal_1m):
                passed_filters_sc += 1
                    
            # فلتر 4: RSI (في منطقة زخم جيد)
            rsi_ok_buy = (current_rsi > 40 and current_rsi < 80)
            rsi_ok_sell = (current_rsi < 60 and current_rsi > 20)
            if (rsi_ok_buy and is_buy_signal_1m) or (rsi_ok_sell and is_sell_signal_1m):
                passed_filters_sc += 1
                    
            # فلتر 5: البولينجر باند (BB)
            bb_ok_buy = (entry_price - latest_bb_lower_5m) < BB_PROXIMITY_THRESHOLD and entry_price > latest_bb_lower_5m
            bb_ok_sell = (latest_bb_upper_5m - entry_price) < BB_PROXIMITY_THRESHOLD and entry_price < latest_bb_upper_5m
            
            if (bb_ok_buy and is_buy_signal_1m) or (bb_ok_sell and is_sell_signal_1m):
                passed_filters_sc += 1

            # فلتر 6: SMA 200 (5m)
            sma_ok_buy = entry_price > latest_sma_200_5m
            sma_ok_sell = entry_price < latest_sma_200_5m
            
            if (sma_ok_buy and is_buy_signal_1m) or (sma_ok_sell and is_sell_signal_1m):
                passed_filters_sc += 1
                    
            # فلتر 7: توافق 5m (HTF)
            if (htf_trend_5m == "BULLISH" and is_buy_signal_1m) or (htf_trend_5m == "BEARISH" and is_sell_signal_1m):
                 passed_filters_sc += 1
                
            calculated_confidence_sc = passed_filters_sc / MIN_FILTERS_FOR_98 

            if calculated_confidence_sc > best_confidence:
                
                best_confidence = calculated_confidence_sc
                best_action = "BUY" if is_buy_signal_1m else "SELL"
                best_trade_type = "SCALPING"
                
                confidence_perc = best_confidence * 100 
                
                if confidence_perc >= 90.0:
                    dynamic_sl_factor = 3.0 - (confidence_perc - 90.0) * ((3.0 - 1.5) / (98.0 - 90.0))
                else:
                    dynamic_sl_factor = 3.0 
                    
                risk_amount_unlimited = current_atr * dynamic_sl_factor 
                
                risk_amount = max(MIN_SL_DISTANCE, min(risk_amount_unlimited, MAX_SL_DISTANCE))
                
                best_sl_distance = risk_amount
                rr_factor = SCALPING_RR_FACTOR
                
                if best_action == "BUY":
                    best_sl = entry_price - risk_amount 
                    best_tp = entry_price + (risk_amount * rr_factor) 
                else: # SELL
                    best_sl = entry_price + risk_amount
                    best_tp = entry_price - (risk_amount * rr_factor)
                    
                best_entry = entry_price
        
        if best_action == "HOLD":
             return price_info_msg, 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE"
            
        return price_info_msg, best_confidence, best_action, best_entry, best_sl, best_tp, best_sl_distance, best_trade_type
        
    except Exception as e:
        print(f"❌ فشل حرج في جلب بيانات التداول أو التحليل: {e}")
        return f"❌ فشل حرج في جلب بيانات التداول أو التحليل: {h(str(e))}", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE"


async def send_auto_trade_signal(confidence_target: float):
    
    threshold = confidence_target
    threshold_percent = int(threshold * 100)
    
    print(f"🔎 بدأ البحث التلقائي عن صفقات {threshold_percent}%...")
    
    try:
        price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type = get_signal_and_confidence(TRADE_SYMBOL, False)
    except Exception as e:
        print(f"❌ خطأ حرج أثناء التحليل التلقائي {threshold_percent}%: {e}")
        return

    confidence_percent = confidence * 100
    DISPLAY_SYMBOL = "XAUUSD" 
    
    rr_factor_used = SCALPING_RR_FACTOR if trade_type == "SCALPING" else LONGTERM_RR_FACTOR
    
    min_confidence_to_send = CONFIDENCE_THRESHOLD_85 # 0.90
    min_filters_to_send = MIN_FILTERS_FOR_85 # 6

    current_filters_passed = int(confidence * MIN_FILTERS_FOR_98) 

    if action != "HOLD" and confidence >= min_confidence_to_send and current_filters_passed >= min_filters_to_send:
        
        alert_confidence_perc = 98 if confidence >= CONFIDENCE_THRESHOLD_98 else 90
        
        print(f"✅ إشارة {action} قوية جداً تم العثور عليها ({trade_type}) (الثقة: {confidence_percent:.2f}%). جارٍ الإرسال...")
        
        trade_type_msg = f"<b>SCALPING / HIGH MOMENTUM</b>" if trade_type == "SCALPING" else f"<b>LONG-TERM / SWING</b>"
        
        trade_msg = f"""
🚨 TRADE TYPE: {trade_type_msg} 🚨
{('🟢' if action == 'BUY' else '🔴')} <b>ALPHA TRADE ALERT - {alert_confidence_perc}% SIGNAL!</b> {('🟢' if action == 'BUY' else '🔴')}
━━━━━━━━━━━━━━━
📈 <b>PAIR:</b> {DISPLAY_SYMBOL} 
🔥 <b>ACTION:</b> <b>{action}</b> (Market Execution)
💰 <b>ENTRY:</b> ${entry:,.2f}
🎯 <b>TAKE PROFIT (TP):</b> ${tp:,.2f}
🛑 <b>STOP LOSS (SL):</b> ${sl:,.2f}
🔒 <b>SUCCESS RATE:</b> <b>{confidence_percent:.2f}%</b> ({current_filters_passed}/{MIN_FILTERS_FOR_98} Filters)
⚖️ <b>RISK/REWARD:</b> 1:{rr_factor_used:.1f} (SL/TP)
━━━━━━━━━━━━━━━
⚠️ <b>ملاحظة هامة (فرق السعر):</b> السعر المعروض هو من منصة {CCXT_EXCHANGE}. يرجى التنفيذ <b>فوراً</b> على سعر السوق في منصتك الخاصة (MT4/5) مع الأخذ في الاعتبار فوارق الأسعار البسيطة.
"""
        all_users = get_all_users_ids()
        vip_users = [uid for uid, is_banned in all_users if is_banned == 0 and is_user_vip(uid)]
        
        trade_id = save_new_trade(action, entry, tp, sl, len(vip_users), trade_type)
        
        if trade_id:
            log_auto_trade_sent(trade_id, confidence, action, trade_type) 
            
            for uid in vip_users:
                try:
                    await bot.send_message(uid, trade_msg)
                except Exception:
                    pass
            
            admin_confirmation_msg = f"✅ تم إرسال صفقة تلقائية ({confidence_percent:.2f}%) لـ {DISPLAY_SYMBOL}: <b>{action}</b> عند ${entry:,.2f}. تم الإرسال إلى {len(vip_users)} مستخدم VIP. ID: {trade_id}"
            if ADMIN_ID != 0:
                 await bot.send_message(ADMIN_ID, admin_confirmation_msg, parse_mode="HTML")
                 
    elif action != "HOLD":
         print(f"❌ لم يتم العثور على إشارة {threshold_percent}% قوية. الثقة: {confidence_percent:.2f}%. الفلاتر المارة: {current_filters_passed}/{min_filters_to_send}.")
    else:
         print(f"❌ لا توجد إشارة واضحة {threshold_percent}% (HOLD).")


POSITIVE_HEADINGS = [
    "🚀 تحليلاتنا تعمل باستمرار!",
    "💡 لا تزال AlphaTradeAI في الخدمة.",
    "📈 نحن نراقب السوق من أجلك.",
    "🛡️ نظام التداول المؤتمت نشط.",
    "⏱️ فرصتك التالية قادمة قريباً."
]

ACTION_PHRASES = [
    "نقوم بتحليل الأطر الزمنية المتعددة",
    "نبحث في عمق البيانات عن أقوى الإشارات",
    "نراقب سلوك المتداولين الكبار",
    "نطبق استراتيجياتنا المتقدمة",
    "نستخدم فلاترنا السداسية للتأكيد"
]

RESULT_PHRASES = [
    "لضمان أعلى دقة ممكنة.",
    "لتحقيق أفضل نتائج لأعضائنا.",
    "لإرسال إشارات ذات ثقة عالية (90%+).",
    "للتأكد من أننا جاهزون للفرصة التالية.",
    "لنحول التقلبات إلى أرباح حقيقية."
]

async def send_periodic_activity_message():
    
    heading = random.choice(POSITIVE_HEADINGS)
    action = random.choice(ACTION_PHRASES)
    result = random.choice(RESULT_PHRASES)
    
    message = f"""
<b>{heading}</b>
━━━━━━━━━━━━━━━
{action} {result}

✅ <b>الحالة الحالية:</b> لا تزال الإشارات التلقائية لـ 98% و 90% نشطة.
"""
    
    all_users = get_all_users_ids()
    vip_users = [uid for uid, is_banned in all_users if is_banned == 0 and is_user_vip(uid)]
    
    print(f"✉️ جارٍ إرسال رسالة النشاط الدوري إلى {len(vip_users)} مستخدم VIP.")
    
    for uid in vip_users:
        try:
            await bot.send_message(uid, message, parse_mode="HTML")
        except Exception:
            pass

# =============== القوائم والأزرار ===============

def broadcast_target_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(text="✅ لجميع المستخدمين", callback_data="broadcast_target_all"),
            InlineKeyboardButton(text="🌟 للـ VIP فقط", callback_data="broadcast_target_vip")
        ],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="broadcast_cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📈 سعر السوق الحالي"), KeyboardButton(text="🔍 الصفقات النشطة")],
            [KeyboardButton(text="🔗 تفعيل مفتاح الاشتراك"), KeyboardButton(text="📝 حالة الاشتراك")],
            [KeyboardButton(text="💰 خطة الأسعار VIP"), KeyboardButton(text="💬 تواصل مع الدعم")],
            [KeyboardButton(text="ℹ️ عن AlphaTradeAI")]
        ],
        resize_keyboard=True
    )

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💡 هات أفضل إشارة الآن 📊"), 
             KeyboardButton(text="تحليل VIP ⚡️")], 
            [KeyboardButton(text="آخر 5 إرسالات تلقائية 🕒"), KeyboardButton(text="📊 جرد الصفقات اليومي")],
            [KeyboardButton(text="جرد الأداء الأسبوعي 📊"), KeyboardButton(text="👥 عدد المستخدمين")],
            [KeyboardButton(text="📢 رسالة لكل المستخدمين"), KeyboardButton(text="🔑 إنشاء مفتاح اشتراك")], 
            [KeyboardButton(text="🚫 حظر مستخدم"), KeyboardButton(text="✅ إلغاء حظر مستخدم")],
            [KeyboardButton(text="🔙 عودة للمستخدم")]
        ],
        resize_keyboard=True
    )

@dp.message(F.text == "جرد الأداء الأسبوعي 📊")
async def show_weekly_report(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    report = generate_weekly_trade_summary() 
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "آخر 5 إرسالات تلقائية 🕒")
async def show_last_auto_sends(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    
    last_trades = get_last_auto_trades(5)
    
    if not last_trades:
        await msg.reply("⚠️ لم يتم إرسال أي صفقات تلقائية (98% أو 90%) بعد.")
        return
        
    report = "📋 <b>آخر 5 صفقات تلقائية مُرسلة</b>\n━━━━━━━━━━━━━━━"
    
    for sent_at, confidence, action, trade_type in last_trades:
        send_time = datetime.fromtimestamp(sent_at).strftime('%Y-%m-%d %H:%M:%S')
        conf_perc = confidence * 100
        
        report += f"""
{('🟢' if action == 'BUY' else '🔴')} <b>{action}</b> ({trade_type})
  - <b>الثقة:</b> {conf_perc:.2f}%
  - <b>الوقت:</b> {send_time} UTC
"""
    await msg.reply(report, parse_mode="HTML")

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    welcome_msg = f"""
🤖 <b>مرحبًا بك في AlphaTradeAI!</b>
🚀 نظام ذكي يتابع سوق الذهب (XAUUSD) بأربعة فلاتر تحليلية.
اختر من القائمة 👇
"""
    await msg.reply(welcome_msg, reply_markup=user_menu())
    
@dp.message(Command("admin"))
async def admin_panel(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 ليس لديك صلاحية الوصول لهذه اللوحة.")
        return
    await msg.reply("🎛️ مرحباً بك في لوحة تحكم الأدمن!", reply_markup=admin_menu())

@dp.message(F.text == "تحليل VIP ⚡️")
async def analyze_market_now(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: 
        await msg.answer("🚫 هذه الميزة مخصصة للأدمن فقط.")
        return
    
    if is_weekend_closure():
        await msg.reply("😴 عذراً، سوق الذهب مغلق حالياً بسبب عطلة نهاية الأسبوع (الجمعة 21:00 UTC إلى الأحد 21:00 UTC).")
        return
    
    await msg.reply(f"⏳ جارٍ تحليل وضع السوق (باستخدام فلاتر {int(CONFIDENCE_THRESHOLD_98*100)}% لضمان أعلى جودة)..")
    
    price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type = get_signal_and_confidence(TRADE_SYMBOL, False)
    confidence_percent = confidence * 100
    
    price_info_msg_esc = h(price_info_msg)
    
    if action == "HOLD" or confidence < CONFIDENCE_THRESHOLD_98:
         status_msg = f"""
💡 <b>تقرير وضع السوق الحالي - XAUUSD</b>
━━━━━━━━━━━━━━━
🔎 <b>الإشارة الحالية:</b> <b>{action}</b>
🔒 <b>أقصى ثقة تم الوصول إليها:</b> <b>{confidence_percent:.2f}%</b>
❌ <b>القرار:</b> لا توجد إشارة قوية ({CONFIDENCE_THRESHOLD_98*100:.0f}%+).
━━━━━━━━━━━━━━━
{price_info_msg_esc}
"""
         await msg.answer(status_msg, parse_mode="HTML")
    
    else: 
        rr_factor_used = SCALPING_RR_FACTOR if trade_type == "SCALPING" else LONGTERM_RR_FACTOR
        trade_type_msg = f"<b>SCALPING / HIGH MOMENTUM</b>" if trade_type == "SCALPING" else f"<b>LONG-TERM / SWING</b>"
        action_esc = f"<b>{action}</b>"
        
        status_msg = f"""
💡 <b>تقرير وضع السوق الحالي - XAUUSD</b>
━━━━━━━━━━━━━━━
🔎 <b>الإشارة المتاحة:</b> <b>{action}</b> ({trade_type})
🔒 <b>الثقة:</b> <b>{confidence_percent:.2f}%</b>
✅ <b>القرار:</b> إشارة قوية جداً (98%+).
━━━━━━━━━━━━━━━
{price_info_msg_esc}

⚠️ <b>تفاصيل الصفقة:</b>
  - <b>ACTION:</b> {action_esc} 
  - <b>ENTRY:</b> ${entry:,.2f}
  - <b>TP:</b> ${tp:,.2f} | <b>SL:</b> ${sl:,.2f}
  - <b>R/R:</b> 1:{rr_factor_used:.1f}
"""
        await msg.answer(status_msg, parse_mode="HTML")

@dp.message(F.text == "💡 هات أفضل إشارة الآن 📊")
async def analyze_market_now_enhanced_admin(msg: types.Message):
    global REQUIRED_MANUAL_CONFIDENCE
    
    if msg.from_user.id != ADMIN_ID: 
        await msg.answer("🚫 هذه الميزة مخصصة للأدمن فقط.")
        return
    
    if is_weekend_closure():
        await msg.reply("😴 عذراً، سوق الذهب مغلق حالياً بسبب عطلة نهاية الأسبوع (الجمعة 21:00 UTC إلى الأحد 21:00 UTC).")
        return

    await msg.reply(f"⏳ جارٍ تحليل السوق بحثًا عن أفضل فرصة تداول حالية وعرضها بأي ثقة...")
    
    price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type = get_signal_and_confidence(TRADE_SYMBOL, True)
    
    confidence_percent = confidence * 100
    threshold_percent_90 = int(CONFIDENCE_THRESHOLD_85 * 100) 
    
    price_info_msg_esc = h(price_info_msg)
    
    if price_info_msg.startswith("❌"):
        await msg.answer(price_info_msg_esc, parse_mode="HTML") 
        return
        
    if action == "HOLD":
        await msg.answer(f"""
💡 <b>تقرير التحليل الفوري المُحسَّن - XAUUSD</b>
━━━━━━━━━━━━━━━
🔎 <b>الإشارة الحالية:</b> <b>HOLD</b> (لا يوجد زخم واضح)
🔒 <b>الثقة:</b> <b>{confidence_percent:.2f}%</b>
❌ <b>القرار:</b> لا توجد إشارة تستوفي شروط الدخول الأولية (EMA Crossover).
━━━━━━━━━━━━━━━
{price_info_msg_esc}
""", parse_mode="HTML")
        return
        
    if action != "HOLD":
         rr_factor_used = SCALPING_RR_FACTOR if trade_type == "SCALPING" else LONGTERM_RR_FACTOR
         trade_type_msg = f"<b>SCALPING / HIGH MOMENTUM</b>" if trade_type == "SCALPING" else f"<b>LONG-TERM / SWING</b>"
         
         auto_send_status = ""
         if confidence >= CONFIDENCE_THRESHOLD_98:
             auto_send_status = "🏆 <b>(جاهزة للإرسال التلقائي 98%+!)</b>"
         elif confidence >= CONFIDENCE_THRESHOLD_85: 
             auto_send_status = "✅ <b>(جاهزة للإرسال التلقائي 90%+)</b>"
         else:
             auto_send_status = "⚠️ <b>(غير كافية للإرسال التلقائي)</b> - للعرض اليدوي فقط."
             
         action_esc = f"<b>{action}</b>"
         entry_esc = f"<b>${entry:,.2f}</b>"
         tp_esc = f"<b>${tp:,.2f}</b>"
         sl_esc = f"<b>${sl:,.2f}</b>"

         trade_msg = f"""
💡 <b>تقرير التحليل الفوري المُحسَّن - XAUUSD</b>
{auto_send_status}
🚨 TRADE TYPE: {trade_type_msg} 🚨
{('🟢' if action == 'BUY' else '🔴')} <b>ALPHA TRADE SIGNAL ({confidence_percent:.2f}%)</b> {('🟢' if action == 'BUY' else '🔴')}
━━━━━━━━━━━━━━━
📈 <b>PAIR:</b> XAUUSD 
🔥 <b>ACTION:</b> {action_esc}
💰 <b>ENTRY:</b> {entry_esc}
🎯 <b>TAKE PROFIT (TP):</b> {tp_esc}
🛑 <b>STOP LOSS (SL):</b> {sl_esc}
🔒 <b>SUCCESS RATE:</b> <b>{confidence_percent:.2f}%</b> (المطلوب لـ 90%: {threshold_percent_90}%)
⚖️ <b>RISK/REWARD:</b> 1:{rr_factor_used:.1f} (SL/TP)
━━━━━━━━━━━━━━━
{price_info_msg_esc}
"""
         await msg.answer(trade_msg, parse_mode="HTML")

@dp.message(F.text == "📊 جرد الصفقات اليومي")
async def daily_inventory_report(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 ليس لديك صلاحية الوصول.")
        return
    
    report = get_daily_trade_report()
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📈 سعر السوق الحالي")
async def get_current_price(msg: types.Message):
    if is_weekend_closure():
        await msg.reply("😴 عذراً، سوق الذهب مغلق حالياً بسبب عطلة نهاية الأسبوع (الجمعة 21:00 UTC إلى الأحد 21:00 UTC).")
        return
        
    current_price = fetch_current_price_ccxt(TRADE_SYMBOL) 
    
    DISPLAY_SYMBOL = "XAUUSD" 

    if current_price is not None:
        price_msg = f"📊 السعر الحالي لـ <b>{DISPLAY_SYMBOL}</b> (المصدر: {CCXT_EXCHANGE}):\nالسعر: <b>${current_price:,.2f}</b>\nالوقت: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n⚠️ <b>ملاحظة:</b> قد يختلف هذا السعر عن منصتك الخاصة (MT4/5)."
        await msg.reply(price_msg, parse_mode="HTML")
    else:
        await msg.reply(f"❌ فشل جلب السعر اللحظي لـ {DISPLAY_SYMBOL} من {CCXT_EXCHANGE}. يرجى المحاولة لاحقاً.")

@dp.message(F.text == "🔍 الصفقات النشطة")
async def show_active_trades(msg: types.Message):
    
    if is_weekend_closure():
        await msg.reply("😴 سوق الذهب مغلق حالياً. لا توجد صفقات نشطة للتداول.")
        return
        
    active_trades = get_active_trades()
    
    if not active_trades:
        await msg.reply("✅ لا توجد حاليًا أي صفقات VIP نشطة. انتظر إشارة قادمة!")
        return
    
    report = "⏳ <b>قائمة الصفقات النشطة حالياً (XAUUSD)</b>\n━━━━━━━━━━━━━━━"
    
    for trade in active_trades:
        trade_id = trade['trade_id']
        action = trade['action']
        entry = trade['entry_price']
        tp = trade['take_profit']
        sl = trade['stop_loss']
        trade_type = trade['trade_type']
        
        signal_emoji = "🟢" if action == "BUY" else "🔴"
        
        report += f"""
{signal_emoji} <b>{action} @ ${entry:,.2f}</b> ({'سريع' if trade_type == 'SCALPING' else 'طويل'})
  - <b>TP:</b> ${tp:,.2f}
  - <b>SL:</b> ${sl:,.2f}
  - <b>ID:</b> <code>{trade_id}</code>
"""
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📝 حالة الاشتراك")
async def show_subscription_status(msg: types.Message):
    status = get_user_vip_status(msg.from_user.id)
    if status == "غير مشترك":
        await msg.reply(f"⚠️ أنت حالياً <b>غير مشترك</b> في خدمة VIP.\nللاشتراك، اطلب مفتاح تفعيل من الأدمن (@{h(ADMIN_USERNAME)}) ثم اضغط '🔗 تفعيل مفتاح الاشتراك'.")
    else:
        await msg.reply(f"✅ أنت مشترك في خدمة VIP.\nالاشتراك ينتهي في: <b>{h(status)}</b>.")

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
        await msg.reply(f"🎉 تم تفعيل مفتاح الاشتراك بنجاح!\n✅ تمت إضافة {days} يوم/أيام إلى اشتراكك.\nالاشتراك الجديد ينتهي في: <b>{h(formatted_date)}</b>.", reply_markup=user_menu())
    else:
        await msg.reply("❌ فشل تفعيل المفتاح. يرجى التأكد من صحة المفتاح وأنه لم يُستخدم من قبل.", reply_markup=user_menu())

@dp.message(F.text == "💰 خطة الأسعار VIP")
async def show_prices(msg: types.Message):
    prices_msg = f"""
🌟 <b>مفتاحك للنجاح يبدأ هنا! 🔑</b>

خدمة AlphaTradeAI تقدم لك تحليل الذهب الأوتوماتيكي بأفضل قيمة. اختر الخطة التي تناسب أهدافك:

━━━━━━━━━━━━━━━
🥇 <b>الخطة الأساسية (تجربة ممتازة)</b>
* المدة: 7 أيام
* السعر: 💰 $15 فقط

🥈 <b>الخطة الفضية (الأكثر شيوعاً)</b>
* المدة: 45 يومًا (شهر ونصف)
* السعر: 💰 $49 فقط

🥉 <b>الخطة الذهبية (صفقة التوفير)</b>
* المدة: 120 يومًا (4 أشهر)
* السعر: 💰 $99 فقط

💎 <b>الخطة البلاتينية (للمتداول الجاد)</b>
* المدة: 200 يوم (أكثر من 6 أشهر)
* السعر: 💰 $149 فقط

━━━━━━━━━━━━━━━
🛒 <b>للاشتراك وتفعيل المفتاح:</b>
يرجى التواصل مباشرة مع الأدمن: 
👤 @{h(ADMIN_USERNAME)}
"""
    await msg.reply(prices_msg)

@dp.message(F.text == "💬 تواصل مع الدعم")
async def contact_support(msg: types.Message):
    support_msg = f"""
🤝 للدعم الفني أو الاستفسارات أو طلب مفتاح اشتراك، يرجى التواصل مباشرة مع الأدمن:
🔗 @{h(ADMIN_USERNAME)}
"""
    await msg.reply(support_msg)

@dp.message(F.text == "ℹ️ عن AlphaTradeAI")
async def about_bot(msg: types.Message):
    threshold_98_percent = int(CONFIDENCE_THRESHOLD_98 * 100)
    threshold_90_percent = int(CONFIDENCE_THRESHOLD_85 * 100) 

    about_msg = f"""
🚀 <b>AlphaTradeAI: ثورة التحليل الكمّي في تداول الذهب!</b> 🚀

نحن لسنا مجرد بوت، بل منصة تحليل ذكية ومؤتمتة بالكامل، مصممة لملاحقة أكبر الفرص في سوق الذهب (XAUUSD). مهمتنا هي تصفية ضجيج السوق وتقديم إشارات <b>مؤكدة فقط</b>.

━━━━━━━━━━━━━━━
🛡️ <b>ماذا يقدم لك الاشتراك VIP؟ (ميزة القوة الخارقة)</b>
1.  <b>إشارات ثنائية الاستراتيجية (Dual Strategy):</b>
    نظامنا يبحث عن نوعين من الصفقات لتغطية جميع ظروف السوق القوية:
    * <b>Scalping:</b> R:R 1:{SCALPING_RR_FACTOR:.1f} مع فلاتر 3m/5m/15m و ADX &gt; {ADX_SCALPING_MIN}. 💡 <b>(محدث إلى 3m)</b>
    * <b>Long-Term:</b> R:R 1:{LONGTERM_RR_FACTOR:.1f} مع فلاتر 15m/30m/1h و ADX &gt; {ADX_LONGTERM_MIN}.
    
2.  <b>إشارات سداسية التأكيد (7-Tier Confirmation):</b>
    كل صفقة تُرسل يجب أن تمر بـ {MIN_FILTERS_FOR_98} فلاتر تحليلية (EMA, RSI, ADX, BB, SMA 200, توافق الأطر الزمنية, DI Crossover).

3.  <b>عتبات الثقة:</b>
    * <b>الإرسال التلقائي (الآمن):</b> لا يتم إرسال أي صفقة تلقائيًا إلا إذا تجاوزت الثقة <b>{threshold_98_percent}%</b> وتجاوزت <b>{MIN_FILTERS_FOR_98} فلاتر</b>. (جدولة: كل 3 دقائق)
    * <b>الإرسال التلقائي (المُكرر):</b> إشارات تتجاوز الثقة <b>{threshold_90_percent}%</b> وتجاوزت <b>{MIN_FILTERS_FOR_85} فلاتر</b>. (جدولة: كل 3 دقائق)

4.  <b>نقاط خروج ديناميكية:</b>
    نقاط TP و SL تتغير مع كل صفقة بناءً على التقلب الفعلي للسوق (ATR) و<b>مستوى الثقة</b> (لضمان وقف واسع عند الثقة المنخفضة ووقف ضيق عند الثقة العالية)، مما يضمن تحديد هدف ووقف مناسبين لظروف السوق الحالية.

━━━━━━━━━━━━━━━
💰 حوّل التحليل إلى ربح. لا تدع الفرص تفوتك! اضغط على '💰 خطة الأسعار VIP' للاطلاع على العروض الحالية.
"""
    await msg.reply(about_msg, parse_mode="HTML")

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
    await msg.reply(f"📊 إجمالي عدد المستخدمين المسجلين في قاعدة البيانات: <b>{total}</b>")

# ----------------------------------------------------------------------------------
# وظائف الإرسال الجماعي
# ----------------------------------------------------------------------------------

@dp.message(F.text == "📢 رسالة لكل المستخدمين")
async def prompt_broadcast_target(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminStates.waiting_broadcast_target)
    await msg.reply(
        "من هو الجمهور الذي تريد إرسال الرسالة إليه؟", 
        reply_markup=broadcast_target_keyboard() 
    )

@dp.callback_query(F.data.startswith("broadcast_target_"))
async def process_broadcast_target(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    
    target = call.data.split('_')[-1]
    
    if target == "cancel":
        await state.clear()
        await call.message.edit_text("❌ تم إلغاء عملية البث.", reply_markup=None)
        await call.answer()
        return
        
    target_msg = "لجميع المستخدمين (VIP وغير VIP)" if target == "all" else "للـ VIP فقط"
    await state.update_data(broadcast_target=target)
    await state.set_state(AdminStates.waiting_broadcast_text)
    
    await call.message.edit_text(
        f"✅ تم اختيار: <b>{h(target_msg)}</b>.\n\nالآن، يرجى إدخال نص الرسالة المراد بثها:"
    )
    await call.answer()

@dp.message(AdminStates.waiting_broadcast_text)
async def send_broadcast(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    
    data = await state.get_data()
    broadcast_target = data.get('broadcast_target', 'all')
    await state.clear()
    
    broadcast_text = msg.text
    all_users = get_all_users_ids()
    sent_count = 0
    
    await msg.reply("⏳ جارٍ إرسال الرسالة...")
    
    for uid, is_banned_status in all_users:
        if is_banned_status == 0 and uid != ADMIN_ID:
            
            should_send = False
            if broadcast_target == 'all':
                should_send = True
            elif broadcast_target == 'vip' and is_user_vip(uid): 
                should_send = True
            
            if should_send:
                try:
                    await bot.send_message(uid, broadcast_text, parse_mode="HTML")
                    sent_count += 1
                except Exception:
                    pass 
                
    target_msg = "لجميع المستخدمين غير المحظورين" if broadcast_target == 'all' else "للمشتركين VIP فقط"
    await msg.reply(f"✅ تم إرسال الرسالة بنجاح إلى <b>{sent_count}</b> مستخدم ({h(target_msg)}).", reply_markup=admin_menu())

# ----------------------------------------------------------------------------------

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
        update_ban_status(user_id_to_ban, 1) 
        await msg.reply(f"✅ تم حظر المستخدم <b>{h(str(user_id_to_ban))}</b> بنجاح.", reply_markup=admin_menu())
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
        update_ban_status(user_id_to_unban, 0) 
        await msg.reply(f"✅ تم إلغاء حظر المستخدم <b>{h(str(user_id_to_unban))}</b> بنجاح.", reply_markup=admin_menu())
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
<b>المفتاح:</b> <code>{h(key)}</code>
<b>عدد الأيام:</b> {days} يوم
"""
        await msg.reply(key_msg, parse_mode="HTML", reply_markup=admin_menu())
        
    except ValueError:
        await msg.reply("❌ عدد الأيام غير صحيح. يرجى إدخال رقم صحيح وموجب.", reply_markup=admin_menu())

# ===============================================
# === دالة متابعة الصفقات (Trade Monitoring) ===
# ===============================================

async def check_open_trades():
    
    active_trades = get_active_trades()
    
    if not active_trades:
        return

    try:
        current_price = fetch_current_price_ccxt(TRADE_SYMBOL)
        if current_price is None:
             # إذا فشل جلب السعر، لا يجب أن ننهي المهمة، بل نخرج فقط من هذه الدورة
             raise Exception("Failed to fetch price.") 
    except Exception as e:
        print(f"❌ فشل في جلب سعر السوق الحالي لمتابعة الصفقات: {e}")
        return # الخروج لانتظار الدورة التالية

    closed_count = 0
    
    for trade in active_trades:
        trade_id = trade['trade_id']
        action = trade['action']
        tp = trade['take_profit']
        sl = trade['stop_loss']
        trade_type = trade['trade_type']
        
        exit_status = None
        close_price = None
        
        if action == "BUY":
            if current_price >= tp:
                exit_status = "HIT_TP"
                close_price = tp 
            elif current_price <= sl:
                exit_status = "HIT_SL"
                close_price = sl
        
        elif action == "SELL":
            if current_price <= tp:
                exit_status = "HIT_TP"
                close_price = tp 
            elif current_price >= sl:
                exit_status = "HIT_SL"
                close_price = sl

        if exit_status:
            update_trade_status(trade_id, exit_status, close_price)
            closed_count += 1
            
            result_emoji = "🏆🎉" if exit_status == "HIT_TP" else "🛑"
            trade_type_msg = f"<b>SCALPING / HIGH MOMENTUM</b>" if trade_type == "SCALPING" else f"<b>LONG-TERM / SWING</b>"
            
            close_msg = f"""
🚨 TRADE TYPE: {trade_type_msg} 🚨
{result_emoji} <b>TRADE CLOSED!</b> {result_emoji}
━━━━━━━━━━━━━━━
📈 <b>PAIR:</b> XAUUSD 
➡️ <b>ACTION:</b> <b>{action}</b>
🔒 <b>RESULT:</b> تم الإغلاق عند <b>{exit_status.replace('HIT_', '')}</b>!
💰 <b>PRICE:</b> ${float(close_price):,.2f}
"""
            all_users = get_all_users_ids()
            for uid, is_banned_status in all_users:
                 if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
                    try:
                        await bot.send_message(uid, close_msg, parse_mode="HTML")
                    except Exception:
                        pass
                        
            if ADMIN_ID != 0:
                await bot.send_message(ADMIN_ID, f"🔔 تم إغلاق الصفقة <b>{trade_id}</b> بنجاح على: {exit_status}", parse_mode="HTML")

# ===============================================
# === إعداد المهام المجدولة (Setup Scheduled Tasks) ===
# ===============================================

async def scheduled_tasks_checker():
    """مهمة متابعة إغلاق الصفقات فقط (كل 30 ثانية)."""
    await asyncio.sleep(5) 
    while True:
        try: # 💡 بداية كتلة Try
            # تستمر حتى في عطلة نهاية الأسبوع لضمان الإغلاق الفوري عند فتح السوق
            await check_open_trades() 
        except Exception as e: # 💡 نهاية كتلة Try وبداية كتلة Except
            print(f"❌ خطأ في مهمة متابعة الصفقات (scheduled_tasks_checker): {e}") 
            
        await asyncio.sleep(TRADE_CHECK_INTERVAL)

async def trade_monitoring_98_percent():
    """مهمة التحليل المستمر وإرسال الإشارات التلقائية 98% (كل 3 دقائق)."""
    await asyncio.sleep(60) 
    while True:
        try: # 💡 بداية كتلة Try
            # 🛑 تتوقف في العطلة
            if not is_weekend_closure():
                await send_auto_trade_signal(CONFIDENCE_THRESHOLD_98)
            else:
                print("😴 عطلة نهاية الأسبوع: تم تخطي التحليل التلقائي 98%.")
        except Exception as e: # 💡 نهاية كتلة Try وبداية كتلة Except
            print(f"❌ خطأ في مهمة التحليل التلقائي 98% (trade_monitoring_98_percent): {e}")
        
        await asyncio.sleep(TRADE_ANALYSIS_INTERVAL_98)

async def trade_monitoring_85_percent():
    """مهمة التحليل المستمر وإرسال الإشارات التلقائية 90% (كل 3 دقائق)."""
    await asyncio.sleep(30) 
    while True:
        try: # 💡 بداية كتلة Try
            # 🛑 تتوقف في العطلة
            if not is_weekend_closure():
                await send_auto_trade_signal(CONFIDENCE_THRESHOLD_85) 
            else:
                print("😴 عطلة نهاية الأسبوع: تم تخطي التحليل التلقائي 90%.")
        except Exception as e: # 💡 نهاية كتلة Try وبداية كتلة Except
            print(f"❌ خطأ في مهمة التحليل التلقائي 90% (trade_monitoring_85_percent): {e}")
        
        await asyncio.sleep(TRADE_ANALYSIS_INTERVAL_85)
        
async def periodic_vip_alert():
    """مهمة إرسال رسائل النشاط الدوري لـ VIP (كل 6 ساعات)، وتتوقف في العطلة."""
    await asyncio.sleep(120) 
    while True:
        try: # 💡 بداية كتلة Try
            # 🛑 تتوقف في العطلة
            if not is_weekend_closure():
                await send_periodic_activity_message()
            else:
                 print(f"😴 عطلة نهاية الأسبوع: تم تخطي رسالة النشاط الدوري.")
        except Exception as e: # 💡 نهاية كتلة Try وبداية كتلة Except
             print(f"❌ خطأ في مهمة التنبيه الدوري (periodic_vip_alert): {e}")
             
        await asyncio.sleep(ACTIVITY_ALERT_INTERVAL) 


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject, data: Dict[str, Any],
    ) -> Any:
        user = data.get('event_from_user')
        if user is None: return await handler(event, data)
        user_id = user.id
        username = user.username or "مستخدم"
        
        state = data.get('state')
        current_state = await state.get_state() if state else None
        
        if isinstance(event, types.Message):
            add_user(user_id, username) 

        if user_id == ADMIN_ID: return await handler(event, data)

        if isinstance(event, types.Message) and (event.text == '/start' or event.text.startswith('/start ')):
             return await handler(event, data) 
        
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

        if isinstance(event, types.Message) and event.text in ["💡 هات أفضل إشارة الآن 📊", "تحليل VIP ⚡️"]:
             await event.answer("⚠️ هذه الميزة مخصصة للأدمن فقط.")
             return

        if not is_user_vip(user_id):
            if isinstance(event, types.Message) and event.text not in allowed_for_all:
                await event.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط. يرجى تفعيل مفتاح اشتراك لتتمكن من استخدامها.")
            return

        return await handler(event, data)


async def main():
    init_db()
    
    dp.message.middleware(AccessMiddleware())
    
    # المهام المجدولة (المفصول كل واحدة عن الأخرى)
    asyncio.create_task(scheduled_tasks_checker()) 
    asyncio.create_task(trade_monitoring_98_percent())
    asyncio.create_task(trade_monitoring_85_percent())
    asyncio.create_task(periodic_vip_alert())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🤖 تم إيقاف البوت بنجاح.")
    except Exception as e:
        print(f"حدث خطأ كبير: {e}")

