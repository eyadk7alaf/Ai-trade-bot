import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import logging
from database import init_db, add_or_update_user, activate_user_with_key
from config import BOT_TOKEN, ADMIN_ID

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode='HTML')
dp = Dispatcher()

def format_expiry(ts):
    import datetime
    if not ts:
        return 'غير محدد'
    return datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S UTC')

@dp.message(Command('start'))
async def cmd_start(msg: types.Message):
    add_or_update_user(msg.from_user.id, getattr(msg.from_user, 'username', None))
    await msg.reply("👋 أهلاً بك في بوت Black web 💲\n"
                    "للاشتراك: أرسل مفتاح التفعيل.\n"
                    "لو أنت الأدمن اضغط على زر 'أوامر الأدمن'")

@dp.message()
async def handle_text(msg: types.Message):
    text = (msg.text or '').strip()
    if msg.from_user.id == ADMIN_ID:
        if text.lower() in ['/admin', 'أوامر الأدمن']:
            keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
            keyboard.add("إنشاء مفتاح", "حظر مستخدم")
            keyboard.add("إلغاء حظر مستخدم", "المفاتيح النشطة")
            keyboard.add("رسالة لكل المستخدمين", "رسالة للمشتركين")
            await msg.reply("📋 قائمة أوامر الأدمن:", reply_markup=keyboard)
            return

    if len(text) > 3:
        ok, info = activate_user_with_key(msg.from_user.id, text)
        if ok:
            await msg.reply(f'✅ تم تفعيل اشتراكك حتى: {format_expiry(info)}')
        else:
            if info=='invalid':
                await msg.reply('❌ المفتاح غير صحيح.')
            elif info=='used':
                await msg.reply('❌ المفتاح مستخدم بالفعل.')
            else:
                await msg.reply('⚠️ حدث خطأ أثناء التفعيل.')
        return

    await msg.reply('❓ أمر غير معروف.')

async def on_startup():
    init_db()
    print('✅ البوت جاهز للعمل')

if __name__ == '__main__':
    try:
        asyncio.run(dp.start_polling(bot, on_startup=on_startup))
    except (KeyboardInterrupt, SystemExit):
        print('Bot stopped')
