import asyncio
import time
import os
import psycopg2
import pandas as pd
import schedule
import random
import uuid
import ccxt 

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
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "XAUT/USDT") 
CCXT_EXCHANGE = os.getenv("CCXT_EXCHANGE", "bybit") 
ADMIN_TRADE_SYMBOL = os.getenv("ADMIN_TRADE_SYMBOL", "XAUT/USDT") 
ADMIN_CAPITAL_DEFAULT = float(os.getenv("ADMIN_CAPITAL_DEFAULT", "100.0")) 
ADMIN_RISK_PER_TRADE = float(os.getenv("ADMIN_RISK_PER_TRADE", "0.02")) 

# ⚠️ المتغيرات العامة (معاملات تحديد نقاط الخروج والدخول - تم التحديث)
SL_FACTOR = 1.8  # عامل وقف الخسارة (يضرب في ATR)
SCALPING_RR_FACTOR = 2.5 # عامل R:R لصفقات الـ Scalping
LONGTERM_RR_FACTOR = 3.5 # عامل R:R لصفقات الـ Long-Term

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.98")) # تم رفع الثقة المطلوبة
TRADE_CHECK_INTERVAL = int(os.getenv("TRADE_CHECK_INTERVAL", "30")) 
ALERT_INTERVAL = int(os.getenv("ALERT_INTERVAL", "14400")) 
TRADE_ANALYSIS_INTERVAL = int(os.getenv("TRADE_ANALYSIS_INTERVAL", "60")) 

# فلاتر ADX الجديدة (تم التحديث)
ADX_SCALPING_MIN = 25
ADX_LONGTERM_MIN = 20
BB_PROXIMITY_THRESHOLD = 0.5 

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

