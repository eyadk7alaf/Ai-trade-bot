import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import logging
from database import init_db, add_or_update_user, activate_user_with_key, create_key, list_keys, get_active_users
from config import BOT_TOKEN, ADMIN_ID
from scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode='HTML')
dp = Dispatcher()

@dp.message(Command('start'))
async def cmd_start(msg: types.Message):
    add_or_update_user(msg.from_user.id, getattr(msg.from_user, 'username', None))
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add('🔑 تفعيل الاشتراك')
    keyboard.add('📊 آخر الصفقات', '📅 جدول اليوم')
    keyboard.add('❓ مساعدة')
    await msg.reply('👋 أهلاً بك في بوت التداول الذكي!\nللاشتراك، اضغط على 🔑 تفعيل الاشتراك.', reply_markup=keyboard)

@dp.message()
async def handle_text(msg: types.Message):
    text = (msg.text or '').strip()
    if text == '❓ مساعدة':
        await msg.reply('💡 أوامر:\n🔑 تفعيل الاشتراك\n📊 آخر الصفقات\n📅 جدول اليوم\n❓ مساعدة')
        return

async def on_startup():
    init_db()
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, start_scheduler, lambda uid, sig: None)
    print('Bot started')

if __name__ == '__main__':
    asyncio.run(dp.start_polling(bot, on_startup=on_startup))