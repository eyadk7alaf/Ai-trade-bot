# main.py
# AlphaTradeAI - Telegram bot (aiogram v2)
# رسمي / فخم - Arabic UI
# Requirements: aiogram==2.25.1

import os
import asyncio
import logging
import sqlite3
import time
import secrets
import html
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.filters import Command  # available in some aiogram builds; safe fallback below
from aiogram.utils import executor

# -------------------- CONFIG --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Environment variable TELEGRAM_BOT_TOKEN is required")

ADMIN_ID = int(os.getenv("ADMIN_ID", "7378889303"))  # default admin id if not set

DB_FILE = "bot_data.db"

# -------------------- LOGGING --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("AlphaTradeAI")

# -------------------- BOT & DISPATCHER --------------------
bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# -------------------- DB HELPERS --------------------
def get_conn():
    conn = sqlite3.connect(DB_FILE)
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
        used_by INTEGER DEFAULT NULL,
        created_at INTEGER,
        expiry INTEGER DEFAULT NULL
    )
    """)
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def add_or_update_user(telegram_id: int, username: str = None, first_name: str = None):
    conn = get_conn()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("INSERT OR IGNORE INTO users (telegram_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                (telegram_id, username, first_name, now))
    cur.execute("UPDATE users SET username=?, first_name=? WHERE telegram_id=?", (username, first_name, telegram_id))
    conn.commit()
    conn.close()

def create_key_record(key_code: str, duration_days: int):
    conn = get_conn()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("INSERT OR REPLACE INTO keys (key_code, duration_days, created_at, used_by, expiry) VALUES (?, ?, ?, NULL, NULL)",
                (key_code, duration_days, now))
    conn.commit()
    conn.close()
    logger.info("Created key %s for %d days", key_code, duration_days)

def list_keys_records():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT key_code, duration_days, used_by, created_at, expiry FROM keys ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def activate_user_with_key(telegram_id: int, key_code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT key_code, duration_days, used_by FROM keys WHERE key_code=?", (key_code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, "invalid"
    if row["used_by"] is not None:
        conn.close()
        return False, "used"
    duration_days = int(row["duration_days"])
    expiry_ts = int(time.time()) + duration_days * 24 * 3600
    cur.execute("UPDATE keys SET used_by=?, expiry=? WHERE key_code=?", (telegram_id, expiry_ts, key_code))
    cur.execute("INSERT OR IGNORE INTO users (telegram_id, created_at) VALUES (?, ?)", (telegram_id, int(time.time())))
    cur.execute("UPDATE users SET subscribed_until=? WHERE telegram_id=?", (expiry_ts, telegram_id))
    conn.commit()
    conn.close()
    logger.info("Activated key %s for user %s until %s", key_code, telegram_id, expiry_ts)
    return True, expiry_ts

def get_all_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users")
    rows = [r["telegram_id"] for r in cur.fetchall()]
    conn.close()
    return rows

def get_active_users():
    now = int(time.time())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users WHERE subscribed_until > ? AND banned=0", (now,))
    rows = [r["telegram_id"] for r in cur.fetchall()]
    conn.close()
    return rows

def ban_user_db(telegram_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (telegram_id,))
    conn.commit()
    conn.close()
    logger.info("Banned user %s", telegram_id)

def unban_user_db(telegram_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=0 WHERE telegram_id=?", (telegram_id,))
    conn.commit()
    conn.close()
    logger.info("Unbanned user %s", telegram_id)

def count_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM users")
    total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) as active FROM users WHERE subscribed_until > ?", (int(time.time()),))
    active = cur.fetchone()["active"]
    conn.close()
    return total, active

def cleanup_expired_subscriptions():
    now = int(time.time())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET subscribed_until=0 WHERE subscribed_until <= ?", (now,))
    conn.commit()
    conn.close()

# -------------------- FSM --------------------
class AdminStates(StatesGroup):
    waiting_create_key = State()
    waiting_ban = State()
    waiting_unban = State()
    waiting_broadcast_all = State()
    waiting_broadcast_subs = State()
    waiting_trade_manual = State()

# -------------------- Keyboards --------------------
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📊 جدول اليوم"), KeyboardButton("📈 آخر الصفقات"))
    kb.row(KeyboardButton("🎟️ تفعيل مفتاح الاشتراك"), KeyboardButton("💬 الدعم الفني"))
    kb.row(KeyboardButton("ℹ️ معلومات عن البوت"))
    return kb

def admin_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("➕ إنشاء مفتاح جديد"), KeyboardButton("🔑 المفاتيح النشطة"))
    kb.row(KeyboardButton("🚫 حظر مستخدم"), KeyboardButton("✅ إلغاء الحظر"))
    kb.row(KeyboardButton("📢 رسالة لكل المستخدمين"), KeyboardButton("📣 رسالة للمشتركين فقط"))
    kb.row(KeyboardButton("📊 إحصائيات المستخدمين"), KeyboardButton("💹 إرسال صفقة يدوياً"))
    kb.row(KeyboardButton("↩️ رجوع للمستخدم"))
    return kb

# -------------------- Helpers --------------------
def fmt_ts(ts):
    try:
        return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "غير محدد"

def escape(s: str):
    if s is None:
        return ""
    return html.escape(str(s))

def generate_key_code():
    return "AT-" + secrets.token_hex(4).upper()

def format_trade_message(pair, side, entry, tp, sl, trust, ts=None):
    ts_text = fmt_ts(ts or int(time.time()))
    arrow = "📈" if str(side).strip().lower() == "buy" else "📉"
    pair_e = escape(pair)
    side_e = escape(side)
    entry_e = escape(entry)
    tp_e = escape(tp)
    sl_e = escape(sl)
    trust_e = escape(trust)
    msg = (
        "💠 <b>إشارة تداول جديدة من AlphaTradeAI</b>\n\n"
        f"💱 <b>الزوج:</b> {pair_e}\n"
        f"{arrow} <b>النوع:</b> {side_e}\n"
        f"💰 <b>الدخول (Enter):</b> {entry_e}\n"
        f"🎯 <b>Take Profit:</b> {tp_e}\n"
        f"🛑 <b>Stop Loss:</b> {sl_e}\n"
        f"🔥 <b>نسبة الثقة:</b> {trust_e}%\n"
        f"⏰ <b>الوقت:</b> {ts_text}"
    )
    return msg

# -------------------- Startup handlers --------------------
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    # تسجيل / تحديث المستخدم
    add_or_update_user(message.from_user.id, getattr(message.from_user, 'username', None), getattr(message.from_user, 'first_name', None))

    welcome = (
        "👑 <b>مرحباً بك في AlphaTradeAI</b>\n\n"
        "نظام إشارات تداول احترافي مدعوم بالذكاء الاصطناعي — تصميم رسمي ومضمون.\n\n"
        "🔹 توصيات مُنسّقة ومحترفة.\n"
        "🔹 إشعارات فورية عند صدور الصفقة.\n"
        "🔹 إدارة مخاطر واضحة ومحددة.\n\n"
        "🔐 لإتمام الاشتراك أرسل مفتاح التفعيل (التنسيق: <code>MYKEY123</code>) أو استخدم الأزرار أدناه."
    )

    if message.from_user.id == ADMIN_ID:
        await message.answer(welcome, reply_markup=admin_menu_kb())
    else:
        await message.answer(welcome, reply_markup=main_menu_kb())

@dp.message_handler(commands=['admin'])
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("🚫 هذا الأمر مخصص للأدمن فقط.")
        return
    await message.reply("⚙️ لوحة تحكم الأدمن — اختر الإجراء:", reply_markup=admin_menu_kb())

@dp.message_handler(commands=['help'])
async def cmd_help(message: types.Message):
    help_text = (
        "<b>دليل الأوامر</b>\n\n"
        "/start - بدء الاستخدام\n"
        "/admin - فتح لوحة الأدمن (للأدمن فقط)\n"
        "/help - عرض هذه الرسالة\n\n"
        "للاشتراك: أرسل مفتاح التفعيل الخاص بك مباشرة في المحادثة."
    )
    await message.reply(help_text)

@dp.message_handler(commands=['stats'])
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("❌ لا تملك صلاحية استخدام هذا الأمر.")
        return
    total, active = count_users()
    await message.reply(f"📊 إحصائيات:\n\n👥 إجمالي المستخدمين: <b>{total}</b>\n✅ المشتركين النشطين: <b>{active}</b>")

# -------------------- Admin/text handler --------------------
@dp.message_handler()
async def handler_all(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id

    # ---------------- Admin flow ----------------
    if user_id == ADMIN_ID:
        current_state = await state.get_state()

        # waiting for create key
        if current_state == AdminStates.waiting_create_key.state:
            parts = text.split()
            if len(parts) >= 1:
                # Accept either "CODE" or "CODE days"
                code = parts[0].strip()
                days = 30
                if len(parts) >= 2:
                    try:
                        days = int(parts[1])
                    except:
                        await message.reply("❌ عدد الأيام غير صحيح. سيتم استخدام 30 يوم افتراضياً.")
                create_key_record(code, days)
                await message.reply(f"✅ تم إنشاء المفتاح: <code>{escape(code)}</code> لمدة {days} يوم.", parse_mode="HTML")
            else:
                await message.reply("❌ الصيغة غير صحيحة. أرسل: <code>CODE 30</code>", parse_mode="HTML")
            await state.finish()
            return

        # waiting ban
        if current_state == AdminStates.waiting_ban.state:
            try:
                tid = int(text)
                ban_user_db(tid)
                await message.reply(f"⛔ تم حظر المستخدم {tid}")
            except:
                await message.reply("❌ أدخل telegram_id صحيح (أرقام فقط).")
            await state.finish()
            return

        # waiting unban
        if current_state == AdminStates.waiting_unban.state:
            try:
                tid = int(text)
                unban_user_db(tid)
                await message.reply(f"✅ تم إلغاء الحظر عن المستخدم {tid}")
            except:
                await message.reply("❌ أدخل telegram_id صحيح (أرقام فقط).")
            await state.finish()
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
            await state.finish()
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
            await state.finish()
            return

        # waiting trade manual
        if current_state == AdminStates.waiting_trade_manual.state:
            # expect: pair, side, entry, tp, sl, trust  (comma separated)
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
            await state.finish()
            return

        # Admin button handling (non-state)
        if text == "➕ إنشاء مفتاح جديد":
            await message.reply("🔑 أرسل الكود (اختياري: تابع بعده عدد الأيام)\nمثال: AT-AB12 30")
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

        if text == "📣 رسالة للمشتركين فقط":
            await message.reply("📣 أرسل الرسالة ليتم إرسالها للمشتركين فقط:")
            await state.set_state(AdminStates.waiting_broadcast_subs)
            return

        if text == "📊 إحصائيات المستخدمين":
            total, active = count_users()
            await message.reply(f"📊 إحصائيات:\n\n👥 إجمالي المستخدمين: <b>{total}</b>\n✅ المشتركين النشطين: <b>{active}</b>", parse_mode="HTML")
            return

        if text == "💹 إرسال صفقة يدوياً":
            await message.reply("💹 أرسل الصفقة بهذا الشكل (مفصولة بفواصل):\nزوج, نوع, الدخول, TP, SL, نسبة_الثقة\nمثال:\nEUR/USD, Buy, 1.0975, 1.0940, 1.0910, 92")
            await state.set_state(AdminStates.waiting_trade_manual)
            return

        if text == "↩️ رجوع للمستخدم":
            await message.reply("تم الرجوع إلى واجهة المستخدم.", reply_markup=main_menu_kb())
            await state.finish()
            return

        # any other admin text
        await message.reply("⚙️ استخدم لوحة الأدمن أو ارسل /admin لفتحها.")
        return

    # ---------------- Regular user flow ----------------
    # try to activate key (simple heuristic: single token, no spaces, length>2)
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
        await message.reply("📊 جدول صفقات اليوم سيتم نشره هنا قريباً. تابعنا للحصول على التحديثات.")
    elif text == "📈 آخر الصفقات":
        await message.reply("📈 آخر الصفقات: (سيتم تحديثها قريبا)")
    elif text == "🎟️ تفعيل مفتاح الاشتراك" or text == "🎟️ تفعيل مفتاح الاشتراك":
        await message.reply("🔑 أرسل كود التفعيل هنا لتفعيل اشتراكك.")
    elif text == "💬 الدعم الفني":
        await message.reply("📞 للتواصل مع الدعم الفني تواصل عبر: @AlphaTradeAI_Support")
    elif text == "ℹ️ معلومات عن البوت" or text == "ℹ️ معلومات عن البوت":
        await message.reply(
            "🤖 <b>AlphaTradeAI</b>\n"
            "نظام إشارات تداول احترافي مدعوم بالذكاء الاصطناعي — توصيات رسمية ومنسقة.\n"
            "للاشتراك أرسل مفتاح التفعيل أو تواصل مع الدعم.",
            parse_mode="HTML"
        )
    else:
        await message.reply("❓ لم يتم التعرف على الأمر. استخدم /start للعودة إلى القائمة الرئيسية.")

# -------------------- Background housekeeping --------------------
async def periodic_cleanup():
    while True:
        try:
            cleanup_expired_subscriptions()
        except Exception as e:
            logger.exception("cleanup error: %s", e)
        await asyncio.sleep(3600)  # كل ساعة

# -------------------- Startup --------------------
async def on_startup(_):
    init_db()
    logger.info("Bot started. Admin ID=%s", ADMIN_ID)
    # start background cleanup
    asyncio.create_task(periodic_cleanup())

# -------------------- Run --------------------
if __name__ == "__main__":
    # run
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
