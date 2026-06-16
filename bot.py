import os
import asyncio
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# ---------- НАСТРОЙКИ (замените на свои) ----------
BOT_TOKEN = "8906719433:AAEEMJHLQjw_W0mBmVd7Bgb2ummKfdhJWyY"
ADMIN_CHAT_ID = -1003725679213
# -------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Здесь хранится связь: ID пересланного сообщения → ID пользователя, которому отвечать
message_map = {}

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Любые ошибки просто логируются, бот не падает."""
    logger.error(msg="Ошибка:", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие на /start."""
    await update.message.reply_text(
        "👋 Добро пожаловать в Wenge Group!\n\n"
        "Просто напишите ваш вопрос, и мы ответим в ближайшее время."
    )

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пересылает сообщение клиента в админский чат."""
    user = update.effective_user
    msg = update.message

    # Формируем подпись с именем пользователя
    user_info = f"@{user.username}" if user.username else user.full_name
    caption = f"📩 Сообщение от {user_info} (ID: {user.id})"

    # Пересылаем сообщение в админский чат
    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    # Запоминаем, какому пользователю нужно ответить, когда админ ответит на это сообщение
    message_map[forwarded.message_id] = user.id
    # Отправляем текстовую подпись отдельным сообщением, привязанным к пересланному
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=caption,
        reply_to_message_id=forwarded.message_id,
    )
    # Подтверждение клиенту
    await msg.reply_text("✅ Ваше сообщение отправлено. Ожидайте ответа.")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пересылает ответ админа обратно клиенту."""
    msg = update.message
    # Если админ отправил сообщение без reply (не ответ на пересланное), игнорируем
    if not msg.reply_to_message:
        return

    # Находим ID клиента, которому нужно ответить
    user_id = message_map.get(msg.reply_to_message.message_id)
    if not user_id:
        await msg.reply_text("❌ Не удалось определить клиента. Убедитесь, что отвечаете на пересланное сообщение.")
        return

    # Пересылаем ответ клиенту (поддерживаются текст, фото, файлы и т.д.)
    try:
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен клиенту.")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа: {e}")
        await msg.reply_text("❌ Не удалось отправить ответ.")

# Фиктивный веб-сервер, чтобы Render видел, что сервис жив
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

async def cleanup(app):
    """Сбрасывает старые подключения, чтобы не было ошибок Conflict."""
    await app.bot.delete_webhook(drop_pending_updates=True)
    logger.info("Вебхук удалён, pending updates сброшены.")

def main():
    # Запускаем веб-сервер в фоновом потоке, чтобы Render не ругался
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    # Сброс старых подключений перед запуском
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(cleanup(app))

    app.add_error_handler(error_handler)

    # Обработчики
    app.add_handler(CommandHandler("start", start))
    # Ответы админов (только в админском чате, только reply)
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY,
        handle_admin_reply
    ))
    # Сообщения от клиентов (личные сообщения боту, кроме команд)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_user_message
    ))

    logger.info("Бот запущен и готов к работе...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
