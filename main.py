import sys
import os
import requests
import json
import time
import asyncio
import psycopg2
import pandas as pd
import schedule
import random
import uuid
import ccxt 
from datetime import datetime, timedelta
from urllib.parse import urlparse
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton 
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from typing import Callable, Dict, Any, Awaitable

# =============== FSM States ===============
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
    
# =============== Configuration ===============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID", "0") 
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "XAUT/USDT") 
CCXT_EXCHANGE = os.getenv("CCXT_EXCHANGE", "bybit") 
ADMIN_TRADE_SYMBOL = os.getenv("ADMIN_TRADE_SYMBOL", "XAUT/USDT") 
ADMIN_CAPITAL_DEFAULT = float(os.getenv("ADMIN_CAPITAL_DEFAULT", "100.0")) 
ADMIN_RISK_PER_TRADE = float(os.getenv("ADMIN_RISK_PER_TRADE", "0.02")) 

CONFIDENCE_THRESHOLD_98 = float(os.getenv("CONFIDENCE_THRESHOLD_98", "0.98")) 
CONFIDENCE_THRESHOLD_90 = float(os.getenv("CONFIDENCE_THRESHOLD_90", "0.90"))

TRADE_ANALYSIS_INTERVAL_98 = int(os.getenv("TRADE_ANALYSIS_INTERVAL_98", "180"))
TRADE_ANALYSIS_INTERVAL_90 = int(os.getenv("TRADE_ANALYSIS_INTERVAL_90", "180"))
TRADE_CHECK_INTERVAL = int(os.getenv("TRADE_CHECK_INTERVAL", "30"))

SL_FACTOR = 3.0           
SCALPING_RR_FACTOR = 1.5  
LONGTERM_RR_FACTOR = 1.5  
MAX_SL_DISTANCE = 7.0     
MIN_SL_DISTANCE = 1.5     

ADX_SCALPING_MIN = 15
ADX_LONGTERM_MIN = 12
BB_PROXIMITY_THRESHOLD = 0.5 

MIN_FILTERS_FOR_98 = 7 
MIN_FILTERS_FOR_90 = 5 

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "I1l_1")

try:
    ADMIN_ID = int(ADMIN_ID_STR)
    if ADMIN_ID == 0: print("⚠️ ADMIN_ID is 0.")
except ValueError:
    print("❌ Error! ADMIN_ID is not a valid number.")
    ADMIN_ID = 0 

if not BOT_TOKEN: raise ValueError("🚫 TELEGRAM_BOT_TOKEN not found.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML")) 
dp = Dispatcher(storage=MemoryStorage())

# ================================== Helper Functions ==================================
def h(text):
    text = str(text)
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

# ================================== PostgreSQL DB ==================================
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL: raise ValueError("🚫 DATABASE_URL not found.")

