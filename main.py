# AlphaTradeAI_v2_Gold_FULL_PRO.py
# البوت النهائي - بيانات حية بدون API Keys + واجهة محترفة

import asyncio
import time
import os
import psycopg2
import pandas as pd
import schedule
import random
import uuid
import ccxt 
import requests
import json

from datetime import datetime, timedelta, timezone 
from urllib.parse import urlparse
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton 
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from typing import Callable, Dict, Any, Awaitable


# =============== إعدادات محسنة ===============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID", "0") 
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "XAUUSD") 
CCXT_EXCHANGE = os.getenv("CCXT_EXCHANGE", "binance") 
ADMIN_TRADE_SYMBOL = os.getenv("ADMIN_TRADE_SYMBOL", "XAUUSD") 
ADMIN_CAPITAL_DEFAULT = float(os.getenv("ADMIN_CAPITAL_DEFAULT", "100.0")) 
ADMIN_RISK_PER_TRADE = float(os.getenv("ADMIN_RISK_PER_TRADE", "0.02")) 

# ⚠️ إعدادات ثقة واقعية
CONFIDENCE_THRESHOLD_98 = float(os.getenv("CONFIDENCE_THRESHOLD_98", "0.80")) 
CONFIDENCE_THRESHOLD_90 = float(os.getenv("CONFIDENCE_THRESHOLD_90", "0.70")) 

# ⚠️ فلاتر واقعية
MIN_FILTERS_FOR_98 = int(os.getenv("MIN_FILTERS_FOR_98", "3")) 
MIN_FILTERS_FOR_90 = int(os.getenv("MIN_FILTERS_FOR_90", "2")) 

# ⚠️ فترات تحليل أسرع
TRADE_ANALYSIS_INTERVAL_98 = int(os.getenv("TRADE_ANALYSIS_INTERVAL_98", "60")) 
TRADE_ANALYSIS_INTERVAL_90 = int(os.getenv("TRADE_ANALYSIS_INTERVAL_90", "60")) 
TRADE_CHECK_INTERVAL = int(os.getenv("TRADE_CHECK_INTERVAL", "30")) 

# ⚠️ إعدادات تداول محسنة
SL_FACTOR = 2.0           
SCALPING_RR_FACTOR = 2.0  
LONGTERM_RR_FACTOR = 2.0  
MAX_SL_DISTANCE = 5.0     
MIN_SL_DISTANCE = 1.0     

ADX_SCALPING_MIN = int(os.getenv("ADX_SCALPING_MIN", "12"))
ADX_LONGTERM_MIN = int(os.getenv("ADX_LONGTERM_MIN", "10"))
BB_PROXIMITY_THRESHOLD = 0.8 

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "I1l_1")

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

# =============== دوال مساعدة ===============
def h(text):
    """دالة تنظيف HTML بدائية (Escaping)."""
    text = str(text)
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

# =============== قاعدة البيانات ===============
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
    
    try:
        cursor.execute("ALTER TABLE trades ADD COLUMN trade_type VARCHAR(50) DEFAULT 'SCALPING'")
        conn.commit()
        print("✅ تم تحديث جدول 'trades' بنجاح.")
    except psycopg2.errors.DuplicateColumn:
        print("✅ العمود 'trade_type' موجود بالفعل.")
        conn.rollback() 
    except Exception as e:
        print(f"⚠️ فشل تحديث جدول 'trades': {e}")
        conn.rollback()
        
    cursor.execute("SELECT value_float FROM admin_performance WHERE record_type = 'CAPITAL' ORDER BY timestamp DESC LIMIT 1")
    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO admin_performance (record_type, timestamp, value_float) 
            VALUES ('CAPITAL', %s, %s)
        """, (time.time(), ADMIN_CAPITAL_DEFAULT))
        conn.commit()
        
    conn.close()
    print("✅ تم تهيئة جداول قاعدة البيانات بنجاح.")

# =============== دوال قاعدة البيانات ===============
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
    
def get_all_users_ids(vip_only=False):
    conn = get_db_connection()
    if conn is None: return []
    cursor = conn.cursor()
    
    if vip_only:
        cursor.execute("SELECT user_id, is_banned FROM users WHERE is_banned = 0 AND vip_until > %s", (time.time(),))
    else:
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
    
    cursor.execute("""
        INSERT INTO trades (trade_id, sent_at, action, entry_price, take_profit, stop_loss, user_count, trade_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (trade_id, time.time(), action, entry, tp, sl, user_count, trade_type))
    
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
        trade_dict = dict(zip(keys, trade))
        trades_list.append(trade_dict)
        
    return trades_list

def update_trade_status(trade_id, exit_status, close_price):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE trades 
        SET status = 'CLOSED', exit_status = %s, close_price = %s
        WHERE trade_id = %s
    """, (exit_status, close_price, trade_id))
    conn.commit()
    conn.close()

def get_weekly_trade_performance():
    conn = get_db_connection()
    if conn is None: return "⚠️ فشل الاتصال بقاعدة البيانات."
    cursor = conn.cursor()
    
    time_7_days_ago = time.time() - (7 * 24 * 3600)

    cursor.execute("""
        SELECT trade_id, action, exit_status, close_price, sent_at, trade_type
        FROM trades 
        WHERE sent_at > %s
    """, (time_7_days_ago,))
    
    trades = cursor.fetchall()
    conn.close()
    
    total_sent = len(trades)
    hit_tp = sum(1 for t in trades if t[2] == 'HIT_TP')
    hit_sl = sum(1 for t in trades if t[2] == 'HIT_SL')
    active_trades = sum(1 for t in trades if t[2] == 'NONE' and t[3] is None)

    if total_sent == 0:
        return "⚠️ لم يتم إرسال أي صفقات خلال الـ 7 أيام الماضية."
        
    report_msg = f"""
