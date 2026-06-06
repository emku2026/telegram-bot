# -*- coding: utf-8 -*-
import logging
import re
import requests
import base64
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import sqlite3

TOKEN = "8298325705:AAHeY8pfoodkVVVZR_6ABdVlpl92HXyfctk"
GROUP_CHAT_ID = -4970587500

# RingCentral credentials
RC_CLIENT_ID = "5jt8YRCPMIrdeFBMagsz6X"
RC_CLIENT_SECRET = "eRdQ1v22qb9dLWkG10dVLS8jVRhsdWQSWeCzTIAGMbP7"
RC_JWT = "eyJraWQiOiI4NzYyZjU5OGQwNTk0NGRiODZiZjVjYTk3ODA0NzYwOCIsInR5cCI6IkpXVCIsImFsZyI6IlJTMjU2In0.eyJhdWQiOiJodHRwczovL3BsYXRmb3JtLnJpbmdjZW50cmFsLmNvbS9yZXN0YXBpL29hdXRoL3Rva2VuIiwic3ViIjoiMzUwNjc3OTAyMCIsImlzcyI6Imh0dHBzOi8vcGxhdGZvcm0ucmluZ2NlbnRyYWwuY29tIiwiZXhwIjozOTI4MDk3OTI3LCJpYXQiOjE3ODA2MTQyODAsImp0aSI6IjFRQ0JKU1paVFd5SmlBOG1SWnNGQ0EifQ.UgFHdmYHHMGXnGAahezVMzrfCR1ZLBv4HcodB5X1pDdqfe9DiZ750MjSh4AoEkutUmDIfK0JagNHetJ_jmJN8CUOzp-hXSAopFnDV05nXEQxZQWlBLA3eiOyzdapqs7KXNRduWSDq_erRMqafUbSY120Gqmp2jF54_8vda4_d6yl9FcFnzz7cib6u_7W2DA1ajzybppt6gWq_yP6EGMVseMqgy09zu673izUPGyuW7zzdAujS80gUyaegZc2dq9oJV_n0VgDE9i8X2Q500k7KEOOj0cS4ZnvRTEQ4SE5zhVPX-GS-ntsQKUslweQ3iN7V9yczLISrvjAmKCKhTZqpQ"
RC_SMS_SENDER = "50564"

logging.basicConfig(level=logging.INFO)
scheduler = AsyncIOScheduler()

rc_access_token = None
rc_token_expiry = None