def get_db_connection():
    try:
        url = urlparse(DATABASE_URL)
        return psycopg2.connect(database=url.path[1:], user=url.username, password=url.password, host=url.hostname, port=url.port)
    except Exception as e:
        print(f"❌ DB connection failed: {e}")
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
            id SERIAL PRIMARY KEY, record_type VARCHAR(50) NOT NULL, timestamp DOUBLE PRECISION NOT NULL, value_float DOUBLE PRECISION NULL, trade_action VARCHAR(10) NULL, trade_symbol VARCHAR(50) NULL, lots_used DOUBLE PRECISION NULL
        );
    """)
    conn.commit()
    try:
        cursor.execute("ALTER TABLE trades ADD COLUMN trade_type VARCHAR(50) DEFAULT 'SCALPING'")
        conn.commit()
    except psycopg2.errors.DuplicateColumn:
        conn.rollback() 
    except Exception as e:
        conn.rollback()
        
    cursor.execute("SELECT value_float FROM admin_performance WHERE record_type = 'CAPITAL' ORDER BY timestamp DESC LIMIT 1")
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO admin_performance (record_type, timestamp, value_float) VALUES ('CAPITAL', %s, %s)", (time.time(), ADMIN_CAPITAL_DEFAULT))
        conn.commit()
        
    conn.close()

def add_user(user_id, username):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, username, joined_at) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET username = %s", (user_id, username, time.time(), username))
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
    cursor.execute("INSERT INTO users (user_id, is_banned) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET is_banned = %s", (user_id, status, status))
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
            cursor.execute("INSERT INTO users (user_id, vip_until) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET vip_until = %s", (user_id, new_vip_until.timestamp(), new_vip_until.timestamp()))
            conn.commit()
            return True, days, new_vip_until
        return False, 0, None
    finally:
        conn.close()

def add_trade(trade_id, action, entry_price, take_profit, stop_loss, user_count, trade_type):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("INSERT INTO trades (trade_id, sent_at, action, entry_price, take_profit, stop_loss, user_count, trade_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (trade_id, time.time(), action, entry_price, take_profit, stop_loss, user_count, trade_type))
    conn.commit()
    conn.close()
    
def get_active_trades():
    conn = get_db_connection()
    if conn is None: return []
    cursor = conn.cursor()
    cursor.execute("SELECT trade_id, action, entry_price, take_profit, stop_loss, sent_at, trade_type FROM trades WHERE status = 'ACTIVE'")
    result = cursor.fetchall()
    conn.close()
    return result

def update_trade_status(trade_id, status, exit_status=None, close_price=None):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    if exit_status:
        cursor.execute("UPDATE trades SET status = %s, exit_status = %s, close_price = %s WHERE trade_id = %s", (status, exit_status, close_price, trade_id))
    else:
        cursor.execute("UPDATE trades SET status = %s WHERE trade_id = %s", (status, trade_id))
    conn.commit()
    conn.close()

def get_admin_capital():
    conn = get_db_connection()
    if conn is None: return ADMIN_CAPITAL_DEFAULT
    cursor = conn.cursor()
    cursor.execute("SELECT value_float FROM admin_performance WHERE record_type = 'CAPITAL' ORDER BY timestamp DESC LIMIT 1")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else ADMIN_CAPITAL_DEFAULT

def update_admin_capital(new_capital, trade_action=None, trade_symbol=None, lots_used=None):
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("INSERT INTO admin_performance (record_type, timestamp, value_float, trade_action, trade_symbol, lots_used) VALUES ('CAPITAL', %s, %s, %s, %s, %s)", (time.time(), new_capital, trade_action, trade_symbol, lots_used))
    conn.commit()
    conn.close()
    
def get_performance_data(days=7):
    conn = get_db_connection()
    if conn is None: return [], []
    cursor = conn.cursor()
    start_time = (datetime.now() - timedelta(days=days)).timestamp()
    cursor.execute("SELECT action, entry_price, close_price, trade_type, sent_at, exit_status FROM trades WHERE status = 'CLOSED' AND sent_at >= %s", (start_time,))
    closed_trades = cursor.fetchall()
    cursor.execute("SELECT timestamp, value_float FROM admin_performance WHERE record_type = 'CAPITAL' AND timestamp >= %s ORDER BY timestamp ASC", (start_time,))
    capital_history = cursor.fetchall()
    conn.close()
    return closed_trades, capital_history

# ================================== CCXT Exchange ==================================
def setup_exchange(exchange_id):
    try:
        exchange_class = getattr(ccxt, exchange_id)
        exchange = exchange_class({'enableRateLimit': True,})
        return exchange
    except AttributeError:
        print(f"❌ Exchange '{exchange_id}' not found in CCXT.")
        return None
    except Exception as e:
        print(f"❌ CCXT setup error: {e}")
        return None

EXCHANGE = setup_exchange(CCXT_EXCHANGE)

# ================================== Notifications & UI ==================================
async def send_broadcast(message_text, target="all_users"):
    if target == "vip_only":
        user_data = get_all_users_ids(vip_only=True)
    else:
        user_data = get_all_users_ids(vip_only=False)
    user_ids = [user_id for user_id, is_banned in user_data if is_banned == 0]
    success_count = 0
    fail_count = 0
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, message_text, parse_mode="HTML")
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            if 'bot was blocked by the user' not in str(e): print(f"⚠️ Failed to send to {user_id}: {e}")
            fail_count += 1
    return success_count, fail_count

def get_admin_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="إرسال صفقة يدوية"), KeyboardButton(text="نتائج الصفقات")],
        [KeyboardButton(text="إدارة المستخدمين"), KeyboardButton(text="بث رسالة")],
        [KeyboardButton(text="توليد مفتاح")],
        [KeyboardButton(text="القائمة الرئيسية (مستخدم)")]
    ], resize_keyboard=True)

def get_user_keyboard(is_vip):
    keyboard = [
        [KeyboardButton(text="الاشتراك وتفعيل المفتاح")],
        [KeyboardButton(text="تقرير الأداء الأسبوعي")],
    ]
    if os.getenv("ADMIN_USERNAME") == "I1l_1":
        keyboard.append([KeyboardButton(text="لوحة تحكم الأدمن")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="إلغاء الأمر")]], resize_keyboard=True)

def format_performance_report(closed_trades):
    if not closed_trades: return "لا توجد صفقات مغلقة في الأيام السبعة الماضية."
    total_trades = len(closed_trades)
    winning_trades = 0
    losing_trades = 0

    for action, entry, close, trade_type, _, exit_status in closed_trades:
        if exit_status == 'TP':
            winning_trades += 1
        elif exit_status == 'SL':
            losing_trades += 1
        elif close is not None:
            pnl_factor = (close / entry) - 1.0
            if action == 'SELL': pnl_factor *= -1 
            if pnl_factor > 0: winning_trades += 1
            elif pnl_factor < 0: losing_trades += 1
            
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    
    return (
        "📊 <b>تقرير الأداء الأسبوعي (آخر 7 أيام)</b>\n\n"
        f"🔹 إجمالي الصفقات: <b>{total_trades}</b>\n"
        f"🔸 الصفقات الرابحة: <b>{winning_trades}</b>\n"
        f"🔻 الصفقات الخاسرة: <b>{losing_trades}</b>\n"
        f"📈 نسبة الفوز: <b>{win_rate:.2f}%</b>"
    )

# ================================== Market Analysis ==================================
async def fetch_ohlcv(symbol, timeframe, limit=100):
    if not EXCHANGE: return None
    try:
        ohlcv = await EXCHANGE.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"❌ Failed to fetch OHLCV for {symbol}: {e}")
        return None

def calculate_adx(df, window=14):
    df['high_low'] = df['high'] - df['low']
    df['high_close'] = abs(df['high'] - df['close'].shift(1))
    df['low_close'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    df['up_move'] = df['high'] - df['high'].shift(1)
    df['down_move'] = df['low'].shift(1) - df['low']
    df['pdm'] = df['up_move'].apply(lambda x: x if x > 0 else 0)
    df['ndm'] = df['down_move'].apply(lambda x: x if x > 0 else 0)
    
    df['pdm'] = df.apply(lambda row: row['pdm'] if row['pdm'] > row['ndm'] else 0, axis=1)
    df['ndm'] = df.apply(lambda row: row['ndm'] if row['ndm'] > row['pdm'] else 0, axis=1)
    
    df['atr'] = df['tr'].ewm(span=window, min_periods=window).mean()
    df['pdi'] = (df['pdm'].ewm(span=window, min_periods=window).mean() / df['atr']) * 100
    df['ndi'] = (df['ndm'].ewm(span=window, min_periods=window).mean() / df['atr']) * 100
    df['dx'] = abs(df['pdi'] - df['ndi']) / (df['pdi'] + df['ndi']) * 100
    
    df['adx'] = df['dx'].ewm(span=window, min_periods=window).mean().fillna(0)
    return df['adx'].iloc[-1], df['pdi'].iloc[-1], df['ndi'].iloc[-1]

def calculate_entry_exit_points(df, action, risk_usd):
    last_close = df['close'].iloc[-1]
    recent_high = df['high'].iloc[-5:-1].max()
    recent_low = df['low'].iloc[-5:-1].min()

    if action == 'BUY':
        sl_raw = recent_low * (1 - MIN_SL_DISTANCE / 10000)
        sl_max = last_close * (1 - MAX_SL_DISTANCE / 10000)
        sl = max(sl_raw, sl_max)
    elif action == 'SELL':
        sl_raw = recent_high * (1 + MIN_SL_DISTANCE / 10000)
        sl_max = last_close * (1 + MAX_SL_DISTANCE / 10000)
        sl = min(sl_raw, sl_max)
    else: return last_close, 0, 0, 0 

    sl_distance_pct = abs(last_close - sl) / last_close * 100
    tp_distance_pct = sl_distance_pct * SCALPING_RR_FACTOR
    
    if action == 'BUY':
        tp = last_close * (1 + tp_distance_pct / 100)
    else:
        tp = last_close * (1 - tp_distance_pct / 100)

    capital = get_admin_capital()
    lots_used = capital * ADMIN_RISK_PER_TRADE
    
    return last_close, tp, sl, lots_used

async def analyze_trade_conditions(symbol, timeframe, min_filters, required_confidence, is_admin_check=False):
    if not EXCHANGE: return None
    df = await fetch_ohlcv(symbol, timeframe)
    if df is None or df.empty: return None

    adx_value, pdi, ndi = calculate_adx(df, window=14)
    adx_min = ADX_SCALPING_MIN
    
    adx_filter_passed = adx_value > adx_min
    if not adx_filter_passed and adx_value < 1.0: return None 
    
    if pdi > ndi:
        action = 'BUY'
        adx_trend_passed = adx_filter_passed
    elif ndi > pdi:
        action = 'SELL'
        adx_trend_passed = adx_filter_passed
    else:
        return None 

    filters_passed = 0
    total_filters = 10 

    if adx_trend_passed: filters_passed += 1
    if random.random() > 0.1: filters_passed += 1
    
    confidence = filters_passed / total_filters if total_filters > 0 else 0
    
    if confidence < required_confidence or filters_passed < min_filters:
        if is_admin_check:
            return {"status": "FAIL", "confidence": confidence, "filters_passed": filters_passed, "required_confidence": required_confidence}
        return None

    capital = get_admin_capital()
    risk_usd = capital * ADMIN_RISK_PER_TRADE
    entry, tp, sl, lots_used = calculate_entry_exit_points(df, action, risk_usd)
    
    if entry == 0: return None

    return {
        "status": "SUCCESS", "action": action, "entry": entry, "tp": tp, "sl": sl,
        "symbol": symbol, "timeframe": timeframe, "trade_type": 'SCALPING',
        "confidence": confidence, "lots_used": lots_used
    }

# ================================== Handlers & Logic ==================================

@dp.message(Command("start"))
async def command_start_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    add_user(user_id, username)
    if is_banned(user_id):
        await message.answer("❌ You are banned.")
        return

    is_vip = is_user_vip(user_id)
    keyboard = get_user_keyboard(is_vip)
    welcome_message = f"Welcome {h(message.from_user.full_name)}!\n"
    if is_vip:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (user_id,))
            vip_until_ts = cursor.fetchone()[0]
            conn.close()
        vip_until_date = datetime.fromtimestamp(vip_until_ts).strftime("%Y-%m-%d %H:%M")
        welcome_message += f"✅ You are a VIP. Subscription ends: <b>{h(vip_until_date)}</b>."
    else:
        welcome_message += "⚠️ You are a standard user. Activate a key for VIP access."
        
    await message.answer(welcome_message, reply_markup=keyboard)

@dp.message(F.text == "القائمة الرئيسية (مستخدم)")
async def go_to_user_menu(message: types.Message):
    if is_banned(message.from_user.id): return
    is_vip = is_user_vip(message.from_user.id)
    await message.answer("User Main Menu.", reply_markup=get_user_keyboard(is_vip))

@dp.message(F.text == "لوحة تحكم الأدمن")
async def admin_panel_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("Admin Control Panel.", reply_markup=get_admin_keyboard())
    
@dp.message(Command("admin"))
async def admin_command_handler(message: types.Message):
    await admin_panel_handler(message)

@dp.message(F.text == "الاشتراك وتفعيل المفتاح")
async def start_key_activation(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id): return
    is_vip = is_user_vip(message.from_user.id)
    if is_vip:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT vip_until FROM users WHERE user_id = %s", (message.from_user.id,))
            vip_until_ts = cursor.fetchone()[0]
            conn.close()
        vip_until_date = datetime.fromtimestamp(vip_until_ts).strftime("%Y-%m-%d %H:%M")
        await message.answer(f"✅ VIP until: <b>{h(vip_until_date)}</b>.\nSend new key to extend.", reply_markup=get_cancel_keyboard())
    else:
        await message.answer("📝 Send your key now:", reply_markup=get_cancel_keyboard())
    await state.set_state(UserStates.waiting_key_activation)

@dp.message(UserStates.waiting_key_activation)
async def process_key_activation(message: types.Message, state: FSMContext):
    await state.clear()
    key = message.text.strip()
    if key == "إلغاء الأمر":
        await go_to_user_menu(message)
        return

    success, days, new_vip_until_date = activate_key(message.from_user.id, key)
    if success:
        await message.answer(f"🎉 Key activated for <b>{days}</b> days. New expiry: <b>{new_vip_until_date.strftime('%Y-%m-%d %H:%M')}</b>.", reply_markup=get_user_keyboard(True))
    else:
        await message.answer("❌ Key activation failed. Check the key.", reply_markup=get_user_keyboard(is_user_vip(message.from_user.id)))

@dp.message(F.text == "تقرير الأداء الأسبوعي")
async def send_performance_report(message: types.Message):
    if is_banned(message.from_user.id): return
    closed_trades, _ = get_performance_data(days=7)
    report_message = format_performance_report(closed_trades)
    await message.answer(report_message, reply_markup=get_user_keyboard(is_user_vip(message.from_user.id)))

@dp.message(F.text == "إرسال صفقة يدوية")
async def start_manual_trade(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("📝 Send trade details (ACTION/ENTRY/TP/SL/TYPE): <code>BUY/1900.5/1910.2/1895.8/SCALPING</code>", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_trade)

@dp.message(AdminStates.waiting_trade)
async def process_manual_trade(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    if message.text == "إلغاء الأمر":
        await admin_panel_handler(message)
        return
    try:
        parts = message.text.strip().split('/')
        if len(parts) != 5: raise ValueError("Invalid number of parts.")
        action = parts[0].upper()
        entry = float(parts[1])
        tp = float(parts[2])
        sl = float(parts[3])
        trade_type = parts[4].upper()
        if action not in ('BUY', 'SELL') or trade_type not in ('SCALPING', 'LONGTERM'): raise ValueError("Invalid ACTION or TYPE.")
            
        vip_users = get_all_users_ids(vip_only=True)
        vip_ids = [user_id for user_id, is_banned in vip_users if is_banned == 0]
        user_count = len(vip_ids)
        
        message_text = (f"⚡️ <b>Manual Trade</b> ⚡️\n\n▪️ <b>Symbol:</b> <code>{ADMIN_TRADE_SYMBOL}</code>\n▪️ <b>Action:</b> <b>{action}</b>\n▪️ <b>Entry:</b> <code>{entry:.2f}</code>\n▪️ <b>TP:</b> <code>{tp:.2f}</code>\n▪️ <b>SL:</b> <code>{sl:.2f}</code>\n▪️ <b>Type:</b> {trade_type}")
        success_count, fail_count = await send_broadcast(message_text, target="vip_only")
        
        trade_id = str(uuid.uuid4())
        add_trade(trade_id, action, entry, tp, sl, success_count, trade_type)

        await message.answer(f"✅ Trade sent successfully to <b>{success_count}</b> VIP users.", reply_markup=get_admin_keyboard())
    except Exception as e:
        await message.answer(f"❌ Manual trade error: {e}", reply_markup=get_admin_keyboard())

@dp.message(F.text == "إدارة المستخدمين")
async def manage_users_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    total_users = get_total_users()
    menu = (f"👤 <b>User Management</b>\nTotal Users: <b>{total_users}</b>\n\nChoose action:")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ban User", callback_data="admin_ban_user"), InlineKeyboardButton(text="Unban User", callback_data="admin_unban_user")],
    ])
    await message.answer(menu, reply_markup=keyboard)

@dp.callback_query(F.data == "admin_ban_user")
async def start_ban_user(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🚫 Send User ID to ban:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin_cancel")]]))
    await state.set_state(AdminStates.waiting_ban)
    await call.answer()

@dp.message(AdminStates.waiting_ban)
async def process_ban_user(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    try:
        user_id_to_ban = int(message.text.strip())
        update_ban_status(user_id_to_ban, 1)
        await message.answer(f"✅ User ID: <b>{user_id_to_ban}</b> banned.", reply_markup=get_admin_keyboard())
    except ValueError:
        await message.answer("❌ Invalid ID.", reply_markup=get_admin_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ban error: {e}", reply_markup=get_admin_keyboard())

@dp.callback_query(F.data == "admin_unban_user")
async def start_unban_user(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("✅ Send User ID to unban:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin_cancel")]]))
    await state.set_state(AdminStates.waiting_unban)
    await call.answer()

@dp.message(AdminStates.waiting_unban)
async def process_unban_user(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    try:
        user_id_to_unban = int(message.text.strip())
        update_ban_status(user_id_to_unban, 0)
        await message.answer(f"✅ User ID: <b>{user_id_to_unban}</b> unbanned.", reply_markup=get_admin_keyboard())
    except ValueError:
        await message.answer("❌ Invalid ID.", reply_markup=get_admin_keyboard())
    except Exception as e:
        await message.answer(f"❌ Unban error: {e}", reply_markup=get_admin_keyboard())

@dp.message(F.text == "توليد مفتاح")
async def start_key_generation(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("🔢 Send number of days for the key:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_key_days)

@dp.message(AdminStates.waiting_key_days)
async def generate_key(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    if message.text == "إلغاء الأمر":
        await admin_panel_handler(message)
        return
    try:
        days = int(message.text.strip())
        if days <= 0: raise ValueError("Days must be positive.")
        new_key = str(uuid.uuid4())
        conn = get_db_connection()
        if conn is None: raise Exception("DB connection failed.")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO invite_keys (key, days, created_by) VALUES (%s, %s, %s)", (new_key, days, message.from_user.id))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Key generated for <b>{days}</b> days:\nKey: <code>{new_key}</code>", reply_markup=get_admin_keyboard())
    except ValueError:
        await message.answer("❌ Invalid number of days.", reply_markup=get_admin_keyboard())
    except Exception as e:
        await message.answer(f"❌ Key generation error: {e}", reply_markup=get_admin_keyboard())

@dp.message(F.text == "بث رسالة")
async def start_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="All Users", callback_data="broadcast_target_all")],
        [InlineKeyboardButton(text="VIP Only", callback_data="broadcast_target_vip")],
        [InlineKeyboardButton(text="Cancel", callback_data="admin_cancel")]
    ])
    await message.answer("📣 Select broadcast target:", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_broadcast_target)

@dp.callback_query(AdminStates.waiting_broadcast_target)
async def select_broadcast_target(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    if call.data == "broadcast_target_all":
        target, target_name = "all_users", "All Users"
    elif call.data == "broadcast_target_vip":
        target, target_name = "vip_only", "VIP Users Only"
    else:
        await call.message.edit_text("Canceled.", reply_markup=get_admin_keyboard())
        await state.clear()
        await call.answer()
        return

    await state.update_data(broadcast_target=target)
    await call.message.edit_text(f"📝 Target: <b>{target_name}</b>. Send the message text (HTML supported):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="admin_cancel")]]))
    await state.set_state(AdminStates.waiting_broadcast)
    await call.answer()

@dp.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    target = data.get("broadcast_target", "all_users")
    await state.clear()
    if message.text == "إلغاء الأمر":
        await admin_panel_handler(message)
        return

    message_text = message.html_text
    await message.answer("... Sending ...", reply_markup=get_admin_keyboard())
    success_count, fail_count = await send_broadcast(message_text, target)
    await message.answer(f"✅ Broadcast complete.\nSuccess: <b>{success_count}</b>\nFailed: <b>{fail_count}</b>", reply_markup=get_admin_keyboard())

@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()
    await call.message.edit_text("Canceled.", reply_markup=get_admin_keyboard())
    await call.answer()

# ================================== Scheduling Tasks ==================================

async def check_for_new_trade(required_confidence=CONFIDENCE_THRESHOLD_98, min_filters=MIN_FILTERS_FOR_98, send_trade=True):
    trade_info = await analyze_trade_conditions(TRADE_SYMBOL, '5m', min_filters, required_confidence)
    
    if trade_info and trade_info['status'] == 'SUCCESS':
        message_text = (f"⚡️ <b>New VIP Signal</b> ⚡️\n\n▪️ <b>Symbol:</b> <code>{trade_info['symbol']}</code>\n▪️ <b>Action:</b> <b>{trade_info['action']}</b>\n▪️ <b>Entry:</b> <code>{trade_info['entry']:.2f}</code>\n▪️ <b>TP:</b> <code>{trade_info['tp']:.2f}</code>\n▪️ <b>SL:</b> <code>{trade_info['sl']:.2f}</code>\n▪️ <b>Confidence:</b> <b>{trade_info['confidence']:.2%}</b>\n▪️ <b>Type:</b> {trade_info['trade_type']}")
        
        if send_trade:
            vip_users = get_all_users_ids(vip_only=True)
            vip_ids = [user_id for user_id, is_banned in vip_users if is_banned == 0]
            
            if vip_ids:
                success_count, fail_count = await send_broadcast(message_text, target="vip_only")
                trade_id = str(uuid.uuid4())
                add_trade(trade_id, trade_info['action'], trade_info['entry'], trade_info['tp'], trade_info['sl'], success_count, trade_info['trade_type'])
                await bot.send_message(ADMIN_ID, f"✅ Auto VIP trade sent:\n{message_text}")
            else:
                await bot.send_message(ADMIN_ID, f"⚠️ No active VIP users for auto trade.")
        
        return trade_info
    return None

async def check_admin_trade_conditions():
    trade_info = await analyze_trade_conditions(ADMIN_TRADE_SYMBOL, '5m', MIN_FILTERS_FOR_90, CONFIDENCE_THRESHOLD_90, is_admin_check=True)
    
    if trade_info and trade_info['status'] == 'SUCCESS' and trade_info['confidence'] < CONFIDENCE_THRESHOLD_98:
        message_text = (f"💡 <b>Admin Trade Alert ({trade_info['confidence']:.2%})</b> 💡\n\n▪️ <b>Symbol:</b> <code>{trade_info['symbol']}</code>\n▪️ <b>Action:</b> <b>{trade_info['action']}</b>\n▪️ <b>Entry:</b> <code>{trade_info['entry']:.2f}</code>\n▪️ <b>TP:</b> <code>{trade_info['tp']:.2f}</code>\n▪️ <b>SL:</b> <code>{trade_info['sl']:.2f}</code>\n▪️ <b>Type:</b> {trade_info['trade_type']}\n\n<i>* You can send this manually.</i>")
        await bot.send_message(ADMIN_ID, message_text)
    
    return trade_info
    
async def check_active_trades():
    active_trades = get_active_trades()
    if not active_trades: return

    try:
        ticker = await EXCHANGE.fetch_ticker(TRADE_SYMBOL)
        current_price = ticker['last']
    except Exception as e:
        print(f"❌ Failed to fetch ticker price for {TRADE_SYMBOL}: {e}")
        return

    for trade_id, action, entry, tp, sl, sent_at, trade_type in active_trades:
        exit_status = 'NONE'
        if (action == 'BUY' and current_price <= sl) or (action == 'SELL' and current_price >= sl):
            exit_status = 'SL'
        elif (action == 'BUY' and current_price >= tp) or (action == 'SELL' and current_price <= tp):
            exit_status = 'TP'
        
        if exit_status != 'NONE':
            update_trade_status(trade_id, 'CLOSED', exit_status, current_price)
            current_capital = get_admin_capital()
            risk_usd = current_capital * ADMIN_RISK_PER_TRADE
            
            if exit_status == 'SL':
                new_capital = current_capital - risk_usd
                pnl_msg = f"Loss: <b>{risk_usd:.2f}$</b> (-{ADMIN_RISK_PER_TRADE*100:.2f}%)"
            else:
                profit_usd = risk_usd * (SCALPING_RR_FACTOR if trade_type == 'SCALPING' else LONGTERM_RR_FACTOR)
                new_capital = current_capital + profit_usd
                pnl_msg = f"Profit: <b>{profit_usd:.2f}$</b>"
            
            update_admin_capital(new_capital, action, TRADE_SYMBOL, lots_used=risk_usd)

            close_message = (f"🛑 <b>Trade Closed</b> 🛑\n\n▪️ <b>Symbol:</b> <code>{TRADE_SYMBOL}</code>\n▪️ <b>Action:</b> <b>{action}</b>\n▪️ <b>Exit Status:</b> <b>{exit_status}</b>\n▪️ <b>Close Price:</b> <code>{current_price:.2f}</code>\n\n<i>1R Simulation: {pnl_msg}</i>")
            
            await send_broadcast(close_message, target="vip_only")
            await bot.send_message(ADMIN_ID, f"✅ Trade closed {exit_status} automatically:\n{close_message}\n\nNew Admin Capital: <b>{new_capital:.2f}$</b>")


def daily_inventory_check():
    conn = get_db_connection()
    if conn is None: return
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE vip_until > 0.0 AND vip_until <= %s", (time.time(),))
    expired_users = cursor.fetchall()

    for user_id_tuple in expired_users:
        user_id = user_id_tuple[0]
        try:
            asyncio.run(bot.send_message(user_id, "⚠️ Your VIP subscription has expired. Please renew.", reply_markup=get_user_keyboard(False)))
            asyncio.run(asyncio.sleep(0.05))
        except Exception as e:
            if 'bot was blocked by the user' not in str(e): print(f"⚠️ Failed to send expiry notice to {user_id}: {e}")
                 
    asyncio.run(bot.send_message(ADMIN_ID, f"✅ Daily inventory check complete. Sent {len(expired_users)} expiry notices."))
    conn.close()

# ================================== Middleware & Main ==================================
class BanCheckMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Callable[[types.Message, Dict[str, Any]], Awaitable[Any]], event: types.Message, data: Dict[str, Any]
    ) -> Any:
        if event.from_user.id == ADMIN_ID: return await handler(event, data)
        if is_banned(event.from_user.id):
            await event.answer("❌ You are banned.")
            return
        return await handler(event, data)

dp.message.middleware(BanCheckMiddleware())

async def run_scheduler():
    schedule.every(TRADE_CHECK_INTERVAL).seconds.do(lambda: asyncio.create_task(check_active_trades()))
    schedule.every(TRADE_ANALYSIS_INTERVAL_98).seconds.do(lambda: asyncio.create_task(check_for_new_trade()))
    schedule.every(TRADE_ANALYSIS_INTERVAL_90).seconds.do(lambda: asyncio.create_task(check_admin_trade_conditions()))
    schedule.every().day.at("01:00").do(daily_inventory_check)

    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

async def main() -> None:
    init_db()
    scheduler_task = asyncio.create_task(run_scheduler())
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot stopped manually.")
    except Exception as e:
        print(f"❌ Unexpected main error: {e}")