# =============== قاعدة بيانات PostgreSQL (بدون تغيير) ===============
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
    cursor.execute("""
        SELECT trade_id, action, entry_price, take_profit, stop_loss, trade_type
        FROM trades 
        WHERE status = 'ACTIVE'
    """)
    trades = cursor.fetchall()
    conn.close()
    keys = ["trade_id", "action", "entry_price", "take_profit", "stop_loss", "trade_type"]
    return [dict(zip(keys, trade)) for trade in trades]

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
📈 **تقرير الأداء الشخصي** 📅 **الفترة:** {start_date} إلى {end_date}
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
    تجلب بيانات الشموع (OHLCV) للرمز والفاصل الزمني المحدد باستخدام CCXT (Bybit).
    """
    
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
        print(f"❌ فشل جلب بيانات OHLCV من CCXT ({CCXT_EXCHANGE}): {e}")
        return pd.DataFrame() 

def fetch_current_price_ccxt(symbol: str) -> float or None:
    """جلب السعر الحالي الفوري لرمز XAUT/USDT (الأولوية القصوى لـ CCXT للدقة)."""
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
        return ticker['ask'] if 'ask' in ticker and ticker['ask'] is not None else ticker['last']
        
    except Exception as e:
        print(f"❌ فشل جلب السعر اللحظي من CCXT ({CCXT_EXCHANGE}): {e}.")
        return None

# =============== برمجية وسيطة للحظر والاشتراك (Access Middleware) (بدون تغيير) ===============
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
    df['-DMS'] = smooth(df['-DM'], window)
    df['TRS'] = smooth(df['tr'], window)
    
    # Directional Indicators (DI)
    df['+DI'] = (df['+DMS'] / df['TRS']) * 100
    df['-DI'] = (df['-DMS'] / df['TRS']) * 100
    
    # Directional Index (DX) and Average Directional Index (ADX)
    df['DX'] = (abs(df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI'])).fillna(0) * 100
    df['ADX'] = smooth(df['DX'], window)
    
    return df

def get_signal_and_confidence(symbol: str) -> tuple[str, float, str, float, float, float, float, str]:
    """
    تحليل مزدوج (Scalping / Long-Term) باستخدام 7 فلاتر لتحديد إشارة فائقة القوة.
    النتيجة الإضافية: trade_type
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
            return f"لا تتوفر بيانات كافية للتحليل لرمز التداول: {DISPLAY_SYMBOL}.", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE"

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
        MIN_SL_DISTANCE = 0.5 
        
        if current_atr < MIN_ATR_THRESHOLD:
            return f"⚠️ السوق هادئ جداً (ATR: {current_atr:.2f} < {MIN_ATR_THRESHOLD}). الإشارة HOLD.", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE"

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
        
        # الشرط الأولي (كروس أوفر على 15m)
        is_buy_signal_15m = (data_15m['EMA_10'].iloc[-2] <= data_15m['EMA_30'].iloc[-2] and data_15m['EMA_10'].iloc[-1] > data_15m['EMA_30'].iloc[-1])
        is_sell_signal_15m = (data_15m['EMA_10'].iloc[-2] >= data_15m['EMA_30'].iloc[-2] and data_15m['EMA_10'].iloc[-1] < data_15m['EMA_30'].iloc[-1])

        if is_buy_signal_15m:
            # فلتر ADX: الاتجاه قوي على 15m
            if current_adx_15m >= ADX_LONGTERM_MIN:
                # فلتر الاتجاهات الكبرى: توافق 30m و 1h
                if htf_trend_30m == "BULLISH" and htf_trend_1h == "BULLISH":
                    # فلتر SMA 200: يجب أن يكون السعر فوق SMA 200 على 30m
                    if entry_price > latest_sma_200_30m:
                        action_lt = "BUY"
                        
        elif is_sell_signal_15m:
            # فلتر ADX: الاتجاه قوي على 15m
            if current_adx_15m >= ADX_LONGTERM_MIN:
                # فلتر الاتجاهات الكبرى: توافق 30m و 1h
                if htf_trend_30m == "BEARISH" and htf_trend_1h == "BEARISH":
                    # فلتر SMA 200: يجب أن يكون السعر تحت SMA 200 على 30m
                    if entry_price < latest_sma_200_30m:
                        action_lt = "SELL"
                        
        if action_lt != "HOLD":
            action = action_lt
            trade_type = "LONG_TERM"
            confidence = 0.99 # ثقة عالية جداً نتيجة لشدة الفلترة
            rr_factor = LONGTERM_RR_FACTOR
        
        # ===============================================
        # === 2. محاولة استخراج إشارة SCALPING (الخيار الثاني) ===
        # ===============================================
        
        else: # إذا لم نجد إشارة Long-Term
            
            action_sc = "HOLD"
            
            # الشرط الأولي (كروس أوفر على 1m)
            ema_fast_prev = data_1m['EMA_5'].iloc[-2]
            ema_slow_prev = data_1m['EMA_20'].iloc[-2]
            ema_fast_current = data_1m['EMA_5'].iloc[-1]
            ema_slow_current = data_1m['EMA_20'].iloc[-1]
            
            is_buy_signal_1m = (ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current)
            is_sell_signal_1m = (ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current)

            if is_buy_signal_1m: 
                # فلتر ADX: الاتجاه قوي على 5m
                if current_adx_5m >= ADX_SCALPING_MIN:
                    # فلتر الاتجاهات: توافق 5m و 15m
                    if htf_trend_5m == "BULLISH" and htf_trend_15m == "BULLISH":
                        # فلتر RSI: يجب أن يكون في منطقة زخم جيد
                        if current_rsi > 50 and current_rsi < 70:
                            # فلتر البولينجر باند: قرب القاع (للارتداد الصاعد)
                            if (entry_price - latest_bb_lower_5m) < BB_PROXIMITY_THRESHOLD and entry_price > latest_bb_lower_5m:
                                # فلتر SMA 200: يجب أن يكون السعر فوقها على 5m
                                if entry_price > latest_sma_200_5m:
                                    action_sc = "BUY"
                                    
            elif is_sell_signal_1m:
                # فلتر ADX: الاتجاه قوي على 5m
                if current_adx_5m >= ADX_SCALPING_MIN:
                    # فلتر الاتجاهات: توافق 5m و 15m
                    if htf_trend_5m == "BEARISH" and htf_trend_15m == "BEARISH":
                         # فلتر RSI: يجب أن يكون في منطقة زخم جيد
                        if current_rsi < 50 and current_rsi > 30:
                            # فلتر البولينجر باند: قرب القمة (للهبوط)
                            if (latest_bb_upper_5m - entry_price) < BB_PROXIMITY_THRESHOLD and entry_price < latest_bb_upper_5m:
                                # فلتر SMA 200: يجب أن يكون السعر تحتها على 5m
                                if entry_price < latest_sma_200_5m:
                                    action_sc = "SELL"
                                    
            if action_sc != "HOLD":
                action = action_sc
                trade_type = "SCALPING"
                confidence = 0.98 # ثقة 98% نتيجة لشدة الفلترة
                rr_factor = SCALPING_RR_FACTOR
            else:
                action = "HOLD"
                trade_type = "NONE"
                confidence = 0.0

        # ===============================================
        # === حساب نقاط الخروج والتقرير النهائي ===
        # ===============================================
        
        if action != "HOLD":
            # SL_DISTANCE يعتمد على ATR (متغير لكل صفقة)
            risk_amount = max(current_atr * SL_FACTOR, MIN_SL_DISTANCE) 
            stop_loss_distance = risk_amount
            
            if action == "BUY":
                stop_loss = entry_price - risk_amount 
                take_profit = entry_price + (risk_amount * rr_factor) 
            
            elif action == "SELL":
                stop_loss = entry_price + risk_amount
                take_profit = entry_price - (risk_amount * rr_factor)
                
            price_msg = f"""
📊 آخر سعر لـ <b>{DISPLAY_SYMBOL}</b> (المصدر: {price_source})
السعر: ${entry_price:,.2f} | الوقت: {latest_time} UTC

**تحليل المؤشرات:**
- **RSI (1m):** {current_rsi:.2f} 
- **ATR (1m):** {current_atr:.2f} 
- **ADX (5m):** {current_adx_5m:.2f} 
- **ADX (15m):** {current_adx_15m:.2f} 
- **SMA 200 (5m):** {latest_sma_200_5m:,.2f}
- **الاتجاهات (5m/15m/30m/1h):** {htf_trend_5m[0]}/{htf_trend_15m[0]}/{htf_trend_30m[0]}/{htf_trend_1h[0]}
"""
            return price_msg, confidence, action, entry_price, stop_loss, take_profit, stop_loss_distance, trade_type
        
        # ❌ رسالة الرفض النهائي (عندما تكون HOLD)
        else:
            price_msg = f"""
📊 آخر سعر لـ <b>{DISPLAY_SYMBOL}</b> (المصدر: {price_source})
السعر: ${entry_price:,.2f} | الوقت: {latest_time} UTC

**تحليل المؤشرات:**
- **RSI (1m):** {current_rsi:.2f} 
- **ATR (1m):** {current_atr:.2f} 
- **ADX (5m/15m):** {current_adx_5m:.2f}/{current_adx_15m:.2f} 
- **الاتجاهات (30m/1h):** {htf_trend_30m[0]}/{htf_trend_1h[0]}
"""
            return price_msg, confidence, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE"
        
    except Exception as e:
        print(f"❌ فشل في جلب بيانات التداول لـ XAUUSD أو التحليل: {e}")
        return f"❌ فشل في جلب بيانات التداول لـ XAUUSD أو التحليل: {e}", 0.0, "HOLD", 0.0, 0.0, 0.0, 0.0, "NONE"


