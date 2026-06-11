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
ADMIN_CHAT_ID = -1003725679213       # ID группы администраторов
ARCHIVE_GROUP_ID = -1003908640963    # ID группы-архива заявок
CHANNEL_USERNAME = "WengeGroup"  # юзернейм канала (без @)
REMINDER_MINUTES = 30

CONTACT_VALERIA = "👩💼 Валерия\nТелефон: +7 (993) 903-36-33\nTelegram: @Valeria_Wenge"
CONTACT_ANTON = "👨💼 Антон\nТелефон: +7 (967) 006-02-86\nTelegram: @AntonWGR"
# ---------------------------------------------

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

message_map = {}
user_requests = defaultdict(list)
user_contacts = {}
user_state = {}
active_requests = {}
stats = {"today": 0, "answered": 0, "total_response_time": timedelta()}

MENU_BUTTONS = {"💰 Запросить ставку", "📋 Другое", "📋 Мои заявки"}
DETAIL_BUTTONS = {"💰 Запросить ставку", "📋 Другое"}
HISTORY_BUTTON = "📋 Мои заявки"

ADMIN_BUTTONS = {"📊 Статистика", "👩💼 Контакты Валерии", "👨💼 Контакты Антона", "✅ Закрыть заявку"}

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💰 Запросить ставку"), KeyboardButton("📋 Другое")],
        [KeyboardButton("📋 Мои заявки")],
    ], resize_keyboard=True)

def get_admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Статистика")],
        [KeyboardButton("👩💼 Контакты Валерии"), KeyboardButton("👨💼 Контакты Антона")],
        [KeyboardButton("✅ Закрыть заявку")],
    ], resize_keyboard=True)

def get_phone_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 Поделиться номером", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

WELCOME_TEXT = "👋 Добро пожаловать в Wenge Group!\n\nВыберите, что вас интересует, или просто напишите свой запрос — мы ответим в ближайшее время."

def get_channel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")]])

