import os
import asyncio
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
REMINDER_MINUTES = 30               # через сколько минут напоминать
# ---------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

message_map = {}         # {forwarded_msg_id: {"user_id": uid, "answered": False}}
user_requests = {}       # {user_id: [{"date": "...", "text": "..."}, ...]}

BUTTONS = [
    [KeyboardButton("🚢 Рассчитать маршрут"), KeyboardButton("💰 Запросить ставку")],
    [KeyboardButton("📞 Связаться с менеджером"), KeyboardButton("📋 Другое")],
    [KeyboardButton("📋 Мои заявки")]
]
reply_keyboard = ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True, one_time_keyboard=False)

DETAIL_BUTTONS = {"🚢 Рассчитать маршрут", "💰 Запросить ставку"}
HISTORY_BUTTON = "📋 Мои заявки"

WELCOME_TEXT = (
    "👋 Добро пожаловать в Wenge Group!\n\n"
    "Выберите, что вас интересует, или просто напишите свой запрос — мы ответим в ближайшее время."
)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительный показ меню (если пользователь всё же напишет /start)."""
    await update.message.reply_text(WELCOME_TEXT, reply_markup=reply_keyboard)

def save_to_history(user_id, text):
    if user_id not in user_requests:
        user_requests[user_id] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    user_requests[user_id].append({"date": now, "text": text})

async def remind_later(context, chat_id, message_id, delay_minutes):
    await asyncio.sleep(delay_minutes * 60)
    data = message_map.get(message_id)
    if data and not data.get("answered", False):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ На эту заявку не ответили уже {delay_minutes} минут.",
                reply_to_message_id=message_id
            )
        except Exception as e:
            logger.error(f"Ошибка отправки напоминания: {e}")

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    user_info = f"@{user.username}" if user.username else user.full_name

    # Если пользователь новый (ещё нет истории), показываем приветствие с кнопками,
    # но не пересылаем админам это первое сообщение
    if user.id not in user_requests:
        # Отправляем приветствие с клавиатурой
        await msg.reply_text(WELCOME_TEXT, reply_markup=reply_keyboard)
        # Записываем пустую историю, чтобы больше не срабатывало
        user_requests[user.id] = []
        # Если это была именно команда /start, то она уже обработана через CommandHandler,
        # но на всякий случай оставим. Не пересылаем.
        return

    # Кнопка «Мои заявки»
    if msg.text and msg.text.strip() == HISTORY_BUTTON:
        requests = user_requests.get(user.id, [])
        if not requests:
            await msg.reply_text("📭 У вас пока нет отправленных заявок.")
        else:
            last_requests = requests[-5:]
            text = "📋 Ваши последние заявки:\n\n"
            for i, req in enumerate(last_requests, 1):
                text += f"{i}. [{req['date']}] {req['text']}\n"
            await msg.reply_text(text)
        return

    # Кнопки, требующие уточнения
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
        return

    # --- Обработка обычного запроса (текст, файлы и т.д.) ---
    caption = f"📩 Сообщение от {user_info} (ID: {user.id})"
    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=caption,
        reply_to_message_id=forwarded.message_id
    )

    # Определяем текстовое представление для истории и архива
    if msg.text:
        content_text = msg.text
    elif msg.caption:
        content_text = f"[Фото/файл] {msg.caption}"
    elif msg.photo:
        content_text = "[Фото]"
    elif msg.document:
        content_text = "[Документ]"
    elif msg.voice:
        content_text = "[Голосовое сообщение]"
    elif msg.video:
        content_text = "[Видео]"
    else:
        content_text = "[Сообщение]"

    save_to_history(user.id, content_text)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    archive_text = (
        f"📥 Новая заявка\n"
        f"🕒 {now}\n"
        f"👤 {user_info} (ID: {user.id})\n"
        f"💬 {content_text}"
    )
    try:
        await context.bot.send_message(chat_id=ARCHIVE_GROUP_ID, text=archive_text)
    except Exception as e:
        logger.error(f"Не удалось отправить в архив: {e}")

    message_map[forwarded.message_id] = {
        "user_id": user.id,
        "answered": False
    }
    asyncio.create_task(
        remind_later(context, ADMIN_CHAT_ID, forwarded.message_id, REMINDER_MINUTES)
    )

    await msg.reply_text("✅ Ваше сообщение отправлено администраторам. Ожидайте ответа.")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message:
        return
    original_msg_id = msg.reply_to_message.message_id
    data = message_map.get(original_msg_id)
    if not data:
        return
    data["answered"] = True
    user_id = data["user_id"]
    try:
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен пользователю.")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа пользователю {user_id}: {e}")
        await msg.reply_text("❌ Не удалось отправить ответ. Возможно, пользователь заблокировал бота.")

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
