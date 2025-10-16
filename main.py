# main.py - AlphaTradeAI single-file bot (aiogram v3)
import os
import asyncio
import sqlite3
import time
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# -------------------- CONFIG --------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable TELEGRAM_BOT_TOKEN is required")

ADMIN_ID = int(os.environ.get("ADMIN_ID", "7378889303"))
DB_PATH = "bot_data.db"

# -------------------- LOGGING --------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AlphaTradeAI")

# -------------------- BOT & DISPATCHER --------------------
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# -------------------- DATABASE HELPERS --------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT UNIQUE,
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
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO keys (key_code, duration_days, used_by, created_at, expiry) VALUES (?, ?, NULL, ?, NULL)",
                (key_code, duration_days, now))
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
    expiry_ts = int(time.time()) + duration * 24 * 3600
    cur.execute("UPDATE keys SET used_by=?, expiry=? WHERE key_code=?", (telegram_id, expiry_ts, key_code))
    cur.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (telegram_id,))
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
    cur.execute("SELECT telegram_id FROM users WHERE banned=0")
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

# -------------------- FSM STATES --------------------
class AdminStates(StatesGroup):
    waiting_create_key = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_broadcast_all = State()
    waiting_broadcast_subs = State()
    waiting_trade_manual = State()

# -------------------- KEYBOARDS --------------------
def main_menu_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 جدول اليوم"), KeyboardButton(text="📈 آخر الصفقات")],
            [KeyboardButton(text="🎟️ تفعيل مفتاح الاشتراك")],
            [KeyboardButton(text="ℹ️ معلومات عن البوت"), KeyboardButton(text="💬 الدعم الفني")]
        ],
        resize_keyboard=True
    )
    return kb

def admin_menu_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔑 إنشاء مفتاح جديد"), KeyboardButton(text="🧾 عرض المفاتيح")],
            [KeyboardButton(text="⛔ حظر مستخدم"), KeyboardButton(text="✅ إلغاء حظر مستخدم")],
            [KeyboardButton(text="📢 رسالة لكل المستخدمين"), KeyboardButton(text="📣 رسالة للمشتركين فقط")],
            [KeyboardButton(text="💹 إرسال صفقة يدوياً"), KeyboardButton(text="↩️ رجوع للمستخدم")]
        ],
        resize_keyboard=True
    )
    return kb

# -------------------- FORMATTING HELPERS --------------------
def fmt_ts(ts):
    try:
        return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S UTC")
    except:
        return "غير محدد"

def format_trade_message(pair, side, entry, tp, sl, trust, ts=None):
    ts_text = fmt_ts(ts) if ts else fmt_ts(time.time())
    arrow = "📈" if side.strip().lower() == "buy" else "📉"
    msg = (
        "💹 <b>صفقة جديدة</b>\n\n"
        f"💱 <b>الزوج:</b> {pair}\n"
        f"{arrow} <b>النوع:</b> {side.title()}\n"
        f"💰 <b>الدخول (Enter):</b> {entry}\n"
        f"🎯 <b>Take Profit:</b> {tp}\n"
        f"🛑 <b>Stop Loss:</b> {sl}\n"
        f"🔥 <b>نسبة الثقة:</b> {trust}%\n"
        f"⏰ <b>الوقت:</b> {ts_text}"
    )
    return msg

# -------------------- START & ADMIN COMMANDS --------------------
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    add_or_update_user = add_or_update_user if 'add_or_update_user' in globals() else None
    # Ensure user exists in DB
    add_or_update_user_func = add_or_update_user
    # record user
    add_or_update_user(msg.from_user.id, getattr(msg.from_user, 'username', None))
    welcome = (
        f"👋 أهلاً {msg.from_user.first_name}!\n\n"
        f"📍 مرحبًا بك في <b>AlphaTradeAI</b> — مساعدك الذكي لإشارات التداول.\n\n"
        f"🎯 توصيات دقيقة ومنسقة. لإتمام الاشتراك: أرسل مفتاح التفعيل 🔑 أو استخدم الأزرار بالأسفل."
    )
    # if admin show admin menu on start as well
    if msg.from_user.id == ADMIN_ID:
        await msg.answer(welcome, reply_markup=admin_menu_kb())
    else:
        await msg.answer(welcome, reply_markup=main_menu_kb())

