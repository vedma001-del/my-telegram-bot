import os
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ---------- НАСТРОЙКИ (замените на свои) ----------
BOT_TOKEN = "8906719433:AAHsjj0c1JxGwheqHH4-J0pr0sOlPEwPSqw"
ADMIN_CHAT_ID = -1003725679213  # ID вашей группы с минусом
# ---------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

message_map = {}

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    user_info = f"@{user.username}" if user.username else user.full_name
    caption = f"📩 Сообщение от {user_info} (ID: {user.id})"

    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=caption,
        reply_to_message_id=forwarded.message_id
    )

    message_map[forwarded.message_id] = user.id
    await msg.reply_text("✅ Ваше сообщение отправлено администраторам. Ожидайте ответа.")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message:
        return
    original_msg_id = msg.reply_to_message.message_id
    user_id = message_map.get(original_msg_id)
    if not user_id:
        return
    try:
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен пользователю.")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа пользователю {user_id}: {e}")
        await msg.reply_text("❌ Не удалось отправить ответ. Возможно, пользователь заблокировал бота.")

# Фиктивный веб-сервер для Render (отвечает на health check)
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

def main():
    # Запускаем веб-сервер в отдельном потоке ДО запуска бота
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_user_message
    ))
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY,
        handle_admin_reply
    ))

    logger.info("Бот запущен и готов к работе...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
