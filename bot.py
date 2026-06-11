import os
import asyncio
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)

# ---------- НАСТРОЙКИ (замените на свои) ----------
BOT_TOKEN = "8906719433:AAHsjj0c1JxGwheqHH4-J0pr0sOlPEwPSqw"
ADMIN_CHAT_ID = -1003725679213       # ID группы администраторов (куда пересылаются запросы)
ARCHIVE_GROUP_ID = -1003908640963    # ID группы-архива (куда дублируются заявки)
CHANNEL_USERNAME = "WengeGroup"  # юзернейм канала (без @)
REMINDER_MINUTES = 30

# Контакты менеджеров
CONTACT_VALERIA = "👩💼 Валерия\nТелефон: +7 (993) 903-36-33\nTelegram: @Valeria_Wenge"
CONTACT_ANTON = "👨💼 Антон\nТелефон: +7 (967) 006-02-86\nTelegram: @AntonWGR"
# ---------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Хранилища
message_map = {}  # {forwarded_msg_id: {"user_id": uid, "answered": False, "request_time": datetime}}
user_requests = defaultdict(list)  # история сообщений пользователя
user_contacts = {}  # контакты пользователей
user_state = {}  # состояние сбора контактов
active_requests = {}  # {user_id: {"archive_msg_id": id, "status": "🆕 Новый", "messages": []}}
stats = {"today": 0, "answered": 0, "total_response_time": timedelta()}

# Клавиатура для пользователей (без «Рассчитать маршрут»)
BUTTONS = [
    [KeyboardButton("💰 Запросить ставку"), KeyboardButton("📋 Другое")],
    [KeyboardButton("📋 Мои заявки")],
]
reply_keyboard = ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)

DETAIL_BUTTONS = {"💰 Запросить ставку", "📋 Другое"}
HISTORY_BUTTON = "📋 Мои заявки"

WELCOME_TEXT = (
    "👋 Добро пожаловать в Wenge Group!\n\n"
    "Выберите, что вас интересует, или просто напишите свой запрос — мы ответим в ближайшее время."
)

def get_channel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")]
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(WELCOME_TEXT, reply_markup=reply_keyboard)
    await update.message.reply_text(
        "Будьте в курсе новостей логистики:",
        reply_markup=get_channel_keyboard(),
    )
    if user.id not in user_requests:
        user_requests[user.id] = []

def save_to_history(user_id, text):
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
                reply_to_message_id=message_id,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки напоминания: {e}")

async def update_archive(context, user_id, status, new_message=None):
    """Обновляет сообщение в архивной группе."""
    if user_id not in active_requests:
        return
    try:
        msg_id = active_requests[user_id]["archive_msg_id"]
        msgs = active_requests[user_id]["messages"]
        if new_message:
            msgs.append(new_message)
        contact = user_contacts.get(user_id, {})
        name = contact.get("name", "Неизвестный")
        phone = contact.get("phone", "")
        contact_str = f"👤 {name}"
        if phone:
            contact_str += f" | 📞 {phone}"
        
        # Формируем текст с историей сообщений
        history = "\n".join(msgs[-10:])  # последние 10 сообщений
        text = (
            f"📥 Заявка #{user_id}\n"
            f"👤 {contact_str}\n"
            f"Статус: {status}\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"---\n"
            f"{history}"
        )
        await context.bot.edit_message_text(
            chat_id=ARCHIVE_GROUP_ID,
            message_id=msg_id,
            text=text
        )
        active_requests[user_id]["status"] = status
    except Exception as e:
        logger.error(f"Не удалось обновить архив: {e}")

async def send_contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    manager = parts[1]
    user_id = int(parts[2])
    text = CONTACT_VALERIA if manager == "valeria" else CONTACT_ANTON
    manager_name = "Валерии" if manager == "valeria" else "Антона"
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        await query.edit_message_text(f"✅ Контакты {manager_name} отправлены клиенту.")
    except Exception as e:
        logger.error(f"Ошибка отправки контактов: {e}")
        await query.edit_message_text("❌ Не удалось отправить контакты.")

