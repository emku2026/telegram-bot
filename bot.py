# -*- coding: utf-8 -*-
import logging
import re
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import sqlite3

TOKEN = "8298325705:AAEiZrEL9YRXxvp-gwCihjimxdK8aDWYGFQ"
GROUP_CHAT_ID = -4970587500

logging.basicConfig(level=logging.INFO)
scheduler = AsyncIOScheduler()

def init_db():
    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY, chat_id INTEGER, user_mention TEXT,
                  message TEXT, remind_time TEXT, sent INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, username TEXT,
                  first_name TEXT, last_name TEXT)''')
    conn.commit()
    conn.close()

def save_user(user):
    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users (user_id, username, first_name, last_name)
                 VALUES (?, ?, ?, ?)''',
              (user.id, user.username or "", user.first_name or "", user.last_name or ""))
    conn.commit()
    conn.close()

def parse_reminder(text):
    mention = re.search(r'@\w+', text)
    mention = mention.group(0) if mention else ""

    now = datetime.now()
    remind_time = None

    if re.search(r'\u0437\u0430\u0432\u0442\u0440\u0430|tomorrow', text, re.IGNORECASE):
        time_match = re.search(r'(\d{1,2}):(\d{2})', text)
        if time_match:
            h, m = int(time_match.group(1)), int(time_match.group(2))
            remind_time = (now + timedelta(days=1)).replace(hour=h, minute=m, second=0)

    hour_match = re.search(u'\u0447\u0435\u0440\u0435\u0437 (\\d+) \u0447\u0430\u0441|in (\\d+) hour', text, re.IGNORECASE)
    if hour_match:
        hours = int(hour_match.group(1) or hour_match.group(2))
        remind_time = now + timedelta(hours=hours)

    today_match = re.search(u'\u0441\u0435\u0433\u043e\u0434\u043d\u044f.*?(\\d{1,2}):(\\d{2})|today.*?(\\d{1,2}):(\\d{2})', text, re.IGNORECASE)
    if today_match:
        h = int(today_match.group(1) or today_match.group(3))
        m = int(today_match.group(2) or today_match.group(4))
        remind_time = now.replace(hour=h, minute=m, second=0)

    return mention, remind_time

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user:
        save_user(update.message.from_user)

    text = update.message.text

    if u'\u043d\u0430\u043f\u043e\u043c\u043d\u0438' not in text.lower() and 'remind' not in text.lower():
        return

    mention, remind_time = parse_reminder(text)

    if not remind_time:
        await update.message.reply_text(
            u"\u274c \u041d\u0435 \u043f\u043e\u043d\u044f\u043b \u0432\u0440\u0435\u043c\u044f. \u041f\u0440\u0438\u043c\u0435\u0440\u044b:\n"
            u"\u2022 \u041d\u0430\u043f\u043e\u043c\u043d\u0438 @john \u043f\u043e\u0437\u0432\u043e\u043d\u0438\u0442\u044c \u0437\u0430\u0432\u0442\u0440\u0430 \u0432 15:00\n"
            u"\u2022 Remind @john to call client today at 3:00\n"
            u"\u2022 \u041d\u0430\u043f\u043e\u043c\u043d\u0438 @kate \u043d\u0430\u043f\u0438\u0441\u0430\u0442\u044c \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u0432 14:00\n"
            u"\u2022 \u041d\u0430\u043f\u043e\u043c\u043d\u0438 @mike \u043f\u043e\u0437\u0432\u043e\u043d\u0438\u0442\u044c \u0447\u0435\u0440\u0435\u0437 2 \u0447\u0430\u0441\u0430"
        )
        return

    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO reminders (chat_id, user_mention, message, remind_time) VALUES (?, ?, ?, ?)",
        (update.effective_chat.id, mention, text, remind_time.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        u"\u2705 \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e \u043d\u0430 " + remind_time.strftime('%d.%m.%Y %H:%M')
    )

async def check_reminders(app):
    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        "SELECT id, chat_id, user_mention, message FROM reminders WHERE remind_time <= ? AND sent = 0",
        (now,)
    )
    reminders = c.fetchall()

    for r in reminders:
        rid, chat_id, mention, message = r
        text = u"\U0001f514 \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435!\n" + mention + "\n" + message
        await app.bot.send_message(chat_id=GROUP_CHAT_ID, text=text)
        c.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (rid,))

    conn.commit()
    conn.close()

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute("SELECT username, first_name, last_name FROM users ORDER BY first_name")
    users = c.fetchall()
    conn.close()

    if not users:
        await update.message.reply_text(u"\U0001f465 \u041f\u043e\u043a\u0430 \u043d\u0438\u043a\u0442\u043e \u043d\u0435 \u043f\u0438\u0441\u0430\u043b \u0432 \u0447\u0430\u0442.")
        return

    text = u"\U0001f465 \u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438 \u0433\u0440\u0443\u043f\u043f\u044b:\n\n"
    for u in users:
        username, first_name, last_name = u
        name = (first_name + " " + last_name).strip()
        if username:
            text += u"\u2022 " + name + " (@" + username + ")\n"
        else:
            text += u"\u2022 " + name + "\n"

    await update.message.reply_text(text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user:
        save_user(update.message.from_user)
    await update.message.reply_text(
        u"\U0001f44b \u041f\u0440\u0438\u0432\u0435\u0442! \u042f \u0431\u043e\u0442 \u0434\u043b\u044f \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0439.\n\n"
        u"\u041a\u043e\u043c\u0430\u043d\u0434\u044b:\n"
        u"\u2022 /users - \u0441\u043f\u0438\u0441\u043e\u043a \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u0439\n\n"
        u"\u041f\u0440\u0438\u043c\u0435\u0440\u044b \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0439:\n"
        u"\u2022 \u041d\u0430\u043f\u043e\u043c\u043d\u0438 @john \u043f\u043e\u0437\u0432\u043e\u043d\u0438\u0442\u044c \u0437\u0430\u0432\u0442\u0440\u0430 \u0432 15:00\n"
        u"\u2022 Remind @john to call client today at 3:00\n"
        u"\u2022 \u041d\u0430\u043f\u043e\u043c\u043d\u0438 @kate \u043d\u0430\u043f\u0438\u0441\u0430\u0442\u044c \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u0432 14:00\n"
        u"\u2022 \u041d\u0430\u043f\u043e\u043c\u043d\u0438 @mike \u043f\u043e\u0437\u0432\u043e\u043d\u0438\u0442\u044c \u0447\u0435\u0440\u0435\u0437 2 \u0447\u0430\u0441\u0430"
    )

async def post_init(app):
    scheduler.add_job(check_reminders, 'interval', seconds=30, args=[app])
    scheduler.start()
    print("Bot zapushchen!")

def main():
    init_db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
