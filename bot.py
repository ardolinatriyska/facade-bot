from email.mime import message
import os
from dotenv import load_dotenv

load_dotenv()

import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import telebot
from telebot.types import KeyboardButton, ReplyKeyboardMarkup


TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
SHEET_ID = os.getenv("SHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")

KYIV_TZ = ZoneInfo("Europe/Kyiv")

if not TOKEN:
    raise ValueError("TOKEN is not set")

bot = telebot.TeleBot(TOKEN)
BOT_INFO = bot.get_me()
BOT_USERNAME = BOT_INFO.username
BOT_ID = BOT_INFO.id

DIALOG_GOAL_TEXT = (
    "Моя задача — допомогти підготувати дані для акту приймання-передачі виконаних робіт.\n"
    "Можу допомогти уточнити:\n"
    "1. обʼєкт;\n"
    "2. захватку;\n"
    "3. вид робіт;\n"
    "4. період виконання;\n"
    "5. працівників;\n"
    "6. години;\n"
    "7. примітки до акту."
)
def is_direct_message_to_bot(message):
    if message.chat.type == "private":
        return True

    text = message.text or ""

    if BOT_USERNAME and f"@{BOT_USERNAME}" in text:
        return True

    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id == BOT_ID

    return False

def handle_dialog_message(message):
    user_name = get_user_name(message)
    text = (message.text or "").replace(f"@{BOT_USERNAME}", "").strip()

    if not text:
        reply = (
            f"{user_name}, я на звʼязку.\n\n"
            f"{DIALOG_GOAL_TEXT}\n\n"
            "Напишіть, що саме потрібно підготувати або уточнити."
        )
    else:
        reply = (
            f"{user_name}, прийняв повідомлення.\n\n"
            f"Текст звернення:\n{text}\n\n"
            f"{DIALOG_GOAL_TEXT}\n\n"
            "Для підготовки акту вкажіть, будь ласка:\n"
            "обʼєкт, захватку, період робіт і вид виконаних робіт."
        )

    send_with_keyboard(message, reply)
    
def show_status(message):
    user = get_user(message.from_user.id, get_user_name(message))

    if not user["shift_started"]:
        send_with_keyboard(
            message,
            f"Працівник: {user['full_name']}\n"
            f"Telegram ID: {message.from_user.id}\n"
            f"Статус: поза зміною"
        )
        return

    current_break = timedelta()

    if user["break_active"] and user["break_start_time"] is not None:
        current_break = now_dt() - user["break_start_time"]

    status_text = (
        f"Працівник: {user['full_name']}\n"
        f"Telegram ID: {message.from_user.id}\n"
        f"Початок зміни: {format_datetime(user['shift_start_time'])}\n"
        f"Статус: {'перерва' if user['break_active'] else 'у зміні'}\n"
        f"Накопичені перерви: {format_duration(user['total_break'] + current_break)}"
    )

    send_with_keyboard(message, status_text)
    
@bot.message_handler(commands=["chat_id"])
def chat_id_command(message):
    bot.send_message(
        message.chat.id,
        f"chat_id цієї групи:\n{message.chat.id}"
    )
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@bot.message_handler(commands=["my_id"])
def my_id_command(message):
    bot.send_message(
        message.chat.id,
        f"Твій user_id:\n{message.from_user.id}"
    )
def get_sheet():
    if GOOGLE_CREDENTIALS_FILE:
        credentials = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE,
            scopes=SCOPES,
        )
    elif GOOGLE_CREDENTIALS:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        credentials = Credentials.from_service_account_info(
            creds_dict,
            scopes=SCOPES,
        )
    else:
        raise ValueError("GOOGLE_CREDENTIALS_FILE або GOOGLE_CREDENTIALS не задано")

    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet
def get_or_create_worksheet(spreadsheet, title, headers):
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=title,
            rows=1000,
            cols=len(headers)
        )

    current_cols = worksheet.col_count
    if current_cols < len(headers):
        worksheet.add_cols(len(headers) - current_cols)

    worksheet.update("A1", [headers])

    return worksheet


def get_lookup_row(spreadsheet, sheet_name, key_column, key_value):
    try:
        sheet = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        return {}

    rows = sheet.get_all_records()

    for row in rows:
        if str(row.get(key_column)) == str(key_value):
            return row

    return {}


