import os
import asyncio
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler, CallbackQueryHandler

# ---------- НАСТРОЙКИ (замените на свои) ----------
BOT_TOKEN = "8906719433:AAEEMJHLQjw_W0mBmVd7Bgb2ummKfdhJWyY"
ADMIN_CHAT_ID = -1003725679213
CONTACTS_STORAGE_ID = -1003908640963
# -------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

user_contacts_cache = {}
user_state = {}
message_map = {}  # {forwarded_msg_id: user_id}

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💰 Запросить ставку"), KeyboardButton("📋 Другое")],
    ], resize_keyboard=True)

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
        logger.info(f"✅ Загружено {len(user_contacts_cache)} контактов")
    except Exception as e:
        logger.warning(f"Ошибка загрузки контактов: {e}")

async def save_contact(context, user_id, name):
    try:
        await context.bot.send_message(chat_id=CONTACTS_STORAGE_ID, text=f"👤 Контакт: {name} | ID: {user_id}")
        user_contacts_cache[user_id] = {"name": name}
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Ошибка при обработке:", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в Wenge Group!\n\n"
        "Выберите, что вас интересует, или просто напишите свой вопрос — мы ответим в ближайшее время.",
        reply_markup=get_user_keyboard()
    )

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    # 1. Проверка контактов
    if user.id in user_contacts_cache:
        pass  # уже знакомы
    elif user.id in user_state:
        # ожидаем имя
        name = msg.text
        await save_contact(context, user.id, name)
        del user_state[user.id]
        await msg.reply_text(f"✅ Спасибо, {name}! Задайте ваш вопрос.", reply_markup=get_user_keyboard())
        return
    else:
        # новый пользователь → просим имя
        user_state[user.id] = "awaiting_name"
        await msg.reply_text("👤 Добро пожаловать! Напишите ваше имя:", reply_markup=get_user_keyboard())
        return

    # 2. Кнопки
    if msg.text == "📋 Другое":
        await msg.reply_text("📋 Напишите ваш вопрос — мы ответим в ближайшее время.")
        return
    if msg.text == "💰 Запросить ставку":
        await msg.reply_text(
            "📋 Для расчёта ставки, пожалуйста, укажите:\n"
            "• Что за груз (наименование, вес, объём)\n"
            "• Откуда и куда\n"
            "• Характеристики груза\n"
            "• Условия поставки (EXW, FOB, FCA)\n"
            "• Вид транспорта\n\n"
            "Напишите всё, что знаете — мы оперативно рассчитаем."
        )
        return

    # 3. Пересылаем запрос в админский чат
    user_info = f"@{user.username}" if user.username else user.full_name
    caption = f"📩 {user_info} (ID: {user.id})"
    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    # Сохраняем связку: ID пересланного сообщения → ID клиента
    message_map[forwarded.message_id] = user.id
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=caption, reply_to_message_id=forwarded.message_id)
    await msg.reply_text("✅ Сообщение отправлено. Ожидайте ответа.")

async def handle_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message:
        return

    # Ищем ID клиента в нашем словаре
    original_msg_id = msg.reply_to_message.message_id
    user_id = message_map.get(original_msg_id)

    if not user_id:
        await msg.reply_text("❌ Не удалось определить клиента. Убедитесь, что отвечаете на пересланное сообщение.")
        return

    # Команда /close
    if msg.text and msg.text.startswith("/close"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👍", callback_data=f"yes_{user_id}"),
             InlineKeyboardButton("👎", callback_data=f"no_{user_id}")]
        ])
        await context.bot.send_message(chat_id=user_id, text="Оцените качество обслуживания:", reply_markup=kb)
        await msg.reply_text("✅ Заявка закрыта.")
        return

    # Обычный ответ клиенту
    try:
        # Пересылаем сообщение админа клиенту
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен клиенту.")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа: {e}")
        await msg.reply_text("❌ Не удалось отправить ответ.")

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

    # Загружаем контакты
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(load_contacts(app))

    # Добавляем обработчик ошибок
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(rating, pattern=r"^yes_|^no_"))
    app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY, handle_admin), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_user), group=2)

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
