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
CONTACT_ANTON = "👨💼 Антон\nТелефон: +7 (967) 006-02-86\nTelegram: @AntonWenge"
# ---------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Хранилища
message_map = {}
user_requests = defaultdict(list)
user_contacts = {}
user_state = {}
stats = {"today": 0, "answered": 0, "total_response_time": timedelta()}

# Клавиатура для пользователей (основная)
BUTTONS = [
    [KeyboardButton("🚢 Рассчитать маршрут"), KeyboardButton("💰 Запросить ставку")],
    [KeyboardButton("📋 Мои заявки"), KeyboardButton("📋 Другое")],
]
reply_keyboard = ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)

# Кнопки-уточнители (не пересылаются, а вызывают подсказку)
DETAIL_BUTTONS = {"🚢 Рассчитать маршрут", "💰 Запросить ставку", "📋 Другое"}
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
    """Приветствие только по /start (или при первом касании, но не после каждого сообщения)."""
    user = update.effective_user
    # Показываем клавиатуру и приветствие
    await update.message.reply_text(WELCOME_TEXT, reply_markup=reply_keyboard)
    await update.message.reply_text(
        "Будьте в курсе новостей логистики:",
        reply_markup=get_channel_keyboard(),
    )
    # Инициализируем историю, если ещё нет
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

# Callback для отправки контактов админом
async def send_contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    manager = parts[1]
    user_id = int(parts[2])

    if manager == "valeria":
        text = CONTACT_VALERIA
        manager_name = "Валерии"
    else:
        text = CONTACT_ANTON
        manager_name = "Антона"

    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        await query.edit_message_text(f"✅ Контакты {manager_name} отправлены клиенту.")
    except Exception as e:
        logger.error(f"Ошибка отправки контактов клиенту {user_id}: {e}")
        await query.edit_message_text("❌ Не удалось отправить контакты. Возможно, клиент заблокировал бота.")