# === دالة الإرسال التلقائي الجديدة (تم التحديث لدعم النوع المزدوج) ===
async def send_vip_trade_signal():
    
    # 1. تحقق من عدم وجود صفقات نشطة بالفعل
    active_trades = get_active_trades()
    if len(active_trades) > 0:
        print(f"🤖 يوجد {len(active_trades)} صفقات نشطة. تم تخطي التحليل التلقائي.")
        return 

    # 2. إجراء التحليل المزدوج
    try:
        price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type = get_signal_and_confidence(TRADE_SYMBOL)
    except Exception as e:
        print(f"❌ خطأ حرج أثناء التحليل التلقائي: {e}")
        return

    confidence_percent = confidence * 100
    DISPLAY_SYMBOL = "XAUUSD" 
    
    # تحديد معامل R:R المستخدم للرسالة
    rr_factor_used = SCALPING_RR_FACTOR if trade_type == "SCALPING" else LONGTERM_RR_FACTOR

    # 3. تحقق من شرط الثقة (98% أو أعلى)
    if action != "HOLD" and confidence >= CONFIDENCE_THRESHOLD:
        
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
🔒 **SUCCESS RATE:** <b>{confidence_percent:.2f}%</b> ({CONFIDENCE_THRESHOLD*100:.0f}%+)
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
         print(f"⚠️ تم العثور على إشارة {action} ({trade_type})، لكن الثقة {confidence_percent:.2f}% لم تصل إلى المطلوب {CONFIDENCE_THRESHOLD*100:.0f}%.")
    else:
         print("💡 لا توجد إشارة واضحة (HOLD).")