async def admin_keyboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительная установка админской клавиатуры."""
    await update.message.reply_text("✅ Админская клавиатура обновлена.", reply_markup=get_admin_keyboard())

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(WELCOME_TEXT, reply_markup=get_user_keyboard())
    await update.message.reply_text("Будьте в курсе новостей логистики:", reply_markup=get_channel_keyboard())
    if user.id not in user_requests:
        user_requests[user.id] = []

def save_to_history(user_id, text):
    if text in MENU_BUTTONS:
        return
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
                reply_markup=get_admin_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка отправки напоминания: {e}")

async def update_archive(context, user_id, status, new_message=None):
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
        if phone: contact_str += f" | 📞 {phone}"
        history = "\n".join(msgs[-10:])
        text = f"📥 Заявка #{user_id}\n👤 {contact_str}\nСтатус: {status}\n🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n---\n{history}"
        await context.bot.edit_message_text(chat_id=ARCHIVE_GROUP_ID, message_id=msg_id, text=text)
        active_requests[user_id]["status"] = status
    except Exception as e:
        logger.error(f"Не удалось обновить архив: {e}")

async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, rating, user_id_str = query.data.split("_")
    text = "👍 Спасибо за ваш отзыв!" if rating == "yes" else "👎 Спасибо за обратную связь!"
    await query.edit_message_text(text)

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ВСЕХ сообщений в админском чате (кроме reply)."""
    msg = update.message
    
    # Если сообщение пустое — игнорируем
    if not msg or not msg.text:
        return
    
    # Обработка кнопки «📊 Статистика»
    if msg.text == "📊 Статистика":
        today = stats["today"]
        answered = stats["answered"]
        avg_time = str(stats["total_response_time"] / answered).split(".")[0] if answered else "—"
        await msg.reply_text(
            f"📊 Статистика за сегодня:\n"
            f"• Заявок: {today}\n"
            f"• Отвечено: {answered}\n"
            f"• Среднее время ответа: {avg_time}",
            reply_markup=get_admin_keyboard()
        )
        return
    
    # Обработка других кнопок (требуют reply)
    if msg.text in ["👩💼 Контакты Валерии", "👨💼 Контакты Антона", "✅ Закрыть заявку"]:
        await msg.reply_text(
            "❗ Используйте эту кнопку reply'ем на сообщение клиента.",
            reply_markup=get_admin_keyboard()
        )
        return

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    user_info = f"@{user.username}" if user.username else user.full_name

    if user.id in user_state:
        state = user_state[user.id]
        if state == "awaiting_name":
            user_contacts.setdefault(user.id, {})["name"] = msg.text
            user_state[user.id] = "awaiting_phone"
            await msg.reply_text("📞 Отправьте ваш номер телефона.", reply_markup=get_phone_keyboard())
            return
        elif state == "awaiting_phone":
            phone = msg.contact.phone_number if msg.contact else msg.text
            user_contacts.setdefault(user.id, {})["phone"] = phone
            del user_state[user.id]
            await msg.reply_text("✅ Контакты сохранены! Выберите действие или напишите запрос.", reply_markup=get_user_keyboard())
            return

    if user.id not in user_contacts:
        user_state[user.id] = "awaiting_name"
        await msg.reply_text("👤 Добро пожаловать! Для начала, пожалуйста, представьтесь. Напишите ваше имя:", reply_markup=get_user_keyboard())
        return

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

    if msg.text and msg.text.strip() in DETAIL_BUTTONS:
        if msg.text.strip() == "📋 Другое":
            await msg.reply_text("📋 Напишите ваш вопрос — мы ответим в ближайшее время.", reply_markup=get_user_keyboard())
        else:
            await msg.reply_text(
                "📋 Для расчёта ставки, пожалуйста, укажите:\n• Что за груз\n• Откуда и куда\n• Характеристики\n• Условия поставки\n• Вид транспорта\n\nНапишите всё, что знаете — мы оперативно рассчитаем.",
                reply_markup=get_user_keyboard()
            )
        return

    content_text = msg.text or "[Сообщение]"
    if msg.caption: content_text = f"[Файл] {msg.caption}"
    elif msg.photo: content_text = "[Фото]"
    elif msg.document: content_text = "[Документ]"
    elif msg.voice: content_text = "[Голосовое]"
    elif msg.video: content_text = "[Видео]"

    save_to_history(user.id, content_text)
    caption = f"📩 {user_info} (ID: {user.id})"
    forwarded = await context.bot.forward_message(chat_id=ADMIN_CHAT_ID, from_chat_id=msg.chat_id, message_id=msg.message_id)
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=caption,
        reply_to_message_id=forwarded.message_id,
        reply_markup=get_admin_keyboard()
    )

    if user.id not in active_requests:
        c = user_contacts.get(user.id, {})
        name, phone = c.get("name", "Неизвестный"), c.get("phone", "")
        cs = f"👤 {name}" + (f" | 📞 {phone}" if phone else "")
        am = await context.bot.send_message(chat_id=ARCHIVE_GROUP_ID, text=f"📥 Заявка #{user.id}\n{cs}\nСтатус: 🆕 Новый\n---\n{content_text}")
        active_requests[user.id] = {"archive_msg_id": am.message_id, "status": "🆕 Новый", "messages": [content_text]}
    else:
        await update_archive(context, user.id, "🔄 В работе", content_text)

    message_map[forwarded.message_id] = {"user_id": user.id, "answered": False, "request_time": datetime.now(timezone.utc)}
    stats["today"] += 1
    asyncio.create_task(remind_later(context, ADMIN_CHAT_ID, forwarded.message_id, REMINDER_MINUTES))
    await msg.reply_text("✅ Сообщение отправлено. Ожидайте ответа.", reply_markup=get_user_keyboard())

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик reply'ев в админском чате."""
    msg = update.message
    if not msg.reply_to_message: 
        return
    
    original_msg_id = msg.reply_to_message.message_id
    data = message_map.get(original_msg_id)
    
    if not data:
        await msg.reply_text("Это сообщение не является заявкой клиента.", reply_markup=get_admin_keyboard())
        return
    
    user_id = data["user_id"]

    if msg.text:
        if msg.text == "👩💼 Контакты Валерии":
            try:
                await context.bot.send_message(chat_id=user_id, text=CONTACT_VALERIA)
                await msg.reply_text("✅ Контакты Валерии отправлены клиенту.", reply_markup=get_admin_keyboard())
            except:
                await msg.reply_text("❌ Ошибка отправки.", reply_markup=get_admin_keyboard())
            return
        
        if msg.text == "👨💼 Контакты Антона":
            try:
                await context.bot.send_message(chat_id=user_id, text=CONTACT_ANTON)
                await msg.reply_text("✅ Контакты Антона отправлены клиенту.", reply_markup=get_admin_keyboard())
            except:
                await msg.reply_text("❌ Ошибка отправки.", reply_markup=get_admin_keyboard())
            return
        
        if msg.text == "✅ Закрыть заявку":
            await update_archive(context, user_id, "✅ Закрыт")
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👍", callback_data=f"rating_yes_{user_id}"),
                 InlineKeyboardButton("👎", callback_data=f"rating_no_{user_id}")]
            ])
            try:
                await context.bot.send_message(chat_id=user_id, text="Ваш запрос закрыт. Оцените качество обслуживания:", reply_markup=kb)
                await msg.reply_text("✅ Заявка закрыта.", reply_markup=get_admin_keyboard())
            except:
                await msg.reply_text("❌ Ошибка при закрытии.", reply_markup=get_admin_keyboard())
            return

    data["answered"] = True
    try:
        await msg.copy(chat_id=user_id, reply_markup=get_user_keyboard())
        await msg.reply_text("✅ Ответ отправлен клиенту.", reply_markup=get_admin_keyboard())
        
        if "request_time" in data:
            stats["total_response_time"] += datetime.now(timezone.utc) - data["request_time"]
            stats["answered"] += 1
        if user_id in active_requests:
            await update_archive(context, user_id, "🔄 В работе")
    except:
        await msg.reply_text("❌ Не удалось отправить ответ.", reply_markup=get_admin_keyboard())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    today, answered = stats["today"], stats["answered"]
    avg = str(stats["total_response_time"] / answered).split(".")[0] if answered else "—"
    await msg.reply_text(
        f"📊 Статистика за сегодня:\n• Заявок: {today}\n• Отвечено: {answered}\n• Среднее время ответа: {avg}",
        reply_markup=get_admin_keyboard()
    )

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
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("admin", admin_keyboard_command))
    app.add_handler(CallbackQueryHandler(rating_callback, pattern=r"^rating_"))
    
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=ADMIN_CHAT_ID) & ~filters.REPLY & ~filters.COMMAND,
        handle_admin_message
    ))
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY,
        handle_admin_reply
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_user_message
    ))
    
    logger.info("Бот запущен и готов к работе...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