# Callback для оценки
async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, rating, user_id_str = query.data.split("_")
    user_id = int(user_id_str)
    if rating == "yes":
        text = "👍 Спасибо за вашу оценку!"
    else:
        text = "👎 Спасибо за обратную связь, мы постараемся улучшить сервис."
    await query.edit_message_text(text)

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    user_info = f"@{user.username}" if user.username else user.full_name

    # 1. Сбор контактов (если пользователь в состоянии ожидания)
    if user.id in user_state:
        state = user_state[user.id]
        if state == "awaiting_name":
            user_contacts.setdefault(user.id, {})["name"] = msg.text
            user_state[user.id] = "awaiting_phone"
            await msg.reply_text(
                "📞 Отправьте ваш номер телефона, чтобы мы могли оперативно связаться.",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                ),
            )
            return
        elif state == "awaiting_phone":
            if msg.contact:
                phone = msg.contact.phone_number
            else:
                phone = msg.text
            user_contacts.setdefault(user.id, {})["phone"] = phone
            del user_state[user.id]
            await msg.reply_text(
                "✅ Контакты сохранены! Теперь напишите ваш запрос, и мы сразу ответим.",
                reply_markup=reply_keyboard,
            )
            return

    # 2. Новый пользователь, ещё не дал контакты → запрашиваем
    if user.id not in user_contacts:
        user_state[user.id] = "awaiting_name"
        await msg.reply_text(
            "👤 Для начала, пожалуйста, представьтесь. Напишите ваше имя:",
            reply_markup=reply_keyboard,
        )
        return

    # 3. Кнопка «Мои заявки» (история)
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

    # 4. Кнопки-уточнители (не пересылаем, а просим уточнить)
    if msg.text and msg.text.strip() in DETAIL_BUTTONS:
        if msg.text.strip() == "📋 Другое":
            await msg.reply_text(
                "📋 Напишите, что вас интересует, или задайте ваш вопрос — мы ответим в ближайшее время.",
                reply_markup=reply_keyboard,
            )
        else:
            await msg.reply_text(
                "📋 Для отправки запроса, пожалуйста, укажите:\n"
                "• Что за груз (наименование, вес, объём)\n"
                "• Откуда и куда\n"
                "• Характеристики груза (опасный, температурный режим и пр.)\n"
                "• Условия поставки (EXW, FOB, FCA и т.д.)\n"
                "• Предпочтительный вид транспорта (авиа, ж/д, авто, море)\n\n"
                "Просто напишите всё, что знаете — мы оперативно рассчитаем.",
                reply_markup=reply_keyboard,
            )
        return

    # 5. Всё остальное — это настоящий запрос, пересылаем админам
    contact = user_contacts.get(user.id, {})
    name = contact.get("name", "")
    phone = contact.get("phone", "")
    contact_str = ""
    if name:
        contact_str += f"👤 {name}"
    if phone:
        contact_str += f" | 📞 {phone}"
    caption = f"📩 Сообщение от {user_info} (ID: {user.id})"
    if contact_str:
        caption += "\n" + contact_str

    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=caption,
        reply_to_message_id=forwarded.message_id,
    )

    # Текст для архива и истории
    if msg.text:
        content_text = msg.text
    elif msg.caption:
        content_text = f"[Файл] {msg.caption}"
    elif msg.photo:
        content_text = "[Фото]"
    elif msg.document:
        content_text = "[Документ]"
    elif msg.voice:
        content_text = "[Голосовое]"
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
        f"{contact_str}\n"
        f"💬 {content_text}"
    )
    try:
        await context.bot.send_message(chat_id=ARCHIVE_GROUP_ID, text=archive_text)
    except Exception as e:
        logger.error(f"Не удалось отправить в архив: {e}")

    request_time = datetime.now(timezone.utc)
    message_map[forwarded.message_id] = {
        "user_id": user.id,
        "answered": False,
        "request_time": request_time,
    }
    stats["today"] += 1

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

    # Команда /contacts с выбором менеджера
    if msg.text and msg.text.startswith("/contacts"):
        user_id = data["user_id"]
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👩💼 Валерия", callback_data=f"sendcontact_valeria_{user_id}"),
                InlineKeyboardButton("👨💼 Антон", callback_data=f"sendcontact_anton_{user_id}"),
            ]
        ])
        await msg.reply_text("Выберите менеджера для отправки контактов:", reply_markup=keyboard)
        return

    # Обычный ответ
    data["answered"] = True
    user_id = data["user_id"]
    try:
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен пользователю.")

        if "request_time" in data:
            response_time = datetime.now(timezone.utc) - data["request_time"]
            stats["total_response_time"] += response_time
            stats["answered"] += 1

        # Оценка
        rating_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👍", callback_data=f"rating_yes_{user_id}"),
                InlineKeyboardButton("👎", callback_data=f"rating_no_{user_id}"),
            ]
        ])
        await context.bot.send_message(
            chat_id=user_id,
            text="Понравился ли вам ответ?",
            reply_markup=rating_keyboard,
        )
    except Exception as e:
        logger.error(f"Ошибка отправки ответа пользователю {user_id}: {e}")
        await msg.reply_text("❌ Не удалось отправить ответ. Возможно, пользователь заблокировал бота.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    today = stats["today"]
    answered = stats["answered"]
    avg_time = (
        str(stats["total_response_time"] / answered).split(".")[0]
        if answered > 0
        else "—"
    )
    text = (
        f"📊 Статистика за сегодня:\n"
        f"• Заявок: {today}\n"
        f"• Отвечено: {answered}\n"
        f"• Среднее время ответа: {avg_time}"
    )
    await msg.reply_text(text)

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
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(send_contact_callback, pattern=r"^sendcontact_"))
    app.add_handler(CallbackQueryHandler(rating_callback, pattern=r"^rating_"))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_user_message,
    ))
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY,
        handle_admin_reply,
    ))

    logger.info("Бот запущен и готов к работе...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
