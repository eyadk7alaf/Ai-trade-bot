# main.py — AlphaTradeAI (single-file, env-configured)
import os
import asyncio
import sqlite3
import time
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ========== إعدادات (قراءة من env) ==========
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable TELEGRAM_BOT_TOKEN is required")

# يمكنك وضع ADMIN_ID في env كرقم أو سيترك القيمة الافتراضية هنا
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7378889303"))

DB_PATH = "bot_data.db"

# ========== لوج ===========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== إعداد البوت و FSM ==========
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())

# ========== حالات الأدمن ==========
class AdminStates(StatesGroup):
    waiting_key = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_broadcast_all = State()
    waiting_broadcast_subs = State()
    waiting_trade_manual = State()

# ========== دوال قاعدة البيانات (مضمنة) ==========
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            subscribed_until INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            key_code TEXT PRIMARY KEY,
            duration_days INTEGER,
            used_by INTEGER,
            created_at INTEGER,
            expiry INTEGER
        )
    """)
    conn.commit()
    conn.close()

def add_or_update_user(telegram_id, username=None):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)", (telegram_id, username))
    cur.execute("UPDATE users SET username=? WHERE telegram_id=?", (username, telegram_id))
    conn.commit(); conn.close()

def create_key_record(key_code, duration_days):
    now = int(time.time())
    expiry = None
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO keys (key_code, duration_days, used_by, created_at, expiry) VALUES (?, ?, NULL, ?, ?)",
                (key_code, duration_days, now, expiry))
    conn.commit(); conn.close()

def list_keys_records():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT key_code, duration_days, used_by, created_at, expiry FROM keys ORDER BY created_at DESC")
    rows = cur.fetchall(); conn.close()
    return rows

def activate_user_with_key(telegram_id, key_code):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT key_code, duration_days, used_by FROM keys WHERE key_code=?", (key_code,))
    row = cur.fetchone()
    if not row:
        conn.close(); return False, 'invalid'
    if row["used_by"] is not None:
        conn.close(); return False, 'used'
    duration = int(row["duration_days"])
    expiry_ts = int(time.time()) + duration*24*3600
    cur.execute("UPDATE keys SET used_by=?, expiry=? WHERE key_code=?", (telegram_id, expiry_ts, key_code))
    cur.execute("UPDATE users SET subscribed_until=? WHERE telegram_id=?", (expiry_ts, telegram_id))
    conn.commit(); conn.close()
    return True, expiry_ts

def get_active_users():
    now = int(time.time())
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users WHERE subscribed_until > ? AND banned=0", (now,))
    rows = [r["telegram_id"] for r in cur.fetchall()]
    conn.close()
    return rows

def get_all_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users")
    rows = [r["telegram_id"] for r in cur.fetchall()]
    conn.close()
    return rows

def ban_user_db(telegram_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (telegram_id,))
    conn.commit(); conn.close()

def unban_user_db(telegram_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE users SET banned=0 WHERE telegram_id=?", (telegram_id,))
    conn.commit(); conn.close()

# ========== كيبوردات (مستخدم + أدمن) ==========
def main_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton("📊 جدول اليوم"), KeyboardButton("📈 آخر الصفقات")],
        [KeyboardButton("🎟️ تفعيل مفتاح الاشتراك")],
        [KeyboardButton("ℹ️ معلومات عن البوت")]
    ], resize_keyboard=True)
    return kb

def admin_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton("إنشاء مفتاح جديد 🔑"), KeyboardButton("عرض المفاتيح 🧾")],
        [KeyboardButton("حظر مستخدم ❌"), KeyboardButton("إلغاء حظر مستخدم ✅")],
        [KeyboardButton("رسالة لكل المستخدمين 📢"), KeyboardButton("رسالة للمشتركين ✅")],
        [KeyboardButton("إرسال صفقة يدوياً 💹"), KeyboardButton("رجوع ↩️")]
    ], resize_keyboard=True)
    return kb

# ========== تنسيقات مساعدة ==========
def fmt_ts(ts):
    try:
        return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S UTC")
    except:
        return "غير محدد"

def format_trade_message(pair, side, tp, entry, sl, trust):
    side_lower = side.strip().lower()
    arrow = "📈" if side_lower == "buy" else "📉"
    msg = (
        "💹 <b>صفقة جديدة</b>\n\n"
        f"💱 <b>الزوج:</b> {pair}\n"
        f"{arrow} <b>النوع:</b> {side.title()}\n"
        f"💰 <b>الدخول (Enter):</b> {entry}\n"
        f"🎯 <b>Take Profit:</b> {tp}\n"
        f"🛑 <b>Stop Loss:</b> {sl}\n"
        f"🔥 <b>نسبة الثقة:</b> {trust}%"
    )
    return msg

# ========== أوامر البداية والادمن ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    add_or_update_user(message.from_user.id, getattr(message.from_user, "username", None))
    welcome = (
        f"👋 أهلاً {message.from_user.first_name}!\n\n"
        f"📍 مرحبًا بك في <b>AlphaTradeAI</b> — ذكاء اصطناعي لتحليل السوق وإرسال صفقات دقيقة.\n\n"
        f"🎯 استخدم الأزرار بالأسفل للتنقل.\n"
    )
    await message.answer(welcome, reply_markup=main_menu_kb())

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    await message.answer("🛠️ لوحة تحكم الأدمن:", reply_markup=admin_menu_kb())

# ========== التعامل مع الأزرار والنصوص (شاملة حالات الأدمن) ==========
@dp.message()
async def handler_all(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id

    # ---------- إذا الأدمن ----------
    if user_id == ADMIN_ID:
        current = await state.get_state()

        # حالات الأدمن: انتظار إنشاء مفتاح
        if current == AdminStates.waiting_key.state:
            # نتوقع: <KEYCODE> <DAYS>
            parts = text.split()
            if len(parts) >= 2:
                key_code = parts[0]
                try:
                    days = int(parts[1])
                    create_key_record(key_code, days)
                    await message.reply(f"✅ تم إنشاء المفتاح:\n🔑 <code>{key_code}</code> لمدة {days} يوم", parse_mode="HTML")
                except ValueError:
                    await message.reply("❌ خطأ في عدد الأيام — أرسل رقم صحيح.")
            else:
                await message.reply("❌ صيغة غير صحيحة. الصيغة: <الكود> <عدد_الأيام>\nمثال: MYKEY123 7")
            await state.clear()
            return

        # انتظار حظر
        if current == AdminStates.waiting_ban.state:
            try:
                tid = int(text)
                ban_user_db(tid)
                await message.reply(f"✅ تم حظر المستخدم: {tid}")
            except:
                await message.reply("❌ أدخل رقم المستخدم (telegram id) صحيح.")
            await state.clear()
            return

        # انتظار فك حظر
        if current == AdminStates.waiting_unban.state:
            try:
                tid = int(text)
                unban_user_db(tid)
                await message.reply(f"✅ تم إلغاء الحظر عن المستخدم: {tid}")
            except:
                await message.reply("❌ أدخل رقم المستخدم (telegram id) صحيح.")
            await state.clear()
            return

        # انتظار بث لكل المستخدمين
        if current == AdminStates.waiting_broadcast_all.state:
            txt = text
            users = get_all_users()
            sent = 0
            for u in users:
                try:
                    await bot.send_message(u, txt)
                    sent += 1
                except Exception:
                    continue
            await message.reply(f"📢 تم إرسال الرسالة إلى {sent} مستخدم (جميع المستخدمين).")
            await state.clear()
            return

        # انتظار بث للمشتركين فقط
        if current == AdminStates.waiting_broadcast_subs.state:
            txt = text
            users = get_active_users()
            sent = 0
            for u in users:
                try:
                    await bot.send_message(u, txt)
                    sent += 1
                except Exception:
                    continue
            await message.reply(f"✅ تم إرسال الرسالة إلى {sent} مشترك.")
            await state.clear()
            return

        # انتظار إدخال صفقة يدوياً
        if current == AdminStates.waiting_trade_manual.state:
            # نتوقع: pair, side, entry, tp, sl, trust
            parts = [p.strip() for p in text.split(",")]
            if len(parts) != 6:
                await message.reply("⚠️ الصيغة غير صحيحة.\nاكتب: زوج, نوع, الدخول, TP, SL, نسبة_الثقة\nمثال:\nEUR/USD, Buy, 1.0975, 1.0940, 1.0910, 92")
                return
            pair, side, entry, tp, sl, trust = parts
            trade_msg = format_trade_message(pair, side, tp, entry, sl, trust)
            sent = 0
            for u in get_active_users():
                try:
                    await bot.send_message(u, trade_msg)
                    sent += 1
                except Exception:
                    continue
            await message.reply(f"💹 تم إرسال الصفقة إلى {sent} مشترك.")
            await state.clear()
            return

        # === إذا الأدمن ضغط زر معين ===
        if text == "إنشاء مفتاح جديد 🔑":
            await message.reply("🔑 أرسل الكود وعدد الأيام (مثال: MYKEY 7)")
            await state.set_state(AdminStates.waiting_key)
            return

        if text == "عرض المفاتيح 🧾":
            rows = list_keys_records()
            if not rows:
                await message.reply("❌ لا توجد مفاتيح حالياً.")
                return
            out = "🧾 <b>قائمة المفاتيح:</b>\n\n"
            for r in rows:
                key_code = r["key_code"]
                days = r["duration_days"]
                used_by = r["used_by"]
                status = f"✅ مستخدم من {used_by}" if used_by else "🟢 متاح"
                out += f"🔑 <code>{key_code}</code> — {days} يوم — {status}\n"
            await message.reply(out, parse_mode="HTML")
            return

        if text == "حظر مستخدم ❌":
            await message.reply("🚫 أرسل telegram_id للمستخدم الذي تريد حظره (رقم فقط).")
            await state.set_state(AdminStates.waiting_ban)
            return

        if text == "إلغاء حظر مستخدم ✅":
            await message.reply("✅ أرسل telegram_id للمستخدم لإلغاء الحظر (رقم فقط).")
            await state.set_state(AdminStates.waiting_unban)
            return

        if text == "رسالة لكل المستخدمين 📢":
            await message.reply("📢 أرسل الرسالة التي تريد إرسالها لجميع المستخدمين:")
            await state.set_state(AdminStates.waiting_broadcast_all)
            return

        if text == "رسالة للمشتركين ✅":
            await message.reply("✅ أرسل الرسالة التي تريد إرسالها للمشتركين فقط:")
            await state.set_state(AdminStates.waiting_broadcast_subs)
            return

        if text == "إرسال صفقة يدوياً 💹":
            await message.reply(
                "💹 أرسل الصفقة بهذا الشكل (مفصولة بفواصل):\n"
                "زوج, نوع (Buy/Sell), الدخول, TP, SL, نسبة_الثقة\n"
                "مثال:\nEUR/USD, Buy, 1.0975, 1.0940, 1.0910, 92"
            )
            await state.set_state(AdminStates.waiting_trade_manual)
            return

    # ---------- المستخدمون العاديون ----------
    # تفعيل مفتاح (نص بدون مسافات وأطول من 3 أحرف => مفتاح محتمل)
    if " " not in text and len(text) > 3:
        ok, info = activate_user_with_key(user_id, text)
        if ok:
            await message.reply(f"✅ تم تفعيل اشتراكك حتى: {fmt_ts(info)}")
        else:
            if info == 'invalid':
                await message.reply("❌ المفتاح غير صحيح.")
            elif info == 'used':
                await message.reply("⚠️ المفتاح مستخدم بالفعل.")
            else:
                await message.reply("❌ حدث خطأ أثناء التفعيل.")
        return

    # أزرار المستخدم
    if text == "📊 جدول اليوم":
        await message.reply("📊 جدول صفقات اليوم سيتم تحديثه قريباً.")
    elif text == "📈 آخر الصفقات":
        await message.reply("📈 لا توجد صفقات سابقة حالياً.")
    elif text == "🎟️ تفعيل مفتاح الاشتراك":
        await message.reply("🔑 أرسل كود التفعيل هنا لتفعيل حسابك.")
    elif text == "ℹ️ معلومات عن البوت":
        await message.reply("🤖 AlphaTradeAI يوفر إشارات يومية مدعومة بتحليل آلي متطور.")
    else:
        await message.reply("❓ أمر غير معروف أو لم يُنفذ بعد.")

# ========== تشغيل البوت ==========
async def main():
    init_db()
    logger.info("✅ قاعدة البيانات جاهزة.")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
