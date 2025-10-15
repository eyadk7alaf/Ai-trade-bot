import asyncio
import sqlite3
import time
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import os

# ================= إعداد البوت =================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = 7378889303
DB_PATH = "bot_data.db"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# ================= FSM للأدمن =================
class AdminStates(StatesGroup):
    waiting_key_creation = State()
    waiting_ban_user = State()
    waiting_unban_user = State()
    waiting_broadcast_all = State()
    waiting_broadcast_subs = State()
    waiting_trade_manual = State()

# ================= قاعدة البيانات =================
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
            active INTEGER DEFAULT 0,
            expiry INTEGER DEFAULT 0,
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE users SET username=? WHERE telegram_id=?", (username, telegram_id))
    else:
        cur.execute("INSERT INTO users (telegram_id, username) VALUES (?,?)", (telegram_id, username))
    conn.commit()
    conn.close()

def activate_user_with_key(telegram_id, key_code):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM keys WHERE key_code=?", (key_code,))
    k = cur.fetchone()
    if not k:
        conn.close()
        return False, 'invalid'
    if k['used_by'] is not None:
        conn.close()
        return False, 'used'
    now = int(time.time())
    expiry = now + k['duration_days']*24*3600
    cur.execute("UPDATE keys SET used_by=?, expiry=? WHERE key_code=?", (telegram_id, expiry, key_code))
    cur.execute("UPDATE users SET active=1, expiry=? WHERE telegram_id=?", (expiry, telegram_id))
    conn.commit()
    conn.close()
    return True, expiry

def create_key(key_code, duration_days):
    conn = get_conn()
    cur = conn.cursor()
    now = int(time.time())
    cur.execute("INSERT INTO keys (key_code, duration_days, created_at) VALUES (?,?,?)", (key_code, duration_days, now))
    conn.commit()
    conn.close()

def list_keys():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM keys ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_active_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id, username, expiry FROM users WHERE active=1 AND banned=0")
    rows = cur.fetchall()
    conn.close()
    return rows

def ban_user(telegram_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (telegram_id,))
    conn.commit()
    conn.close()

def unban_user(telegram_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=0 WHERE telegram_id=?", (telegram_id,))
    conn.commit()
    conn.close()

# ================= قائمة الأدمن =================
ADMIN_BUTTONS = [
    ["إنشاء مفتاح 🔑", "عرض المفاتيح 📜"],
    ["حظر مستخدم ❌", "إلغاء حظر مستخدم ✅"],
    ["رسالة لكل المستخدمين 📢", "رسالة للمشتركين ✅"],
    ["إرسال صفقة يدوياً 💹"]
]

# ================= الرسائل الأساسية =================
@dp.message(Command("start"))
async def start(msg: types.Message):
    add_or_update_user(msg.from_user.id, getattr(msg.from_user, 'username', None))
    await msg.answer(
        f"👋 أهلاً {msg.from_user.first_name}!\n"
        "هذا بوت تجريبي لإرسال الصفقات وإدارة الاشتراكات.\n"
        "للاشتراك، أرسل مفتاح التفعيل 🔑"
    )

@dp.message(Command("admin"))
async def admin(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("❌ غير مسموح بالدخول هنا.")
        return
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=b) for b in row] for row in ADMIN_BUTTONS],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await msg.reply("📋 قائمة أوامر الأدمن:", reply_markup=keyboard)