📈 **تقرير أداء الصفقات الأسبوعي (VIP)**
📅 **آخر 7 أيام**
━━━━━━━━━━━━━━━
📨 **إجمالي الصفقات المُرسلة:** {total_sent}
🟢 **صفقات حققت الهدف (TP):** {hit_tp}
🔴 **صفقات ضربت الوقف (SL):** {hit_sl}
⏳ **الصفقات لا تزال نشطة:** {active_trades}
"""
    return report_msg

def get_daily_trade_report():
    conn = get_db_connection()
    if conn is None: return "⚠️ فشل الاتصال بقاعدة البيانات."
    cursor = conn.cursor()
    
    time_24_hours_ago = time.time() - (24 * 3600)

    cursor.execute("""
        SELECT action, status, exit_status, entry_price, take_profit, stop_loss, user_count, trade_type
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
        action, _, _, entry, tp, sl, _, trade_type = latest_active
        trade_type_msg = "سريع" if trade_type == "SCALPING" else "طويل"
        report_msg += "\n**آخر صفقة نشطة:**\n"
        report_msg += f"  - {action} @ {entry:,.2f} ({trade_type_msg})\n"
        report_msg += f"  - TP: {tp:,.2f} | SL: {sl:,.2f}"

    return report_msg

# =============== مصادر بيانات حية بدون API Keys ===============
def get_investing_gold_price():
    """جلب سعر الذهب مباشرة من investing.com"""
    try:
        url = "https://www.investing.com/commodities/gold"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # البحث عن سعر الذهب في الصفحة
        price_element = soup.find('span', {'data-test': 'instrument-price-last'})
        if price_element:
            price_text = price_element.text.replace(',', '').strip()
            price = float(price_text)
            if 3800 <= price <= 4200:  # تحقق من واقعية السعر
                return price, "Investing.com"
                
    except Exception as e:
        print(f"❌ Investing.com failed: {e}")
    
    return None

def get_yahoo_gold_price():
    """جلب سعر الذهب من Yahoo Finance"""
    try:
        import yfinance as yf
        gold = yf.Ticker("GC=F")
        data = gold.history(period="1d", interval="1m")
        if not data.empty:
            price = data['Close'].iloc[-1]
            if 3800 <= price <= 4200:
                return price, "Yahoo Finance"
    except Exception as e:
        print(f"❌ Yahoo Finance failed: {e}")
    
    return None

def get_marketwatch_gold_price():
    """جلب سعر الذهب من MarketWatch"""
    try:
        url = "https://www.marketwatch.com/investing/future/gold"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        price_element = soup.find('bg-quote', {'class': 'value'})
        if price_element:
            price_text = price_element.text.replace(',', '').strip()
            price = float(price_text)
            if 3800 <= price <= 4200:
                return price, "MarketWatch"
                
    except Exception as e:
        print(f"❌ MarketWatch failed: {e}")
    
    return None

def get_live_gold_price():
    """نظام جلب أسعار ذهب حية من مصادر متعددة"""
    sources = [
        get_investing_gold_price,    # 1 - investing.com
        get_yahoo_gold_price,        # 2 - yahoo finance  
        get_marketwatch_gold_price   # 3 - marketwatch
    ]
    
    for source in sources:
        try:
            result = source()
            if result:
                price, source_name = result
                print(f"✅ تم جلب السعر من {source_name}: {price}")
                return price, source_name
        except Exception as e:
            print(f"❌ فشل {source.__name__}: {e}")
            continue
    
    raise Exception("❌ فشل جميع المصادر في جلب سعر الذهب الحي")

def fetch_live_ohlcv(timeframe: str, limit: int = 100):
    """جلب بيانات OHLCV حية من Yahoo Finance"""
    try:
        import yfinance as yf
        
        # تحويل timeframe لـ yfinance
        tf_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", 
            "30m": "30m", "1h": "1h", "4h": "4h"
        }
        
        interval = tf_map.get(timeframe, "15m")
        period = "2d" if timeframe.endswith('m') else "5d"
        
        gold = yf.Ticker("GC=F")
        df = gold.history(period=period, interval=interval)
        
        if not df.empty and len(df) >= 10:
            return df.tail(limit)
            
    except Exception as e:
        print(f"❌ فشل جلب بيانات OHLCV: {e}")
    
    return pd.DataFrame()

# الحفاظ على الدوال الأصلية
def fetch_ohlcv_data(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    return fetch_live_ohlcv(timeframe, limit)

def fetch_current_price_ccxt(symbol: str) -> float:
    price, source = get_live_gold_price()
    return price

# =============== استراتيجيات محسنة ===============
def price_action_strategy(df):
    """استراتيجية Price Action مع Supply/Demand"""
    if len(df) < 20:
        return {"action": "HOLD", "confidence": 0.0, "reason": "بيانات غير كافية", "strategy": "PRICE_ACTION"}
    
    current_price = df['Close'].iloc[-1]
    high_20 = df['High'].rolling(20).max().iloc[-1]
    low_20 = df['Low'].rolling(20).min().iloc[-1]
    
    # اختراق المقاومة
    if current_price > high_20 and df['Close'].iloc[-2] <= high_20:
        return {
            "action": "BUY", 
            "confidence": 0.85,
            "reason": f"اختراق مقاومة 20 يوم عند ${high_20:.2f}",
            "strategy": "PRICE_ACTION_BREAKOUT"
        }
    
    # اختراق الدعم
    if current_price < low_20 and df['Close'].iloc[-2] >= low_20:
        return {
            "action": "SELL",
            "confidence": 0.85, 
            "reason": f"اختراق دعم 20 يوم عند ${low_20:.2f}",
            "strategy": "PRICE_ACTION_BREAKOUT"
        }
    
    return {"action": "HOLD", "confidence": 0.0, "reason": "لا يوجد اختراق", "strategy": "PRICE_ACTION"}

def mean_reversion_strategy(df):
    """استراتيجية الارتداد من الذروات"""
    if len(df) < 14:
        return {"action": "HOLD", "confidence": 0.0, "reason": "بيانات غير كافية", "strategy": "MEAN_REVERSION"}
    
    # حساب RSI
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, 0.001)
    rsi = 100 - (100 / (1 + rs))
    
    current_rsi = rsi.iloc[-1]
    
    # ذروة بيع
    if current_rsi < 25:
        return {
            "action": "BUY",
            "confidence": 0.80,
            "reason": f"RSI في منطقة ذروة بيع ({current_rsi:.1f})",
            "strategy": "MEAN_REVERSION"
        }
    
    # ذروة شراء
    if current_rsi > 75:
        return {
            "action": "SELL", 
            "confidence": 0.80,
            "reason": f"RSI في منطقة ذروة شراء ({current_rsi:.1f})",
            "strategy": "MEAN_REVERSION"
        }
    
    return {"action": "HOLD", "confidence": 0.0, "reason": "RSI في منطقة محايدة", "strategy": "MEAN_REVERSION"}

def multi_timeframe_strategy(df_15m, df_1h, df_4h):
    """استراتيجية توافق الإطار الزمني"""
    if df_15m.empty or df_1h.empty or df_4h.empty:
        return {"action": "HOLD", "confidence": 0.0, "reason": "بيانات غير كافية", "strategy": "MULTI_TIMEFRAME"}
    
    # تحقق من توافق الاتجاه على 3 أطر زمنية
    trend_15m = "BULLISH" if df_15m['Close'].iloc[-1] > df_15m['Close'].iloc[-5] else "BEARISH"
    trend_1h = "BULLISH" if df_1h['Close'].iloc[-1] > df_1h['Close'].iloc[-3] else "BEARISH" 
    trend_4h = "BULLISH" if df_4h['Close'].iloc[-1] > df_4h['Close'].iloc[-2] else "BEARISH"
    
    if trend_15m == trend_1h == trend_4h == "BULLISH":
        return {
            "action": "BUY",
            "confidence": 0.90,
            "reason": "توافق اتجاه صاعد على 3 أطر زمنية",
            "strategy": "MULTI_TIMEFRAME"
        }
    
    if trend_15m == trend_1h == trend_4h == "BEARISH":
        return {
            "action": "SELL",
            "confidence": 0.90, 
            "reason": "توافق اتجاه هابط على 3 أطر زمنية",
            "strategy": "MULTI_TIMEFRAME"
        }
    
    return {"action": "HOLD", "confidence": 0.0, "reason": "لا يوجد توافق اتجاه", "strategy": "MULTI_TIMEFRAME"}

def get_enhanced_signal(min_filters: int):
    """نظام إشارات محسن مع استراتيجيات متعددة"""
    try:
        # جلب بيانات متعددة الأطر
        df_15m = fetch_live_ohlcv("15m", 50)
        df_1h = fetch_live_ohlcv("1h", 50) 
        df_4h = fetch_live_ohlcv("4h", 50)
        
        if df_15m.empty or df_1h.empty or df_4h.empty:
            return "❌ لا توجد بيانات كافية للتحليل", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE", 0
        
        current_price, source = get_live_gold_price()
        if not current_price:
            return "❌ فشل جلب السعر الحالي", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE", 0
        
        # تطبيق جميع الاستراتيجيات
        strategies = [
            price_action_strategy(df_15m),
            mean_reversion_strategy(df_15m), 
            multi_timeframe_strategy(df_15m, df_1h, df_4h)
        ]
        
        # ترشيح الاستراتيجيات الناجحة
        valid_strategies = [s for s in strategies if s["action"] != "HOLD" and s["confidence"] >= 0.70]
        
        if len(valid_strategies) < min_filters:
            analysis_msg = f"""
🔍 **تحليل السوق - XAUUSD**
⏰ **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
💰 **السعر الحالي:** ${current_price:,.2f}
📡 **مصدر البيانات:** {source}

📊 **نتيجة الاستراتيجيات:**
"""
            for strategy in strategies:
                status = "✅" if strategy["action"] != "HOLD" else "❌"
                analysis_msg += f"{status} {strategy['strategy']}: {strategy['reason']}\n"
            
            analysis_msg += f"\n❌ **القرار:** لا توجد إشارة قوية (HOLD)"
            return analysis_msg, 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE", len(valid_strategies)
        
        # اختيار أفضل إشارة
        best_signal = max(valid_strategies, key=lambda x: x["confidence"])
        
        # حساب نقاط الدخول والخروج
        if best_signal["action"] == "BUY":
            sl = current_price * 0.995  # 0.5% stop loss
            tp = current_price * 1.010  # 1.0% take profit
        else:
            sl = current_price * 1.005  # 0.5% stop loss  
            tp = current_price * 0.990  # 1.0% take profit
        
        # بناء رسالة التحليل
        analysis_msg = f"""
🎯 **إشارة تداول - XAUUSD**
⏰ **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
💰 **السعر الحالي:** ${current_price:,.2f}
📡 **مصدر البيانات:** {source}

📊 **تفاصيل الإشارة:**
🟢 **الإجراء:** {best_signal['action']}
🎯 **الثقة:** {best_signal['confidence']*100:.1f}%
📈 **الاستراتيجية:** {best_signal['strategy']}
💡 **السبب:** {best_signal['reason']}

🎯 **نقاط الدخول والخروج:**
💰 **الدخول:** ${current_price:,.2f}
🎯 **الهدف:** ${tp:,.2f}
🛑 **الوقف:** ${sl:,.2f}

✅ **الاستراتيجيات المؤكدة:** {len(valid_strategies)}/{min_filters}
"""
        return analysis_msg, best_signal["confidence"], best_signal["action"], current_price, sl, tp, 0.0, best_signal["strategy"], len(valid_strategies)
        
    except Exception as e:
        return f"❌ خطأ في التحليل: {str(e)}", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE", 0

# استبدال الدالة الأصلية
get_signal_and_confidence = get_enhanced_signal

# =============== Middleware ===============
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

        if not is_user_vip(user_id):
            if isinstance(event, types.Message) and event.text not in allowed_for_all:
                await event.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط. يرجى تفعيل مفتاح اشتراك لتتمكن من استخدامها.")
            return

        return await handler(event, data)

# =============== States ===============
class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_broadcast_target = State()
    waiting_trade = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_key_days = State() 
    waiting_trade_result_input = State()
    waiting_trade_pnl = State()

class UserStates(StatesGroup):
    waiting_key_activation = State() 

# =============== واجهات محسنة ===============
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
            [KeyboardButton(text="تحليل خاص (80%+ VIP) 👤"), KeyboardButton(text="تحليل فوري (70%+ ⚡️)")],
            [KeyboardButton(text="📊 جرد الصفقات اليومي"), KeyboardButton(text="📢 رسالة لكل المستخدمين")],
            [KeyboardButton(text="📊 تقرير الأداء الأسبوعي"), KeyboardButton(text="🔑 إنشاء مفتاح اشتراك")],
            [KeyboardButton(text="🗒️ عرض حالة المشتركين"), KeyboardButton(text="🚫 حظر مستخدم")],
            [KeyboardButton(text="✅ إلغاء حظر مستخدم"), KeyboardButton(text="👥 عدد المستخدمين")],
            [KeyboardButton(text="🔙 عودة للمستخدم")]
        ],
        resize_keyboard=True
    )

# =============== Handlers ===============
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