def save_shift_to_sheet(message, user, shift_end, total_time, work_time):
    spreadsheet = get_sheet()

    worker = get_lookup_row(
        spreadsheet,
        "workers",
        "telegram_user_id",
        message.from_user.id
    )

    capture = get_lookup_row(
        spreadsheet,
        "captures",
        "telegram_chat_id",
        message.chat.id
    )

    headers = [
        "timestamp",
        "date",
        "chat_id",
        "telegram_user_id",
        "worker_name",
        "role",
        "brigade",
        "project",
        "capture",
        "sheet_name",
        "shift_start",
        "shift_end",
        "total_time",
        "break_time",
        "work_time",
        "work_hours",
    ]

    worksheet = get_or_create_worksheet(
        spreadsheet,
        "shifts",
        headers
    )

    worksheet.append_row([
        format_datetime(now_dt()),
        shift_end.strftime("%d.%m.%Y"),
        str(message.chat.id),
        str(message.from_user.id),
        worker.get("ПІБ") or user["full_name"],
        worker.get("роль", ""),
        worker.get("бригада", ""),
        capture.get("project", ""),
        capture.get("capture", ""),
        capture.get("sheet_name", ""),
        format_datetime(user["shift_start_time"]),
        format_datetime(shift_end),
        format_duration(total_time),
        format_duration(user["total_break"]),
        format_duration(work_time),
        round(work_time.total_seconds() / 3600, 2),
    ])
def get_capture_sheet(chat_id):
    spreadsheet = get_sheet()
    captures_sheet = spreadsheet.worksheet("captures")

    rows = captures_sheet.get_all_records()

    for row in rows:
        if str(row["telegram_chat_id"]) == str(chat_id):
            return spreadsheet.worksheet(row["sheet_name"])

    return None

users = {}

START_SHIFT_TEXT = "Початок зміни"
START_BREAK_TEXT = "Перерва"
STOP_BREAK_TEXT = "Стоп перерви"
END_SHIFT_TEXT = "Кінець зміни"
STATUS_TEXT = "Мій статус"


def now_dt():
    return datetime.now(KYIV_TZ)


def format_datetime(value):
    if value is None:
        return "-"
    return value.strftime("%d.%m.%Y %H:%M:%S")


