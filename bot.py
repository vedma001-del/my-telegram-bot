import os
import asyncio
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
import json

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

# ---------- НАСТРОЙКИ (замени на свои) ----------
BOT_TOKEN = "8906719433:AAEEMJHLQjw_W0mBmVd7Bgb2ummKfdhJWyY"
ADMIN_CHAT_ID = -1003725679213 
ARCHIVE_GROUP_ID = -1003908640963
CONTACTS_STORAGE_ID = -1003908640963  # ID этой же архивной группы или нового закрытого канала/чата
CHANNEL_USERNAME = "WengeGroup"
REMINDER_MINUTES = 30

CONTACT_VALERIA = "👩💼 Валерия\nТелефон: +7 (993) 903-36-33\nTelegram: @Valeria_Wenge"
CONTACT_ANTON = "👨💼 Антон\nТелефон: +7 (967) 006-02-86\nTelegram: @AntonWGR"
# ---------------------------------------------

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

message_map = {}
user_contacts_cache = {}
user_state = {}
stats = {"today": 0, "answered": 0, "total_response_time": timedelta()}

BUTTONS = ["💰 Запросить ставку", "📋 Другое", "📋 Мои заявки"]
DETAIL_BUTTONS = ["💰 Запросить ставку", "📋 Другое"]
HISTORY_BUTTON = "📋 Мои заявки"

ADMIN_STATS_BUTTON = "📊 Статистика"
ADMIN_CLOSE_BUTTON = "✅ Закрыть заявку"
ADMIN_VALERIA_BUTTON = "👩💼 Валерия"
ADMIN_ANTON_BUTTON = "👨💼 Антон"

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton(BUTTONS[0]), KeyboardButton(BUTTONS[1])],
        [KeyboardButton(BUTTONS[2])],
    ], resize_keyboard=True)

def get_admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton(ADMIN_STATS_BUTTON)],
        [KeyboardButton(ADMIN_VALERIA_BUTTON), KeyboardButton(ADMIN_ANTON_BUTTON)],
        [KeyboardButton(ADMIN_CLOSE_BUTTON)],
    ], resize_keyboard=True)

def get_phone_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 Поделиться номером", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

WELCOME_TEXT = "👋 Добро пожаловать в Wenge Group!\n\nВыберите, что вас интересует, или просто напишите свой запрос — мы ответим в ближайшее время."

def get_channel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")]])

async def load_contacts_from_archive(app):
    """Загружает контакты из архивного чата при старте."""
    global user_contacts_cache
    try:
        # Ищем последние 100 сообщений в чате хранения контактов
        updates = await app.bot.get_updates(offset=-100, timeout=1)
        for update in updates:
            if update.message and update.message.text and update.message.text.startswith("👤"):
                # Парсим сообщение формата: 👤 Имя | 📞 Телефон | ID: 123456
                text = update.message.text
                if "ID:" in text:
                    parts = text.split("|")
                    id_part = parts[-1].strip()
                    user_id = int(id_part.split(":")[-1].strip())
                    name_part = parts[0].replace("👤", "").strip()
                    phone = parts[1].replace("📞", "").strip() if len(parts) > 1 else ""
                    user_contacts_cache[user_id] = {"name": name_part, "phone": phone}
        logger.info(f"Загружено {len(user_contacts_cache)} контактов из архива.")
    except Exception as e:
        logger.error(f"Ошибка загрузки контактов: {e}")

async def save_contact_to_archive(context, user_id, name, phone):
    """Сохраняет контакт в архивный чат."""
    try:
        msg_text = f"👤 {name} | 📞 {phone} | ID: {user_id}"
        await context.bot.send_message(chat_id=CONTACTS_STORAGE_ID, text=msg_text)
        user_contacts_cache[user_id] = {"name": name, "phone": phone}
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения контакта: {e}")
        return False

