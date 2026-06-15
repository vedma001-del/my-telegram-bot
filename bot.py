import os
import asyncio
import threading
import logging
import sqlite3
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

# ---------- НАСТРОЙКИ (замени на свои) ----------
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

# База данных
conn = sqlite3.connect('wenge_bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, phone TEXT)')
cursor.execute('CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT, date TEXT, archive_msg_id INTEGER, status TEXT DEFAULT "🆕 Новый")')
cursor.execute('CREATE TABLE IF NOT EXISTS stats (date TEXT PRIMARY KEY, total INTEGER DEFAULT 0, answered INTEGER DEFAULT 0, total_time REAL DEFAULT 0)')
conn.commit()

message_map = {}
user_state = {}

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

# Работа с БД
def get_user_from_db(user_id):
    cursor.execute('SELECT name, phone FROM users WHERE user_id = ?', (user_id,))
    return cursor.fetchone()

def save_user_to_db(user_id, name, phone):
    cursor.execute('INSERT OR REPLACE INTO users (user_id, name, phone) VALUES (?, ?, ?)', (user_id, name, phone))
    conn.commit()

def save_request_to_db(user_id, text, archive_msg_id, status='🆕 Новый'):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cursor.execute('INSERT INTO requests (user_id, text, date, archive_msg_id, status) VALUES (?, ?, ?, ?, ?)', 
                   (user_id, text, now, archive_msg_id, status))
    conn.commit()

def get_user_requests(user_id, limit=10):
    cursor.execute('SELECT text, date, status FROM requests WHERE user_id = ? ORDER BY id DESC LIMIT ?', (user_id, limit))
    return cursor.fetchall()

def get_active_request(user_id):
    cursor.execute('SELECT id, archive_msg_id FROM requests WHERE user_id = ? AND status != ? ORDER BY id DESC LIMIT 1', (user_id, '✅ Закрыт'))
    return cursor.fetchone()

def update_request_status(user_id, status):
    cursor.execute('UPDATE requests SET status = ? WHERE user_id = ? AND status != ?', (status, user_id, '✅ Закрыт'))
    conn.commit()

def get_today_stats():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor.execute('SELECT total, answered, total_time FROM stats WHERE date = ?', (today,))
    return cursor.fetchone()

def update_today_stats(total_delta=0, answered_delta=0, time_delta=0.0):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor.execute('INSERT INTO stats (date, total, answered, total_time) VALUES (?, ?, ?, ?) ON CONFLICT(date) DO UPDATE SET total = total + ?, answered = answered + ?, total_time = total_time + ?', 
                   (today, total_delta, answered_delta, time_delta, total_delta, answered_delta, time_delta))
    conn.commit()

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
        update_request_status(user_id, '✅ Закрыт')
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"rating_yes_{user_id}"), InlineKeyboardButton("👎", callback_data=f"rating_no_{user_id}")]])
        await context.bot.send_message(chat_id=user_id, text="Ваш запрос закрыт. Оцените качество обслуживания:", reply_markup=kb)
        await msg.reply_text("✅ Заявка закрыта.", reply_markup=get_admin_keyboard())
    else:
        data["answered"] = True
        await msg.copy(chat_id=user_id)
        await msg.reply_text("✅ Ответ отправлен.", reply_markup=get_admin_keyboard())
        update_today_stats(answered_delta=1)
        update_request_status(user_id, '🔄 В работе')

async def handle_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text == ADMIN_STATS_BUTTON:
        row = get_today_stats()
        if row:
            total, answered, total_time = row
            avg = str(timedelta(seconds=total_time) / answered).split(".")[0] if answered else "—"
            text = f"📊 Статистика за сегодня:\n• Заявок: {total}\n• Отвечено: {answered}\n• Среднее время ответа: {avg}"
        else:
            text = "📊 За сегодня заявок пока нет."
        await msg.reply_text(text, reply_markup=get_admin_keyboard())

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    user_info = f"@{user.username}" if user.username else user.full_name

    db_user = get_user_from_db(user.id)

    if not db_user and user.id not in user_state:
        user_state[user.id] = "awaiting_name"
        await msg.reply_text("👤 Добро пожаловать! Представьтесь, пожалуйста. Напишите ваше имя:", reply_markup=get_user_keyboard())
        return

    if user.id in user_state:
        state = user_state[user.id]
        if state == "awaiting_name":
            user_state[user.id] = {"state": "awaiting_phone", "name": msg.text}
            await msg.reply_text("📞 Отправьте ваш номер телефона.", reply_markup=get_phone_keyboard())
            return
        elif isinstance(state, dict) and state.get("state") == "awaiting_phone":
            phone = msg.contact.phone_number if msg.contact else msg.text
            save_user_to_db(user.id, state["name"], phone)
            del user_state[user.id]
            await msg.reply_text("✅ Контакты сохранены! Выберите действие или напишите запрос.", reply_markup=get_user_keyboard())
            return

    if msg.text == HISTORY_BUTTON:
        requests = get_user_requests(user.id, limit=10)
        if not requests:
            await msg.reply_text("📭 У вас пока нет заявок.")
        else:
            text = "📋 Ваши последние заявки:\n\n"
            for i, (req_text, date, status) in enumerate(requests, 1):
                short_text = (req_text[:50] + '...') if len(req_text) > 50 else req_text
                text += f"{i}. [{date}] {status}: {short_text}\n"
            await msg.reply_text(text)
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

    update_today_stats(total_delta=1)

    active_req = get_active_request(user.id)
    if not active_req:
        archive_msg = await context.bot.send_message(
            chat_id=ARCHIVE_GROUP_ID, 
            text=f"📥 Заявка #{user.id}\n👤 {db_user[0]} | 📞 {db_user[1]}\nСтатус: 🆕 Новый\n---\n{content_text}"
        )
        save_request_to_db(user.id, content_text, archive_msg.message_id)
    else:
        update_request_status(user.id, '🔄 В работе')
        save_request_to_db(user.id, content_text, active_req[1], '🔄 В работе')

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
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(rating_callback, pattern=r"^rating_"))
    app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.TEXT & ~filters.COMMAND, handle_admin_stats), group=0)
    app.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.REPLY, handle_admin_reply), group=1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_user_message), group=2)
    
    logger.info("Бот запущен и готов к работе...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