async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, rating, user_id_str = query.data.split("_")
    if rating == "yes":
        text = "👍 Спасибо за ваш отзыв! Рады, что всё прошло успешно."
    else:
        text = "👎 Спасибо за обратную связь, мы постараемся улучшить сервис."
    await query.edit_message_text(text)

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    user_info = f"@{user.username}" if user.username else user.full_name

    # Сбор контактов
    if user.id in user_state:
        state = user_state[user.id]
        if state == "awaiting_name":
            user_contacts.setdefault(user.id, {})["name"] = msg.text
            user_state[user.id] = "awaiting_phone"
            await msg.reply_text(
                "📞 Отправьте ваш номер телефона.",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True,
                ),
            )
            return
        elif state == "awaiting_phone":
            phone = msg.contact.phone_number if msg.contact else msg.text
            user_contacts.setdefault(user.id, {})["phone"] = phone
            del user_state[user.id]
            await msg.reply_text(
                "✅ Контакты сохранены! Теперь напишите ваш запрос.",
                reply_markup=reply_keyboard,
            )
            return

    # Новый пользователь
    if user.id not in user_contacts:
        user_state[user.id] = "awaiting_name"
        await msg.reply_text("👤 Представьтесь, пожалуйста. Напишите ваше имя:", reply_markup=reply_keyboard)
        return

    # История
    if msg.text and msg.text.strip() == HISTORY_BUTTON:
        requests = user_requests.get(user.id, [])
        if not requests:
            await msg.reply_text("📭 У вас пока нет отправленных заявок.")
        else:
            last = requests[-5:]
            text = "📋 Ваши последние заявки:\n\n"
            for i, req in enumerate(last, 1):
                text += f"{i}. [{req['date']}] {req['text']}\n"
            await msg.reply_text(text)
        return

    # Кнопки-уточнители
    if msg.text and msg.text.strip() in DETAIL_BUTTONS:
        if msg.text.strip() == "📋 Другое":
            await msg.reply_text("📋 Напишите ваш вопрос — мы ответим в ближайшее время.", reply_markup=reply_keyboard)
        else:
            await msg.reply_text(
                "📋 Для расчёта ставки, пожалуйста, укажите:\n"
                "• Что за груз (наименование, вес, объём)\n"
                "• Откуда и куда\n"
                "• Характеристики груза\n"
                "• Условия поставки (EXW, FOB, FCA)\n"
                "• Вид транспорта\n\n"
                "Напишите всё, что знаете — мы оперативно рассчитаем.",
                reply_markup=reply_keyboard,
            )
        return

    # Обычное сообщение → это продолжение заявки или новый запрос
    content_text = msg.text or "[Сообщение]"
    if msg.caption:
        content_text = f"[Файл] {msg.caption}"
    elif msg.photo: content_text = "[Фото]"
    elif msg.document: content_text = "[Документ]"
    elif msg.voice: content_text = "[Голосовое]"
    elif msg.video: content_text = "[Видео]"

    save_to_history(user.id, content_text)

    # Пересылаем админам
    caption = f"📩 {user_info} (ID: {user.id})"
    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=caption, reply_to_message_id=forwarded.message_id)

    # Архив: создаём или обновляем
    if user.id not in active_requests:
        # Новая заявка
        contact = user_contacts.get(user.id, {})
        name = contact.get("name", "Неизвестный")
        phone = contact.get("phone", "")
        contact_str = f"👤 {name}"
        if phone: contact_str += f" | 📞 {phone}"
        archive_msg = await context.bot.send_message(
            chat_id=ARCHIVE_GROUP_ID,
            text=f"📥 Заявка #{user.id}\n{contact_str}\nСтатус: 🆕 Новый\n---\n{content_text}"
        )
        active_requests[user.id] = {
            "archive_msg_id": archive_msg.message_id,
            "status": "🆕 Новый",
            "messages": [content_text]
        }
    else:
        # Обновляем существующую
        active_requests[user.id]["status"] = "🔄 В работе"
        await update_archive(context, user.id, "🔄 В работе", content_text)

    message_map[forwarded.message_id] = {
        "user_id": user.id,
        "answered": False,
        "request_time": datetime.now(timezone.utc),
    }
    stats["today"] += 1
    asyncio.create_task(remind_later(context, ADMIN_CHAT_ID, forwarded.message_id, REMINDER_MINUTES))
    await msg.reply_text("✅ Сообщение отправлено. Ожидайте ответа.")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message:
        return
    original_msg_id = msg.reply_to_message.message_id
    data = message_map.get(original_msg_id)
    if not data:
        return

    user_id = data["user_id"]

    # Команда /contacts
    if msg.text and msg.text.startswith("/contacts"):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👩💼 Валерия", callback_data=f"sendcontact_valeria_{user_id}"),
             InlineKeyboardButton("👨💼 Антон", callback_data=f"sendcontact_anton_{user_id}")]
        ])
        await msg.reply_text("Выберите менеджера:", reply_markup=keyboard)
        return

    # Команда /close – закрыть заявку и запросить отзыв
    if msg.text and msg.text.startswith("/close"):
        await update_archive(context, user_id, "✅ Закрыт")
        # Отправляем отзыв клиенту
        rating_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👍", callback_data=f"rating_yes_{user_id}"),
             InlineKeyboardButton("👎", callback_data=f"rating_no_{user_id}")]
        ])
        try:
            await context.bot.send_message(chat_id=user_id, text="Ваш запрос закрыт. Оцените качество обслуживания:", reply_markup=rating_kb)
            await msg.reply_text("✅ Заявка закрыта, клиенту отправлен запрос на отзыв.")
        except Exception as e:
            logger.error(f"Ошибка при закрытии: {e}")
        return

    # Обычный ответ
    data["answered"] = True
    try:
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен.")
        if "request_time" in data:
            stats["total_response_time"] += datetime.now(timezone.utc) - data["request_time"]
            stats["answered"] += 1
        # Обновляем статус в архиве на «В работе»
        if user_id in active_requests:
            await update_archive(context, user_id, "🔄 В работе")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа: {e}")
        await msg.reply_text("❌ Не удалось отправить ответ.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    today = stats["today"]
    answered = stats["answered"]
    avg_time = str(stats["total_response_time"] / answered).split(".")[0] if answered else "—"
    await msg.reply_text(f"📊 Статистика за сегодня:\n• Заявок: {today}\n• Отвечено: {answered}\n• Среднее время ответа: {avg_time}")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(send_contact_callback, pattern=r"^sendcontact_"))
    app.add_handler(CallbackQueryHandler(rating_callback, pattern=r"^rating_"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_user_message))
    app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY, handle_admin_reply))
    logger.info("Бот запущен и готов к работе...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
