# main.py — AlphaTradeAI (aiogram v3)
# متطلبات: aiogram>=3.0.0
import os
import asyncio
import sqlite3
import time
import secrets
import html
import logging
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# -------------------- config --------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Environment variable TELEGRAM_BOT_TOKEN is required")

ADMIN_ID = int(os.getenv("ADMIN_ID", "7378889303"))

DB_PATH = "bot_data.db"

# -------------------- logging --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AlphaTradeAI")

# -------------------- bot & dispatcher --------------------
bot = Bot(token=TELEGRAM_BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())

# -------------------- database helpers --------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        subscribed_until INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0,
        created_at INTEGER
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
    logger.info("Database initialized")

def add_or_update_user(telegram_id: int, username: Optional[str], first_name: Optional[str]):
    conn = get_conn(); cur = conn.cursor()
    now = int(time.time())
    cur.execute("INSERT OR IGNORE INTO users (telegram_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                (telegram_id, username, first_name, now))
    cur.execute("UPDATE users SET username=?, first_name=? WHERE telegram_id=?", (username, first_name, telegram_id))
    conn.commit(); conn.close()

def create_key_record(key_code: str, duration_days: int):
    conn = get_conn(); cur = conn.cursor()
    now = int(time.time())
    cur.execute("INSERT OR REPLACE INTO keys (key_code, duration_days, used_by, created_at, expiry) VALUES (?, ?, NULL, ?, NULL)",
                (key_code, duration_days, now))
    conn.commit(); conn.close()
    logger.info("Created key %s for %d days", key_code, duration_days)

def list_keys_records():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT key_code, duration_days, used_by, created_at, expiry FROM keys ORDER BY created_at DESC")
    rows = cur.fetchall(); conn.close()
    return rows

def activate_user_with_key(telegram_id: int, key_code: str):
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
    cur.execute("INSERT OR IGNORE INTO users (telegram_id, created_at) VALUES (?, ?)", (telegram_id, int(time.time())))
    cur.execute("UPDATE users SET subscribed_until=? WHERE telegram_id=?", (expiry_ts, telegram_id))
    conn.commit(); conn.close()
    logger.info("Activated key %s for user %s until %s", key_code, telegram_id, expiry_ts)
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

def ban_user_db(telegram_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (telegram_id,))
    conn.commit(); conn.close()
    logger.info("Banned user %s", telegram_id)

def unban_user_db(telegram_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE users SET banned=0 WHERE telegram_id=?", (telegram_id,))
    conn.commit(); conn.close()
    logger.info("Unbanned user %s", telegram_id)

def count_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS total FROM users")
    total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) AS active FROM users WHERE subscribed_until > ?", (int(time.time()),))
    active = cur.fetchone()["active"]
    conn.close()
    return total, active

def cleanup_expired_subscriptions():
    now = int(time.time())
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE users SET subscribed_until=0 WHERE subscribed_until <= ?", (now,))
    conn.commit(); conn.close()

# -------------------- FSM states --------------------
class AdminStates(StatesGroup):
    waiting_create_key = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_broadcast_all = State()
    waiting_broadcast_subs = State()
    waiting_trade_manual = State()

# -------------------- keyboards --------------------
def main_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 جدول اليوم"), KeyboardButton(text="📈 آخر الصفقات")],
        [KeyboardButton(text="🎟️ تفعيل مفتاح الاشتراك"), KeyboardButton(text="💬 الدعم الفني")],
        [KeyboardButton(text="ℹ️ عن AlphaTradeAI")]
    ], resize_keyboard=True)
    return kb

def admin_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ إنشاء مفتاح جديد"), KeyboardButton(text="🔑 المفاتيح النشطة")],
        [KeyboardButton(text="🚫 حظر مستخدم"), KeyboardButton(text="✅ إلغاء الحظر")],
        [KeyboardButton(text="📢 رسالة لكل المستخدمين"), KeyboardButton(text="📣 رسالة للمشتركين")],
        [KeyboardButton(text="📊 إحصائيات المستخدمين"), KeyboardButton(text="💹 إرسال صفقة يدوياً")],
        [KeyboardButton(text="↩️ رجوع للمستخدم")]
    ], resize_keyboard=True)
    return kb

# -------------------- helpers --------------------
def fmt_ts(ts: Optional[int]):
    try:
        return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "غير محدد"

def escape(s: Optional[str]):
    if s is None:
        return ""
    return html.escape(str(s))

def generate_key_code():
    return "AT-" + secrets.token_hex(4).upper()

