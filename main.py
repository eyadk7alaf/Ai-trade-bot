import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import logging
from database import (
    init_db,
    add_or_update_user,
    activate_user_with_key,
    create_key,
    list_keys,
    get_active_users
)
from config import BOT_TOKEN, ADMIN_ID
from scheduler import start_scheduler
from signals import generate_signal

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode='HTML')
dp = Dispatcher()

# فورمات الوقت
def format_expiry(ts):
    if not ts:
        return 'غير محدد'
    import datetime
    dt = datetime.datetime.utcfromtimestamp(ts)
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')

# رسالة start
@dp.message(Command('start'))
async def cmd_start(msg: types.Message):
    add_or_update_user(msg.from_user.id, getattr(msg.from_user, 'username', None))
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add('🔑 تفعيل الاشتراك')
    keyboard.add('📊 آخر الصفقات', '📅 جدول اليوم')
    keyboard.add('❓ مساعدة')
    await msg.reply(
        '👋 أهلاً بك في بوت التداول الذكي!\nللاشتراك اضغط 🔑 تفعيل الاشتراك.',
        reply_markup=keyboard
    )

# التعامل مع الرسائل
@dp.message()
async def handle_text(msg: types.Message):
    text = (msg.text or '').strip()

    # أوامر الأدمن
    if msg.from_user.id == ADMIN_ID:
        if text == '/admin':
            keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
            keyboard.add('➕ إنشاء مفتاح', '🚫 حظر/إلغاء حظر مستخدم')
            keyboard.add('📜 عرض المفاتيح', '📢 رسالة للجميع')
            keyboard.add('✉️ رسالة للمشتركين')
            await msg.reply('🛠️ لوحة الأدمن', reply_markup=keyboard)
            return

    # مساعدة
    if text == '❓ مساعدة':
        await msg.reply(
            '💡 أوامر:\n🔑 تفعيل الاشتراك\n📊 آخر الصفقات\n📅 جدول اليوم\n❓ مساعدة'
        )
        return

    # تفعيل الاشتراك
    if text == '🔑 تفعيل الاشتراك':
        await msg.reply('🗝️ أرسل مفتاح التفعيل الخاص بك')
        return

    # آخر الصفقات
    if text == '📊 آخر الصفقات':
        from database import user_is_active
        if not user_is_active(msg.from_user.id):
            await msg.reply('🚫 تحتاج لتفعيل الاشتراك للوصول لآخر الصفقات')
            return
        await msg.reply('📊 آخر الصفقات: لا توجد صفقات بعد')
        return

    # جدول اليوم
    if text == '📅 جدول اليوم':
        from database import user_is_active
        if not user_is_active(msg.from_user.id):
            await msg.reply('🚫 تحتاج لتفعيل الاشتراك للوصول لجدول اليوم')
            return
        await msg.reply('📅 جدول اليوم: لا توجد إشارات مخططة بعد')
        return

    # التعامل مع مفتاح التفعيل
    if len(text) > 3 and ('-' in text or text.isalnum()):
        ok, info = activate_user_with_key(msg.from_user.id, text)
        if ok:
            await msg.reply(f'✅ تم تفعيل اشتراكك حتى: {format_expiry(info)}')
        else:
            if info == 'invalid':
                await msg.reply('🚫 المفتاح غير صحيح')
            elif info == 'used':
                await msg.reply('🚫 المفتاح مستخدم بالفعل')
            else:
                await msg.reply('حدث خطأ أثناء التفعيل')
        return

    await msg.reply('❌ أمر غير معروف')

# إرسال الصفقات
async def send_signal_to_user(user_id, signal):
    text = (
        f"📈 صفقة جديدة على {signal['symbol']}\n"
        f"🔹 نوع: {signal['type']} ({signal['mode']})\n"
        f"💰 الدخول: {signal['entry']}\n"
        f"🛑 SL: {signal['sl']}\n"
        f"🎯 TP: {signal['tp']}\n"
        f"✅ نسبة النجاح المتوقعة: {signal['rate']}%\n"
        f"⏰ وقت الإشارة: {signal['time']}"
    )
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        print('send signal error', e)

# عند التشغيل
async def on_startup():
    init_db()
    from database import list_keys
    if len(list_keys()) == 0:
        create_key('XAU-1D-DEMO', 1)
        create_key('EUR-7D-DEMO', 7)
        create_key('GBP-30D-DEMO', 30)
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, start_scheduler, send_signal_to_user)
    print('Bot started')

if __name__ == '__main__':
    asyncio.run(dp.start_polling(bot, on_startup=on_startup))
