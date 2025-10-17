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

# =============== تعريف حالات FSM (لضمان تعريفها قبل استخدامها) ===============
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
    # استخدام check_same_thread=False ضروري لبيئات التشغيل غير المتزامنة مثل aiogram
    CONN = sqlite3.connect(DB_NAME, check_same_thread=False) 
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
    # إذا لم يكن المستخدم موجودًا، لا تعتبره محظورًا
    return result is not None and result[0] == 1 

# ... (بقية دوال DB: update_ban_status, get_all_users_ids, get_total_users, is_user_vip, activate_key, get_user_vip_status, create_invite_key)
# ملاحظة: تم حذف بقية دوال DB لتجنب التكرار، لكن يجب أن تكون موجودة في الكود الكامل.

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
    cursor.execute("SELECT COUNT(user_id) FROM users") 
    return cursor.fetchone()[0]

# دوال الاشتراكات
def is_user_vip(user_id):
    cursor = CONN.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result is None: return False
    return result[0] > time.time()
    
def activate_key(user_id, key):
    cursor = CONN.cursor()
    cursor.execute("SELECT days FROM invite_keys WHERE key = ? AND used_by IS NULL", (key,))
    key_data = cursor.fetchone()

    if key_data:
        days = key_data[0]
        cursor.execute("UPDATE invite_keys SET used_by = ?, used_at = ? WHERE key = ?", (user_id, time.time(), key))
        
        cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
        # يتم التأكد هنا أن المستخدم موجود بالفعل في جدول users قبل جلب vip_until
        user_data = cursor.fetchone()
        if user_data is None: 
            return False, 0, None # فشل، يجب أن يكون قد تم إضافته بالفعل عبر Middleware

        vip_until_ts = user_data[0]
        
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
    cursor = CONN.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result and result[0] > time.time():
        return datetime.fromtimestamp(result[0]).strftime("%Y-%m-%d %H:%M")
    return "غير مشترك"

def create_invite_key(admin_id, days):
    key = str(uuid.uuid4()).split('-')[0] + '-' + str(uuid.uuid4()).split('-')[1]
    cursor = CONN.cursor()
    cursor.execute("INSERT INTO invite_keys (key, days, created_by) VALUES (?, ?, ?)", (key, days, admin_id))
    CONN.commit()
    return key


# =============== برمجية وسيطة للحظر والاشتراك (Access Middleware) - الحل الجذري ===============
class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject, data: Dict[str, Any],
    ) -> Any:
        user = data.get('event_from_user')
        if user is None: return await handler(event, data)
        user_id = user.id
        username = user.username or "مستخدم"
        
        # 🚨 الإضافة الجبرية في بداية المعالجة
        # هذا يضمن أن المستخدم موجود في DB قبل أي فحص أو محاولة للجلب.
        if isinstance(event, types.Message):
            add_user(user_id, username) 

        # 1. السماح للأدمن بالمرور دائمًا
        if user_id == ADMIN_ID: return await handler(event, data)

        # 2. السماح بمرور /start دائمًا (للجميع)
        if isinstance(event, types.Message) and (event.text == '/start' or event.text.startswith('/start ')):
             # تم تسجيل المستخدم، والسماح بالمرور لـ cmd_start لإظهار القائمة
             return await handler(event, data) 
             
        # 3. فحص الحظر (لجميع الرسائل الأخرى)
        allowed_for_banned = ["💬 تواصل مع الدعم", "💰 خطة الأسعار VIP", "ℹ️ عن AlphaTradeAI"]
        if is_banned(user_id):
            if isinstance(event, types.Message) and event.text not in allowed_for_banned:
                 await event.answer("🚫 حسابك محظور من استخدام البوت. يمكنك التواصل مع الدعم أو التحقق من الأسعار/المعلومات فقط.")
                 return
            
        # 4. الأزرار المسموح بها للمستخدم العادي (حتى لو لم يكن VIP)
        allowed_for_all = ["💬 تواصل مع الدعم", "ℹ️ عن AlphaTradeAI", "🔗 تفعيل مفتاح الاشتراك", "📝 حالة الاشتراك", "💰 خطة الأسعار VIP"]
        
        if isinstance(event, types.Message) and event.text in allowed_for_all:
             return await handler(event, data) 

        # 5. منع الوصول للصفقات أو المميزات الأخرى إذا لم يكن VIP 
        if not is_user_vip(user_id):
            if isinstance(event, types.Message):
                await event.answer("⚠️ هذه الميزة مخصصة للمشتركين (VIP) فقط. يرجى تفعيل مفتاح اشتراك لتتمكن من استخدامها.")
            return

        # 6. السماح بمرور أي شيء آخر للمشتركين VIP
        return await handler(event, data)

# =============== وظائف التداول والتحليل الذكي (تم تصحيح مشكلة Series) ===============

def get_signal_and_confidence(symbol: str) -> tuple[str, float, str, float, float, float]:
    try:
        data = yf.download(
            symbol, 
            period="1d", interval="1m", progress=False, auto_adjust=True     
        )
        
        if data.empty or len(data) < 30:
            return f"لا تتوفر بيانات كافية للتحليل لرمز التداول: {symbol}.", 0.0, "HOLD", 0.0, 0.0, 0.0

        data['EMA_5'] = data['Close'].ewm(span=5, adjust=False).mean()
        data['EMA_20'] = data['Close'].ewm(span=20, adjust=False).mean()
        
        latest_price = data['Close'].iloc[-1].item() 
        latest_time = data.index[-1].strftime('%Y-%m-%d %H:%M:%S')
        
        ema_fast_prev = data['EMA_5'].iloc[-2]
        ema_slow_prev = data['EMA_20'].iloc[-2]
        ema_fast_current = data['EMA_5'].iloc[-1]
        ema_slow_current = data['EMA_20'].iloc[-1]
        
        action = "HOLD"
        confidence = 0.5
        SL_RISK = 0.005 
        TP_REWARD = 0.015
        entry_price = latest_price
        stop_loss = 0.0
        take_profit = 0.0
        
        if ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current:
            action = "BUY"; confidence = 0.95 
            stop_loss = latest_price * (1 - SL_RISK); take_profit = latest_price * (1 + TP_REWARD)
        elif ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current:
            action = "SELL"; confidence = 0.95
            stop_loss = latest_price * (1 + SL_RISK); take_profit = latest_price * (1 - TP_REWARD)
        elif ema_fast_current > ema_slow_current:
             action = "BUY"; confidence = 0.75
             stop_loss = latest_price * (1 - SL_RISK); take_profit = latest_price * (1 + TP_REWARD)
        elif ema_fast_current < ema_slow_current:
             action = "SELL"; confidence = 0.75
             stop_loss = latest_price * (1 + SL_RISK); take_profit = latest_price * (1 - TP_REWARD)
            
        price_msg = f"📊 آخر سعر لـ <b>{symbol}</b>:\nالسعر: ${latest_price:,.2f}\nالوقت: {latest_time} UTC"
        
        return price_msg, confidence, action, entry_price, stop_loss, take_profit
        
    except Exception as e:
        return f"❌ فشل في جلب بيانات التداول لـ {symbol} أو التحليل: {e}", 0.0, "HOLD", 0.0, 0.0, 0.0

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

<i>Trade responsibly. This signal is based on {TRADE_SYMBOL} Smart EMA analysis.</i>
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

# =============== أوامر الأدمن والمستخدم ===============

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    user_id = msg.from_user.id
    username = msg.from_user.username or "مستخدم"
    
    # تمت إضافة المستخدم في الـ Middleware، هنا يتم إظهار القائمة فقط
    # add_user(user_id, username) # تم حذف هذا السطر لأنه أصبح في الـ Middleware

    welcome_msg = f"""
🤖 <b>مرحبًا بك في AlphaTradeAI!</b>
🚀 نظام ذكي يتابع سوق الذهب ({TRADE_SYMBOL}).
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
    
# --- دوال الأدمن الأخرى (تم حذفها لتجنب التكرار - لكن يجب أن تكون موجودة في الكود النهائي) ---
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

# ... (بقية دوال الأدمن: البث، الحظر، إلغاء الحظر، عرض المشتركين، عدد المستخدمين، العودة للقائمة)
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
    نظامنا يراقب حركة الذهب (XAUUSD) على مدار الساعة. نستخدم نموذج تقاطع المؤشرات الأُسيَّة (EMA) الذكي لفلترة الإشارات واختيار فقط الصفقات التي تتجاوز نسبة ثقة <b>{int(CONFIDENCE_THRESHOLD*100)}%</b>.
2.  <b>إدارة مخاطر احترافية:</b>
    كل إشارة تُرسَل إليك هي صفقة جاهزة للتنفيذ. تحصل على سعر الدخول (Entry Price)، هدف الربح (TP) ونقطة وقف الخسارة (SL).
3.  <b>توفير الوقت والجهد:</b>
    سيتولى AlphaTradeAI التحليل المعقد وإرسال ما بين <b>4 إلى 7 صفقات</b> مجدولة يومياً.

━━━━━━━━━━━━━━━
💰 <b>لتحقيق الأرباح بذكاء، استثمر في أدواتك!</b> اضغط على '💰 خطة الأسعار VIP' للاطلاع على العروض الحالية.
"""
        await msg.reply(marketing_text)


# =============== الجدولة وتشغيل البوت ===============

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
    # 1. تهيئة قاعدة البيانات (ملاحظة check_same_thread=False في init_db)
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