@dp.message(Command("admin"))
async def cmd_admin(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    await msg.reply("⚙️ لوحة تحكم الأدمن — اختر الأمر:", reply_markup=admin_menu_kb())

# -------------------- MESSAGE HANDLER (buttons + text) --------------------
@dp.message()
async def handler_all(msg: Message, state: FSMContext):
    text = (msg.text or "").strip()
    user_id = msg.from_user.id

    # ---------------- Admin flow ----------------
    if user_id == ADMIN_ID:
        current_state = await state.get_state()

        # States handling
        if current_state == AdminStates.waiting_create_key.state:
            parts = text.split()
            if len(parts) >= 2:
                key_code = parts[0].strip()
                try:
                    days = int(parts[1])
                    create_key_record(key_code, days)
                    await msg.reply(f"✅ تم إنشاء المفتاح:\n🔑 <code>{key_code}</code>\n⏳ لمدة {days} يوم.", parse_mode="HTML")
                except ValueError:
                    await msg.reply("❌ عدد الأيام يجب أن يكون رقمًا صحيحًا.")
            else:
                await msg.reply("❌ استخدم: <code>KEYCODE 7</code> (الكود ثم عدد الأيام).", parse_mode="HTML")
            await state.clear()
            return

        if current_state == AdminStates.waiting_ban.state:
            try:
                tid = int(text)
                ban_user_db(tid)
                await msg.reply(f"⛔ تم حظر المستخدم {tid}")
            except:
                await msg.reply("❌ أدخل telegram_id صحيح (أرقام فقط).")
            await state.clear()
            return

        if current_state == AdminStates.waiting_unban.state:
            try:
                tid = int(text)
                unban_user_db(tid)
                await msg.reply(f"✅ تم إلغاء الحظر عن المستخدم {tid}")
            except:
                await msg.reply("❌ أدخل telegram_id صحيح (أرقام فقط).")
            await state.clear()
            return

        if current_state == AdminStates.waiting_broadcast_all.state:
            txt = text
            users = get_all_users()
            sent = 0
            for u in users:
                try:
                    await bot.send_message(u, txt)
                    sent += 1
                except Exception:
                    continue
            await msg.reply(f"📢 تم إرسال الرسالة إلى {sent} مستخدم (جميع المستخدمين).")
            await state.clear()
            return

        if current_state == AdminStates.waiting_broadcast_subs.state:
            txt = text
            users = get_active_users()
            sent = 0
            for u in users:
                try:
                    await bot.send_message(u, txt)
                    sent += 1
                except Exception:
                    continue
            await msg.reply(f"✅ تم إرسال الرسالة إلى {sent} مشترك.")
            await state.clear()
            return

        if current_state == AdminStates.waiting_trade_manual.state:
            # expect: pair, side, entry, tp, sl, trust (comma separated)
            parts = [p.strip() for p in text.split(",")]
            if len(parts) != 6:
                await msg.reply("⚠️ الصيغة غير صحيحة.\nاكتب: زوج, نوع, الدخول, TP, SL, نسبة_الثقة\nمثال:\nEUR/USD, Buy, 1.0975, 1.0940, 1.0910, 92")
                return
            pair, side, entry, tp, sl, trust = parts
            trade_msg = format_trade_message(pair, side, entry, tp, sl, trust, int(time.time()))
            sent = 0
            for u in get_active_users():
                try:
                    await bot.send_message(u, trade_msg)
                    sent += 1
                except Exception:
                    continue
            await msg.reply(f"💹 تم إرسال الصفقة إلى {sent} مشترك.")
            await state.clear()
            return

        # Admin button commands (no state)
        if text == "🔑 إنشاء مفتاح جديد":
            await msg.reply("🔑 أرسل الكود وعدد الأيام (مثال: MYKEY 7):")
            await state.set_state(AdminStates.waiting_create_key)
            return

        if text == "🧾 عرض المفاتيح":
            rows = list_keys_records()
            if not rows:
                await msg.reply("⚠️ لا توجد مفاتيح حالياً.")
                return
            out = "🧾 <b>قائمة المفاتيح:</b>\n\n"
            for r in rows:
                status = f"✅ مستخدم من {r['used_by']}" if r['used_by'] else "🟢 متاح"
                out += f"🔑 <code>{r['key_code']}</code> — {r['duration_days']} يوم — {status}\n"
            await msg.reply(out, parse_mode="HTML")
            return

        if text == "⛔ حظر مستخدم":
            await msg.reply("🚫 أرسل telegram_id للمستخدم لحظره (رقم فقط):")
            await state.set_state(AdminStates.waiting_ban)
            return

        if text == "✅ إلغاء حظر مستخدم":
            await msg.reply("✅ أرسل telegram_id للمستخدم لإلغاء الحظر (رقم فقط):")
            await state.set_state(AdminStates.waiting_unban)
            return

        if text == "📢 رسالة لكل المستخدمين":
            await msg.reply("📢 أرسل الرسالة التي تريد إرسالها لجميع المستخدمين:")
            await state.set_state(AdminStates.waiting_broadcast_all)
            return

        if text == "📣 رسالة للمشتركين فقط":
            await msg.reply("📣 أرسل الرسالة التي تريد إرسالها للمشتركين فقط:")
            await state.set_state(AdminStates.waiting_broadcast_subs)
            return

        if text == "💹 إرسال صفقة يدوياً":
            await msg.reply(
                "💹 أرسل الصفقة بهذا الشكل (مفصولة بفواصل):\n"
                "زوج, نوع (Buy/Sell), الدخول, TP, SL, نسبة_الثقة\n"
                "مثال:\nEUR/USD, Buy, 1.0975, 1.0940, 1.0910, 92"
            )
            await state.set_state(AdminStates.waiting_trade_manual)
            return

    # ---------------- Regular user flow ----------------
    # If user sends a short token-like text (no spaces, length>3) -> try activate
    if " " not in text and len(text) > 3:
        ok, info = activate_user_with_key(user_id, text)
        if ok:
            await msg.reply(f"✅ تم تفعيل اشتراكك حتى: {fmt_ts(info)}")
        else:
            if info == 'invalid':
                await msg.reply("❌ المفتاح غير صحيح.")
            elif info == 'used':
                await msg.reply("⚠️ المفتاح مستخدم بالفعل.")
            else:
                await msg.reply("❌ حدث خطأ أثناء التفعيل.")
        return

    # User keyboard commands
    if text == "📊 جدول اليوم":
        await msg.reply("📊 جدول صفقات اليوم سيتم تحديثه قريباً.")
    elif text == "📈 آخر الصفقات":
        await msg.reply("📈 لا توجد صفقات سابقة حالياً.")
    elif text == "🎟️ تفعيل مفتاح الاشتراك":
        await msg.reply("🔑 أرسل كود التفعيل هنا لتفعيل حسابك.")
    elif text == "ℹ️ معلومات عن البوت":
        await msg.reply("🤖 AlphaTradeAI: توصيات ذكية، إشعارات فورية، وإدارة مخاطر.")
    elif text == "💬 الدعم الفني" or text == "💬 الدعم الفني":
        await msg.reply("📞 للتواصل مع الدعم: @AlphaTradeAI_Support")
    else:
        await msg.reply("❓ أمر غير معروف أو غير مدعوم. استخدم /start للعودة للقائمة.")

# -------------------- RUN --------------------
async def main():
    init_db()
    logger.info("✅ Database initialized.")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
