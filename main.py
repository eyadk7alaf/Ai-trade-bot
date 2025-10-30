import asyncio
import time
import os
import psycopg2
import pandas as pd
import schedule
import random
import uuid
import ccxt 


# --- Binance REST + unified fetcher + live analysis helpers (injected) ---
import requests, time, json
from datetime import datetime

def fetch_binance_klines(symbol, interval='1m', limit=500, logger=print):
    try:
        s = symbol.replace('/','').upper()
        if not s.endswith('USDT'):
            cand = [s, s+'USDT']
        else:
            cand = [s]
        url = 'https://api.binance.com/api/v3/klines'
        for c in cand:
            params = {'symbol': c, 'interval': interval, 'limit': limit}
            try:
                r = requests.get(url, params=params, timeout=10)
                r.raise_for_status()
                data = r.json()
                if data:
                    import pandas as _pd
                    df = _pd.DataFrame(data, columns=['OpenTime','Open','High','Low','Close','Volume','CloseTime','QAV','NumTrades','TBBase','TBQuote','Ignore'])
                    df['Open'] = df['Open'].astype(float)
                    df['High'] = df['High'].astype(float)
                    df['Low'] = df['Low'].astype(float)
                    df['Close'] = df['Close'].astype(float)
                    df['Volume'] = df['Volume'].astype(float)
                    df.index = _pd.to_datetime(df['OpenTime'], unit='ms')
                    return df[['Open','High','Low','Close','Volume']]
            except Exception as e:
                logger(f"DEBUG: binance candidate {c} failed: {e}")
        logger(f"⚠️ Binance returned no data for {symbol} (candidates={cand})")
        return None
    except Exception as e:
        logger(f"⚠️ fetch_binance_klines top error for {symbol}: {e}")
        return None

def fetch_ohlcv_unified(symbol, timeframe='1m', limit=500, logger=print):
    # try Binance REST first
    df = fetch_binance_klines(symbol, interval=timeframe, limit=limit, logger=logger)
    if df is not None and not df.empty:
        return df
    # try ccxt public fetch
    try:
        import ccxt, pandas as _pd
        ex = ccxt.binance()
        candidates = [symbol, symbol.replace('/',''), symbol.replace('/','')+'USDT', symbol.replace('/','')+'/USDT']
        for c in candidates:
            try:
                o = ex.fetch_ohlcv(c, timeframe=timeframe, limit=limit)
                if o and len(o):
                    df = _pd.DataFrame(o, columns=['Timestamp','Open','High','Low','Close','Volume'])
                    df['Timestamp'] = _pd.to_datetime(df['Timestamp'], unit='ms')
                    df.set_index('Timestamp', inplace=True)
                    return df[['Open','High','Low','Close','Volume']]
            except Exception:
                continue
    except Exception as e:
        logger(f"DEBUG: ccxt fetch failed: {e}")
    # fallback yfinance minimal
    try:
        import yfinance as yf, pandas as _pd
        yf_symbol = globals().get('YF_SYMBOL_MAPPING', {}).get(symbol.upper(), symbol)
        period = '2d' if timeframe.endswith('m') else '5d'
        interval = timeframe if timeframe.endswith('m') else ('1h' if timeframe.endswith('h') else '1d')
        dfy = yf.download(tickers=yf_symbol, period=period, interval=interval, progress=False, threads=False, auto_adjust=False)
        if dfy is not None and not dfy.empty:
            dfy.index = _pd.to_datetime(dfy.index).tz_localize(None)
            return dfy[['Open','High','Low','Close','Volume']]
    except Exception as e:
        logger(f"DEBUG: yfinance fallback failed: {e}")
    return None

def get_adx_safe(df, window=14):
    try:
        if df is None or getattr(df, 'shape', (0,))[0] < window:
            return None
        try:
            from ta.trend import ADXIndicator
            adx = ADXIndicator(high=df['High'], low=df['Low'], close=df['Close'], window=window).adx()
            return float(adx.iloc[-1]) if not adx.empty and not adx.isna().all() else None
        except Exception:
            if 'calculate_adx' in globals():
                tmp = calculate_adx(df.copy(), window=window)
                return float(tmp['ADX'].iloc[-1]) if 'ADX' in tmp.columns and len(tmp) and not tmp['ADX'].isna().all() else None
            return None
    except Exception:
        return None

def get_rsi_safe(df, window=14):
    try:
        if df is None or getattr(df, 'shape', (0,))[0] < window:
            return None
        try:
            from ta.momentum import RSIIndicator
            rsi = RSIIndicator(close=df['Close'], window=window).rsi()
            return float(rsi.iloc[-1]) if not rsi.empty and not rsi.isna().all() else None
        except Exception:
            import pandas as _pd
            delta = df['Close'].diff()
            up = delta.where(delta>0, 0.0).rolling(window).mean()
            down = -delta.where(delta<0, 0.0).rolling(window).mean()
            rs = up / down.replace(0, 1e-10)
            rsi = 100 - (100 / (1 + rs))
            return float(rsi.iloc[-1]) if not rsi.empty and not rsi.isna().all() else None
    except Exception:
        return None