# ================= التعامل مع النصوص =================
@dp.message()
async def handle_text(msg: types.Message, state: FSMContext):
    text = msg.text.strip()
    user_id = msg.from_user.id

    # ======= أوامر الأدمن =======
    if user_id == ADMIN_ID:
        current_state = await state.get_state()
        
        # إنشاء مفتاح
        if current_state == AdminStates.waiting_key_creation:
            try:
                key, days = text.split()
                days = int(days)
                create_key(key, days)
                await msg.reply(f"✅ تم إنشاء المفتاح {key} لمدة {days} يوم.")
            except:
                await msg.reply("❌ خطأ، تأكد من الصيغة: الكود ثم عدد الأيام.")
            await state.clear()
            return

        # الحظر
        if current_state == AdminStates.waiting_ban_user:
            try:
                ban_user(int(text))
                await msg.reply(f"❌ تم حظر المستخدم {text}")
            except:
                await msg.reply("❌ خطأ، تأكد من الأيدي.")
            await state.clear()
            return

        # إلغاء الحظر
        if current_state == AdminStates.waiting_unban_user:
            try:
                unban_user(int(text))
                await msg.reply(f"✅ تم إلغاء الحظر عن المستخدم {text}")
            except:
                await msg.reply("❌ خطأ، تأكد من الأيدي.")
            await state.clear()
            return

        # رسالة لكل المستخدمين
        if current_state == AdminStates.waiting_broadcast_all:
            users = get_active_users()
            count = 0
            for u in users:
                try:
                    await bot.send_message(u['telegram_id'], text)
                    count += 1
                except:
                    continue
            await msg.reply(f"📢 تم إرسال الرسالة لـ {count} مستخدمين.")
            await state.clear()
            return

        # رسالة للمشتركين فقط
        if current_state == AdminStates.waiting_broadcast_subs:
            subs = get_active_users()
            count = 0
            for u in subs:
                try:
                    await bot.send_message(u['telegram_id'], text)
                    count += 1
                except:
                    continue
            await msg.reply(f"✅ تم إرسال الرسالة لـ {count} مشترك.")
            await state.clear()
            return

        # إرسال صفقة يدوياً
        if current_state == AdminStates.waiting_trade_manual:
            users = get_active_users()
            for u in users:
                try:
                    await bot.send_message(u['telegram_id'], f"💹 صفقة جديدة:\n{text}")
                except:
                    continue
            await msg.reply("💹 تم إرسال الصفقة للمشتركين.")
            await state.clear()
            return

        # التعامل مع ضغط الأزرار
        if text == "إنشاء مفتاح 🔑":
            await msg.reply("🪄 أرسل الكود وعدد الأيام مفصول بمسافة:\nمثال: MYKEY 7")
            await state.set_state(AdminStates.waiting_key_creation)
            return
        elif text == "عرض المفاتيح 📜":
            rows = list_keys()
            reply = "📜 <b>قائمة المفاتيح:</b>\n"
            for r in rows:
                used = "✅ مستخدم" if r['used_by'] else "🟢 متاح"
                reply += f"🔑 <code>{r['key_code']}</code> - {r['duration_days']} يوم - {used}\n"
            await msg.reply(reply)
            return
        elif text == "حظر مستخدم ❌":
            await msg.reply("🛑 أرسل أيدي المستخدم لحظره:")
            await state.set_state(AdminStates.waiting_ban_user)
            return
        elif text == "إلغاء حظر مستخدم ✅":
            await msg.reply("✅ أرسل أيدي المستخدم لإلغاء الحظر:")
            await state.set_state(AdminStates.waiting_unban_user)
            return
        elif text == "رسالة لكل المستخدمين 📢":
            await msg.reply("📢 أرسل الرسالة لتصل لكل المستخدمين:")
            await state.set_state(AdminStates.waiting_broadcast_all)
            return
        elif text == "رسالة للمشتركين ✅":
            await msg.reply("✅ أرسل الرسالة لتصل للمشتركين فقط:")
            await state.set_state(AdminStates.waiting_broadcast_subs)
            return
        elif text == "إرسال صفقة يدوياً 💹":
            await msg.reply("💹 أرسل الصفقة بهذا الشكل:\nزوج العملة، نوع الصفقة، السعر، SL، TP، نسبة النجاح")
            await state.set_state(AdminStates.waiting_trade_manual)
            return

    # ======= تفعيل مفتاح الاشتراك للمستخدمين =======
    if len(text) > 3 and " " not in text:
        ok, info = activate_user_with_key(user_id, text)
        if ok:
