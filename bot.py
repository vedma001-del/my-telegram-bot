import os
import asyncio
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

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

# ---------- НАСТРОЙКИ ----------
BBOT_TOKEN = "8906719433:AAEEMJHLQjw_W0mBmVd7Bgb2ummKfdhJWyY"
ADMIN_CHAT_ID = -1003725679213 
ARCHIVE_GROUP_ID = -1003908640963
CONTACTS_STORAGE_ID = -1003908640963  # ID этой же архивной группы или нового закрытого канала/чата
CHANNEL_USERNAME = "WengeGroup"
REMINDER_MINUTES = 30

CONTACT_VALERIA = "👩💼 Валерия\nТелефон: +7 (993) 903-36-33\nTelegram: @Valeria_Wenge"
CONTACT_ANTON = "👨💼 Антон\nТелефон: +7 (967) 006-02-86\nTelegram: @AntonWGR"
# --------------------------------

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилища
message_map = {}
user_state = {}
user_contacts_cache = {}  # {user_id: {"name": ..., "phone": ...}}
active_requests = {}      # {user_id: {"archive_msg_id": ..., "status": ..., "messages": [...]}}
stats = {"today": 0, "answered": 0, "total_response_time": timedelta()}

BUTTONS = ["💰 Запросить ставку", "📋 Другое", "📋 Мои заявки"]
DETAIL_BUTTONS = ["💰 Запросить ставку", "📋 Другое"]
HISTORY_BUTTON = "📋 Мои заявки"

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton(BUTTONS[0]), KeyboardButton(BUTTONS[1])],
        [KeyboardButton(BUTTONS[2])],
    ], resize_keyboard=True)

def get_phone_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 Поделиться номером", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

WELCOME_TEXT = "👋 Добро пожаловать в Wenge Group!\n\nВыберите, что вас интересует, или просто напишите свой запрос — мы ответим в ближайшее время."

def get_channel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")]])

# --- Загрузка контактов из Telegram при старте ---
async def load_contacts_from_telegram(app):
    """Загружает контакты из архивного чата при запуске."""
    global user_contacts_cache
    try:
        updates = await app.bot.get_updates(offset=-100, timeout=5)
        if updates:
            for u in updates:
                if u.message and u.message.text and u.message.text.startswith("👤 Контакт:"):
                    try:
                        # Формат: 👤 Контакт: Имя | 📞 Телефон | ID: 123456
                        text = u.message.text
                        id_part = text.split("ID:")[1].strip()
                        user_id = int(id_part)
                        name_part = text.split("👤 Контакт:")[1].split("|")[0].strip()
                        phone_part = text.split("📞")[1].split("|")[0].strip() if "📞" in text else ""
                        user_contacts_cache[user_id] = {"name": name_part, "phone": phone_part}
                    except:
                        continue
        logger.info(f"✅ Загружено {len(user_contacts_cache)} контактов из Telegram.")
    except Exception as e:
        logger.warning(f"Не удалось загрузить контакты: {e}")

# --- Сохранение контакта в Telegram ---
async def save_contact_to_telegram(context, user_id, name, phone):
    try:
        text = f"👤 Контакт: {name} | 📞 {phone} | ID: {user_id}"
        await context.bot.send_message(chat_id=CONTACTS_STORAGE_ID, text=text)
        user_contacts_cache[user_id] = {"name": name, "phone": phone}
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения контакта: {e}")
        return False

# --- Напоминания ---
async def remind_later(context, chat_id, message_id, delay_minutes):
    await asyncio.sleep(delay_minutes * 60)
    if message_map.get(message_id, {}).get("answered", False) is False:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ На эту заявку не ответили уже {delay_minutes} минут.", reply_to_message_id=message_id)
        except: pass

# --- Команда /start ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, reply_markup=get_user_keyboard())
    await update.message.reply_text("Будьте в курсе новостей логистики:", reply_markup=get_channel_keyboard())

# --- Оценка ---
async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "👍 Спасибо за ваш отзыв!" if "yes" in query.data else "👎 Спасибо за обратную связь!"
    await query.edit_message_text(text)