def format_duration(duration):
    total_seconds = int(duration.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def main_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(
        KeyboardButton(START_SHIFT_TEXT),
        KeyboardButton(START_BREAK_TEXT),
    )
    markup.row(
        KeyboardButton(STOP_BREAK_TEXT),
        KeyboardButton(END_SHIFT_TEXT),
    )
    markup.row(KeyboardButton(STATUS_TEXT))
    return markup


def get_user_name(message):
    first = message.from_user.first_name or ""
    last = message.from_user.last_name or ""
    full_name = f"{first} {last}".strip()
    if not full_name:
        full_name = message.from_user.username or str(message.from_user.id)
    return full_name


def get_user(user_id, full_name):
    return users.setdefault(
        user_id,
        {
            "full_name": full_name,
            "shift_started": False,
            "shift_start_time": None,
            "break_active": False,
            "break_start_time": None,
            "total_break": timedelta(),
        },
    )


def send_with_keyboard(message, text):
    bot.send_message(message.chat.id, text, reply_markup=main_keyboard())


def start_shift(message):
    user = get_user(message.from_user.id, get_user_name(message))

    if user["shift_started"]:
        send_with_keyboard(
            message,
            f"{user['full_name']}\nЗміна вже відкрита.\nПочаток: {format_datetime(user['shift_start_time'])}",
        )
        return

    user["shift_started"] = True
    user["shift_start_time"] = now_dt()
    user["break_active"] = False
    user["break_start_time"] = None
    user["total_break"] = timedelta()

    send_with_keyboard(
        message,
        f"{user['full_name']}\nПочаток зміни зафіксовано.\nЧас: {format_datetime(user['shift_start_time'])}",
    )


def start_break(message):
    user = get_user(message.from_user.id, get_user_name(message))

    if not user["shift_started"]:
        send_with_keyboard(
            message,
            "Перерва недоступна.\nСпочатку натисніть «Початок зміни».",
        )
        return

    if user["break_active"]:
        send_with_keyboard(
            message,
            f"{user['full_name']}\nПерерва вже триває.\nПочаток перерви: {format_datetime(user['break_start_time'])}",
        )
        return

    user["break_active"] = True
    user["break_start_time"] = now_dt()

    send_with_keyboard(
        message,
        f"{user['full_name']}\nПерерва почалась.\nЧас: {format_datetime(user['break_start_time'])}",
    )


def stop_break(message):
    user = get_user(message.from_user.id, get_user_name(message))

    if not user["shift_started"]:
        send_with_keyboard(message, "Зміна ще не розпочата.")
        return

    if not user["break_active"] or user["break_start_time"] is None:
        send_with_keyboard(message, "Активної перерви зараз немає.")
        return

    break_end = now_dt()
    break_duration = break_end - user["break_start_time"]
    user["total_break"] += break_duration
    user["break_active"] = False
    user["break_start_time"] = None

    send_with_keyboard(
        message,
        f"{user['full_name']}\nПерерву завершено.\n"
        f"Тривалість цієї перерви: {format_duration(break_duration)}\n"
        f"Загальний час перерв: {format_duration(user['total_break'])}",
    )


def end_shift(message):
    user = get_user(message.from_user.id, get_user_name(message))

    if not user["shift_started"] or user["shift_start_time"] is None:
        send_with_keyboard(message, "Немає активної зміни для завершення.")
        return

    shift_end = now_dt()

    if user["break_active"] and user["break_start_time"] is not None:
        user["total_break"] += shift_end - user["break_start_time"]
        user["break_active"] = False
        user["break_start_time"] = None

    total_time = shift_end - user["shift_start_time"]
    work_time = total_time - user["total_break"]
    save_shift_to_sheet(
        message,
        user,
        shift_end,
        total_time,
        work_time
    )

    summary = (
        f"{user['full_name']}\n"
        "Кінець зміни зафіксовано.\n\n"
        f"Початок: {format_datetime(user['shift_start_time'])}\n"
        f"Кінець: {format_datetime(shift_end)}\n"
        f"Загальна тривалість: {format_duration(total_time)}\n"
        f"Перерви: {format_duration(user['total_break'])}\n"
        f"Чистий робочий час: {format_duration(work_time)}"
    )

    user["shift_started"] = False
    user["shift_start_time"] = None
    user["break_active"] = False
    user["break_start_time"] = None
    user["total_break"] = timedelta()

    send_with_keyboard(message, summary)
    def show_status(message):
        user = get_user(message.from_user.id, get_user_name(message))

        if not user["shift_started"]:
            send_with_keyboard(
                message,
                f"Працівник: {user['full_name']}\n"
                f"Telegram ID: {message.from_user.id}\n"
                f"Статус: поза зміною"
            )
            return

        current_break = timedelta()

        if user["break_active"] and user["break_start_time"] is not None:
            current_break = now_dt() - user["break_start_time"]

        status_text = (
            f"Працівник: {user['full_name']}\n"
            f"Telegram ID: {message.from_user.id}\n"
            f"Початок зміни: {format_datetime(user['shift_start_time'])}\n"
            f"Статус: {'перерва' if user['break_active'] else 'у зміні'}\n"
            f"Накопичені перерви: {format_duration(user['total_break'] + current_break)}"
        )

        if user["break_active"]:
            status_text += (
                f"\nПочаток перерви: "
                f"{format_datetime(user['break_start_time'])}"
            )

        send_with_keyboard(message, status_text)
def start_command(message):
    full_name = get_user_name(message)
    get_user(message.from_user.id, full_name)

    send_with_keyboard(
        message,
        f"Вітаю, {full_name}.\n\n"
        "Використовуйте кнопки нижче або команди:\n"
        "/work - початок зміни\n"
        "/break - почати перерву\n"
        "/stop_break - завершити перерву\n"
        "/stop - кінець зміни\n"
        "/status - мій статус",
    )


@bot.message_handler(commands=["work"])
def work_command(message):
    start_shift(message)


@bot.message_handler(commands=["break"])
def break_command(message):
    start_break(message)


@bot.message_handler(commands=["stop_break"])
def stop_break_command(message):
    stop_break(message)


@bot.message_handler(commands=["stop"])
def stop_command(message):
    end_shift(message)


@bot.message_handler(commands=["status"])
def status_command(message):
    show_status(message)


@bot.message_handler(content_types=["text"])
def handle_text(message):
    text = (message.text or "").strip()

    commands_map = {
        START_SHIFT_TEXT: start_shift,
        START_BREAK_TEXT: start_break,
        STOP_BREAK_TEXT: stop_break,
        END_SHIFT_TEXT: end_shift,
        STATUS_TEXT: show_status,
    }

    handler = commands_map.get(text)

    if handler:
        handler(message)
        
    
    if is_direct_message_to_bot(message):
        handle_dialog_message(message)
        return

    if message.chat.type == "private":
        send_with_keyboard(
            message,
            "Команда не розпізнана. Будь ласка, скористайтеся кнопками меню.",
        )


print("Bot is running...")
bot.infinity_polling(skip_pending=True)
