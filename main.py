# main.py - بوت التليجرام الأساسي (aiogram v3)
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
import logging
from database import init_db, add_or_update_user, activate_user_with_key, create_key, list_keys, get_active_users
from config import BOT_TOKEN, ADMIN_ID
from scheduler import start_scheduler
import time

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode='HTML')
dp = Dispatcher()

def format_expiry(ts):
    if not ts: return 'غير محدد'
    import datetime
    dt = datetime.datetime.utcfromtimestamp(ts)
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')

@dp.message(Command('start'))
async def cmd_start(msg: Message):
    add_or_update_user(msg.from_user.id, getattr(msg.from_user, 'username', None))
    await msg.reply("أهلاً! هذا بوت 'توصيات AI'\nللاشتراك: أرسل مفتاح التفعيل الذي اشتريته.\nلو أنت الأدمن ارسل /admin")

@dp.message()
async def handle_text(msg: Message):
    text = (msg.text or '').strip()
    if msg.from_user.id == ADMIN_ID and text.startswith('/createkey'):
        parts = text.split()
        if len(parts) == 3:
            k = parts[1].strip()
            dur = int(parts[2])
            create_key(k, dur)
            await msg.reply(f'تم إنشاء المفتاح {k} لمدة {dur} يوم.')
        else:
            await msg.reply('استخدام: /createkey <KEYCODE> <DAYS>')
        return
    if msg.from_user.id == ADMIN_ID and text.startswith('/listkeys'):
        rows = list_keys()
        txt = 'قائمة المفاتيح:\n'
        for r in rows:
            used = r['used_by'] if r['used_by'] else 'متاح'
            txt += f"{r['key_code']} - {r['duration_days']}d - used_by: {used}\n"
        await msg.reply(txt)
        return
    if msg.from_user.id == ADMIN_ID and text.startswith('/users'):
        rows = get_active_users()
        txt = 'المشتركين النشطين:\n'
        for r in rows:
            txt += f"{r['telegram_id']} - {r['username']} - expiry: {format_expiry(r['expiry'])}\n"
        await msg.reply(txt)
        return
    if len(text) > 3 and ('-' in text or text.isalnum()):
        ok, info = activate_user_with_key(msg.from_user.id, text)
        if ok:
            await msg.reply(f'تم تفعيل اشتراكك حتى: {format_expiry(info)}')
        else:
            if info == 'invalid':
                await msg.reply('المفتاح غير صحيح، تأكد وحاول مرة أخرى.')
            elif info == 'used':
                await msg.reply('المفتاح مستخدم بالفعل.')
            else:
                await msg.reply('حدث خطأ أثناء التفعيل.')
        return
    await msg.reply('أمر غير معروف. لأوامر الادمن: /admin (لو أنت الأدمن)')

async def send_signal_to_user(user_id, signal):
    text = (f"📈 <b>صفقة جديدة على XAUUSD</b>\n"
            f"🔹 نوع الصفقة: {signal['type']} ({signal['mode']})\n"
            f"💰 الدخول: {signal['entry']}\n"
            f"🛑 Stop Loss: {signal['sl']}\n"
            f"🎯 Take Profit: {signal['tp']}\n"
            f"✅ نسبة النجاح المتوقعة: {signal['rate']}%\n"
            f"⏰ وقت الإشارة: {signal['time']}")
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        print('send signal error', e)

async def on_startup():
    init_db()
    from database import list_keys
    if len(list_keys())==0:
        create_key('XAU-1D-DEMO', 1)
        create_key('XAU-7D-DEMO', 7)
        create_key('XAU-30D-DEMO', 30)
    # start scheduler in background, pass send_signal_to_user to avoid circular import
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, start_scheduler, send_signal_to_user)
    print('Bot started')

if __name__ == '__main__':
    import asyncio
    try:
        asyncio.run(dp.start_polling(bot, on_startup=on_startup))
    except (KeyboardInterrupt, SystemExit):
        print('Bot stopped')