def get_atr_safe(df, window=14):
    try:
        if df is None or getattr(df, 'shape', (0,))[0] < window:
            return None
        try:
            from ta.volatility import AverageTrueRange
            atr = AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close'], window=window).average_true_range()
            return float(atr.iloc[-1]) if not atr.empty and not atr.isna().all() else None
        except Exception:
            import pandas as _pd
            high = df['High']; low = df['Low']; close = df['Close']
            tr = _pd.concat([high-low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
            return float(tr.rolling(window).mean().iloc[-1]) if not tr.empty and not tr.isna().all() else None
    except Exception:
        return None

def run_analysis_for_symbol(symbol='XAUUSDT', timeframes=('1m','5m','15m','30m','1h')):
    import pandas as _pd
    results = {}
    for tf in timeframes:
        df = fetch_ohlcv_unified(symbol, timeframe=tf, limit=500)
        if df is None or df.empty:
            results[tf] = None
            continue
        adx = get_adx_safe(df, window=14)
        rsi = get_rsi_safe(df, window=14)
        atr = get_atr_safe(df, window=14)
        last_price = float(df['Close'].iloc[-1]) if 'Close' in df.columns and not df['Close'].isna().all() else None
        results[tf] = {'adx': adx, 'rsi': rsi, 'atr': atr, 'last_price': last_price, 'time': str(df.index[-1])}
    votes = {'buy':0,'sell':0,'hold':0}
    total = 0
    for tf, vals in results.items():
        if vals is None:
            continue
        total += 1
        adx = vals.get('adx'); rsi = vals.get('rsi')
        if adx is not None and adx > 25 and rsi is not None:
            if rsi < 40:
                votes['sell'] += 1
            elif rsi > 60:
                votes['buy'] += 1
            else:
                votes['hold'] += 1
        else:
            votes['hold'] += 1
    final = 'HOLD'
    if votes['buy'] > votes['sell'] and votes['buy'] >= 2:
        final = 'BUY'
    elif votes['sell'] > votes['buy'] and votes['sell'] >= 2:
        final = 'SELL'
    confidence = round((max(votes.values()) / total * 100) if total>0 else 0.0, 2)
    last_price = None; latest_time = None
    for tf in ('1m','5m','15m','30m','1h'):
        r = results.get(tf)
        if r and r.get('last_price') is not None:
            last_price = r.get('last_price'); latest_time = r.get('time'); break
    report = {'symbol': symbol, 'signal': final, 'confidence': confidence, 'votes': votes, 'results': results, 'last_price': last_price, 'time': latest_time}
    return report

_running = {'stop': False}
def start_analysis_loop(symbols=('XAUUSDT',), interval_seconds=60, logger=print):
    logger(f"Starting analysis for {symbols} every {interval_seconds}s")
    try:
        while not _running.get('stop', False):
            for sym in symbols:
                try:
                    rep = run_analysis_for_symbol(sym)
                    logger(json.dumps(rep, ensure_ascii=False))
                except Exception as e:
                    logger(f"Analysis error for {sym}: {e}")
            time.sleep(interval_seconds)
    except Exception as e:
        logger(f"start_analysis_loop error: {e}")
# --- end Binance REST helpers ---
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


# Symbol mappings to adapt between exchange and Yahoo tickers
BINANCE_SYMBOL_MAPPING = {
    "XAUUSD": "XAU/USDT",
    "XAUT": "XAU/USDT",
    "XAUT/USDT": "XAU/USDT",
    "XAUUSD/X": "XAU/USDT",
    "GOLD": "XAU/USDT",
    # add more mappings if needed
}

# Yahoo Finance mapping (ensure exists)


def safe_confidence_ratio(passed: int, total: int) -> float:
    """Return confidence percentage (0-100). Avoid division by zero and provide fallback logic."""
    try:
        if total <= 0:
            # no filters; treat as 0.0 confidence but avoid division by zero
            return 0.0
        val = (passed / total) * 100.0
        # clamp
        if val < 0: val = 0.0
        if val > 100: val = 100.0
        return round(val, 2)
    except Exception:
        return 0.0

YF_SYMBOL_MAPPING = globals().get('YF_SYMBOL_MAPPING', {
    "XAUUSD": "XAUUSD=X",
    "GOLD": "GC=F",
})


# =============== تعريف حالات FSM المُعدَّلة ===============
class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_broadcast_target = State() # 👈 حالة جديدة لاختيار هدف البث
    waiting_trade = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_key_days = State() 
    # waiting_new_capital = State() # 👈 تم إزالة هذه الحالة
    waiting_trade_result_input = State()
    waiting_trade_pnl = State()

class UserStates(StatesGroup):
    waiting_key_activation = State() 
    
# =============== إعداد البوت والمتغيرات (من Environment Variables) ===============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID", "0") 
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "XAUT/USDT") 
CCXT_EXCHANGE = os.getenv("CCXT_EXCHANGE", "binance") 
ADMIN_TRADE_SYMBOL = os.getenv("ADMIN_TRADE_SYMBOL", "XAUT/USDT") 
ADMIN_CAPITAL_DEFAULT = float(os.getenv("ADMIN_CAPITAL_DEFAULT", "100.0")) 
ADMIN_RISK_PER_TRADE = float(os.getenv("ADMIN_RISK_PER_TRADE", "0.02")) 

# ⚠️ متغيرات الثقة المُعدَّلة والمفصولة ⚠️
CONFIDENCE_THRESHOLD_98 = float(os.getenv("CONFIDENCE_THRESHOLD_98", "0.98")) # الثقة المطلوبة للإرسال التلقائي لـ VIP (98%)
CONFIDENCE_THRESHOLD_90 = float(os.getenv("CONFIDENCE_THRESHOLD_90", "0.90")) # الثقة المطلوبة للتحليل الفوري للأدمن (90%)

# ⚠️ متغيرات فحص الجدولة (كل 3 دقائق)
TRADE_ANALYSIS_INTERVAL_98 = int(os.getenv("TRADE_ANALYSIS_INTERVAL_98", "180")) # 180 ثانية = 3 دقائق
TRADE_ANALYSIS_INTERVAL_90 = int(os.getenv("TRADE_ANALYSIS_INTERVAL_90", "180")) # 180 ثانية = 3 دقائق
TRADE_CHECK_INTERVAL = int(os.getenv("TRADE_CHECK_INTERVAL", "30")) # تصحيح NameError

ALERT_INTERVAL = int(os.getenv("ALERT_INTERVAL", "14400")) # فاصل التنبيهات لا يزال كما هو

# ⚠️ المتغيرات العامة (معاملات تحديد نقاط الخروج والدخول - تم التحديث لتقليل المخاطر)
SL_FACTOR = 3.0           
SCALPING_RR_FACTOR = 1.5  
LONGTERM_RR_FACTOR = 1.5  
MAX_SL_DISTANCE = 7.0     
MIN_SL_DISTANCE = 1.5     

# فلاتر ADX الجديدة (تم تعديلها لتصبح أكثر مرونة)
ADX_SCALPING_MIN = 15
ADX_LONGTERM_MIN = 12
BB_PROXIMITY_THRESHOLD = 0.5 

# الحد الأدنى لعدد الفلاتر المارة (تم تعديل 90% هنا لـ 5 فلاتر)
MIN_FILTERS_FOR_98 = 7 
MIN_FILTERS_FOR_90 = 5 # ⚠️ تم تخفيض هذا الرقم لتلبية طلب زيادة الصفقات

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

# =========================================================================================
# === دوال مساعدة في العرض (لتعمل مع الكود القديم الذي لا يحتوي على h) ===
# =========================================================================================

def h(text):
    """دالة تنظيف HTML بدائية (Escaping)."""
    text = str(text)
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

# =============== قاعدة بيانات PostgreSQL (مع التعديل الحاسم على init_db) ===============
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

# دوال CRUD أساسية
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
    
    # 2. **التعامل مع جداول المستخدمين القديمة (Migration Fix)**
    try:
        # محاولة إضافة trade_type، سيتم تجاهل الأمر إذا كان العمود موجوداً
        cursor.execute("ALTER TABLE trades ADD COLUMN trade_type VARCHAR(50) DEFAULT 'SCALPING'")
        conn.commit()
        print("✅ تم تحديث جدول 'trades' بنجاح. تم إضافة العمود.")
    except psycopg2.errors.DuplicateColumn:
        print("✅ العمود 'trade_type' موجود بالفعل. تم تخطي التحديث.")
        conn.rollback() 
    except Exception as e:
        print(f"⚠️ فشل تحديث جدول 'trades' لسبب غير متوقع. {e}")
        conn.rollback()
        
    # 3. إعداد رأس مال الأدمن الافتراضي
    cursor.execute("SELECT value_float FROM admin_performance WHERE record_type = 'CAPITAL' ORDER BY timestamp DESC LIMIT 1")
    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO admin_performance (record_type, timestamp, value_float) 
            VALUES ('CAPITAL', %s, %s)
        """, (time.time(), ADMIN_CAPITAL_DEFAULT))
        conn.commit()
        
    conn.close()
    print("✅ تم تهيئة جداول قاعدة البيانات (PostgreSQL) بنجاح.")

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
        # VIP يعني غير محظور واشتراكه نشط
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

# دوال الاشتراكات
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

# === دوال إدارة الصفقات 
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
    
    # يجب جلب trade_type الآن لأنها موجودة في الجدول
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

# ⚠️ التعديل المطلوب: دالة جرد الصفقات الأسبوعية للمستخدمين 
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
    active_trades = sum(1 for t in trades if t[2] == 'NONE' and t[3] is None) # الصفقات التي لم تغلق بعد

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

    # ⚠️ يجب جلب trade_type لإظهارها في التقرير
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

# =============== دوال إدارة الأداء الشخصي (بدون تغيير) ===============
def get_admin_financial_status():
    conn = get_db_connection()
    if conn is None: return ADMIN_CAPITAL_DEFAULT
    cursor = conn.cursor()
    cursor.execute("SELECT value_float FROM admin_performance WHERE record_type = 'CAPITAL' ORDER BY timestamp DESC LIMIT 1")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result and result[0] is not None else ADMIN_CAPITAL_DEFAULT

def update_admin_capital(new_capital: float):
    # هذه الدالة كانت تُستخدم من زر "تعديل رأس المال" الذي تم حذفه
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("INSERT INTO admin_performance (record_type, timestamp, value_float) VALUES ('CAPITAL', %s, %s)", (time.time(), new_capital))
    conn.commit()
    conn.close()

def save_admin_trade_result(trade_symbol: str, action: str, lots: float, pnl: float):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    
    cursor.execute("INSERT INTO admin_performance (record_type, timestamp, value_float, trade_symbol, trade_action, lots_used) VALUES ('TRADE_RESULT', %s, %s, %s, %s, %s)", 
                   (time.time(), pnl, trade_symbol, action, lots))
    
    current_capital = get_admin_financial_status() 
    new_capital = current_capital + pnl
    
    cursor.execute("INSERT INTO admin_performance (record_type, timestamp, value_float) VALUES ('CAPITAL', %s, %s)", (time.time(), new_capital))
    
    conn.commit()
    conn.close()
    
def get_admin_trades_in_period(days: int = 7):
    conn = get_db_connection()
    if conn is None: return ADMIN_CAPITAL_DEFAULT, []
    cursor = conn.cursor()
    start_time = time.time() - (days * 24 * 3600)

    cursor.execute("SELECT value_float FROM admin_performance WHERE record_type = 'CAPITAL' AND timestamp < %s ORDER BY timestamp DESC LIMIT 1", (start_time,))
    start_capital_result = cursor.fetchone()
    start_capital = start_capital_result[0] if start_capital_result and start_capital_result[0] is not None else ADMIN_CAPITAL_DEFAULT

    cursor.execute("""
        SELECT value_float, trade_action, lots_used, trade_symbol, timestamp
        FROM admin_performance 
        WHERE record_type = 'TRADE_RESULT' AND timestamp >= %s
        ORDER BY timestamp ASC
    """, (start_time,))
    trades = cursor.fetchall()
    conn.close()
    return start_capital, trades

def generate_weekly_performance_report():
    start_capital, trades = get_admin_trades_in_period(days=7)
    current_capital = get_admin_financial_status()
    total_profit = sum(t[0] for t in trades)
    
    if start_capital == 0:
         percentage_gain = 0.0
    else:
        percentage_gain = ((current_capital - start_capital) / start_capital * 100)
    
    start_date = datetime.fromtimestamp(time.time() - (7 * 24 * 3600)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    
    report = f"""
📈 **** 📅 **الفترة:** {start_date} إلى {end_date}
━━━━━━━━━━━━━━━
💰 **رأس مال البداية:** ${start_capital:,.2f}
💵 **رأس مال اليوم:** ${current_capital:,.2f}
"""
    if current_capital >= start_capital:
        report += f"🟢 **صافي الربح/الخسارة:** ${total_profit:,.2f}\n"
        report += f"📊 **نسبة النمو:** <b>+{percentage_gain:.2f}%</b>"
    else:
        report += f"🔴 **صافي الربح/الخسارة:** ${total_profit:,.2f}\n"
        report += f"📊 **نسبة التراجع:** <b>{percentage_gain:.2f}%</b>"

    if trades:
        successful_trades = sum(1 for t in trades if t[0] > 0)
        losing_trades = sum(1 for t in trades if t[0] <= 0)
        report += f"\n\n✅ **الصفقات الرابحة:** {successful_trades}\n"
        report += f"❌ **الصفقات الخاسرة:** {losing_trades}"
    else:
        report += "\n\n⚠️ لم يتم تسجيل أي صفقات خاصة خلال هذه الفترة."
    return report
    
def calculate_lot_size_for_admin(symbol: str, stop_loss_distance: float) -> tuple[float, str]:
    """
    يحسب حجم اللوت المناسب بناءً على رأس مال الأدمن والمخاطرة (2%).
    """
    
    capital = get_admin_financial_status() 
    risk_percent = ADMIN_RISK_PER_TRADE
    
    if stop_loss_distance == 0 or capital <= 0:
        return 0.0, "خطأ في البيانات"
        
    risk_amount = capital * risk_percent 
    
    lot_size = risk_amount / (stop_loss_distance * 100) 
    
    lot_size = max(0.01, round(lot_size, 2))
    asset_info = "XAUT/USDT (Lot=100 units)"
    
    return lot_size, asset_info

# ===============================================
# === دوال جلب البيانات الفورية (بدون تغيير) ===
# ===============================================

def fetch_ohlcv_data(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    """
    تجلب بيانات الشموع (OHLCV) للرمز والفاصل الزمني المحدد.
    المحاولة الأولى: CCXT (الافتراضي: CCXT_EXCHANGE، عادةً 'binance') بدون مفاتيح.
    إن فشل أو لم يُرجع بيانات كافية -> fallback إلى yfinance (رمز XAUUSD=X).
    """
    # محاولة CCXT أولاً
    try:
        api_key = os.getenv("BYBIT_API_KEY", "")
        secret = os.getenv("BYBIT_SECRET", "")
        exchange_name = CCXT_EXCHANGE.lower() if CCXT_EXCHANGE else "binance"
        exchange_class = getattr(ccxt, exchange_name)
        exchange_config = {}
        # إذا وُجدت مفاتيح ونظام يطلبها (يُترك دعم المفتاح كما هو)
        if api_key and secret and exchange_name in ('bybit', 'bybitus', 'bybittest'):
            exchange_config = {'apiKey': api_key, 'secret': secret}
        exchange = exchange_class(exchange_config) if exchange_config else exchange_class()
        exchange.load_markets()
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if ohlcv and len(ohlcv) > 0:
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            df = df[['Open','High','Low','Close','Volume']]
            if len(df) >= 3:
                return df
    except Exception as e:
        print(f"CCXT fetch failed ({CCXT_EXCHANGE}): {e}")
    
# --- Fallback to yfinance ---
    try:
        import yfinance as yf
        yf_symbol = YF_SYMBOL_MAPPING.get(symbol.upper(), symbol)
        period = '2d' if timeframe.endswith('m') else '5d'
        interval = '1m' if timeframe == '1m' else ('5m' if timeframe == '5m' else '30m' if timeframe == "30m" else '60m')
        try:
            df_y = yf.download(tickers=yf_symbol, period=period, interval=interval, progress=False, threads=False, auto_adjust=False)
            if df_y is not None and not df_y.empty:
                df_y = df_y.rename(columns={'Open':'Open','High':'High','Low':'Low','Close':'Close','Volume':'Volume'})[['Open','High','Low','Close','Volume']]
                df_y.index = pd.to_datetime(df_y.index).tz_localize(None)
                return df_y
            else:
                print(f"⚠️ yfinance returned no data for {yf_symbol} (period={period}, interval={interval})")
        except Exception as e:
            print(f"⚠️ yfinance primary download failed for {yf_symbol}: {e}")

        env_fb = os.getenv('YF_FALLBACK_SYMBOL','').strip()
        if env_fb:
            try:
                df_y = yf.download(tickers=env_fb, period=period, interval=interval, progress=False, threads=False, auto_adjust=False)
                if df_y is not None and not df_y.empty:
                    print(f"✅ using YF_FALLBACK_SYMBOL {env_fb}")
                    df_y = df_y.rename(columns={'Open':'Open','High':'High','Low':'Low','Close':'Close','Volume':'Volume'})[['Open','High','Low','Close','Volume']]
                    df_y.index = pd.to_datetime(df_y.index).tz_localize(None)
                    return df_y
            except Exception as e:
                print(f"⚠️ yfinance env fallback failed for {env_fb}: {e}")

        for alt in ['GC=F','XAU=X','XAUUSD=X']:
            if alt == yf_symbol:
                continue
            try:
                df_y = yf.download(tickers=alt, period=period, interval=interval, progress=False, threads=False, auto_adjust=False)
                if df_y is not None and not df_y.empty:
                    print(f"✅ using alternative yf symbol {alt}")
                    df_y = df_y.rename(columns={'Open':'Open','High':'High','Low':'Low','Close':'Close','Volume':'Volume'})[['Open','High','Low','Close','Volume']]
                    df_y.index = pd.to_datetime(df_y.index).tz_localize(None)
                    return df_y
            except Exception as e:
                print(f"⚠️ yfinance alt {alt} failed: {e}")

        print(f"❌ No data found for {symbol} via yfinance (tried mapping, env fallback and alternatives)")
        return pd.DataFrame()
    except Exception as e:
        print(f"yfinance fallback failed: {e}")
        return pd.DataFrame()

    except Exception as e:
        print(f"❌ فشل جلب بيانات OHLCV من CCXT ({CCXT_EXCHANGE}): {e}")
        return pd.DataFrame() 



def fetch_current_price_ccxt(symbol: str) -> float or None:
    """Robust current price fetch with multiple exchange symbol attempts and retries."""
    try:
        import yfinance as yf
    except Exception:
        yf = None

    # prepare exchange name and class
    exchange_name = (CCXT_EXCHANGE or os.getenv('CCXT_EXCHANGE', 'binance')).lower() if 'CCXT_EXCHANGE' in globals() else os.getenv('CCXT_EXCHANGE', 'binance')
    try:
        exchange_class = getattr(ccxt, exchange_name)
    except Exception:
        exchange_class = getattr(ccxt, 'binance')

    # prepare candidate symbols for exchange
    ex_candidates = []
    # canonical mapping if available
    ex_candidates.append(BINANCE_SYMBOL_MAPPING.get(symbol.upper(), symbol))
    # common transformations
    sym_up = symbol.upper()
    if sym_up.endswith('USD'):
        ex_candidates.append(sym_up.replace('USD', '/USDT'))
        ex_candidates.append(sym_up.replace('USD', '/USDC'))
    ex_candidates.append(sym_up + '/USDT')
    ex_candidates.append(sym_up + '/USDC')
    # some tickers might be 'XAUT' etc.
    if sym_up.startswith('XAU') and 'XAU/USDT' not in ex_candidates:
        ex_candidates.append('XAU/USDT')
        ex_candidates.append('XAUT/USDT')
    # deduplicate while preserving order
    seen = set(); ex_candidates = [x for x in ex_candidates if x and not (x in seen or seen.add(x))]

    # 1) Try CCXT candidates with small retry
    try:
        api_key = os.getenv("BYBIT_API_KEY", "")
        secret = os.getenv("BYBIT_SECRET", "")
        exchange_config = {}
        if api_key and secret and exchange_name in ('bybit', 'bybitus', 'bybittest'):
            exchange_config = {'apiKey': api_key, 'secret': secret}
        try:
            exchange = exchange_class(exchange_config) if exchange_config else exchange_class()
        except Exception:
            exchange = exchange_class()

        for ex_sym in ex_candidates:
            if not ex_sym: 
                continue
            try:
                # try a couple of variations
                for attempt in range(2):
                    try:
                        ticker = exchange.fetch_ticker(ex_sym)
                        break
                    except Exception as e:
                        # try small modifications then retry
                        alt = ex_sym.replace('/', '')
                        try:
                            ticker = exchange.fetch_ticker(alt)
                            break
                        except Exception:
                            ticker = None
                    time.sleep(0.1)
                if ticker:
                    if isinstance(ticker, dict):
                        price = ticker.get('last') or ticker.get('close') or ticker.get('ask') or ticker.get('bid')
                        if price:
                            return float(price)
                    else:
                        try:
                            return float(ticker['last'])
                        except Exception:
                            pass
            except Exception as e:
                # catch JSON decode or other errors and continue to next candidate
                print(f"⚠️ Failed to get ticker '{ex_sym}' reason: {e}")
    except Exception as e:
        print(f"⚠️ CCXT top-level error: {e}")

    # 2) yfinance fallback and env fallback (unchanged logic)
    try:
        if yf is None:
            import yfinance as yf
        yf_sym = YF_SYMBOL_MAPPING.get(symbol.upper(), symbol)
        try:
            df = yf.download(tickers=yf_sym, period='1d', interval='1m', progress=False, threads=False, auto_adjust=False)
            if df is not None and not df.empty:
                return float(df['Close'].iloc[-1])
            else:
                print(f"⚠️ yfinance returned no data for {yf_sym}")
        except RuntimeError as ye:
            # handle Yahoo servicedown message gracefully
            print(f"⚠️ yfinance runtime error for {yf_sym}: {ye}")
        except Exception as e:
            print(f"⚠️ yfinance primary failed for {yf_sym}: {e}")

        env_fb = os.getenv('YF_FALLBACK_SYMBOL', '').strip()
        if env_fb:
            try:
                df2 = yf.download(tickers=env_fb, period='1d', interval='1m', progress=False, threads=False, auto_adjust=False)
                if df2 is not None and not df2.empty:
                    print(f"✅ Using YF_FALLBACK_SYMBOL {env_fb}")
                    return float(df2['Close'].iloc[-1])
            except Exception as e:
                print(f"⚠️ yfinance env fallback failed for {env_fb}: {e}")

        for alt in ['GC=F', 'XAU=X', 'XAUUSD=X']:
            if alt == yf_sym:
                continue
            try:
                df3 = yf.download(tickers=alt, period='1d', interval='1m', progress=False, threads=False, auto_adjust=False)
                if df3 is not None and not df3.empty:
                    print(f"✅ using alternative yf symbol {alt}")
                    return float(df3['Close'].iloc[-1])
            except Exception as e:
                print(f"⚠️ yfinance alt {alt} failed: {e}")
    except Exception as e:
        print(f"⚠️ yfinance section failed: {e}")

    print("❌ Unable to fetch current price from CCXT/yfinance for symbol: {0}".format(symbol))
    return None

# =============== برمجية وسيطة للحظر والاشتراك (Access Middleware) (تم تعديلها) ===============
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
        
        # ⚠️ الزر الذي تم حذفه غير موجود هنا الآن.
        
        if isinstance(event, types.Message) and event.text in allowed_for_all:
             return await handler(event, data) 

        if not is_user_vip(user_id):
            if isinstance(event, types.Message) and event.text not in allowed_for_all:
                await event.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط. يرجى تفعيل مفتاح اشتراك لتتمكن من استخدامها.")
            return

        return await handler(event, data)

# =============== وظائف التداول والتحليل (تم تطبيق كل التعديلات هنا) ===============

def calculate_adx(df, window=14):
    """حساب مؤشر ADX, +DI, و -DI."""
    # True Range (TR)
    df['tr'] = pd.concat([df['High'] - df['Low'], (df['High'] - df['Close'].shift()).abs(), (df['Low'] - df['Close'].shift()).abs()], axis=1).max(axis=1)
    # Directional Movement (DM)
    df['up'] = df['High'] - df['High'].shift()
    df['down'] = df['Low'].shift() - df['Low']
    df['+DM'] = df['up'].where((df['up'] > 0) & (df['up'] > df['down']), 0)
    df['-DM'] = df['down'].where((df['down'] > 0) & (df['down'] > df['up']), 0)
    
    # Exponential smoothing of TR and DMs
    def smooth(series, periods):
        return series.ewm(com=periods - 1, adjust=False).mean()
        
    df['+DMS'] = smooth(df['+DM'], window)
    df['-DMS'] = smooth(df['+DM'], window)
    df['TRS'] = smooth(df['tr'], window)
    
    # Directional Indicators (DI)
    df['+DI'] = (df['+DMS'] / df['TRS']) * 100
    df['-DI'] = (df['-DMS'] / df['TRS']) * 100
    
    # Directional Index (DX) and Average Directional Index (ADX)
    df['DX'] = (abs(df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI'])).fillna(0) * 100
    df['ADX'] = smooth(df['DX'], window)
    
    return df

def get_signal_and_confidence(symbol: str, min_filters: int) -> tuple[str, float, str, float, float, float, float, str, int]:
    """
    تحليل مزدوج (Scalping / Long-Term) باستخدام 7 فلاتر لتحديد إشارة فائقة القوة.
    min_filters: الحد الأدنى المطلوب من الفلاتر (5 للـ 90%، 7 للـ 98%).
    النتيجة الإضافية: trade_type، عدد الفلاتر
    """
    global SL_FACTOR, SCALPING_RR_FACTOR, LONGTERM_RR_FACTOR, ADX_SCALPING_MIN, ADX_LONGTERM_MIN, BB_PROXIMITY_THRESHOLD
    
    try:
        # جلب البيانات لجميع الأطر الزمنية المطلوبة
        data_1m = fetch_ohlcv_data(symbol, "1m", limit=200)
        data_5m = fetch_ohlcv_data(symbol, "5m", limit=200)
        data_15m = fetch_ohlcv_data(symbol, "15m", limit=200)
        data_30m = fetch_ohlcv_data(symbol, "30m", limit=200)
        data_1h = fetch_ohlcv_data(symbol, "1h", limit=200) # إضافة 1h

        DISPLAY_SYMBOL = "XAUUSD" 
        
        # ************** شرط البيانات الكافية **************
        if data_1m.empty or data_5m.empty or data_15m.empty or data_30m.empty or data_1h.empty: 
            return f"لا تتوفر بيانات كافية للتحليل لرمز التداول: {h(DISPLAY_SYMBOL)}.", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE", 0

        # ************** جلب السعر اللحظي **************
        current_spot_price = fetch_current_price_ccxt(symbol)
        price_source = CCXT_EXCHANGE
        
        if current_spot_price is None:
            current_spot_price = data_1m['Close'].iloc[-1].item()
            price_source = f"تحليل ({CCXT_EXCHANGE})" 
            
        entry_price = current_spot_price 
        latest_time = data_1m.index[-1].strftime('%Y-%m-%d %H:%M:%S')

        # === حساب المؤشرات على 1m (للسكالبينج)
        data_1m['EMA_5'] = data_1m['Close'].ewm(span=5, adjust=False).mean()
        data_1m['EMA_20'] = data_1m['Close'].ewm(span=20, adjust=False).mean()
        
        delta_1m = data_1m['Close'].diff()
        gain_1m = delta_1m.where(delta_1m > 0, 0)
        loss_1m = -delta_1m.where(delta_1m < 0, 0)
        RS_1m = gain_1m.ewm(com=14-1, min_periods=14, adjust=False).mean() / loss_1m.ewm(com=14-1, min_periods=14, adjust=False).mean().replace(0, 1e-10)
        data_1m['RSI'] = 100 - (100 / (1 + RS_1m))
        
        high_low = data_1m['High'] - data_1m['Low']
        high_close = (data_1m['High'] - data_1m['Close'].shift()).abs()
        low_close = (data_1m['Low'] - data_1m['Close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        data_1m['ATR'] = tr.rolling(14).mean()
        
        current_atr = data_1m['ATR'].iloc[-1]
        current_rsi = data_1m['RSI'].iloc[-1]
        
        MIN_ATR_THRESHOLD = 0.5 
        
        # ⚠️ فلتر التقلب (الفلتر الأول)
        # تم ترك هذا الفلتر ليكون فلتر أساسي لإخراج الإشارة إذا كان السوق هادئًا جداً
        # سيتم التعامل مع هذا الفلتر كشرط للخروج التام من الدالة إذا فشل.
        if current_atr < MIN_ATR_THRESHOLD:
            # رسالة معدلة لعرض الفلاتر ونسبة الثقة الصفرية
            price_msg = f"""
📊 آخر سعر لـ <b>{h(DISPLAY_SYMBOL)}</b> (المصدر: {h(price_source)})
السعر: ${entry_price:,.2f} | الوقت: {h(latest_time)} UTC

**تحليل المؤشرات:**
- **ATR (1m):** {current_atr:.2f} 
- **الفلاتر المُجتازة:** <b>0/{MIN_FILTERS_FOR_98 if min_filters == MIN_FILTERS_FOR_98 else MIN_FILTERS_FOR_90}</b>
"""
            return price_msg, 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE", 0

        # === حساب المؤشرات على 5m (للفلاتر)
        data_5m = calculate_adx(data_5m)
        current_adx_5m = data_5m['ADX'].iloc[-1]
        data_5m['EMA_10'] = data_5m['Close'].ewm(span=10, adjust=False).mean()
        data_5m['EMA_30'] = data_5m['Close'].ewm(span=30, adjust=False).mean()
        htf_trend_5m = "BULLISH" if data_5m['EMA_10'].iloc[-1] > data_5m['EMA_30'].iloc[-1] else "BEARISH"
        data_5m['SMA_200'] = data_5m['Close'].rolling(window=200).mean() # فلتر SMA 200
        latest_sma_200_5m = data_5m['SMA_200'].iloc[-1]
        data_5m['BB_MA'] = data_5m['Close'].rolling(window=20).mean()
        data_5m['BB_STD'] = data_5m['Close'].rolling(window=20).std()
        data_5m['BB_UPPER'] = data_5m['BB_MA'] + (data_5m['BB_STD'] * 2)
        data_5m['BB_LOWER'] = data_5m['BB_MA'] - (data_5m['BB_STD'] * 2)
        latest_bb_lower_5m = data_5m['BB_LOWER'].iloc[-1]
        latest_bb_upper_5m = data_5m['BB_UPPER'].iloc[-1]
        
        # === حساب المؤشرات على 15m (للفلاتر والـ Long-Term)
        data_15m = calculate_adx(data_15m)
        current_adx_15m = data_15m['ADX'].iloc[-1]
        data_15m['EMA_10'] = data_15m['Close'].ewm(span=10, adjust=False).mean()
        data_15m['EMA_30'] = data_15m['Close'].ewm(span=30, adjust=False).mean()
        htf_trend_15m = "BULLISH" if data_15m['EMA_10'].iloc[-1] > data_15m['EMA_30'].iloc[-1] else "BEARISH"
        
        # === حساب المؤشرات على 30m (للفلاتر)
        data_30m['EMA_10'] = data_30m['Close'].ewm(span=10, adjust=False).mean()
        data_30m['EMA_30'] = data_30m['Close'].ewm(span=30, adjust=False).mean()
        htf_trend_30m = "BULLISH" if data_30m['EMA_10'].iloc[-1] > data_30m['EMA_30'].iloc[-1] else "BEARISH"
        data_30m['SMA_200'] = data_30m['Close'].rolling(window=200).mean() 
        latest_sma_200_30m = data_30m['SMA_200'].iloc[-1]
        
        # === حساب المؤشرات على 1h (للفلاتر)
        data_1h['EMA_10'] = data_1h['Close'].ewm(span=10, adjust=False).mean()
        data_1h['EMA_30'] = data_1h['Close'].ewm(span=30, adjust=False).mean()
        htf_trend_1h = "BULLISH" if data_1h['EMA_10'].iloc[-1] > data_1h['EMA_30'].iloc[-1] else "BEARISH"
        
        
        # ===============================================
        # === 1. محاولة استخراج إشارة LONG-TERM (الأولوية) ===
        # ===============================================
        
        action_lt = "HOLD"
        lt_filters_passed = 0
        lt_total_filters = 5 # إجمالي الفلاتر في هذا المسار
        
        # الشرط الأولي (كروس أوفر على 15m)
        is_buy_signal_15m = (data_15m['EMA_10'].iloc[-2] <= data_15m['EMA_30'].iloc[-2] and data_15m['EMA_10'].iloc[-1] > data_15m['EMA_30'].iloc[-1])
        is_sell_signal_15m = (data_15m['EMA_10'].iloc[-2] >= data_15m['EMA_30'].iloc[-2] and data_15m['EMA_10'].iloc[-1] < data_15m['EMA_30'].iloc[-1])

        if is_buy_signal_15m:
            lt_filters_passed += 1 # 1. كروس أوفر 15m
            if current_adx_15m >= ADX_LONGTERM_MIN:
                lt_filters_passed += 1 # 2. فلتر ADX
                if htf_trend_30m == "BULLISH":
                    lt_filters_passed += 1 # 3. فلتر اتجاه 30m
                    if htf_trend_1h == "BULLISH":
                        lt_filters_passed += 1 # 4. فلتر اتجاه 1h
                        if entry_price > latest_sma_200_30m:
                            lt_filters_passed += 1 # 5. فلتر SMA 200
                            
            if lt_filters_passed >= min_filters:
                action_lt = "BUY"
                
        elif is_sell_signal_15m:
            lt_filters_passed += 1 # 1. كروس أوفر 15m
            if current_adx_15m >= ADX_LONGTERM_MIN:
                lt_filters_passed += 1 # 2. فلتر ADX
                if htf_trend_30m == "BEARISH":
                    lt_filters_passed += 1 # 3. فلتر اتجاه 30m
                    if htf_trend_1h == "BEARISH":
                        lt_filters_passed += 1 # 4. فلتر اتجاه 1h
                        if entry_price < latest_sma_200_30m:
                            lt_filters_passed += 1 # 5. فلتر SMA 200
                            
            if lt_filters_passed >= min_filters:
                action_lt = "SELL"
                
        if action_lt != "HOLD":
            action = action_lt
            trade_type = "LONG_TERM"
            # الثقة الديناميكية لـ Long-Term (على أساس 5 فلاتر)
            confidence = min(1.0, 0.85 + (lt_filters_passed * (0.15/lt_total_filters))) 
            rr_factor = LONGTERM_RR_FACTOR
            filters_passed = lt_filters_passed
            total_filters_used = lt_total_filters
        
        # ===============================================
        # === 2. محاولة استخراج إشارة SCALPING (الخيار الثاني) ===
        # ===============================================
        
        else: # إذا لم نجد إشارة Long-Term
            
            action_sc = "HOLD"
            sc_filters_passed = 0
            sc_total_filters = 7 # إجمالي الفلاتر في هذا المسار
            
            # الشرط الأولي (كروس أوفر على 1m)
            ema_fast_prev = data_1m['EMA_5'].iloc[-2]
            ema_slow_prev = data_1m['EMA_20'].iloc[-2]
            ema_fast_current = data_1m['EMA_5'].iloc[-1]
            ema_slow_current = data_1m['EMA_20'].iloc[-1]
            
            is_buy_signal_1m = (ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current)
            is_sell_signal_1m = (ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current)

            if is_buy_signal_1m: 
                sc_filters_passed += 1 # 1. كروس أوفر 1m
                # فلتر ADX
                if current_adx_5m >= ADX_SCALPING_MIN:
                    sc_filters_passed += 1 # 2. فلتر ADX
                    # فلتر الاتجاهات
                    if htf_trend_5m == "BULLISH":
                        sc_filters_passed += 1 # 3. فلتر اتجاه 5m
                        if htf_trend_15m == "BULLISH":
                            sc_filters_passed += 1 # 4. فلتر اتجاه 15m
                            # فلتر RSI
                            if current_rsi > 50 and current_rsi < 70:
                                sc_filters_passed += 1 # 5. فلتر RSI
                                # فلتر البولينجر باند (قرب القاع)
                                if (entry_price - latest_bb_lower_5m) < BB_PROXIMITY_THRESHOLD and entry_price > latest_bb_lower_5m:
                                    sc_filters_passed += 1 # 6. فلتر BB
                                    # فلتر SMA 200
                                    if entry_price > latest_sma_200_5m:
                                        sc_filters_passed += 1 # 7. فلتر SMA 200
                                    
                if sc_filters_passed >= min_filters:
                    action_sc = "BUY"
                                    
            elif is_sell_signal_1m:
                sc_filters_passed += 1 # 1. كروس أوفر 1m
                # فلتر ADX
                if current_adx_5m >= ADX_SCALPING_MIN:
                    sc_filters_passed += 1 # 2. فلتر ADX
                    # فلتر الاتجاهات
                    if htf_trend_5m == "BEARISH":
                        sc_filters_passed += 1 # 3. فلتر اتجاه 5m
                        if htf_trend_15m == "BEARISH":
                            sc_filters_passed += 1 # 4. فلتر اتجاه 15m
                             # فلتر RSI
                        if current_rsi < 50 and current_rsi > 30:
                            sc_filters_passed += 1 # 5. فلتر RSI
                            # فلتر البولينجر باند (قرب القمة)
                            if (latest_bb_upper_5m - entry_price) < BB_PROXIMITY_THRESHOLD and entry_price < latest_bb_upper_5m:
                                sc_filters_passed += 1 # 6. فلتر BB
                                # فلتر SMA 200
                                if entry_price < latest_sma_200_5m:
                                    sc_filters_passed += 1 # 7. فلتر SMA 200
                                    
            if sc_filters_passed >= min_filters:
                action_sc = "SELL"
                                    
            if action_sc != "HOLD":
                action = action_sc
                trade_type = "SCALPING"
                # الثقة الديناميكية لـ Scalping (على أساس 7 فلاتر)
                confidence = min(1.0, 0.88 + (sc_filters_passed * (0.12/sc_total_filters))) 
                rr_factor = SCALPING_RR_FACTOR
                filters_passed = sc_filters_passed
                total_filters_used = sc_total_filters
            else:
                action = "HOLD"
                trade_type = "NONE"
                confidence = 0.0
                filters_passed = 0
                total_filters_used = MIN_FILTERS_FOR_98 if min_filters == MIN_FILTERS_FOR_98 else MIN_FILTERS_FOR_90


        # ===============================================
        # === حساب نقاط الخروج والتقرير النهائي ===
        # ===============================================
        
        if action != "HOLD" and filters_passed >= min_filters:
            
            # SL_DISTANCE يعتمد على ATR (متغير لكل صفقة) مع حد أدنى
            risk_amount = max(current_atr * SL_FACTOR, MIN_SL_DISTANCE) 
            # يجب أن لا يتجاوز الحد الأقصى للمخاطرة
            risk_amount = min(risk_amount, MAX_SL_DISTANCE)
            stop_loss_distance = risk_amount
            
            if action == "BUY":
                stop_loss = entry_price - risk_amount 
                take_profit = entry_price + (risk_amount * rr_factor) 
            
            elif action == "SELL":
                stop_loss = entry_price + risk_amount
                take_profit = entry_price - (risk_amount * rr_factor)
                
            price_msg = f"""
📊 آخر سعر لـ <b>{h(DISPLAY_SYMBOL)}</b> (المصدر: {h(price_source)})
السعر: ${entry_price:,.2f} | الوقت: {h(latest_time)} UTC

**تحليل المؤشرات:**
- **RSI (1m):** {current_rsi:.2f} 
- **ATR (1m):** {current_atr:.2f} 
- **ADX (5m):** {current_adx_5m:.2f} 
- **ADX (15m):** {current_adx_15m:.2f} 
- **SMA 200 (5m):** {latest_sma_200_5m:,.2f}
- **الاتجاهات (5m/15m/30m/1h):** {htf_trend_5m[0]}/{htf_trend_15m[0]}/{htf_trend_30m[0]}/{htf_trend_1h[0]}
- **الفلاتر المُجتازة:** <b>{filters_passed}/{total_filters_used}</b>
"""
            return price_msg, confidence, action, entry_price, stop_loss, take_profit, stop_loss_distance, trade_type, filters_passed
        
        # ❌ رسالة الرفض النهائي (عندما تكون HOLD) - ⚠️ التعديل المطلوب هنا
        else:
            confidence_percent = (filters_passed / total_filters_used) * 100 if total_filters_used > 0 else 0.0
            
            price_msg = f"""
💡 **تقرير التحليل - XAUUSD**
━━━━━━━━━━━━━━━
🔎 **الإشارة الحالية:** HOLD
🔒 **نسبة الثقة:** <b>{confidence_percent:.2f}%</b> (المطلوب: {int(min_filters / total_filters_used * 100)}%)
❌ **القرار:** لا توجد إشارة قوية (HOLD).
━━━━━━━━━━━━━━━
📊 آخر سعر لـ <b>{h(DISPLAY_SYMBOL)}</b> (المصدر: {h(price_source)})
السعر: ${entry_price:,.2f} | الوقت: {h(latest_time)} UTC
**تحليل المؤشرات:**
- **RSI (1m):** {current_rsi:.2f} 
- **ATR (1m):** {current_atr:.2f} 
- **ADX (5m/15m):** {current_adx_5m:.2f}/{current_adx_15m:.2f} 
- **الاتجاهات (30m/1h):** {htf_trend_30m[0]}/{htf_trend_1h[0]}
- **الفلاتر المُجتازة:** <b>{filters_passed}/{total_filters_used}</b>
"""
            return price_msg, confidence, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE", filters_passed
        
    except Exception as e:
        print(f"❌ فشل في جلب بيانات التداول لـ XAUUSD أو التحليل: {e}")
        return f"❌ فشل في جلب بيانات التداول لـ XAUUSD أو التحليل: {h(str(e))}", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE", 0


# === دالة الإرسال التلقائي لـ 98% (VIP) ===
async def send_vip_trade_signal_98():
    
    # 1. تحقق من عدم وجود صفقات نشطة بالفعل
    active_trades = get_active_trades()
    if len(active_trades) > 0:
        print(f"🤖 يوجد {len(active_trades)} صفقات نشطة. تم تخطي التحليل التلقائي (98%).")
        return 

    # 2. إجراء التحليل المزدوج بحد أدنى 7 فلاتر (98%)
    try:
        price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type, filters_passed = get_signal_and_confidence(TRADE_SYMBOL, MIN_FILTERS_FOR_98)
    except Exception as e:
        print(f"❌ خطأ حرج أثناء التحليل التلقائي (98%): {e}")
        return

    confidence_percent = confidence * 100
    DISPLAY_SYMBOL = "XAUUSD" 
    rr_factor_used = SCALPING_RR_FACTOR if trade_type == "SCALPING" else LONGTERM_RR_FACTOR

    # 3. تحقق من شرط الثقة (98% أو أعلى)
    if action != "HOLD" and confidence >= CONFIDENCE_THRESHOLD_98:
        
        print(f"✅ إشارة {action} قوية جداً تم العثور عليها ({trade_type}) (الثقة: {confidence_percent:.2f}%). جارٍ الإرسال...")
        
        trade_type_msg = "SCALPING / HIGH MOMENTUM" if trade_type == "SCALPING" else "LONG-TERM / SWING"
        
        trade_msg = f"""
🚨 TRADE TYPE: **{trade_type_msg}** 🚨
{('🟢' if action == 'BUY' else '🔴')} <b>ALPHA TRADE ALERT - VIP SIGNAL!</b> {('🟢' if action == 'BUY' else '🔴')}
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD 
🔥 **ACTION:** {action} (Market Execution)
💰 **ENTRY:** ${entry:,.2f}
🎯 **TAKE PROFIT (TP):** ${tp:,.2f}
🛑 **STOP LOSS (SL):** ${sl:,.2f}
🔒 **SUCCESS RATE:** <b>{confidence_percent:.2f}%</b> ({CONFIDENCE_THRESHOLD_98*100:.0f}%+)
⚖️ **RISK/REWARD:** 1:{rr_factor_used:.1f} (SL/TP)
━━━━━━━━━━━━━━━
⚠️ نفذ الصفقة **فوراً** على سعر السوق.
"""
        # 4. حفظ الصفقة وإرسالها
        all_users = get_all_users_ids()
        vip_users = [uid for uid, is_banned in all_users if is_banned == 0 and is_user_vip(uid)]
        
        trade_id = save_new_trade(action, entry, tp, sl, len(vip_users), trade_type)
        
        if trade_id:
            for uid in vip_users:
                try:
                    await bot.send_message(uid, trade_msg, parse_mode="HTML")
                except Exception:
                    pass
            
            # إرسال إشعار للأدمن (تأكيد أن الصفقة أُرسلت)
            if ADMIN_ID != 0:
                 await bot.send_message(ADMIN_ID, f"🔔 **تم إرسال إشارة VIP تلقائية بنجاح!**\nID: {trade_id}\n{trade_msg}", parse_mode="HTML")
                 
    elif action != "HOLD":
         print(f"⚠️ تم العثور على إشارة {action} ({trade_type})، لكن الثقة {confidence_percent:.2f}% لم تصل إلى المطلوب {CONFIDENCE_THRESHOLD_98*100:.0f}%.")
    else:
         print("💡 لا توجد إشارة واضحة (HOLD) (98%).")

# === دالة تحليل إضافية لـ 90% (لتشغيل المهام المجدولة للأدمن فقط) ===
async def send_trade_signal_90():
    """هذه الدالة للتحقق الداخلي فقط ولا ترسل رسائل VIP."""
    # لا يوجد فحص للصفقات النشطة هنا، مهمتها فقط البحث عن فرص جيدة للأدمن.
    
    # 1. إجراء التحليل المزدوج بحد أدنى 5 فلاتر (90%)
    try:
        price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type, filters_passed = get_signal_and_confidence(TRADE_SYMBOL, MIN_FILTERS_FOR_90)
    except Exception as e:
        print(f"❌ خطأ حرج أثناء التحليل التلقائي (90%): {e}")
        return

    confidence_percent = confidence * 100
    
    # 2. تحقق من شرط الثقة (90% أو أعلى)
    if action != "HOLD" and confidence >= CONFIDENCE_THRESHOLD_90 and ADMIN_ID != 0:
        
        # ⚠️ ملاحظة: لا نرسل رسائل للمشتركين هنا، فقط للأدمن كإشعار إضافي
        
        # 3. إرسال إشعار للأدمن (إذا كانت الثقة أعلى من 90% وأقل من 98%)
        if confidence < CONFIDENCE_THRESHOLD_98:
            rr_factor_used = SCALPING_RR_FACTOR if trade_type == "SCALPING" else LONGTERM_RR_FACTOR
            trade_type_msg = "SCALPING / MEDIUM MOMENTUM" if trade_type == "SCALPING" else "LONG-TERM / SWING"
            
            admin_alert_msg = f"""
🔔 **إشعار فرصة (90%+)** 🔔
🚨 TRADE TYPE: **{trade_type_msg}** 🚨
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD 
🔥 **ACTION:** {action}
💰 **ENTRY:** ${entry:,.2f}
🎯 **TAKE PROFIT (TP):** ${tp:,.2f}
🛑 **STOP LOSS (SL):** ${sl:,.2f}
🔒 **SUCCESS RATE:** <b>{confidence_percent:.2f}%</b> (لم تصل لـ 98%)
⚖️ **RISK/REWARD:** 1:{rr_factor_used:.1f}
━━━━━━━━━━━━━━━
{price_info_msg}
"""
            await bot.send_message(ADMIN_ID, admin_alert_msg, parse_mode="HTML")

    elif action != "HOLD":
         print(f"⚠️ تم العثور على إشارة {action} ({trade_type})، لكن الثقة {confidence_percent:.2f}% لم تصل إلى المطلوب {CONFIDENCE_THRESHOLD_90*100:.0f}%.")
    else:
         print("💡 لا توجد إشارة واضحة (HOLD) (90%).")

# ⚠️ التعديل المطلوب: إرسال نتيجة الصفقة للأدمن 
async def notify_admin_trade_result(trade_id, action, exit_status, close_price, trade_type):
    if ADMIN_ID == 0:
        return
        
    result_emoji = "🏆🎉" if exit_status == "HIT_TP" else "🛑"
    trade_type_msg = "SCALPING" if trade_type == "SCALPING" else "LONG-TERM"
    
    admin_result_msg = f"""
🔔 **متابعة إغلاق الصفقة #{trade_id}** 🔔
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD 
➡️ **ACTION:** {action} ({trade_type_msg})
🔒 **RESULT:** {result_emoji} تم الإغلاق عند **{exit_status.replace('HIT_', '')}**!
💰 **PRICE:** ${close_price:,.2f}
"""
    try:
        await bot.send_message(ADMIN_ID, admin_result_msg, parse_mode="HTML")
    except Exception as e:
        print(f"❌ فشل إرسال نتيجة الصفقة للأدمن {e}")


# =============== باقي الكود (تم تحديث بعض الدوال لدعم trade_type) ===============

def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📈 سعر السوق الحالي"), KeyboardButton(text="🔍 الصفقات النشطة")],
            [KeyboardButton(text="🔗 تفعيل مفتاح الاشتراك"), KeyboardButton(text="📝 حالة الاشتراك")],
            [KeyboardButton(text="💰 خطة الأسعار VIP"), KeyboardButton(text="💬 تواصل مع الدعم")],
            [KeyboardButton(text="ℹ️ عن AlphaTradeAI"), ] # ⚠️ التعديل المطلوب: إضافة زر التقرير الأسبوعي
        ],
        resize_keyboard=True
    )

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="تحليل خاص (98% VIP) 👤"), KeyboardButton(text="تحليل فوري (90%+ ⚡️)")],
            [KeyboardButton(text=" 📊")], # ⚠️ تغيير اسم الزر لتمييزه عن تقرير البوت
            [KeyboardButton(text="📊 جرد الصفقات اليومي"), KeyboardButton(text="📢 رسالة لكل المستخدمين")],
            [KeyboardButton(text="📊 تقرير الأداء الأسبوعي"), KeyboardButton(text="🔑 إنشاء مفتاح اشتراك")],
            [KeyboardButton(text="🗒️ عرض حالة المشتركين")],
            [KeyboardButton(text="🚫 حظر مستخدم"), KeyboardButton(text="✅ إلغاء حظر مستخدم")],
            [KeyboardButton(text="👥 عدد المستخدمين"), KeyboardButton(text="🔙 عودة للمستخدم")]
        ],
        resize_keyboard=True
    )

# ... (بقية دوال الأدمن: process_trade_pnl_after_entry, process_manual_trade_result, prompt_trade_result) تبقى كما هي ...

@dp.message(AdminStates.waiting_trade_pnl)
async def process_trade_pnl_after_entry(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        pnl = float(msg.text.strip().replace('+', '').replace(',', ''))
        data = await state.get_data()
        
        if 'symbol' not in data or 'action' not in data or 'lots' not in data:
            await state.clear()
            await msg.reply("❌ فقدت بيانات الصفقة الأصلية. يرجى استخدام زر **'تسجيل نتيجة صفقة 📝'** يدوياً.", reply_markup=admin_menu())
            return

        save_admin_trade_result(data['symbol'], data['action'], data['lots'], pnl)
        new_capital = get_admin_financial_status()
        await state.clear()
        
        await msg.reply(
            f"✅ تم تسجيل نتيجة الصفقة بنجاح: **${pnl:,.2f}**.\n"
            f"💰 رأس مالك الحالي أصبح: **${new_capital:,.2f}**.",
            reply_markup=admin_menu()
        )
    except ValueError:
        await msg.reply("❌ إدخال غير صحيح للربح/الخسارة. يرجى إدخال قيمة عددية موجبة أو سالبة.", reply_markup=admin_menu())
    except Exception:
         await state.clear()
         await msg.reply("❌ حدث خطأ غير متوقع أثناء تسجيل النتيجة. يرجى مراجعة اللوغ.", reply_markup=admin_menu())

@dp.message(AdminStates.waiting_trade_result_input)
async def process_manual_trade_result(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await state.clear()
    
    try:
        parts = msg.text.strip().split()
        if len(parts) != 4: raise ValueError("Invalid format")
            
        symbol = parts[0].upper()
        action = parts[1].upper()
        lots = float(parts[2])
        pnl = float(parts[3])
        
        if action not in ['BUY', 'SELL'] or lots <= 0: raise ValueError("Invalid values")
        
        save_admin_trade_result(symbol, action, lots, pnl)
        new_capital = get_admin_financial_status()
        
        display_symbol = "XAUUSD" 
        
        await msg.reply(
            f"✅ تم تسجيل الصفقة اليدوية: {h(display_symbol)} ({action})، PnL: ${pnl:,.2f}.\n"
            f"💰 رأس مالك الحالي أصبح: **${new_capital:,.2f}**.",
            reply_markup=admin_menu()
        )
    except ValueError:
        await msg.reply("❌ صيغة الإدخال غير صحيحة. يرجى اتباع المثال: `XAUT/USDT BUY 0.05 -2.50`", reply_markup=admin_menu())

@dp.message(F.text == "تسجيل نتيجة صفقة 📝")
async def prompt_trade_result(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    current_state = await state.get_state()
    
    if current_state == AdminStates.waiting_trade_pnl.state:
         await msg.reply("يرجى إدخال قيمة الربح/الخسارة الصافية (مثال: **+6** أو **-2**).")
         return
         
    await state.set_state(AdminStates.waiting_trade_result_input)
    await msg.reply("يرجى إدخال ملخص نتيجة الصفقة اليدوية بالترتيب التالي (افصل بينهما بمسافة):\n**الرمز العمل اللوت الربح/الخسارة**\n\nمثال: `XAUT/USDT BUY 0.05 -2.50`")

@dp.message(F.text == " 📊") # ⚠️ الزر المُعدَّل
async def show_weekly_report_admin(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    report = generate_weekly_performance_report()
    await msg.reply(report, parse_mode="HTML")

# ⚠️ التعديل المطلوب: دالة تقرير الأداء الأسبوعي للمستخدمين 
@dp.message(F.text == "📊 تقرير الأداء الأسبوعي")
async def show_weekly_report_user(msg: types.Message):
    report = get_weekly_trade_performance()
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

# ----------------------------------------------------------------------------------
# دالة تحليل فوري ⚡️ (الزر الجديد لـ الأدمن 90%+ لـ 98%-)
# ----------------------------------------------------------------------------------
@dp.message(F.text == "تحليل فوري (90%+ ⚡️)")
async def analyze_market_now(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: 
        await msg.answer("🚫 هذه الميزة مخصصة للأدمن فقط.")
        return
    
    # ⚠️ تستخدم CONFIDENCE_THRESHOLD_90 (90%) كحد أدنى و MIN_FILTERS_FOR_90 (5 فلاتر)
    await msg.reply(f"⏳ جارٍ تحليل السوق بحثًا عن فرصة تداول تتراوح ثقتها بين {int(CONFIDENCE_THRESHOLD_90 * 100)}% و {int(CONFIDENCE_THRESHOLD_98 * 100)}%...")
    
    price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type, filters_passed = get_signal_and_confidence(TRADE_SYMBOL, MIN_FILTERS_FOR_90)
    confidence_percent = confidence * 100
    threshold_percent_90 = int(CONFIDENCE_THRESHOLD_90 * 100)
    threshold_percent_98 = int(CONFIDENCE_THRESHOLD_98 * 100)
    
    
    # (1) إذا لم تتوفر إشارة أساساً أو الثقة غير كافية
    if action == "HOLD" or confidence < CONFIDENCE_THRESHOLD_90:
         # ⚠️ التعديل: عرض عدد الفلاتر ونسبة الثقة المئوية (تم تعديل رسالة HOLD داخل get_signal_and_confidence)
         await msg.answer(price_info_msg, parse_mode="HTML")
    
    # (2) إذا كانت الثقة كافية (90% أو أعلى)، لكن لم تصل لـ 98%
    elif confidence >= CONFIDENCE_THRESHOLD_90 and confidence < CONFIDENCE_THRESHOLD_98:
         rr_factor_used = SCALPING_RR_FACTOR if trade_type == "SCALPING" else LONGTERM_RR_FACTOR
         trade_type_msg = "SCALPING / MEDIUM MOMENTUM" if trade_type == "SCALPING" else "LONG-TERM / SWING"
         
         trade_msg = f"""
✅ **إشارة جاهزة (ثقة {confidence_percent:.2f}%)**
🚨 TRADE TYPE: **{trade_type_msg}** 🚨
{('🟢' if action == 'BUY' else '🔴')} <b>ALPHA TRADE SIGNAL (90%+)</b> {('🟢' if action == 'BUY' else '🔴')}
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD 
🔥 **ACTION:** {action}
💰 **ENTRY:** ${entry:,.2f}
🎯 **TAKE PROFIT (TP):** ${tp:,.2f}
🛑 **STOP LOSS (SL):** ${sl:,.2f}
⚖️ **RISK/REWARD:** 1:{rr_factor_used:.1f} (SL/TP)
━━━━━━━━━━━━━━━
**📊 ملاحظة:** هذه الإشارة للتنفيذ اليدوي الآن، وثقتها لم تصل لـ {threshold_percent_98}%.
{price_info_msg}
"""
         await msg.answer(trade_msg, parse_mode="HTML")
    
    # (3) إذا كانت الثقة 98% (نادر الحدوث هنا لكنه ممكن)
    elif confidence >= CONFIDENCE_THRESHOLD_98:
         await msg.answer(f"✅ تم إيجاد إشارة فائقة القوة ({action}) ({trade_type}) على XAUUSD!\nنسبة الثقة: <b>{confidence_percent:.2f}%</b>.\n**تم إرسال الإشارة التلقائية لـ VIP إذا لم تكن هناك صفقات نشطة.**", parse_mode="HTML")

# ----------------------------------------------------------------------------------


@dp.message(F.text == "تحليل خاص (98% VIP) 👤")
async def analyze_private_pair(msg: types.Message):
    global SCALPING_RR_FACTOR, LONGTERM_RR_FACTOR, CONFIDENCE_THRESHOLD_98 
    
    if msg.from_user.id != ADMIN_ID: await msg.answer("🚫 هذه الميزة خاصة بالإدمن."); return
    
    # ⚠️ نستخدم 98% (CONFIDENCE_THRESHOLD_98) هنا لاختبار الإشارات القوية VIP
    TESTING_CONFIDENCE_THRESHOLD = CONFIDENCE_THRESHOLD_98 

    await msg.reply(f"⏳ جارٍ تحليل الزوج الخاص: **XAUUSD** (الذهب) لثقة {int(TESTING_CONFIDENCE_THRESHOLD*100)}%+...")
    
    price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type, filters_passed = get_signal_and_confidence(ADMIN_TRADE_SYMBOL, MIN_FILTERS_FOR_98)
    
    confidence_percent = confidence * 100
    threshold_percent = int(TESTING_CONFIDENCE_THRESHOLD * 100)
    
    
    # ⚠️ المنطق المُعدَّل: إذا لم تتحقق الثقة المطلوبة (98%)، نظهر رسالة الـ HOLD الواضحة
    if action == "HOLD" or confidence < TESTING_CONFIDENCE_THRESHOLD:
        # ⚠️ التعديل: عرض عدد الفلاتر ونسبة الثقة المئوية (تم تعديل رسالة HOLD داخل get_signal_and_confidence)
        await msg.answer(price_info_msg, parse_mode="HTML")
        return
    
    # إذا كانت الإشارة قوية وتجاوزت الثقة المطلوبة (هنا 98% أو أعلى)
    rr_factor_used = SCALPING_RR_FACTOR if trade_type == "SCALPING" else LONGTERM_RR_FACTOR
    trade_type_msg = "SCALPING / HIGH MOMENTUM" if trade_type == "SCALPING" else "LONG-TERM / SWING"
    
    private_msg = f"""
🚨 TRADE TYPE: **{trade_type_msg}** 🚨
{('🟢' if action == 'BUY' else '🔴')} <b>YOUR PERSONAL TRADE - GOLD (XAUUSD)</b> {('🟢' if action == 'BUY' else '🔴')}
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD 
🔥 **ACTION:** {action} (Market Execution)
💰 **ENTRY:** ${entry:,.2f}
🎯 **TARGET (TP):** ${tp:,.2f}
🛑 **STOP LOSS (SL):** ${sl:,.2f}
🔒 **SUCCESS RATE:** <b>{confidence_percent:.2f}%</b>
⚖️ **RISK/REWARD:** 1:{rr_factor_used:.1f} (SL/TP)
━━━━━━━━━━━━━━━
**📊 ملاحظة هامة (إدارة المخاطر):**
تم تحديد نقاط الدخول والخروج فنيًا. يرجى **تحديد حجم اللوت** المناسب لرأس مالك وإستراتيجية المخاطرة الخاصة بك يدوياً.
"""
    await msg.answer(private_msg, parse_mode="HTML")
    await msg.answer("❓ **هل دخلت هذه الصفقة؟** (استخدم 'تسجيل نتيجة صفقة 📝' لتسجيل النتيجة يدوياً)", parse_mode="HTML")
# ----------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------
# تعديل زر البث 📢 رسالة لكل المستخدمين
# ----------------------------------------------------------------------------------
@dp.message(F.text == "📢 رسالة لكل المستخدمين")
async def prompt_broadcast_target(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminStates.waiting_broadcast_target)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 جميع المستخدمين (النشطين)", callback_data="broadcast_all")],
        [InlineKeyboardButton(text="⭐️ مشتركين VIP فقط", callback_data="broadcast_vip")]
    ])
    await msg.reply("🎯 يرجى تحديد الفئة التي تريد إرسال الرسالة إليها:", reply_markup=keyboard)

@dp.callback_query(AdminStates.waiting_broadcast_target)
async def process_broadcast_target(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    target = callback.data.replace("broadcast_", "")
    
    await state.update_data(broadcast_target=target)
    await state.set_state(AdminStates.waiting_broadcast)
    
    target_msg = "جميع المستخدمين (غير المحظورين)" if target == "all" else "مشتركين VIP فقط"
    await bot.send_message(callback.from_user.id, f"📝 أدخل نص الرسالة المراد بثها لـ **{target_msg}**:")

@dp.message(AdminStates.waiting_broadcast)
async def send_broadcast(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    
    data = await state.get_data()
    target = data.get('broadcast_target', 'all')
    
    await state.clear()
    
    broadcast_text = msg.text
    sent_count = 0
    
    await msg.reply(f"⏳ جارٍ إرسال الرسالة إلى {target}...")
    
    if target == 'all':
        users_to_send = get_all_users_ids()
    elif target == 'vip':
        users_to_send = get_all_users_ids(vip_only=True)
    else:
        users_to_send = []

    for uid, is_banned_status in users_to_send:
        # نتحقق من الحظر فقط إذا كان الهدف "all"، لأن get_all_users_ids(vip_only=True) يفلتر المحظورين
        if uid != ADMIN_ID: 
            if target == 'all' and is_banned_status == 1:
                continue
            
            try:
                await bot.send_message(uid, broadcast_text, parse_mode="HTML")
                sent_count += 1
            except Exception:
                pass 
                
    await msg.reply(f"✅ تم إرسال الرسالة بنجاح إلى **{sent_count}** مستخدم.", reply_markup=admin_menu())
# ----------------------------------------------------------------------------------

# ... (بقية دوال المستخدم والأدمن) تبقى كما هي

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
        update_ban_status(user_id_to_unban, 0) 
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
    if conn is None: return await msg.reply("❌ فشل الاتصال بقاعدة البيانات.")
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
        report += f"  - اليوزر: @{h(username) if username else 'لا يوجد'}\n"
        report += f"  - الحالة: {ban_status} / {vip_status}\n\n"
        
    await msg.reply(report, parse_mode="HTML")


@dp.message(F.text == "📈 سعر السوق الحالي")
async def get_current_price(msg: types.Message):
    current_price = fetch_current_price_ccxt(TRADE_SYMBOL) 
    
    DISPLAY_SYMBOL = "XAUUSD" 

    if current_price is not None:
        price_msg = f"📊 السعر الحالي لـ <b>{h(DISPLAY_SYMBOL)}</b> (المصدر: {h(CCXT_EXCHANGE)}):\nالسعر: <b>${current_price:,.2f}</b>\nالوقت: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        await msg.reply(price_msg, parse_mode="HTML")
    else:
        await msg.reply(f"❌ فشل جلب السعر اللحظي لـ {h(DISPLAY_SYMBOL)} من {h(CCXT_EXCHANGE)}. يرجى المحاولة لاحقاً.")

@dp.message(F.text == "🔍 الصفقات النشطة")
async def show_active_trades(msg: types.Message):
    
    active_trades = get_active_trades()
    
    if not active_trades:
        await msg.reply("✅ لا توجد حاليًا أي صفقات VIP نشطة. انتظر إشارة قادمة!")
        return
    
    report = "⏳ **قائمة الصفقات النشطة حالياً (XAUUSD)**\n━━━━━━━━━━━━━━━"
    
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
  - **ID:** <code>{trade_id}</code>
"""
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📝 حالة الاشتراك")
async def show_subscription_status(msg: types.Message):
    status = get_user_vip_status(msg.from_user.id)
    if status == "غير مشترك":
        await msg.reply(f"⚠️ أنت حالياً **غير مشترك** في خدمة VIP.\nللاشتراك، اطلب مفتاح تفعيل من الأدمن (@{h(ADMIN_USERNAME)}) ثم اضغط '🔗 تفعيل مفتاح الاشتراك'.")
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
    threshold_percent_98 = int(CONFIDENCE_THRESHOLD_98 * 100)
    threshold_percent_90 = int(CONFIDENCE_THRESHOLD_90 * 100)

    about_msg = f"""
🚀 <b>AlphaTradeAI: ثورة التحليل الكمّي في تداول الذهب!</b> 🚀

نحن لسنا مجرد بوت، بل منصة تحليل ذكية ومؤتمتة بالكامل، مصممة لملاحقة أكبر الفرص في سوق الذهب (XAUUSD). مهمتنا هي تصفية ضجيج السوق وتقديم إشارات <b>مؤكدة فقط</b>.

━━━━━━━━━━━━━━━
🛡️ **ماذا يقدم لك الاشتراك VIP؟ (ميزة القوة الخارقة)**
1.  <b>إشارات ثنائية الاستراتيجية (Dual Strategy):</b>
    نظامنا يبحث عن نوعين من الصفقات لتغطية جميع ظروف السوق القوية:
    * **Scalping:** R:R 1:{SCALPING_RR_FACTOR:.1f} مع فلاتر 1m/5m/15m و ADX > {ADX_SCALPING_MIN}.
    * **Long-Term:** R:R 1:{LONGTERM_RR_FACTOR:.1f} مع فلاتر 15m/30m/1h و ADX > {ADX_LONGTERM_MIN}.
    
2.  <b>إشارات متعددة التأكيد ({MIN_FILTERS_FOR_98}-Tier Confirmation):</b>
    كل صفقة تُرسل يجب أن تمر بـ {MIN_FILTERS_FOR_98} فلاتر تحليلية (EMA, RSI, ADX, BB, SMA 200, توافق الأطر الزمنية, ATR ديناميكي).

3.  <b>عتبات الثقة:</b>
    * **الإرسال التلقائي:** لا يتم إرسال أي صفقة تلقائيًا إلا إذا تجاوزت الثقة **{threshold_percent_98}%** (يتطلب {MIN_FILTERS_FOR_98} فلاتر).
    * **التحليل المُحسَّن (الأدمن):** يمكن طلب صفقة كاملة إذا تجاوزت الثقة **{threshold_percent_90}%** (يتطلب {MIN_FILTERS_FOR_90} فلاتر).

4.  <b>نقاط خروج ديناميكية:</b>
    نقاط TP و SL تتغير مع كل صفقة بناءً على التقلب الفعلي للسوق (ATR)، مما يضمن تحديد هدف ووقف مناسبين لظروف السوق الحالية.

━━━━━━━━━━━━━━━
💰 حوّل التحليل إلى ربح. لا تدع الفرص تفوتك! اضغط على '💰 خطة الأسعار VIP' للاطلاع على العروض الحالية.
"""
    await msg.reply(about_msg)

# ===============================================
# === دالة متابعة الصفقات (Trade Monitoring) (تم تحديث الرسالة وإضافة إشعار الأدمن) ===
# ===============================================

async def check_open_trades():
    
    active_trades = get_active_trades()
    
    if not active_trades:
        return

    try:
        current_price = fetch_current_price_ccxt(TRADE_SYMBOL)
        if current_price is None:
             raise Exception("Failed to fetch price.")
    except Exception as e:
        print(f"❌ فشل في جلب سعر السوق الحالي لمتابعة الصفقات: {e}")
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
            trade_type_msg = "SCALPING / HIGH MOMENTUM" if trade_type == "SCALPING" else "LONG-TERM / SWING"
            
            close_msg = f"""
🚨 TRADE TYPE: **{trade_type_msg}** 🚨
{result_emoji} <b>TRADE CLOSED!</b> {result_emoji}
━━━━━━━━━━━━━━━
📈 **PAIR:** XAUUSD 
➡️ **ACTION:** {action}
🔒 **RESULT:** تم الإغلاق عند **{exit_status.replace('HIT_', '')}**!
💰 **PRICE:** ${close_price:,.2f}
"""
            all_users = get_all_users_ids()
            # إرسال لكل المستخدمين الـ VIP النشطين
            for uid, is_banned_status in all_users:
                 if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
                    try:
                        await bot.send_message(uid, close_msg, parse_mode="HTML")
                    except Exception:
                        pass
                        
            # ⚠️ التعديل المطلوب: إرسال النتيجة للأدمن
            if ADMIN_ID != 0:
                await notify_admin_trade_result(trade_id, action, exit_status, close_price, trade_type)
                
# ===============================================
# === إعداد المهام المجدولة (Setup Scheduled Tasks) ===
# ===============================================

WEEKEND_CLOSURE_ALERT_SENT = False
WEEKEND_OPENING_ALERT_SENT = False

def is_weekend_closure():
    """التحقق مما إذا كان إغلاق عطلة نهاية الأسبوع (لتجنب التنبيهات)."""
    now_utc = datetime.now(timezone.utc) 
    weekday = now_utc.weekday() 
    
    # من إغلاق الجمعة (الساعة 21:00 بتوقيت UTC) حتى فتح الأحد (الساعة 21:00 بتوقيت UTC)
    if weekday == 5 or (weekday == 6 and now_utc.hour < 21) or (weekday == 4 and now_utc.hour >= 21): 
        return True
    return False 

async def weekend_alert_checker():
    """التحقق الدوري لإرسال رسائل الإغلاق والفتح."""
    global WEEKEND_CLOSURE_ALERT_SENT, WEEKEND_OPENING_ALERT_SENT
    await asyncio.sleep(60) 
    
    while True:
        now_utc = datetime.now(timezone.utc)
        
        # 1. رسالة الإغلاق (الجمعة 21:00 UTC)
        if now_utc.weekday() == 4 and now_utc.hour >= 21 and not WEEKEND_CLOSURE_ALERT_SENT:
            if not is_weekend_closure(): # للتأكد من أنها أول مرة تدخل فترة الإغلاق
                alert_msg = "😴 **إغلاق السوق (عطلة نهاية الأسبوع)!** 😴\n\nتم إيقاف جميع تحليلات وإشارات AlphaTradeAI حتى فتح السوق يوم الأحد (21:00 UTC). نراكم على خير!"
                
                all_vip_users = get_all_users_ids(vip_only=True)
                for uid, _ in all_vip_users:
                    try:
                        await bot.send_message(uid, alert_msg, parse_mode="HTML")
                    except:
                        pass
                        
                if ADMIN_ID != 0:
                    await bot.send_message(ADMIN_ID, "✅ تم إرسال رسالة إغلاق السوق بنجاح.")
                    
                WEEKEND_CLOSURE_ALERT_SENT = True
                WEEKEND_OPENING_ALERT_SENT = False # إعادة ضبط لرسالة الفتح
        
        # 2. رسالة الفتح (الأحد 21:00 UTC)
        elif now_utc.weekday() == 6 and now_utc.hour >= 21 and not WEEKEND_OPENING_ALERT_SENT:
            if not is_weekend_closure(): # للتأكد من أنها أول مرة تخرج من فترة الإغلاق
                alert_msg = "🔔 **فتح السوق! هيا بنا!** 🔔\n\nتم استئناف تحليل وإشارات AlphaTradeAI. ترقبوا الإشارة القادمة!"

                all_vip_users = get_all_users_ids(vip_only=True)
                for uid, _ in all_vip_users:
                    try:
                        await bot.send_message(uid, alert_msg, parse_mode="HTML")
                    except:
                        pass
                        
                if ADMIN_ID != 0:
                    await bot.send_message(ADMIN_ID, "✅ تم إرسال رسالة فتح السوق بنجاح.")
                    
                WEEKEND_OPENING_ALERT_SENT = True
                WEEKEND_CLOSURE_ALERT_SENT = False # إعادة ضبط لرسالة الإغلاق
        
        # 3. إعادة ضبط المتغيرات في الأيام العادية
        elif now_utc.weekday() != 4 and now_utc.weekday() != 6:
            WEEKEND_CLOSURE_ALERT_SENT = False
            WEEKEND_OPENING_ALERT_SENT = False

        await asyncio.sleep(60 * 60) # التحقق كل ساعة


async def scheduled_trades_checker():
    """مهمة متابعة الصفقات وإغلاقها."""
    await asyncio.sleep(5) 
    while True:
        await check_open_trades()
        await asyncio.sleep(TRADE_CHECK_INTERVAL)

async def trade_monitoring_98_percent():
    """مهمة التحليل المستمر وإرسال الإشارات التلقائية لـ 98% (كل 3 دقائق)."""
    await asyncio.sleep(30) # ابدأ بعد 30 ثانية من تشغيل البوت

    while True:
        if not is_weekend_closure():
            await send_vip_trade_signal_98()
        else:
            print("🤖 السوق مغلق (عطلة نهاية الأسبوع)، تم إيقاف التحليل التلقائي 98%.")
            
        await asyncio.sleep(TRADE_ANALYSIS_INTERVAL_98)

async def trade_monitoring_90_percent():
    """مهمة التحليل المستمر وإرسال الإشارات لـ 90% (فقط للأدمن، كل 3 دقائق)."""
    await asyncio.sleep(60) # ابدأ بعد 60 ثانية من تشغيل البوت

    while True:
        if not is_weekend_closure():
            await send_trade_signal_90()
        else:
            print("🤖 السوق مغلق (عطلة نهاية الأسبوع)، تم إيقاف التحليل التلقائي 90%.")
            
        await asyncio.sleep(TRADE_ANALYSIS_INTERVAL_90)


async def main():
    init_db()
    
    dp.message.middleware(AccessMiddleware())
    
    # 🌟 مهمة متابعة الصفقات وإغلاقها
    asyncio.create_task(scheduled_trades_checker()) 
    
    # 🌟 مهمة التحليل المستمر وإرسال الإشارات التلقائية (98% لـ VIP)
    asyncio.create_task(trade_monitoring_98_percent())
    
    # 🌟 مهمة التحليل الإضافي (90% للأدمن)
    asyncio.create_task(trade_monitoring_90_percent())
    
    # 🌟 مهمة التحقق من عطلة نهاية الأسبوع
    asyncio.create_task(weekend_alert_checker())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🤖 تم إيقاف البوت بنجاح.")
    except Exception as e:
        print(f"حدث خطأ كبير: {e}")

if __name__ == '__main__':
    try:
        start_analysis_loop(symbols=('XAUUSDT',), interval_seconds=60)
    except Exception as e:
        print(f'Auto-start error: {e}')