def format_trade_message(pair, side, entry, tp, sl, trust, ts=None):
    ts_text = fmt_ts(ts or int(time.time()))
    arrow = "📈" if str(side).strip().lower() == "buy" else "📉"
    return (
        "💠 <b>إشارة تداول جديدة من AlphaTradeAI</b>\n\n"
        f"💱 <b>الزوج:</b> {escape(pair)}\n"
        f"{arrow} <b>النوع:</b> {escape(side)}\n"
        f"💰 <b>الدخول (Enter):</b> {escape(entry)}\n"
        f"🎯 <b>Take Profit:</b> {escape(tp)}\n"
        f"🛑 <b>Stop Loss:</b> {escape(sl)}\n"
        f"🔥 <b>نسبة الثقة:</b> {escape(trust)}%\n"
        f"⏰ <b>الوقت:</b> {ts_text}"
    )

# -------------------- handlers --------------------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # تسجيل/تحديث المستخدم
    add_or_update_user(message.from_user.id, getattr(message.from_user, 'username', None), getattr(message.from_user, 'first_name', None))

    welcome = (
        f"👋 <b>مرحبًا بك يا {escape(message.from_user.first_name)}</b>!\n\n"
        "💼 <b>AlphaTradeAI</b> ليس مجرد بوت إشارات؛ إنه مساعد تداول مدعوم بالذكاء الاصطناعي.\n\n"
        "🔹 إشارات مُنسّقة ومدروسة.\n"
        "🔹 بيانات حقيقية وتحليلات لحظية.\n"
        "🔹 ادارة مخاطرة واضحة وتوصيات احترافية.\n\n"
        "🔐 لإتمام الاشتراك أرسل مفتاح التفعيل (مثال: <code>MYKEY123</code>) أو استخدم الأزرار بالأسفل."
    )

    if message.from_user.id == ADMIN_ID:
        await message.answer(welcome, reply_markup=admin_menu_kb())
    else:
        await message.answer(welcome, reply_markup=main_menu_kb())

@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    await message.reply("⚙️ لوحة تحكم الأدمن — اختر إجراء من القائمة:", reply_markup=admin_menu_kb())