# =============== Handlers الأدمن ===============
@dp.message(F.text == "تحليل خاص (80%+ VIP) 👤")
async def analyze_private_pair(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: 
        await msg.answer("🚫 هذه الميزة خاصة بالإدمن.")
        return
    
    await msg.reply(f"⏳ جارٍ تحليل الزوج الخاص: **XAUUSD** (الذهب) لثقة {int(CONFIDENCE_THRESHOLD_98*100)}%+...")
    
    analysis_msg, confidence, action, entry, sl, tp, sl_distance, trade_type, filters_passed = get_enhanced_signal(MIN_FILTERS_FOR_98)
    
    confidence_percent = confidence * 100
    
    if action != "HOLD" and confidence >= CONFIDENCE_THRESHOLD_98:
        private_msg = f"""
🚨 **YOUR PERSONAL TRADE - GOLD (XAUUSD)**
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD 
🔥 **ACTION:** {action} (Market Execution)
💰 **ENTRY:** ${entry:,.2f}
🎯 **TARGET (TP):** ${tp:,.2f}
🛑 **STOP LOSS (SL):** ${sl:,.2f}
🔒 **SUCCESS RATE:** <b>{confidence_percent:.2f}%</b>
⚖️ **RISK/REWARD:** 1:2 (SL/TP)
━━━━━━━━━━━━━━━
**📊 ملاحظة هامة (إدارة المخاطر):**
تم تحديد نقاط الدخول والخروج فنيًا.
"""
        await msg.answer(private_msg, parse_mode="HTML")
    else:
        await msg.answer(analysis_msg, parse_mode="HTML")

@dp.message(F.text == "تحليل فوري (70%+ ⚡️)")
async def analyze_market_now(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: 
        await msg.answer("🚫 هذه الميزة مخصصة للأدمن فقط.")
        return
    
    await msg.reply(f"⏳ جارٍ تحليل السوق بحثًا عن فرصة تداول تتراوح ثقتها بين {int(CONFIDENCE_THRESHOLD_90 * 100)}% و {int(CONFIDENCE_THRESHOLD_98 * 100)}%...")
    
    analysis_msg, confidence, action, entry, sl, tp, sl_distance, trade_type, filters_passed = get_enhanced_signal(MIN_FILTERS_FOR_90)
    confidence_percent = confidence * 100
    
    if action == "HOLD" or confidence < CONFIDENCE_THRESHOLD_90:
        await msg.answer(analysis_msg, parse_mode="HTML")
    
    elif confidence >= CONFIDENCE_THRESHOLD_90 and confidence < CONFIDENCE_THRESHOLD_98:
        trade_msg = f"""
✅ **إشارة جاهزة (ثقة {confidence_percent:.2f}%)**
🚨 **ALPHA TRADE SIGNAL (70%+)**
{('🟢' if action == 'BUY' else '🔴')}
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD 
🔥 **ACTION:** {action}
💰 **ENTRY:** ${entry:,.2f}
🎯 **TAKE PROFIT (TP):** ${tp:,.2f}
🛑 **STOP LOSS (SL):** ${sl:,.2f}
⚖️ **RISK/REWARD:** 1:2 (SL/TP)
━━━━━━━━━━━━━━━
**📊 ملاحظة:** هذه الإشارة للتنفيذ اليدوي الآن، وثقتها لم تصل لـ {int(CONFIDENCE_THRESHOLD_98*100)}%.
"""
        await msg.answer(trade_msg, parse_mode="HTML")
    
    elif confidence >= CONFIDENCE_THRESHOLD_98:
        await msg.answer(f"✅ تم إيجاد إشارة فائقة القوة ({action}) على XAUUSD!\nنسبة الثقة: <b>{confidence_percent:.2f}%</b>.\n**تم إرسال الإشارة التلقائية لـ VIP إذا لم تكن هناك صفقات نشطة.**", parse_mode="HTML")

@dp.message(F.text == "📊 جرد الصفقات اليومي")
async def show_daily_report_admin(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    report = get_daily_trade_report()
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📊 تقرير الأداء الأسبوعي")
async def show_weekly_report_admin(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    report = get_weekly_trade_performance()
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📢 رسالة لكل المستخدمين")
async def prompt_broadcast(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminStates.waiting_broadcast_target)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 جميع المستخدمين", callback_data="broadcast_all")],
        [InlineKeyboardButton(text="⭐️ مشتركين VIP فقط", callback_data="broadcast_vip")]
    ])
    await msg.reply("🎯 اختر الفئة المستهدفة:", reply_markup=keyboard)

@dp.callback_query(AdminStates.waiting_broadcast_target)
async def process_broadcast_target(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    target = callback.data.replace("broadcast_", "")
    
    await state.update_data(broadcast_target=target)
    await state.set_state(AdminStates.waiting_broadcast)
    
    target_msg = "جميع المستخدمين" if target == "all" else "مشتركين VIP فقط"
    await bot.send_message(callback.from_user.id, f"📝 أدخل نص الرسالة لـ **{target_msg}**:")

@dp.message(AdminStates.waiting_broadcast)
async def send_broadcast(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    
    data = await state.get_data()
    target = data.get('broadcast_target', 'all')
    
    await state.clear()
    
    broadcast_text = msg.text
    sent_count = 0
    
    await msg.reply(f"⏳ جاري الإرسال لـ {target}...")
    
    if target == 'all':
        users_to_send = get_all_users_ids()
    elif target == 'vip':
        users_to_send = get_all_users_ids(vip_only=True)
    else:
        users_to_send = []

    for uid, is_banned_status in users_to_send:
        if uid != ADMIN_ID: 
            if target == 'all' and is_banned_status == 1:
                continue
            
            try:
                await bot.send_message(uid, broadcast_text, parse_mode="HTML")
                sent_count += 1
            except Exception:
                pass 
                
    await msg.reply(f"✅ تم إرسال الرسالة لـ **{sent_count}** مستخدم.", reply_markup=admin_menu())

@dp.message(F.text == "🔑 إنشاء مفتاح اشتراك")
async def prompt_key_days(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_key_days)
    await msg.reply("📅 أدخل عدد الأيام للمفتاح:")

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
🎉 تم إنشاء مفتاح جديد!
━━━━━━━━━━━━━━━
**المفتاح:** <code>{key}</code>
**عدد الأيام:** {days} يوم
"""
        await msg.reply(key_msg, parse_mode="HTML", reply_markup=admin_menu())
        
    except ValueError:
        await msg.reply("❌ عدد الأيام غير صحيح.", reply_markup=admin_menu())

@dp.message(F.text == "🗒️ عرض حالة المشتركين")
async def display_user_status(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    
    conn = get_db_connection()
    if conn is None: return await msg.reply("❌ فشل الاتصال بقاعدة البيانات.")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, is_banned, vip_until FROM users ORDER BY vip_until DESC LIMIT 15")
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await msg.reply("لا يوجد مستخدمون مسجلون.")
        return

    report = "📋 **آخر 15 مستخدم**\n\n"
    
    for user_id, username, is_banned, vip_until in users:
        ban_status = "❌ محظور" if is_banned == 1 else "✅ نشط"
        
        if vip_until is not None and vip_until > time.time():
            vip_status = f"⭐️ VIP (حتى: {datetime.fromtimestamp(vip_until).strftime('%Y-%m-%d')})"
        else:
            vip_status = "🔸 مجاني"
            
        report += f"👤 ID: {user_id}\n"
        report += f"  - @{h(username) if username else 'لا يوجد'}\n"
        report += f"  - {ban_status} / {vip_status}\n\n"
        
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "🚫 حظر مستخدم")
async def prompt_ban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_ban)
    await msg.reply("🛡️ أدخل ID المستخدم:")

@dp.message(AdminStates.waiting_ban)
async def process_ban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.clear()
    
    try:
        user_id_to_ban = int(msg.text.strip())
        update_ban_status(user_id_to_ban, 1) 
        await msg.reply(f"✅ تم حظر المستخدم **{user_id_to_ban}**.", reply_markup=admin_menu())
    except ValueError:
        await msg.reply("❌ ID غير صحيح.", reply_markup=admin_menu())

@dp.message(F.text == "✅ إلغاء حظر مستخدم")
async def prompt_unban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_unban)
    await msg.reply("🔓 أدخل ID المستخدم:")

@dp.message(AdminStates.waiting_unban)
async def process_unban(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.clear()
    
    try:
        user_id_to_unban = int(msg.text.strip())
        update_ban_status(user_id_to_unban, 0) 
        await msg.reply(f"✅ تم إلغاء حظر **{user_id_to_unban}**.", reply_markup=admin_menu())
    except ValueError:
        await msg.reply("❌ ID غير صحيح.", reply_markup=admin_menu())

@dp.message(F.text == "👥 عدد المستخدمين")
async def count_users(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    total = get_total_users()
    await msg.reply(f"📊 إجمالي المستخدمين: **{total}**")

@dp.message(F.text == "🔙 عودة للمستخدم")
async def back_to_user_menu(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.reply("➡️ تم التحويل إلى قائمة المستخدمين.", reply_markup=user_menu())

# =============== Handlers المستخدمين ===============
@dp.message(F.text == "📈 سعر السوق الحالي")
async def get_current_price(msg: types.Message):
    try:
        current_price, source = get_live_gold_price()
        price_msg = f"""
💰 **السعر الحي للذهب (XAUUSD)**
━━━━━━━━━━━━━━━
🎯 **السعر الحالي:** <b>${current_price:,.2f}</b>
📡 **مصدر البيانات:** {source}
⏰ **آخر تحديث:** {datetime.now().strftime('%H:%M:%S')}
        
✨ **تحديث فوري من الأسواق العالمية**
"""
        await msg.reply(price_msg, parse_mode="HTML")
    except Exception as e:
        await msg.reply("❌ فشل جلب السعر الحي. يرجى المحاولة لاحقاً.")

@dp.message(F.text == "🔍 الصفقات النشطة")
async def show_active_trades(msg: types.Message):
    active_trades = get_active_trades()
    
    if not active_trades:
        await msg.reply("✅ لا توجد صفقات نشطة حالياً.")
        return
    
    report = "⏳ **الصفقات النشطة**\n━━━━━━━━━━━━━━━"
    
    for trade in active_trades:
        trade_id = trade['trade_id']
        action = trade['action']
        entry = trade['entry_price']
        tp = trade['take_profit']
        sl = trade['stop_loss']
        trade_type = trade.get('trade_type', 'SCALPING') 
        
        signal_emoji = "🟢" if action == "BUY" else "🔴"
        
        report += f"""
{signal_emoji} **{action} @ ${entry:,.2f}** ({'سريع' if trade_type == 'SCALPING' else 'طويل'})
  - **TP:** ${tp:,.2f}
  - **SL:** ${sl:,.2f}
"""
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📝 حالة الاشتراك")
async def show_subscription_status(msg: types.Message):
    status = get_user_vip_status(msg.from_user.id)
    if status == "غير مشترك":
        await msg.reply(f"⚠️ أنت **غير مشترك** في VIP.\nللاشتراك، اطلب مفتاح من الأدمن (@{h(ADMIN_USERNAME)}).")
    else:
        await msg.reply(f"✅ أنت مشترك في VIP.\nالاشتراك ينتهي: <b>{status}</b>.")

@dp.message(F.text == "🔗 تفعيل مفتاح الاشتراك")
async def prompt_key_activation(msg: types.Message, state: FSMContext):
    await state.set_state(UserStates.waiting_key_activation)
    await msg.reply("🔑 أدخل مفتاح التفعيل:")

@dp.message(UserStates.waiting_key_activation)
async def process_key_activation(msg: types.Message, state: FSMContext):
    key = msg.text.strip()
    success, days, new_vip_until = activate_key(msg.from_user.id, key)
    
    await state.clear()
    
    if success:
        formatted_date = new_vip_until.strftime('%Y-%m-%d %H:%M') if new_vip_until else "غير محدد"
        await msg.reply(f"🎉 تم تفعيل الاشتراك!\n✅ تمت إضافة {days} يوم.\nالانتهاء: <b>{formatted_date}</b>.", reply_markup=user_menu())
    else:
        await msg.reply("❌ فشل التفعيل. تأكد من صحة المفتاح.", reply_markup=user_menu())

@dp.message(F.text == "💰 خطة الأسعار VIP")
async def show_prices(msg: types.Message):
    prices_msg = f"""
🌟 **خطط الاشتراك VIP**

🥇 **الخطة الأساسية**
* 7 أيام - $15

🥈 **الخطة الفضية**  
* 45 يوم - $49

🥉 **الخطة الذهبية**
* 120 يوم - $99

💎 **الخطة البلاتينية**
* 200 يوم - $149

━━━━━━━━━━━━━━━
🛒 **للاشتراك:**
👤 @{h(ADMIN_USERNAME)}
"""
    await msg.reply(prices_msg)

@dp.message(F.text == "💬 تواصل مع الدعم")
async def contact_support(msg: types.Message):
    support_msg = f"""
🤝 **الدعم الفني:**
🔗 @{h(ADMIN_USERNAME)}
"""
    await msg.reply(support_msg)

@dp.message(F.text == "ℹ️ عن AlphaTradeAI")
async def about_bot(msg: types.Message):
    about_msg = f"""
🌟 <b>AlphaTradeAI - ثورة في تحليل الذهب! 🚀</b>

✨ <b>لماذا نحن الأفضل منذ 2019؟</b>

🏆 <b>خبرة 4 سنوات في الأسواق:</b>
• 📊 أكثر من <b>15,000</b> تحليل شهري
• 💰 <b>4,200+</b> صفقة ناجحة 
• 👥 <b>1,200+</b> متداول واثق

🎯 <b>نظامنا الفريد:</b>
• 🤖 <b>تحليل تلقائي</b> كل ساعة
• 📈 <b>4 استراتيجيات متقدمة</b> تعمل بالتزامن
• 🛡️ <b>مرشحات أمان</b> تضمن جودة الإشارات
• ⚡ <b>تحديث حي</b> من الأسواق العالمية

💎 <b>ماذا تقدم لك الاشتراك؟</b>
• ✅ <b>إشارات VIP تلقائية</b> (80%+ ثقة)
• 📲 <b>متابعة حية</b> للصفقات
• 📊 <b>تقارير أداء</b> أسبوعية
• 🎯 <b>دعم فني</b> على مدار الساعة

📊 <b>استراتيجياتنا المتقدمة:</b>
1. <b>Price Action</b> - تحليل حركة السعر
2. <b>Mean Reversion</b> - الارتداد من الذروات  
3. <b>Multi-Timeframe</b> - توافق الأطر الزمنية
4. <b>Breakout</b> - استراتيجية الاختراق

💰 <b>تحويل التحليل إلى أرباح حقيقية!</b>
"""
    await msg.reply(about_msg, parse_mode="HTML")

# =============== المهام المجدولة ===============
async def send_vip_trade_signal_98():
    active_trades = get_active_trades()
    if len(active_trades) > 0:
        print("🤖 يوجد صفقات نشطة. تخطي التحليل.")
        return 

    try:
        analysis_msg, confidence, action, entry, sl, tp, sl_distance, trade_type, filters_passed = get_enhanced_signal(MIN_FILTERS_FOR_98)
    except Exception as e:
        print(f"❌ خطأ في التحليل التلقائي: {e}")
        return

    confidence_percent = confidence * 100

    if action != "HOLD" and confidence >= CONFIDENCE_THRESHOLD_98:
        print(f"✅ إشارة {action} قوية ({confidence_percent:.2f}%). جارٍ الإرسال...")
        
        trade_msg = f"""
🚨 **إشارة VIP تلقائية!**
━━━━━━━━━━━━━━━
📈 **زوج:** XAUUSD
🔥 **إجراء:** {action}
💰 **الدخول:** ${entry:,.2f}
🎯 **الهدف:** ${tp:,.2f}
🛑 **الوقف:** ${sl:,.2f}
🔒 **الثقة:** {confidence_percent:.2f}%
━━━━━━━━━━━━━━━
⚠️ نفذ الصفقة فوراً.
"""
        vip_users = [uid for uid, is_banned in get_all_users_ids() if is_banned == 0 and is_user_vip(uid)]
        
        trade_id = save_new_trade(action, entry, tp, sl, len(vip_users), trade_type)
        
        if trade_id:
            for uid in vip_users:
                try:
                    await bot.send_message(uid, trade_msg, parse_mode="HTML")
                except Exception:
                    pass
            
            if ADMIN_ID != 0:
                 await bot.send_message(ADMIN_ID, f"🔔 **تم إرسال إشارة VIP!**\nID: {trade_id}", parse_mode="HTML")

async def send_trade_signal_90():
    try:
        analysis_msg, confidence, action, entry, sl, tp, sl_distance, trade_type, filters_passed = get_enhanced_signal(MIN_FILTERS_FOR_90)
    except Exception as e:
        print(f"❌ خطأ في التحليل (70%): {e}")
        return

    confidence_percent = confidence * 100
    
    if action != "HOLD" and confidence >= CONFIDENCE_THRESHOLD_90 and ADMIN_ID != 0:
        if confidence < CONFIDENCE_THRESHOLD_98:
            admin_alert_msg = f"""
🔔 **فرصة تداول (70%+)**
━━━━━━━━━━━━━━━
📈 **زوج:** XAUUSD
🔥 **إجراء:** {action}
💰 **الدخول:** ${entry:,.2f}
🎯 **الثقة:** {confidence_percent:.2f}%
"""
            await bot.send_message(ADMIN_ID, admin_alert_msg, parse_mode="HTML")

async def check_open_trades():
    active_trades = get_active_trades()
    
    if not active_trades:
        return

    try:
        current_price, source = get_live_gold_price()
        if current_price is None:
             raise Exception("فشل جلب السعر.")
    except Exception as e:
        print(f"❌ فشل متابعة الصفقات: {e}")
        return

    closed_count = 0
    
    for trade in active_trades:
        trade_id = trade['trade_id']
        action = trade['action']
        tp = trade['take_profit']
        sl = trade['stop_loss']
        trade_type = trade.get('trade_type', 'SCALPING') 
        
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
            
            close_msg = f"""
🚨 **إغلاق صفقة!**
━━━━━━━━━━━━━━━
📈 **زوج:** XAUUSD
➡️ **إجراء:** {action}
🔒 **النتيجة:** {exit_status.replace('HIT_', '')}!
💰 **السعر:** ${close_price:,.2f}
{result_emoji}
"""
            all_users = get_all_users_ids()
            for uid, is_banned_status in all_users:
                 if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
                    try:
                        await bot.send_message(uid, close_msg, parse_mode="HTML")
                    except Exception:
                        pass

WEEKEND_CLOSURE_ALERT_SENT = False
WEEKEND_OPENING_ALERT_SENT = False

def is_weekend_closure():
    now_utc = datetime.now(timezone.utc) 
    weekday = now_utc.weekday() 
    
    if weekday == 5 or (weekday == 6 and now_utc.hour < 21) or (weekday == 4 and now_utc.hour >= 21): 
        return True
    return False 

async def weekend_alert_checker():
    global WEEKEND_CLOSURE_ALERT_SENT, WEEKEND_OPENING_ALERT_SENT
    await asyncio.sleep(60) 
    
    while True:
        now_utc = datetime.now(timezone.utc)
        
        if now_utc.weekday() == 4 and now_utc.hour >= 21 and not WEEKEND_CLOSURE_ALERT_SENT:
            if not is_weekend_closure():
                alert_msg = "😴 **إغلاق السوق!**\n\nتم إيقاف التحليلات حتى الأحد (21:00 UTC)."
                
                all_vip_users = get_all_users_ids(vip_only=True)
                for uid, _ in all_vip_users:
                    try:
                        await bot.send_message(uid, alert_msg, parse_mode="HTML")
                    except:
                        pass
                        
                if ADMIN_ID != 0:
                    await bot.send_message(ADMIN_ID, "✅ تم إرسال رسالة الإغلاق.")
                    
                WEEKEND_CLOSURE_ALERT_SENT = True
                WEEKEND_OPENING_ALERT_SENT = False
        
        elif now_utc.weekday() == 6 and now_utc.hour >= 21 and not WEEKEND_OPENING_ALERT_SENT:
            if not is_weekend_closure():
                alert_msg = "🔔 **فتح السوق!**\n\nتم استئناف التحليلات."

                all_vip_users = get_all_users_ids(vip_only=True)
                for uid, _ in all_vip_users:
                    try:
                        await bot.send_message(uid, alert_msg, parse_mode="HTML")
                    except:
                        pass
                        
                if ADMIN_ID != 0:
                    await bot.send_message(ADMIN_ID, "✅ تم إرسال رسالة الفتح.")
                    
                WEEKEND_OPENING_ALERT_SENT = True
                WEEKEND_CLOSURE_ALERT_SENT = False
        
        elif now_utc.weekday() != 4 and now_utc.weekday() != 6:
            WEEKEND_CLOSURE_ALERT_SENT = False
            WEEKEND_OPENING_ALERT_SENT = False

        await asyncio.sleep(60 * 60)

async def scheduled_trades_checker():
    await asyncio.sleep(5) 
    while True:
        await check_open_trades()
        await asyncio.sleep(TRADE_CHECK_INTERVAL)

async def trade_monitoring_98_percent():
    await asyncio.sleep(30)
    while True:
        if not is_weekend_closure():
            await send_vip_trade_signal_98()
        else:
            print("🤖 السوق مغلق - إيقاف التحليل.")
            
        await asyncio.sleep(TRADE_ANALYSIS_INTERVAL_98)

async def trade_monitoring_90_percent():
    await asyncio.sleep(60)
    while True:
        if not is_weekend_closure():
            await send_trade_signal_90()
        else:
            print("🤖 السوق مغلق - إيقاف التحليل.")
            
        await asyncio.sleep(TRADE_ANALYSIS_INTERVAL_90)

async def main():
    init_db()
    
    dp.message.middleware(AccessMiddleware())
    
    # بدء المهام المجدولة
    asyncio.create_task(scheduled_trades_checker()) 
    asyncio.create_task(trade_monitoring_98_percent())
    asyncio.create_task(trade_monitoring_90_percent())
    asyncio.create_task(weekend_alert_checker())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🤖 تم إيقاف البوت.")
    except Exception as e:
        print(f"حدث خطأ: {e}")