async def remind_later(context, chat_id, message_id, delay_minutes):
    await asyncio.sleep(delay_minutes * 60)
    if message_map.get(message_id, {}).get("answered", False) is False:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ На эту заявку не ответили уже {delay_minutes} минут.", reply_to_message_id=message_id)
        except Exception as e:
            logger.error(f"Ошибка напоминания: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, reply_markup=get_user_keyboard())
    await update.message.reply_text("Будьте в курсе новостей логистики:", reply_markup=get_channel_keyboard())

async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "👍 Спасибо за ваш отзыв!" if "yes" in query.data else "👎 Спасибо за обратную связь!"
    await query.edit_message_text(text)

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message:
        return

    data = message_map.get(msg.reply_to_message.message_id)
    if not data:
        return

    user_id = data["user_id"]

    if msg.text == ADMIN_VALERIA_BUTTON:
        await context.bot.send_message(chat_id=user_id, text=CONTACT_VALERIA)
        await msg.reply_text("✅ Контакты Валерии отправлены.", reply_markup=get_admin_keyboard())
    elif msg.text == ADMIN_ANTON_BUTTON:
        await context.bot.send_message(chat_id=user_id, text=CONTACT_ANTON)
        await msg.reply_text("✅ Контакты Антона отправлены.", reply_markup=get_admin_keyboard())
    elif msg.text == ADMIN_CLOSE_BUTTON:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"rating_yes_{user_id}"), InlineKeyboardButton("👎", callback_data=f"rating_no_{user_id}")]])
        await context.bot.send_message(chat_id=user_id, text="Ваш запрос закрыт. Оцените качество обслуживания:", reply_markup=kb)
        await msg.reply_text("✅ Заявка закрыта.", reply_markup=get_admin_keyboard())
    else:
        data["answered"] = True
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен.", reply_markup=get_admin_keyboard())
        stats["answered"] += 1

async def handle_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == ADMIN_STATS_BUTTON:
        today = stats["today"]
        answered = stats["answered"]
        avg_time = str(stats["total_response_time"] / answered).split(".")[0] if answered else "—"
        await msg.reply_text(f"📊 Статистика за сегодня:\n• Заявок: {today}\n• Отвечено: {answered}\n• Среднее время ответа: {avg_time}", reply_markup=get_admin_keyboard())

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    user_info = f"@{user.username}" if user.username else user.full_name

    # Проверка контактов в кэше
    if user.id in user_contacts_cache:
        pass
    elif user.id in user_state:
        # Идет процесс регистрации
        state = user_state[user.id]
        if state == "awaiting_name":
            user_state[user.id] = {"state": "awaiting_phone", "name": msg.text}
            await msg.reply_text("📞 Отправьте ваш номер телефона.", reply_markup=get_phone_keyboard())
            return
        elif isinstance(state, dict) and state.get("state") == "awaiting_phone":
            phone = msg.contact.phone_number if msg.contact else msg.text
            await save_contact_to_archive(context, user.id, state["name"], phone)
            del user_state[user.id]
            await msg.reply_text("✅ Контакты сохранены! Выберите действие или напишите запрос.", reply_markup=get_user_keyboard())
            return
    else:
        # Новый пользователь
        user_state[user.id] = "awaiting_name"
        await msg.reply_text("👤 Добро пожаловать! Представьтесь, пожалуйста. Напишите ваше имя:", reply_markup=get_user_keyboard())
        return

    if msg.text == HISTORY_BUTTON:
        await msg.reply_text("📭 У вас пока нет отправленных заявок.")
        return

    if msg.text in DETAIL_BUTTONS:
        if msg.text == "📋 Другое":
            await msg.reply_text("📋 Напишите ваш вопрос — мы ответим в ближайшее время.", reply_markup=get_user_keyboard())
        else:
            await msg.reply_text(
                "📋 Для расчёта ставки, пожалуйста, укажите:\n"
                "• Что за груз (наименование, вес, объём)\n"
                "• Откуда и куда\n"
                "• Характеристики груза\n"
                "• Условия поставки (EXW, FOB, FCA)\n"
                "• Вид транспорта\n\n"
                "Напишите всё, что знаете — мы оперативно рассчитаем.",
                reply_markup=get_user_keyboard()
            )
        return

    content_text = msg.text or "[Сообщение]"
    if msg.caption: content_text = f"[Файл] {msg.caption}"
    elif msg.photo: content_text = "[Фото]"
    elif msg.document: content_text = "[Документ]"
    elif msg.voice: content_text = "[Голосовое]"
    elif msg.video: content_text = "[Видео]"

    caption = f"📩 {user_info} (ID: {user.id})"
    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=caption, reply_to_message_id=forwarded.message_id)

    stats["today"] += 1
    message_map[forwarded.message_id] = {"user_id": user.id, "answered": False}
    asyncio.create_task(remind_later(context, ADMIN_CHAT_ID, forwarded.message_id, REMINDER_MINUTES))
    await msg.reply_text("✅ Сообщение отправлено. Ожидайте ответа.", reply_markup=get_user_keyboard())

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), HealthHandler).serve_forever()

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Загружаем контакты из архива при старте
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(load_contacts_from_archive(app))
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(rating_callback, pattern=r"^rating_"))
    app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.TEXT & ~filters.COMMAND, handle_admin_stats), group=0)
    app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY, handle_admin_reply), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_user_message), group=2)
    
    logger.info("Бот запущен и готов к работе...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
