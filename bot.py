import os
import asyncio
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler, CallbackQueryHandler

# ---------- НАСТРОЙКИ ----------
BOT_TOKEN = "8906719433:AAEEMJHLQjw_W0mBmVd7Bgb2ummKfdhJWyY"
ADMIN_CHAT_ID = -1003725679213
CONTACTS_STORAGE_ID = -1003908640963
# --------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

user_contacts_cache = {}
user_state = {}

def get_user_keyboard():
    buttons = [
        [KeyboardButton("💰 Запросить ставку"), KeyboardButton("📋 Другое")],
        [KeyboardButton("📋 Мои заявки")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

async def load_contacts(app):
    global user_contacts_cache
    try:
        updates = await app.bot.get_updates(offset=-100, timeout=5)
        if updates:
            for u in updates:
                if u.message and u.message.text and u.message.text.startswith("👤 Контакт:"):
                    try:
                        text = u.message.text
                        id_part = text.split("ID:")[1].strip()
                        user_id = int(id_part)
                        name_part = text.split("👤 Контакт:")[1].split("|")[0].strip()
                        user_contacts_cache[user_id] = {"name": name_part}
                    except:
                        continue
        logger.info(f"Загружено {len(user_contacts_cache)} контактов.")
    except Exception as e:
        logger.warning(f"Ошибка загрузки: {e}")

async def save_contact(context, user_id, name):
    try:
        await context.bot.send_message(chat_id=CONTACTS_STORAGE_ID, text=f"👤 Контакт: {name} | ID: {user_id}")
        user_contacts_cache[user_id] = {"name": name}
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Добро пожаловать! Выберите действие.", reply_markup=get_user_keyboard())

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    # Проверяем, есть ли контакты
    if user.id in user_contacts_cache:
        pass
    elif user.id in user_state:
        # Сохраняем имя
        name = msg.text
        await save_contact(context, user.id, name)
        del user_state[user.id]
        await msg.reply_text(f"✅ Спасибо, {name}! Напишите ваш вопрос.", reply_markup=get_user_keyboard())
        return
    else:
        # Запрашиваем имя
        user_state[user.id] = "awaiting_name"
        await msg.reply_text("👤 Добро пожаловать! Напишите ваше имя:", reply_markup=get_user_keyboard())
        return

    # Кнопки
    if msg.text == "📋 Другое":
        await msg.reply_text("📋 Напишите ваш вопрос.")
        return
    if msg.text == "💰 Запросить ставку":
        await msg.reply_text("📋 Для расчёта ставки, пожалуйста, укажите:\n"
                "• Что за груз (наименование, вес, объём)\n"
                "• Откуда и куда\n"
                "• Характеристики груза\n"
                "• Условия поставки (EXW, FOB, FCA)\n"
                "• Вид транспорта\n\n"
                "Напишите всё, что знаете — мы оперативно рассчитаем.")
        return

    # Пересылаем запрос
    user_info = f"@{user.username}" if user.username else user.full_name
    caption = f"📩 {user_info} (ID: {user.id})"
    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=caption, reply_to_message_id=forwarded.message_id)
    await msg.reply_text("✅ Сообщение отправлено. Ожидайте ответа.")

async def handle_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message: return

    # Определяем ID клиента
    user_id = None
    if msg.reply_to_message.forward_origin:
        user_id = msg.reply_to_message.forward_origin.sender_user.id
    
    if not user_id: return

    # Команда /close
    if msg.text and msg.text.startswith("/close"):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"yes_{user_id}"), InlineKeyboardButton("👎", callback_data=f"no_{user_id}")]])
        await context.bot.send_message(chat_id=user_id, text="Оцените качество обслуживания:", reply_markup=kb)
        await msg.reply_text("✅ Заявка закрыта.")
        return

    # Обычный ответ
    try:
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен.")
    except:
        await msg.reply_text("❌ Ошибка.")

async def rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👍 Спасибо за отзыв!")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), HealthHandler).serve_forever()

def main():
    threading.Thread(target=run_health, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(load_contacts(app))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(rating, pattern=r"^yes_|^no_"))
    app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY, handle_admin), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_user), group=2)

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