# =============== باقي الكود (تم تحديث بعض الدوال لدعم trade_type) ===============
# (العديد من الدوال مثل القوائم، الاتصال، الإدارة، التحليل الفوري، إلخ... بدون تغيير)
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
            [KeyboardButton(text="تحليل خاص (VIP) 👤"), KeyboardButton(text="تقرير الأداء الأسبوعي 📊")],
            [KeyboardButton(text="تسجيل نتيجة صفقة 📝"), KeyboardButton(text="تعديل رأس المال 💵")],
            [KeyboardButton(text="تحليل فوري ⚡️"), KeyboardButton(text="📊 جرد الصفقات اليومي")],
            [KeyboardButton(text="📢 رسالة لكل المستخدمين"), KeyboardButton(text="🔑 إنشاء مفتاح اشتراك")],
            [KeyboardButton(text="🚫 حظر مستخدم"), KeyboardButton(text="✅ إلغاء حظر مستخدم")],
            [KeyboardButton(text="👥 عدد المستخدمين"), KeyboardButton(text="🗒️ عرض حالة المشتركين")],
            [KeyboardButton(text="🔙 عودة للمستخدم")]
        ],
        resize_keyboard=True
    )

@dp.message(F.text == "تعديل رأس المال 💵")
async def prompt_new_capital(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    current_capital = get_admin_financial_status()
    await state.set_state(AdminStates.waiting_new_capital)
    await msg.reply(
        f"💰 رأس مالك الحالي المُسجل هو: **${current_capital:,.2f}**\n"
        "يرجى إدخال رأس المال الجديد كقيمة عددية (مثال: 105.50):"
    )

@dp.message(AdminStates.waiting_new_capital)
async def process_new_capital(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        new_capital = float(msg.text.strip().replace('$', '').replace(',', ''))
        if new_capital <= 0: raise ValueError
        update_admin_capital(new_capital)
        await state.clear()
        await msg.reply(f"✅ تم تحديث رأس مالك بنجاح! رأس المال الجديد هو: **${new_capital:,.2f}**", reply_markup=admin_menu())
    except ValueError:
        await msg.reply("❌ قيمة رأس المال غير صحيحة. يرجى إدخال رقم موجب فقط.", reply_markup=admin_menu())

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
         await msg.reply("❌ حدث خطأ غير متوقع أثناء تسجيل النتيجة. يرجى المحاولة يدوياً أو مراجعة اللوغ.", reply_markup=admin_menu())

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
            f"✅ تم تسجيل الصفقة اليدوية: {display_symbol} ({action})، PnL: ${pnl:,.2f}.\n"
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

@dp.message(F.text == "تقرير الأداء الأسبوعي 📊")
async def show_weekly_report(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    report = generate_weekly_performance_report()
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
# دالة تحليل فوري ⚡️ 
# ----------------------------------------------------------------------------------
@dp.message(F.text == "تحليل فوري ⚡️")
async def analyze_market_now(msg: types.Message):
    if msg.from_user.id != ADMIN_ID and not is_user_vip(msg.from_user.id): 
        await msg.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط.")
        return
    
    await msg.reply("⏳ جارٍ تحليل السوق بحثًا عن فرصة تداول ذات ثقة عالية...")
    
    price_info_msg, confidence, action, _, _, _, _, trade_type = get_signal_and_confidence(TRADE_SYMBOL)
    confidence_percent = confidence * 100
    threshold_percent = int(CONFIDENCE_THRESHOLD * 100)
    
    # ⚠️ التحقق من حالة البيانات أولا (رسالة الخطأ/البيانات غير الكافية)
    if confidence == 0.0 and sl == 0.0 and ("لا تتوفر" in price_info_msg or "عرضي" in price_info_msg or "هادئ" in price_info_msg):
        await msg.answer(f"❌ فشل التحليل:\n{price_info_msg}", parse_mode="HTML")
        return
    
    # (1) إذا لم تتوفر إشارة أساساً أو الثقة غير كافية
    if action == "HOLD" or confidence < CONFIDENCE_THRESHOLD:
         status_msg = f"""
💡 **تقرير التحليل الفوري - XAUUSD**
━━━━━━━━━━━━━━━
🔎 **الإشارة الحالية:** {action}
🔒 **الثقة:** <b>{confidence_percent:.2f}%</b> (المطلوب: {threshold_percent}%)
❌ **القرار:** {('لا توجد إشارة واضحة (HOLD)' if action == 'HOLD' else 'الثقة غير كافية للدخول')}
━━━━━━━━━━━━━━━
{price_info_msg}
"""
         await msg.answer(status_msg, parse_mode="HTML")
    
    # (2) إذا كانت الثقة كافية (98% أو أعلى) - تم تصحيح الرسالة هنا
    elif confidence >= CONFIDENCE_THRESHOLD:
         await msg.answer(f"✅ تم إيجاد إشارة فائقة القوة ({action}) ({trade_type}) على XAUUSD!\nنسبة الثقة: <b>{confidence_percent:.2f}%</b> (أعلى من المطلوب {threshold_percent}%).\n**تم إرسال الإشارة التلقائية لـ VIP إذا لم تكن هناك صفقات نشطة.**", parse_mode="HTML")
# ----------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------
# دالة تحليل خاص (VIP) 👤 
# ----------------------------------------------------------------------------------
@dp.message(F.text == "تحليل خاص (VIP) 👤")
async def analyze_private_pair(msg: types.Message):
    global SCALPING_RR_FACTOR, LONGTERM_RR_FACTOR 
    
    if msg.from_user.id != ADMIN_ID: await msg.answer("🚫 هذه الميزة خاصة بالإدمن."); return
    
    await msg.reply(f"⏳ جارٍ تحليل الزوج الخاص: **XAUUSD** (الذهب)...")
    
    price_info_msg, confidence, action, entry, sl, tp, sl_distance, trade_type = get_signal_and_confidence(ADMIN_TRADE_SYMBOL)
    
    confidence_percent = confidence * 100
    threshold_percent = int(CONFIDENCE_THRESHOLD * 100)
    
    # ⚠️ التحقق من حالة البيانات أولا (رسالة الخطأ/البيانات غير الكافية)
    if confidence == 0.0 and sl == 0.0 and ("لا تتوفر" in price_info_msg or "عرضي" in price_info_msg or "هادئ" in price_info_msg):
        await msg.answer(f"❌ فشل التحليل:\n{price_info_msg}", parse_mode="HTML")
        return
        
    if action == "HOLD" or confidence < CONFIDENCE_THRESHOLD:
        status_msg = f"""
💡 **التحليل الخاص - XAUUSD**
━━━━━━━━━━━━━━━
🔎 **الإشارة الحالية:** {action}
🔒 **الثقة:** <b>{confidence_percent:.2f}%</b> (المطلوب: {threshold_percent}%)
❌ **القرار:** {('لا توجد إشارة واضحة (HOLD)' if action == 'HOLD' else 'الثقة غير كافية للدخول')}
━━━━━━━━━━━━━━━
{price_info_msg}

**📊 ملاحظة:** لم يتم توليد صفقة قوية لتجنب المخاطر.
"""
        await msg.answer(status_msg, parse_mode="HTML")
        return
    
    # إذا كانت الإشارة قوية وتجاوزت الثقة المطلوبة
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

@dp.message(F.text == "📊 جرد الصفقات اليومي")
async def daily_inventory_report(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 ليس لديك صلاحية الوصول.")
        return
    
    report = get_daily_trade_report()
    await msg.reply(report, parse_mode="HTML")

@dp.message(F.text == "📈 سعر السوق الحالي")
async def get_current_price(msg: types.Message):
    current_price = fetch_current_price_ccxt(TRADE_SYMBOL) 
    
    DISPLAY_SYMBOL = "XAUUSD" 

    if current_price is not None:
        price_msg = f"📊 السعر الحالي لـ <b>{DISPLAY_SYMBOL}</b> (المصدر: {CCXT_EXCHANGE}):\nالسعر: <b>${current_price:,.2f}</b>\nالوقت: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        await msg.reply(price_msg, parse_mode="HTML")
    else:
        await msg.reply(f"❌ فشل جلب السعر اللحظي لـ {DISPLAY_SYMBOL} من {CCXT_EXCHANGE}. يرجى المحاولة لاحقاً.")

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
        trade_type = trade['trade_type']
        
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

نحن لسنا مجرد بوت، بل منصة تحليل ذكية ومؤتمتة بالكامل، مصممة لملاحقة أكبر الفرص في سوق الذهب (XAUUSD). مهمتنا هي تصفية ضجيج السوق وتقديم إشارات <b>مؤكدة فقط</b>.

━━━━━━━━━━━━━━━
🛡️ **ماذا يقدم لك الاشتراك VIP؟ (ميزة القوة الخارقة)**
1.  <b>إشارات ثنائية الاستراتيجية (Dual Strategy):</b>
    نظامنا يبحث عن نوعين من الصفقات لتغطية جميع ظروف السوق القوية:
    * **Scalping:** R:R 1:{SCALPING_RR_FACTOR:.1f} مع فلاتر 1m/5m/15m و ADX > {ADX_SCALPING_MIN}.
    * **Long-Term:** R:R 1:{LONGTERM_RR_FACTOR:.1f} مع فلاتر 15m/30m/1h و ADX > {ADX_LONGTERM_MIN}.
    
2.  <b>إشارات سداسية التأكيد (6-Tier Confirmation):</b>
    كل صفقة تُرسل يجب أن تمر بـ 7 فلاتر تحليلية (EMA, RSI, ADX, BB, SMA 200, توافق الأطر الزمنية).

3.  <b>أعلى درجات الثقة:</b>
    لا يتم إرسال أي صفقة إلا إذا تجاوزت نسبة الثقة **{threshold_percent}%** (حالياً يتم الإرسال عند {threshold_percent}% أو أعلى)، وهذا يضمن جودة إشارة استثنائية.

4.  <b>نقاط خروج ديناميكية:</b>
    نقاط TP و SL تتغير مع كل صفقة بناءً على التقلب الفعلي للسوق (ATR)، مما يضمن تحديد هدف ووقف مناسبين لظروف السوق الحالية.

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
        if is_banned_status == 0 and uid != ADMIN_ID: 
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
        report += f"  - اليوزر: @{username if username else 'لا يوجد'}\n"
        report += f"  - الحالة: {ban_status} / {vip_status}\n\n"
        
    await msg.reply(report, parse_mode="HTML")

# ===============================================
# === دالة متابعة الصفقات (Trade Monitoring) (تم تحديث الرسالة) ===
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
            for uid, is_banned_status in all_users:
                 if is_banned_status == 0 and uid != ADMIN_ID and is_user_vip(uid):
                    try:
                        await bot.send_message(uid, close_msg, parse_mode="HTML")
                    except Exception:
                        pass
                        
            if ADMIN_ID != 0:
                await bot.send_message(ADMIN_ID, f"🔔 تم إغلاق الصفقة **{trade_id}** بنجاح على: {exit_status}", parse_mode="HTML")

# ===============================================
# === إعداد المهام المجدولة (Setup Scheduled Tasks) ===
# ===============================================

def is_weekend_closure():
    """التحقق مما إذا كان إغلاق عطلة نهاية الأسبوع (لتجنب التنبيهات)."""
    now_utc = datetime.now(timezone.utc) 
    weekday = now_utc.weekday() 
    
    # من إغلاق الجمعة (الساعة 21:00 بتوقيت UTC) حتى فتح الأحد (الساعة 21:00 بتوقيت UTC)
    if weekday == 5 or (weekday == 6 and now_utc.hour < 21) or (weekday == 4 and now_utc.hour >= 21): 
        return True
    return False 


async def scheduled_tasks():
    await asyncio.sleep(5) 
    while True:
        # متابعة الصفقات المفتوحة
        await check_open_trades()
        await asyncio.sleep(TRADE_CHECK_INTERVAL)
        
async def trade_monitoring_and_alert():
    """مهمة المراقبة المستمرة وإرسال التنبيهات/الإشارات التلقائية."""
    await asyncio.sleep(60) # ابدأ بعد دقيقة من تشغيل البوت

    while True:
        
        if not is_weekend_closure():
            # 1. تحليل السوق وإرسال الإشارة إذا تجاوزت الثقة 98%
            await send_vip_trade_signal()
        else:
            print("🤖 السوق مغلق (عطلة نهاية الأسبوع)، تم إيقاف التحليل التلقائي.")
            
        # الانتظار للفاصل الزمني المحدد (60 ثانية)
        await asyncio.sleep(TRADE_ANALYSIS_INTERVAL)


async def main():
    init_db()
    
    dp.message.middleware(AccessMiddleware())
    
    # 🌟 مهمة متابعة الصفقات وإغلاقها
    asyncio.create_task(scheduled_tasks()) 
    
    # 🌟 مهمة التحليل المستمر وإرسال الإشارات التلقائية
    asyncio.create_task(trade_monitoring_and_alert())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🤖 تم إيقاف البوت بنجاح.")
    except Exception as e:
        print(f"حدث خطأ كبير: {e}")
