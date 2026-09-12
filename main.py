import os
import asyncio
from datetime import datetime, time, timedelta
import sqlite3
import urllib.parse
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ----------------- Configuration -----------------
TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = pytz.timezone("Asia/Kolkata")
DB_NAME = "bot_data.db"

# Hosted GitHub Pages URL
WEBAPP_BASE_URL = "https://rcbgolu018.github.io/Counterr/"

# Conversation States
(
    SET_EXAM,
    SET_ROUTINE_STATE,
    SET_REMINDER,
    ADD_TASK_STATE,
    LOG_STUDY_STATE,
    ADD_NOTE_STATE,
) = range(6)

# Background Tasks Trackers
active_hourly_tasks = {}    # {user_id: asyncio.Task}
active_pomodoro_tasks = {}  # {user_id: asyncio.Task}

# ----------------- Database Setup & Handlers -----------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            exam_name TEXT,
            exam_date TEXT,
            reminder_time TEXT,
            current_streak INTEGER DEFAULT 0,
            last_study_date TEXT
        )
        """
    )
    
    for col, col_type in [("current_streak", "INTEGER DEFAULT 0"), ("last_study_date", "TEXT")]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    # Tasks table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_text TEXT,
            is_done INTEGER DEFAULT 0
        )
        """
    )
    # Daily Routine Checklist table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_routine_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_text TEXT,
            is_done INTEGER DEFAULT 0
        )
        """
    )
    # Study logs table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS study_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            log_date TEXT,
            hours REAL
        )
        """
    )
    # Revision Diary / Notes Vault
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS revision_notes (
            note_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            note_text TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()

def get_user_data(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT exam_name, exam_date, reminder_time, current_streak, last_study_date FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "exam_name": row[0],
            "exam_date": row[1],
            "reminder_time": row[2],
            "current_streak": row[3] or 0,
            "last_study_date": row[4],
        }
    return None

def update_user_field(user_id, field, value):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def get_all_active_reminders():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, reminder_time FROM users WHERE reminder_time IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

# Daily Routine Checklist Helpers
def set_daily_routine_items(user_id, lines):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_routine_items WHERE user_id = ?", (user_id,))
    for line in lines:
        if line.strip():
            cursor.execute("INSERT INTO daily_routine_items (user_id, item_text, is_done) VALUES (?, ?, 0)", (user_id, line.strip()))
    conn.commit()
    conn.close()

def get_daily_routine_items(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT item_id, item_text, is_done FROM daily_routine_items WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def toggle_routine_item(item_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE daily_routine_items SET is_done = CASE WHEN is_done = 1 THEN 0 ELSE 1 END WHERE item_id = ?", (item_id,))
    conn.commit()
    conn.close()

def clear_all_routine_items_for_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_routine_items")
    conn.commit()
    conn.close()

# Syllabus Tasks Helpers
def add_user_task(user_id, task_text):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (user_id, task_text, is_done) VALUES (?, ?, 0)", (user_id, task_text))
    conn.commit()
    conn.close()

def get_user_tasks(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT task_id, task_text, is_done FROM tasks WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def toggle_task(task_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET is_done = CASE WHEN is_done = 1 THEN 0 ELSE 1 END WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()

def clear_completed_tasks(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE user_id = ? AND is_done = 1", (user_id,))
    conn.commit()
    conn.close()

# Study Log & Streak Helpers
def add_study_hours(user_id, hours):
    today = datetime.now(TIMEZONE).date()
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO study_logs (user_id, log_date, hours) VALUES (?, ?, ?)", (user_id, today_str, hours))

    cursor.execute("SELECT current_streak, last_study_date FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    streak = row[0] if (row and row[0]) else 0
    last_date = row[1] if row else None

    if last_date == today_str:
        pass
    elif last_date == yesterday_str:
        streak += 1
    else:
        streak = 1

    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute("UPDATE users SET current_streak = ?, last_study_date = ? WHERE user_id = ?", (streak, today_str, user_id))
    conn.commit()
    conn.close()
    return streak

def get_study_stats(user_id):
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    seven_days_ago = (datetime.now(TIMEZONE) - timedelta(days=7)).strftime("%Y-%m-%d")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(hours) FROM study_logs WHERE user_id = ? AND log_date = ?", (user_id, today_str))
    today_hours = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT SUM(hours) FROM study_logs WHERE user_id = ? AND log_date >= ?", (user_id, seven_days_ago))
    week_hours = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT SUM(hours) FROM study_logs WHERE user_id = ?", (user_id,))
    total_hours = cursor.fetchone()[0] or 0.0
    conn.close()
    return round(today_hours, 2), round(week_hours, 2), round(total_hours, 2)

# Revision Vault Helpers
def add_revision_note(user_id, note_text):
    now_str = datetime.now(TIMEZONE).strftime("%d %b, %I:%M %p")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO revision_notes (user_id, note_text, created_at) VALUES (?, ?, ?)", (user_id, note_text, now_str))
    conn.commit()
    conn.close()

def get_revision_notes(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT note_id, note_text, created_at FROM revision_notes WHERE user_id = ? ORDER BY note_id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def clear_all_revision_notes(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM revision_notes WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ----------------- Time Calculations & URLs -----------------
def get_time_remaining(target_dt_str):
    if not target_dt_str:
        return None
    try:
        target_dt = datetime.strptime(target_dt_str, "%Y-%m-%d %H:%M")
        target_dt = TIMEZONE.localize(target_dt)
        now = datetime.now(TIMEZONE)
        diff = target_dt - now

        if diff.total_seconds() <= 0:
            return "⚠️ Exam time nikal chuka hai!"

        days = diff.days
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{days} Days, {hours} Hours, {minutes} Minutes, {seconds} Seconds"
    except Exception:
        return None

def get_webapp_url(exam_name, exam_date_str):
    dt = datetime.strptime(exam_date_str, "%Y-%m-%d %H:%M")
    iso_date = dt.strftime("%Y-%m-%dT%H:%M:00+05:30")
    params = {
        "name": exam_name,
        "date": iso_date
    }
    return f"{WEBAPP_BASE_URL}?{urllib.parse.urlencode(params)}"

# ----------------- Keyboards -----------------
def main_menu_keyboard(user_id):
    u_data = get_user_data(user_id)
    streak = u_data.get("current_streak", 0) if u_data else 0
    
    if u_data and u_data.get("exam_date") and u_data.get("exam_name"):
        webapp_url = get_webapp_url(u_data["exam_name"], u_data["exam_date"])
        live_btn = InlineKeyboardButton("🔴 WebApp Live (1s)", web_app=WebAppInfo(url=webapp_url))
    else:
        live_btn = InlineKeyboardButton("🔴 WebApp Live (1s)", callback_data="btn_live_not_set")

    keyboard = [
        [
            InlineKeyboardButton("⏳ Static Countdown", callback_data="btn_static_cd"),
            live_btn
        ],
        [
            InlineKeyboardButton("🔄 Hourly Live Message", callback_data="btn_hourly_cd")
        ],
        [
            InlineKeyboardButton("📝 Daily Routine Checklist", callback_data="btn_routine_menu"),
            InlineKeyboardButton(f"🔥 Streak: {streak}d", callback_data="btn_stats_menu")
        ],
        [
            InlineKeyboardButton("⏱️ Pomodoro Timer", callback_data="btn_pomodoro_menu"),
            InlineKeyboardButton("📌 Quick Formula Vault", callback_data="btn_notes_menu")
        ],
        [
            InlineKeyboardButton("📋 General To-Do", callback_data="btn_tasks_menu"),
            InlineKeyboardButton("📊 Study Log & Stats", callback_data="btn_stats_menu")
        ],
        [
            InlineKeyboardButton("🎯 Set Event / Exam", callback_data="btn_set_exam"),
            InlineKeyboardButton("⏰ Reminders", callback_data="btn_reminder_menu")
        ],
        [
            InlineKeyboardButton("💡 Study Quote", callback_data="btn_quote")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def not_set_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Set Event & Date Now", callback_data="btn_set_exam")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="btn_back_main")]
    ])

def routine_keyboard(user_id):
    items = get_daily_routine_items(user_id)
    kb = []
    if not items:
        kb.append([InlineKeyboardButton("➕ Subah Ka Routine Set Karein", callback_data="btn_set_routine")])
    else:
        for item_id, text, is_done in items:
            mark = "✅" if is_done else "⬜"
            kb.append([InlineKeyboardButton(f"{mark} {text}", callback_data=f"rtoggle_{item_id}")])
        kb.append([
            InlineKeyboardButton("✏️ Naya Routine Likhein", callback_data="btn_set_routine")
        ])
    kb.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_back_main")])
    return InlineKeyboardMarkup(kb)

def tasks_keyboard(user_id):
    tasks = get_user_tasks(user_id)
    kb = []
    for tid, text, is_done in tasks:
        mark = "✅" if is_done else "⬜"
        kb.append([InlineKeyboardButton(f"{mark} {text}", callback_data=f"toggle_{tid}")])
    kb.append([
        InlineKeyboardButton("➕ Add Task", callback_data="btn_add_task"),
        InlineKeyboardButton("🗑️ Clear Done", callback_data="btn_clear_done")
    ])
    kb.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="btn_back_main")])
    return InlineKeyboardMarkup(kb)

def notes_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Note / Formula", callback_data="btn_add_note")],
        [InlineKeyboardButton("🗑️ Clear All Notes", callback_data="btn_clear_notes")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="btn_back_main")]
    ])

# ----------------- Automated Scheduled Jobs -----------------
async def end_of_day_review_job(context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    for uid in users:
        items = get_daily_routine_items(uid)
        today_h, _, _ = get_study_stats(uid)
        u_data = get_user_data(uid)
        streak = u_data.get("current_streak", 0) if u_data else 0

        total_tasks = len(items)
        done_tasks = sum(1 for _, _, is_d in items if is_d)
        pct = int((done_tasks / total_tasks * 100)) if total_tasks > 0 else 0

        msg = (
            "🌙 **End of Day (EOD) Performance Review**\n\n"
            f"🎯 **Routine Tasks:** `{done_tasks}/{total_tasks} completed ({pct}%)`\n"
            f"⏱️ **Today's Study Log:** `{today_h}` Hours\n"
            f"🔥 **Current Study Streak:** `{streak}` Days\n\n"
            "⚠️ _Aapka daily routine agle 28 minutes me (11:58 PM) automatically reset ho jayega._\n\n"
            "Aaj ka target poora hua toh shabash! Achi neend lein aur kal subah naye targets set karein. 🛌"
        )
        try:
            await context.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
        except Exception:
            pass

async def reset_daily_routines_job(context: ContextTypes.DEFAULT_TYPE):
    clear_all_routine_items_for_all_users()
    print("[Cron] 11:58 PM: Saare daily routine checklists reset kar diye gaye hain.")

async def morning_briefing_job(context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    for uid in users:
        u_data = get_user_data(uid)
        if not u_data:
            continue
        event_name = u_data.get("exam_name") or "Target Exam"
        exam_date = u_data.get("exam_date")
        remaining = get_time_remaining(exam_date) if exam_date else "Date not set"

        text = (
            f"🌅 **Good Morning! Aaj Ka Target Set Karein**\n\n"
            f"🎯 **Target:** `{event_name}`\n"
            f"⏳ **Time Left:** `{remaining}`\n\n"
            f"📌 **Subah Ka Target:** Bot me **'📝 Daily Routine Checklist'** par jaakar aaj ke targets add karein.\n\n"
            f"🔥 _\"Aaj ka har ek tick aapko selection ke aur kareeb le jayega!\"_"
        )
        try:
            await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
        except Exception:
            pass

async def hourly_countdown_worker(chat_id, message_id, event_name, exam_date_str, context: ContextTypes.DEFAULT_TYPE):
    try:
        while True:
            await asyncio.sleep(3600)
            if chat_id not in active_hourly_tasks:
                break
            remaining = get_time_remaining(exam_date_str)
            if not remaining or "nikal chuka" in remaining:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"🎯 **Target:** `{event_name}`\n\n🎉 **Exam time aa gaya!**",
                    parse_mode="Markdown"
                )
                break
            current_time_str = datetime.now(TIMEZONE).strftime("%I:%M %p")
            stop_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop Hourly Tracker", callback_data="btn_stop_hourly")]])
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"🔄 **HOURLY LIVE COUNTDOWN (Active)**\n\n"
                    f"🎯 **Target:** `{event_name}`\n"
                    f"📅 **Date:** `{exam_date_str} IST`\n\n"
                    f"⏳ **Time Left:**\n👉 `{remaining}`\n\n"
                    f"🕒 _Last updated: {current_time_str} IST_\n"
                    f"⏱️ _Yeh message har 1 ghante me auto-update hoga._"
                ),
                reply_markup=stop_kb,
                parse_mode="Markdown"
            )
    except asyncio.CancelledError:
        pass
    finally:
        active_hourly_tasks.pop(chat_id, None)

async def pomodoro_worker(chat_id, context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.sleep(1500)
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔔 **Pomodoro Complete (25 mins)!**\n\nFocus session pura hua. 5 minute ka short break lein aur paani pi lein. ☕",
            parse_mode="Markdown"
        )
    except asyncio.CancelledError:
        pass
    finally:
        active_pomodoro_tasks.pop(chat_id, None)

# ----------------- Start & Menu Handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = (
        "🎯 **Study & Exam Tracker Bot**\n\n"
        "Apne daily targets, countdown, study streak aur revision vault manage karein.\n"
        "Neeche buttons se choose karein:"
    )
    kb = main_menu_keyboard(user_id)
    if update.message:
        await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.edit_text(msg, reply_markup=kb, parse_mode="Markdown")

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    u_data = get_user_data(user_id)

    if data.startswith("rtoggle_"):
        item_id = int(data.split("_")[1])
        toggle_routine_item(item_id)
        await query.answer("Routine status updated!")
        await query.message.edit_reply_markup(reply_markup=routine_keyboard(user_id))
        return

    if data.startswith("toggle_"):
        tid = int(data.split("_")[1])
        toggle_task(tid)
        await query.answer("Task updated!")
        await query.message.edit_reply_markup(reply_markup=tasks_keyboard(user_id))
        return

    await query.answer()

    if data == "btn_routine_menu":
        items = get_daily_routine_items(user_id)
        status_info = (
            "📋 **Daily Routine Checklist**\n\n"
            "• Subah routine daalein aur din bhar tick karein.\n"
            "• Yeh list raat **11:58 PM** par automatically fresh reset ho jayega.\n\n"
        )
        if not items:
            status_info += "⚠️ _Aaj ke liye koi routine add nahi kiya gaya hai. Neeche button se add karein:_"
        else:
            done_count = sum(1 for _, _, is_d in items if is_d)
            status_info += f"📊 **Progress:** `{done_count}/{len(items)} Completed`"

        await query.message.edit_text(status_info, reply_markup=routine_keyboard(user_id), parse_mode="Markdown")

    elif data == "btn_static_cd":
        if not u_data or not u_data.get("exam_date"):
            await query.message.edit_text("⚠️ Pehle Exam set karein!", reply_markup=not_set_keyboard(), parse_mode="Markdown")
            return
        remaining = get_time_remaining(u_data["exam_date"])
        await query.message.reply_text(
            f"🎯 **Target:** `{u_data.get('exam_name')}`\n"
            f"📅 **Date:** `{u_data['exam_date']} IST`\n\n"
            f"⏳ **Time Left:**\n👉 `{remaining}`",
            parse_mode="Markdown"
        )

    elif data == "btn_hourly_cd":
        if not u_data or not u_data.get("exam_date"):
            await query.message.edit_text("⚠️ Pehle Exam set karein!", reply_markup=not_set_keyboard(), parse_mode="Markdown")
            return
        if user_id in active_hourly_tasks:
            active_hourly_tasks[user_id].cancel()

        event_name = u_data.get("exam_name")
        exam_date = u_data.get("exam_date")
        remaining = get_time_remaining(exam_date)
        current_time_str = datetime.now(TIMEZONE).strftime("%I:%M %p")
        stop_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop Hourly Tracker", callback_data="btn_stop_hourly")]])

        sent_msg = await query.message.reply_text(
            f"🔄 **HOURLY LIVE COUNTDOWN (Active)**\n\n"
            f"🎯 **Target:** `{event_name}`\n"
            f"📅 **Date:** `{exam_date} IST`\n\n"
            f"⏳ **Time Left:**\n👉 `{remaining}`\n\n"
            f"🕒 _Started at: {current_time_str} IST_\n"
            f"⏱️ _Yeh message har 1 ghante me silently update hoga._",
            reply_markup=stop_kb,
            parse_mode="Markdown"
        )
        task = asyncio.create_task(hourly_countdown_worker(user_id, sent_msg.message_id, event_name, exam_date, context))
        active_hourly_tasks[user_id] = task

    elif data == "btn_stop_hourly":
        if user_id in active_hourly_tasks:
            active_hourly_tasks[user_id].cancel()
            active_hourly_tasks.pop(user_id, None)
            await query.message.edit_text("⏹️ **Hourly Tracker stopped.**", parse_mode="Markdown")

    elif data == "btn_pomodoro_menu":
        is_running = user_id in active_pomodoro_tasks
        status_text = "🟢 **Active**" if is_running else "⚪ **Inactive**"
        kb = [
            [InlineKeyboardButton("▶️ Start 25m Focus Session", callback_data="btn_start_pomodoro")],
            [InlineKeyboardButton("⏹️ Stop Pomodoro", callback_data="btn_stop_pomodoro")],
            [InlineKeyboardButton("🔙 Back", callback_data="btn_back_main")]
        ]
        await query.message.edit_text(
            f"⏱️ **Pomodoro Study Timer**\n\nStatus: {status_text}\n\n25 minute bina phone dekhe padhai karein.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )

    elif data == "btn_start_pomodoro":
        if user_id in active_pomodoro_tasks:
            await query.message.reply_text("Ek session pehle se chal raha hai!")
            return
        task = asyncio.create_task(pomodoro_worker(user_id, context))
        active_pomodoro_tasks[user_id] = task
        await query.message.reply_text("⏳ **Pomodoro Started (25 mins)!** Padhai shuru karein. 🎯")

    elif data == "btn_stop_pomodoro":
        if user_id in active_pomodoro_tasks:
            active_pomodoro_tasks[user_id].cancel()
            active_pomodoro_tasks.pop(user_id, None)
            await query.message.edit_text("⏹️ Pomodoro session stop ho gaya.")

    elif data == "btn_notes_menu":
        notes = get_revision_notes(user_id)
        if not notes:
            text = "📌 **Quick Formula & Revision Vault**\n\nAapne abhi tak koi formulas ya quick notes save nahi kiye hain."
        else:
            text = "📌 **Aapka Formula & Quick Revision Vault:**\n\n"
            for _, n_text, created_at in notes[:10]:
                text += f"🔹 `{n_text}` _({created_at})_\n"
            if len(notes) > 10:
                text += f"\n_...aur {len(notes)-10} notes saved hain._"

        await query.message.edit_text(text, reply_markup=notes_keyboard(), parse_mode="Markdown")

    elif data == "btn_clear_notes":
        clear_all_revision_notes(user_id)
        await query.message.edit_text("✅ Saare formulas/notes vault se clear ho gaye.", reply_markup=notes_keyboard())

    elif data == "btn_tasks_menu":
        await query.message.edit_text("📋 **General Tasks Checklist:**", reply_markup=tasks_keyboard(user_id))

    elif data == "btn_clear_done":
        clear_completed_tasks(user_id)
        await query.message.edit_reply_markup(reply_markup=tasks_keyboard(user_id))

    elif data == "btn_stats_menu":
        today_h, week_h, total_h = get_study_stats(user_id)
        streak = u_data.get("current_streak", 0) if u_data else 0
        kb = [
            [InlineKeyboardButton("➕ Log Study Hours", callback_data="btn_log_study")],
            [InlineKeyboardButton("🔙 Back", callback_data="btn_back_main")]
        ]
        await query.message.edit_text(
            f"📊 **Study Hours & Consistency**\n\n"
            f"🔥 **Current Study Streak:** `{streak}` Days\n"
            f"📅 **Aaj ki Padhai:** `{today_h}` Hours\n"
            f"📈 **Last 7 Days:** `{week_h}` Hours\n"
            f"🏆 **All-Time Total:** `{total_h}` Hours\n\n"
            f"Roz study hours log karke apni streak banaye rakhein!",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )

    elif data == "btn_reminder_menu":
        rem_status = f"`{u_data['reminder_time']}` IST" if (u_data and u_data.get("reminder_time")) else "Koi reminder set nahi hai."
        kb = [
            [InlineKeyboardButton("➕ Set Daily Reminder", callback_data="btn_set_rem")],
            [InlineKeyboardButton("❌ Cancel All Reminders", callback_data="btn_cancel_rem")],
            [InlineKeyboardButton("🔙 Back", callback_data="btn_back_main")]
        ]
        await query.message.edit_text(
            f"⏰ **Reminder Settings**\n\n🔔 **Active:** {rem_status}",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )

    elif data == "btn_cancel_rem":
        jobs = context.job_queue.get_jobs_by_name(str(user_id))
        for job in jobs:
            job.schedule_removal()
        update_user_field(user_id, "reminder_time", None)
        await query.message.reply_text("✅ Reminders cancel ho gaye.", reply_markup=main_menu_keyboard(user_id))

    elif data == "btn_quote":
        import random
        quotes = [
            "Consistency is what transforms average into excellence.",
            "Aaj ka hard work kal ka confidence banega.",
            "Success is the sum of small efforts repeated day in and day out.",
            "Aaj ka dard kal ka result banega."
        ]
        await query.message.reply_text(f"🔥 *Motivation:* \"{random.choice(quotes)}\"", parse_mode="Markdown")

    elif data == "btn_live_not_set":
        await query.message.edit_text("⚠️ Pehle Exam set karein!", reply_markup=not_set_keyboard(), parse_mode="Markdown")

    elif data == "btn_back_main":
        await start(update, context)

# ----------------- Conversation: Set Routine -----------------
async def ask_routine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    msg = (
        "📝 **Set Daily Routine Checklist**\n\n"
        "Apne aaj ke saare targets ek hi message me alag-alag line me likhkar bhejein:\n\n"
        "📌 **Example:**\n"
        "06:30 AM - Morning Revision\n"
        "Physics Chapter 4 Practice\n"
        "Mock Test 1 & Analysis\n"
        "Evening Walk & Dinner\n\n"
        "_(Yeh raat 11:58 PM par automatically reset ho jayega)_"
    )
    await query.message.reply_text(msg, parse_mode="Markdown")
    return SET_ROUTINE_STATE

async def save_routine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    if not lines:
        await update.message.reply_text("Kripya kam se kam ek task likhein:")
        return SET_ROUTINE_STATE

    set_daily_routine_items(user_id, lines)
    await update.message.reply_text(
        "✅ **Aapka Daily Routine Checklist ban gaya!**\n\nDin bhar isme diye gaye boxes ko tap karke tick karein. Raat **11:58 PM** par yeh khud-b-khud fresh reset ho jayega.",
        reply_markup=routine_keyboard(user_id),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ----------------- Conversation: Exam Setup -----------------
async def ask_exam_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    msg = "🎯 **Event Setup:**\nFormat: `Event Name | YYYY-MM-DD HH:MM`\nExample: `NEET | 2027-05-02 14:00`"
    await query.message.reply_text(msg, parse_mode="Markdown")
    return SET_EXAM

async def save_exam_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    try:
        parts = text.split("|")
        if len(parts) != 2:
            raise ValueError()
        name, dt_str = parts[0].strip(), parts[1].strip()
        datetime.strptime(dt_str, "%Y-%m-%d %H:%M")

        update_user_field(user_id, "exam_name", name)
        update_user_field(user_id, "exam_date", dt_str)

        await update.message.reply_text(
            f"✅ **Event Set!**\n🎯 `{name}` on `{dt_str} IST`",
            reply_markup=main_menu_keyboard(user_id),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ Galat format! Use: `Event Name | YYYY-MM-DD HH:MM`")
        return SET_EXAM

# ----------------- Conversation: Reminder -----------------
async def ask_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("⏰ Reminder time dalein (`HH:MM` 24-hr format, jaise `06:30` ya `21:00`):")
    return SET_REMINDER

async def send_reminder_alarm(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(
        chat_id=job.chat_id,
        text="⏰ **Study Reminder!**\nDesk par baithne ka waqt ho gaya hai. Apne targets check karein!",
        parse_mode="Markdown"
    )

async def save_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    try:
        h, m = map(int, text.split(":"))
        rem_time = time(hour=h, minute=m, tzinfo=TIMEZONE)

        old_jobs = context.job_queue.get_jobs_by_name(str(user_id))
        for j in old_jobs:
            j.schedule_removal()

        context.job_queue.run_daily(send_reminder_alarm, time=rem_time, chat_id=user_id, name=str(user_id))
        update_user_field(user_id, "reminder_time", text)

        await update.message.reply_text(f"✅ Reminder scheduled: **{text} IST**", reply_markup=main_menu_keyboard(user_id), parse_mode="Markdown")
        return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ Galat format! Example: `07:30`")
        return SET_REMINDER

# ----------------- Conversation: General Task -----------------
async def ask_new_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📋 Task ka naam likhein:")
    return ADD_TASK_STATE

async def save_new_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    add_user_task(user_id, text)
    await update.message.reply_text(f"✅ Task added: `{text}`", reply_markup=tasks_keyboard(user_id), parse_mode="Markdown")
    return ConversationHandler.END

# ----------------- Conversation: Log Study Hours -----------------
async def ask_log_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("⏱️ Aaj kitne ghante padhai ki? (e.g. `3.5` ya `5`):")
    return LOG_STUDY_STATE

async def save_study_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    try:
        hrs = float(text)
        if hrs <= 0 or hrs > 24:
            raise ValueError()
        streak = add_study_hours(user_id, hrs)
        today_h, week_h, total_h = get_study_stats(user_id)
        await update.message.reply_text(
            f"✅ **{hrs} Hours Logged!**\n\n"
            f"🔥 **Current Streak:** `{streak}` Days Streak!\n"
            f"📅 **Today:** `{today_h}` hrs | 🏆 **Total:** `{total_h}` hrs",
            reply_markup=main_menu_keyboard(user_id),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ Valid number dalein (jaise `2` ya `4.5`).")
        return LOG_STUDY_STATE

# ----------------- Conversation: Revision Note / Formula -----------------
async def ask_new_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📌 Jo formula, concept ya rule save karna hai yahan text me bhejein:\n\nExample: `Ohm's Law: V = I * R`")
    return ADD_NOTE_STATE

async def save_new_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    add_revision_note(user_id, text)
    await update.message.reply_text(
        f"✅ **Saved to Formula Vault!**\n\n📌 `{text}`",
        reply_markup=notes_keyboard(),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ----------------- Startup Recovery & Jobs -----------------
async def restore_and_setup_jobs(application: Application):
    reminders = get_all_active_reminders()
    for user_id, time_str in reminders:
        try:
            h, m = map(int, time_str.split(":"))
            rem_time = time(hour=h, minute=m, tzinfo=TIMEZONE)
            application.job_queue.run_daily(send_reminder_alarm, time=rem_time, chat_id=user_id, name=str(user_id))
        except Exception as e:
            print(f"Error restoring reminder: {e}")

    eod_time = time(hour=23, minute=30, tzinfo=TIMEZONE)
    application.job_queue.run_daily(end_of_day_review_job, time=eod_time, name="eod_review_job")

    reset_time = time(hour=23, minute=58, tzinfo=TIMEZONE)
    application.job_queue.run_daily(reset_daily_routines_job, time=reset_time, name="routine_reset_job")

    morning_time = time(hour=7, minute=0, tzinfo=TIMEZONE)
    application.job_queue.run_daily(morning_briefing_job, time=morning_time, name="morning_briefing_job")

    print("Background Jobs Active: EOD Review (11:30 PM), Auto-Reset (11:58 PM), Morning Briefing (7:00 AM).")

# ----------------- Main Execution -----------------
def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN environment variable nahi mila! Railway ke Variables tab me BOT_TOKEN set karein.")

    init_db()
    app = Application.builder().token(TOKEN).post_init(restore_and_setup_jobs).build()

    exam_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_exam_date, pattern="^btn_set_exam$")],
        states={SET_EXAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_exam_date)]},
        fallbacks=[CommandHandler("start", start)],
    )
    routine_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_routine, pattern="^btn_set_routine$")],
        states={SET_ROUTINE_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_routine)]},
        fallbacks=[CommandHandler("start", start)],
    )
    reminder_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_reminder, pattern="^btn_set_rem$")],
        states={SET_REMINDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_reminder)]},
        fallbacks=[CommandHandler("start", start)],
    )
    task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_new_task, pattern="^btn_add_task$")],
        states={ADD_TASK_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_task)]},
        fallbacks=[CommandHandler("start", start)],
    )
    study_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_log_hours, pattern="^btn_log_study$")],
        states={LOG_STUDY_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_study_hours)]},
        fallbacks=[CommandHandler("start", start)],
    )
    note_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_new_note, pattern="^btn_add_note$")],
        states={ADD_NOTE_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_note)]},
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(exam_conv)
    app.add_handler(routine_conv)
    app.add_handler(reminder_conv)
    app.add_handler(task_conv)
    app.add_handler(study_conv)
    app.add_handler(note_conv)
    app.add_handler(CallbackQueryHandler(button_router))

    print("Exam Tracker Super Bot is up and running...")
    app.run_polling()

if __name__ == "__main__":
    main()