def init_db():
    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY, chat_id INTEGER, user_mention TEXT,
                  message TEXT, remind_time TEXT, sent INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, username TEXT,
                  first_name TEXT, last_name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sms_log
                 (id INTEGER PRIMARY KEY, message_id TEXT UNIQUE, received_at TEXT)''')
    conn.commit()
    conn.close()


def is_sms_forwarded(message_id):
    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute("SELECT id FROM sms_log WHERE message_id = ?", (str(message_id),))
    result = c.fetchone()
    conn.close()
    return result is not None


def mark_sms_forwarded(message_id):
    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO sms_log (message_id, received_at) VALUES (?, ?)",
              (str(message_id), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def get_rc_token():
    global rc_access_token, rc_token_expiry

    if rc_access_token and rc_token_expiry and datetime.now() < rc_token_expiry:
        return rc_access_token

    credentials = base64.b64encode(f"{RC_CLIENT_ID}:{RC_CLIENT_SECRET}".encode()).decode()

    response = requests.post(
        "https://platform.ringcentral.com/restapi/oauth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": RC_JWT
        }
    )

    if response.status_code == 200:
        data = response.json()
        rc_access_token = data["access_token"]
        rc_token_expiry = datetime.now() + timedelta(seconds=data.get("expires_in", 3600) - 60)
        logging.info("RingCentral token obtained successfully")
        return rc_access_token
    else:
        logging.error(f"RC token error: {response.status_code} {response.text}")
        return None


async def check_rc_sms(app):
    try:
        token = get_rc_token()
        if not token:
            return

        date_from = (datetime.utcnow() - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

        response = requests.get(
            "https://platform.ringcentral.com/restapi/v1.0/account/~/extension/~/message-store",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "messageType": "SMS",
                "dateFrom": date_from,
                "perPage": 20
            }
        )

        if response.status_code != 200:
            logging.error(f"RC SMS fetch error: {response.status_code} {response.text}")
            return

        data = response.json()
        records = data.get("records", [])

        logging.info(f"RC SMS records found: {len(records)}")
        for msg in records:
            msg_id = str(msg.get("id", ""))
            direction = msg.get("direction", "")
            from_number = msg.get("from", {}).get("phoneNumber", "")
            logging.info(f"SMS: direction={direction} from={from_number} id={msg_id}")

            if direction != "Inbound":
                continue
            if RC_SMS_SENDER not in from_number:
                continue
            if is_sms_forwarded(msg_id):
                continue

            # Get SMS text
            text = msg.get("subject", "").strip()

            # If text is in attachment
            if not text:
                for att in msg.get("attachments", []):
                    if att.get("type") == "Text":
                        att_response = requests.get(
                            att["uri"],
                            headers={"Authorization": f"Bearer {token}"}
                        )
                        if att_response.status_code == 200:
                            text = att_response.text.strip()
                        break

            if not text:
                text = u"(текст недоступен)"

            telegram_msg = u"\U0001f4f1 \u041d\u043e\u0432\u043e\u0435 SMS \u043e\u0442 CitizenShipper:\n\n" + text
            await app.bot.send_message(chat_id=GROUP_CHAT_ID, text=telegram_msg)
            mark_sms_forwarded(msg_id)
            logging.info(f"Forwarded SMS {msg_id} to Telegram")

    except Exception as e:
        logging.error(f"check_rc_sms error: {e}")


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

    if re.search(u'\u0437\u0430\u0432\u0442\u0440\u0430|tomorrow', text, re.IGNORECASE):
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


async def mc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(u"\u041f\u0440\u0438\u043c\u0435\u0440: /mc 1543888")
        return

    mc_number = context.args[0].replace("MC-", "").replace("mc-", "").strip()

    await update.message.reply_text(u"\U0001f50d \u041f\u0440\u043e\u0432\u0435\u0440\u044f\u044e MC-" + mc_number + u"...")

    try:
        response = requests.get(
            f"https://safer.fmcsa.dot.gov/query.asp?searchtype=ANY&query_type=queryCarrierSnapshot&query_param=MC_MX&query_string={mc_number}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )

        text = response.text
        logging.info(f"FMCSA response length: {len(text)}")
        logging.info(f"FMCSA snippet: {text[1000:2000]}")

        import re as re2

        def parse_field(html, label):
            pattern = r'<td[^>]*>\s*' + re2.escape(label) + r'\s*</td>\s*<td[^>]*>(.*?)</td>'
            match = re2.search(pattern, html, re2.DOTALL | re2.IGNORECASE)
            if not match:
                pattern2 = label + r'[^<]*</[^>]+>\s*<td[^>]*>(.*?)</td>'
                match = re2.search(pattern2, html, re2.DOTALL | re2.IGNORECASE)
            if match:
                val = re2.sub(r'<[^>]+>', '', match.group(1))
                val = val.replace('&nbsp;', ' ').strip()
                return val
            return ""

        name   = parse_field(text, "Legal Name")
        dot    = parse_field(text, "USDOT Number")
        status = parse_field(text, "Operating Status")
        phone  = parse_field(text, "Phone")

        # Insurance - look for active/authorized keywords
        ins = "Active" if "insurance" in text.lower() and "unavailable" not in text.lower() else "Check manually"

        if name:
            emoji = u"\u2705" if "AUTHORIZED" in status.upper() or "ACTIVE" in status.upper() else u"\u274c"
            reply = (
                f"{emoji} MC-{mc_number}\n\n"
                f"\U0001f3e2 {name}\n"
                f"\U0001f4cb USDOT: {dot}\n"
                f"\U0001f4ca Status: {status}\n"
                f"\U0001f6e1 Insurance: {ins}\n"
                f"\U0001f4de Phone: {phone}" if phone else
                f"{emoji} MC-{mc_number}\n\n"
                f"\U0001f3e2 {name}\n"
                f"\U0001f4cb USDOT: {dot}\n"
                f"\U0001f4ca Status: {status}\n"
                f"\U0001f6e1 Insurance: {ins}"
            )
        else:
            reply = u"\u274c MC-" + mc_number + u" \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0432 \u0431\u0430\u0437\u0435 FMCSA."

        await update.message.reply_text(reply)

    except Exception as e:
        logging.error(f"MC check error: {e}")
        await update.message.reply_text(u"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0435. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u043f\u043e\u0437\u0436\u0435.")


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute("SELECT id, user_mention, message, remind_time FROM reminders WHERE sent = 0 ORDER BY remind_time")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(u"\U0001f4cb \u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0439.")
        return

    text = u"\U0001f4cb \u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0435 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f:\n\n"
    for row in rows:
        rid, mention, message, remind_time = row
        dt = datetime.strptime(remind_time, "%Y-%m-%d %H:%M:%S").strftime("%d.%m %H:%M")
        text += f"#{rid} {mention} — {dt}\n{message}\n\n"

    await update.message.reply_text(text)


async def del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(u"\u041f\u0440\u0438\u043c\u0435\u0440: /del 5")
        return

    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text(u"\u041d\u0430\u043f\u0438\u0448\u0438 \u043d\u043e\u043c\u0435\u0440: /del 5")
        return

    conn = sqlite3.connect("reminders.db")
    c = conn.cursor()
    c.execute("SELECT id FROM reminders WHERE id = ? AND sent = 0", (rid,))
    row = c.fetchone()

    if not row:
        await update.message.reply_text(u"\u274c \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 #" + str(rid) + u" \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e.")
        conn.close()
        return

    c.execute("DELETE FROM reminders WHERE id = ?", (rid,))
    conn.commit()
    conn.close()

    await update.message.reply_text(u"\u2705 \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 #" + str(rid) + u" \u0443\u0434\u0430\u043b\u0435\u043d\u043e.")


async def r_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user:
        save_user(update.message.from_user)

    text = " ".join(context.args) if context.args else ""

    if not text:
        await update.message.reply_text(
            u"\u041f\u0440\u0438\u043c\u0435\u0440\u044b:\n"
            u"\u2022 /r @john \u0437\u0430\u0432\u0442\u0440\u0430 \u0432 15:00\n"
            u"\u2022 /r @kate \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u0432 14:00\n"
            u"\u2022 /r @mike \u0447\u0435\u0440\u0435\u0437 2 \u0447\u0430\u0441\u0430\n"
            u"\u2022 /r @john tomorrow at 15:00"
        )
        return

    mention, remind_time = parse_reminder(text)

    if not remind_time:
        await update.message.reply_text(
            u"\u274c \u041d\u0435 \u043f\u043e\u043d\u044f\u043b \u0432\u0440\u0435\u043c\u044f. \u041f\u0440\u0438\u043c\u0435\u0440\u044b:\n"
            u"\u2022 /r @john \u0437\u0430\u0432\u0442\u0440\u0430 \u0432 15:00\n"
            u"\u2022 /r @kate \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u0432 14:00\n"
            u"\u2022 /r @mike \u0447\u0435\u0440\u0435\u0437 2 \u0447\u0430\u0441\u0430"
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user:
        save_user(update.message.from_user)
    await update.message.reply_text(
        u"\U0001f44b \u041f\u0440\u0438\u0432\u0435\u0442! \u042f \u0431\u043e\u0442 \u0434\u043b\u044f \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0439.\n\n"
        u"\u041a\u043e\u043c\u0430\u043d\u0434\u044b:\n"
        u"\u2022 /r @john \u0437\u0430\u0432\u0442\u0440\u0430 \u0432 15:00 - \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435\n"
        u"\u2022 /users - \u0441\u043f\u0438\u0441\u043e\u043a \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u0439\n\n"
        u"\u041f\u0440\u0438\u043c\u0435\u0440\u044b:\n"
        u"\u2022 /r @john \u0437\u0430\u0432\u0442\u0440\u0430 \u0432 15:00\n"
        u"\u2022 /r @kate \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u0432 14:00\n"
        u"\u2022 /r @mike \u0447\u0435\u0440\u0435\u0437 2 \u0447\u0430\u0441\u0430\n"
        u"\u2022 /r @john tomorrow at 15:00"
    )


async def post_init(app):
    scheduler.add_job(check_reminders, 'interval', seconds=30, args=[app])
    scheduler.add_job(check_rc_sms, 'interval', seconds=60, args=[app])
    scheduler.start()
    print("Bot zapushchen!")


def main():
    init_db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("r", r_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("del", del_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