@dp.message()
async def generic_handler(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id
    current_state = await state.get_state()

    # ----------- admin state flows -----------
    if user_id == ADMIN_ID:
        # waiting create key
        if current_state == AdminStates.waiting_create_key.state:
            parts = text.split()
            if len(parts) >= 1:
                code = parts[0].strip()
                days = 30
                if len(parts) >= 2:
                    try:
                        days = int(parts[1])
                    except:
                        await message.reply("⚠️ عدد الأيام غير صحيح، سيتم استخدام 30 يوم افتراضياً.")
                create_key_record(code, days)
                await message.reply(f"✅ تم إنشاء المفتاح: <code>{escape(code)}</code> لمدة {days} يوم.", parse_mode="HTML")
            else:
                await message.reply("❌ الصيغة غير صحيحة. مثال: <code>AT-AB12 30</code>", parse_mode="HTML")
            await state.clear()
            return

        # waiting ban
        if current_state == AdminStates.waiting_ban.state:
            try:
                tid = int(text)
                ban_user_db(tid)
                await message.reply(f"⛔ تم حظر المستخدم {tid}")
            except:
                await message.reply("❌ أدخل telegram_id صالح (أرقام فقط).")
            await state.clear()
            return

        # waiting unban
        if current_state == AdminStates.waiting_unban.state:
            try:
                tid = int(text)
                unban_user_db(tid)
                await message.reply(f"✅ تم إلغاء الحظر عن المستخدم {tid}")
            except:
                await message.reply("❌ أدخل telegram_id صالح (أرقام فقط).")
            await state.clear()
            return

        # waiting broadcast all
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
            await message.reply(f"📢 تم إرسال الرسالة إلى {sent} مستخدم (جميع المستخدمين).")
            await state.clear()
            return

        # waiting broadcast subs
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
            await message.reply(f"📣 تم إرسال الرسالة إلى {sent} مشترك.")
            await state.clear()
            return

        # waiting trade manual
        if current_state == AdminStates.waiting_trade_manual.state:
            parts = [p.strip() for p in text.split(",")]
            if len(parts) != 6:
                await message.reply("⚠️ الصيغة غير صحيحة.\nاكتب: زوج, نوع, الدخول, TP, SL, نسبة_الثقة\nمثال:\nEUR/USD, Buy, 1.0975, 1.0940, 1.0910, 92")
                return
            pair, side, entry, tp, sl, trust = parts
            trade_msg = format_trade_message(pair, side, entry, tp, sl, trust)
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

        # Admin button handling (no state)
        if text == "➕ إنشاء مفتاح جديد":
            await message.reply("🔑 أرسل الكود (اختياري: اكتب بعدها عدد الأيام). مثال: AT-AB12 30")
            await state.set_state(AdminStates.waiting_create_key)
            return

        if text == "🔑 المفاتيح النشطة":
            rows = list_keys_records()
            if not rows:
                await message.reply("📝 لا توجد مفاتيح مسجلة حالياً.")
                return
            out = "🧾 <b>المفاتيح المسجلة:</b>\n\n"
            for r in rows:
                used = f"✅ مستخدم (by {r['used_by']})" if r["used_by"] else "🟢 متاح"
                created = fmt_ts(r["created_at"]) if r["created_at"] else "غير محدد"
                expiry = fmt_ts(r["expiry"]) if r["expiry"] else "غير محدد"
                out += f"🔑 <code>{escape(r['key_code'])}</code> — {r['duration_days']} يوم — {used}\n    إنشاء: {created} | انتهاء: {expiry}\n\n"
            await message.reply(out, parse_mode="HTML")
            return

        if text == "🚫 حظر مستخدم":
            await message.reply("🚫 أرسل telegram_id للمستخدم لحظره (أرقام فقط)")
            await state.set_state(AdminStates.waiting_ban)
            return

        if text == "✅ إلغاء الحظر":
            await message.reply("✅ أرسل telegram_id للمستخدم لإلغاء الحظر (أرقام فقط)")
            await state.set_state(AdminStates.waiting_unban)
            return

        if text == "📢 رسالة لكل المستخدمين":
            await message.reply("📢 أرسل الرسالة ليتم إرسالها لكل المستخدمين:")
            await state.set_state(AdminStates.waiting_broadcast_all)
            return

        if text == "📣 رسالة للمشتركين":
            await message.reply("📣 أرسل الرسالة ليتم إرسالها للمشتركين فقط:")
            await state.set_state(AdminStates.waiting_broadcast_subs)
            return

        if text == "📊 إحصائيات المستخدمين":
            total, active = count_users()
            await message.reply(f"📊 إحصائيات:\n\n👥 إجمالي المستخدمين: <b>{total}</b>\n✅ المشتركين النشطين: <b>{active}</b>", parse_mode="HTML")
            return

        if text == "💹 إرسال صفقة يدوياً":
            await message.reply("💹 أرسل الصفقة بهذا الشكل (مفصولة بفواصل):\nزوج, نوع (Buy/Sell), الدخول, TP, SL, نسبة_الثقة\nمثال:\nEUR/USD, Buy, 1.0975, 1.0940, 1.0910, 92")
            await state.set_state(AdminStates.waiting_trade_manual)
            return

        if text == "↩️ رجوع للمستخدم":
            await message.reply("✅ تم الرجوع إلى قائمة المستخدم.", reply_markup=main_menu_kb())
            await state.clear()
            return

        # default admin fallback
        await message.reply("⚙️ استخدم لوحة الأدمن أو اكتب /admin لفتحها.")
        return

    # ---------------- regular user flow ----------------
    # try to activate key (single token)
    if " " not in text and len(text) > 2:
        ok, info = activate_user_with_key(user_id, text)
        if ok:
            await message.reply(f"✅ تم تفعيل اشتراكك حتى: <b>{fmt_ts(info)}</b>", parse_mode="HTML")
        else:
            if info == "invalid":
                await message.reply("❌ المفتاح غير صحيح. تأكد من الكود وحاول مرة أخرى.")
            elif info == "used":
                await message.reply("⚠️ هذا المفتاح مستخدم بالفعل.")
            else:
                await message.reply("❌ حدث خطأ أثناء التفعيل.")
        return

    # user buttons
    if text == "📊 جدول اليوم":
        await message.reply("📊 جدول صفقات اليوم سيتم تحديثه هنا قريباً.")
    elif text == "📈 آخر الصفقات":
        await message.reply("📈 لا توجد صفقات سابقة حالياً.")
    elif text == "🎟️ تفعيل مفتاح الاشتراك":
        await message.reply("🔑 أرسل كود التفعيل هنا لتفعيله.")
    elif text == "💬 الدعم الفني":
        await message.reply("📞 تواصل معنا عبر: @AlphaTradeAI_Support")
    elif text == "ℹ️ عن AlphaTradeAI" or text == "ℹ️ معلومات عن البوت":
        await message.reply(
            "🤖 <b>AlphaTradeAI</b>\n"
            "نظام إشارات تداول احترافي مدعوم بالذكاء الاصطناعي.\n"
            "🔹 إشارات مدروسة، إشعارات فورية، وإدارة مخاطرة واضحة.",
            parse_mode="HTML"
        )
    else:
        await message.reply("❓ لم يتم التعرف على الأمر. استخدم /start للعودة للقائمة.")

# -------------------- background cleanup --------------------
async def periodic_cleanup():
    while True:
        try:
            cleanup_expired_subscriptions()
        except Exception as e:
            logger.exception("cleanup error: %s", e)
        await asyncio.sleep(3600)

# -------------------- startup --------------------
async def on_startup():
    init_db()
    logger.info("Bot started. Admin ID=%s", ADMIN_ID)
    # start background cleanup task
    asyncio.create_task(periodic_cleanup())

# -------------------- run --------------------
if __name__ == "__main__":
    # run dispatcher
    dp.startup.register(lambda _: on_startup())  # register startup
    dp.run_polling(bot)