# --- Админские команды (Reply + команда) ---
async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.text: return

    data = message_map.get(msg.reply_to_message.message_id)
    if not data: return

    user_id = data["user_id"]

    if msg.text.startswith("/valeria"):
        await context.bot.send_message(chat_id=user_id, text=CONTACT_VALERIA)
        await msg.reply_text("✅ Контакты Валерии отправлены.")
    elif msg.text.startswith("/anton"):
        await context.bot.send_message(chat_id=user_id, text=CONTACT_ANTON)
        await msg.reply_text("✅ Контакты Антона отправлены.")
    elif msg.text.startswith("/close"):
        if user_id in active_requests:
            # Обновляем архивное сообщение
            try:
                archive_msg_id = active_requests[user_id].get("archive_msg_id")
                if archive_msg_id:
                    await context.bot.edit_message_text(
                        chat_id=ARCHIVE_GROUP_ID,
                        message_id=archive_msg_id,
                        text=f"📥 Заявка #{user_id}\nСтатус: ✅ Закрыт"
                    )
            except: pass
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"rating_yes_{user_id}"), InlineKeyboardButton("👎", callback_data=f"rating_no_{user_id}")]])
        await context.bot.send_message(chat_id=user_id, text="Ваш запрос закрыт. Оцените качество обслуживания:", reply_markup=kb)
        await msg.reply_text("✅ Заявка закрыта.")
    elif msg.text.startswith("/stats"):
        await msg.reply_text(f"📊 Заявок сегодня: {stats['today']}, отвечено: {stats['answered']}")
    else:
        # Обычный ответ
        data["answered"] = True
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен.")
        stats["answered"] += 1
        if user_id in active_requests:
            active_requests[user_id]["status"] = "🔄 В работе"

# --- Сообщения клиентов ---
async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    user_info = f"@{user.username}" if user.username else user.full_name

    # Есть ли контакты в кэше?
    if user.id in user_contacts_cache:
        pass
    elif user.id in user_state:
        # Идёт регистрация
        state = user_state[user.id]
        if state == "awaiting_name":
            user_state[user.id] = {"state": "awaiting_phone", "name": msg.text}
            await msg.reply_text("📞 Отправьте ваш номер телефона.", reply_markup=get_phone_keyboard())
            return
        elif isinstance(state, dict) and state.get("state") == "awaiting_phone":
            phone = msg.contact.phone_number if msg.contact else msg.text
            await save_contact_to_telegram(context, user.id, state["name"], phone)
            del user_state[user.id]
            await msg.reply_text("✅ Контакты сохранены! Выберите действие или напишите запрос.", reply_markup=get_user_keyboard())
            return
    else:
        # Новый пользователь
        user_state[user.id] = "awaiting_name"
        await msg.reply_text("👤 Добро пожаловать! Представьтесь, пожалуйста. Напишите ваше имя:", reply_markup=get_user_keyboard())
        return

    # История
    if msg.text == HISTORY_BUTTON:
        await msg.reply_text("📭 История заявок пока недоступна в этой версии.")
        return

    # Кнопки
    if msg.text in DETAIL_BUTTONS:
        if msg.text == "📋 Другое":
            await msg.reply_text("📋 Напишите ваш вопрос — мы ответим в ближайшее время.", reply_markup=get_user_keyboard())
        else:
            await msg.reply_text(
                "📋 Для расчёта ставки, пожалуйста, укажите:\n"
                "• Что за груз\n• Откуда и куда\n• Характеристики\n• Условия поставки\n• Вид транспорта",
                reply_markup=get_user_keyboard()
            )
        return

    # Обработка запроса
    content_text = msg.text or "[Сообщение]"
    caption = f"📩 {user_info} (ID: {user.id})"
    forwarded = await msg.forward(chat_id=ADMIN_CHAT_ID)
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=caption, reply_to_message_id=forwarded.message_id)

    # Архив
    if user.id not in active_requests:
        archive_msg = await context.bot.send_message(
            chat_id=ARCHIVE_GROUP_ID,
            text=f"📥 Заявка #{user.id}\n👤 {user_contacts_cache.get(user.id, {}).get('name', 'Неизвестный')}\nСтатус: 🆕 Новый\n---\n{content_text}"
        )
        active_requests[user.id] = {"archive_msg_id": archive_msg.message_id, "status": "🆕 Новый"}
    else:
        active_requests[user.id]["status"] = "🔄 В работе"

    stats["today"] += 1
    message_map[forwarded.message_id] = {"user_id": user.id, "answered": False}
    asyncio.create_task(remind_later(context, ADMIN_CHAT_ID, forwarded.message_id, REMINDER_MINUTES))
    await msg.reply_text("✅ Сообщение отправлено. Ожидайте ответа.", reply_markup=get_user_keyboard())

# --- Веб-сервер для Render ---
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

    # Загружаем контакты из Telegram
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(load_contacts_from_telegram(app))

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(rating_callback, pattern=r"^rating_"))
    app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY, handle_admin_reply), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_user_message), group=2)

    logger.info("Бот запущен и готов к работе...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
