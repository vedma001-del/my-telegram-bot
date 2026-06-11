import os
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

# ---------- НАСТРОЙКИ (замените на свои) ----------
BOT_TOKEN = "8906719433:AAHsjj0c1JxGwheqHH4-J0pr0sOlPEwPSqw"
ADMIN_CHAT_ID = -1003725679213       # ID группы администраторов (куда пересылаются запросы)
ARCHIVE_GROUP_ID = -1003908640963    # ID группы-архива (куда дублируются заявки)
# ---------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

message_map = {}

# Клавиатура с кнопками для пользователей
BUTTONS = [
    [KeyboardButton("🚢 Рассчитать маршрут"), KeyboardButton("💰 Запросить ставку")],
    [KeyboardButton("📞 Связаться с менеджером"), KeyboardButton("📋 Другое")]
]
reply_keyboard = ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True, one_time_keyboard=False)

# Список кнопок, требующих уточнения
DETAIL_BUTTONS = {"🚢 Рассчитать маршрут", "💰 Запросить ставку"}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и показ кнопок."""
    await update.message.reply_text(
        "👋 Добро пожаловать в Wenge Group!\n\n"
        "Выберите, что вас интересует, или просто напишите свой запрос — мы ответим в ближайшее время.",
        reply_markup=reply_keyboard
    )

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    user_info = f"@{user.username}" if user.username else user.full_name

    # Если нажата одна из кнопок «Рассчитать маршрут» или «Запросить ставку»,
    # просим уточнить данные и не пересылаем админам
    if msg.text and msg.text.strip() in DETAIL_BUTTONS:
        await msg.reply_text(
            "📋 Для отправки запроса, пожалуйста, укажите:\n"
            "• Что за груз (наименование, вес, объём)\n"
            "• Откуда и куда\n"
            "• Характеристики груза (опасный, температурный режим и пр.)\n"
            "• Условия поставки (EXW, FOB, FCA и т.д.)\n"
            "• Предпочтительный вид транспорта (авиа, ж/д, авто, море)\n\n"
            "Просто напишите всё, что знаете — мы оперативно рассчитаем.",
            reply_markup=reply_keyboard
        )
        return  # останавливаемся, не идём в пересылку

    # Если сообщение не кнопка-запрос — обрабатываем как обычную заявку
    caption = f"📩 Сообщение от {user_info} (ID: {user.id})"

    # 1. Пересылаем в админский чат
    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=caption,
        reply_to_message_id=forwarded.message_id
    )

    # 2. Сохраняем в архивную группу
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    archive_text = (
        f"📥 Новая заявка\n"
        f"🕒 {now}\n"
        f"👤 {user_info} (ID: {user.id})\n"
        f"💬 {msg.text or '[не текст]'}"
    )
    try:
        await context.bot.send_message(chat_id=ARCHIVE_GROUP_ID, text=archive_text)
    except Exception as e:
        logger.error(f"Не удалось отправить в архив: {e}")

    # 3. Запоминаем связку для ответа
    message_map[forwarded.message_id] = user.id

    # 4. Подтверждение клиенту
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

# Фиктивный веб-сервер для Render
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
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
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
