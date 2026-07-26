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
@bot.message_handler(commands=["thread_id"])
def thread_id_command(message):
    thread_id = getattr(message, "message_thread_id", None)

    bot.send_message(
        message.chat.id,
        f"chat_id:\n{message.chat.id}\n\nthread_id:\n{thread_id}",
        message_thread_id=thread_id
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


def get_active_captures():
    spreadsheet = get_sheet()
    captures_sheet = spreadsheet.worksheet("Захватки")
    captures = []

    for row in captures_sheet.get_all_records():
        if str(row.get("active", "")).strip().upper() != "TRUE":
            continue
        if not row.get("назва"):
            continue
        captures.append({
            "capture_id": str(row.get("capture_id", "")).strip(),
            "name": str(row.get("назва", "")).strip(),
            "project": str(row.get("обʼєкт", "")).strip(),
        })

    return captures


def save_shift_to_sheet(message, user, shift_end, total_time, work_time):
    capture = user.get("shift_capture")
    if not capture:
        raise ValueError("Не вибрано захватку для зміни")

    worker = get_worker(message.from_user.id) or {}
    spreadsheet = get_sheet()
    worksheet = spreadsheet.worksheet("Зміни")
    if worksheet.row_values(1) != SHIFT_HEADERS:
        worksheet.update("A1", [SHIFT_HEADERS])

    worksheet.append_row([
        shift_end.strftime("%d.%m.%Y"),
        worker.get("name") or user["full_name"],
        worker.get("role", ""),
        format_duration(work_time),
        capture["name"],
        capture["project"],
        worker.get("brigade", ""),
        format_datetime(user["shift_start_time"]),
        format_datetime(shift_end),
        format_duration(total_time),
        format_duration(user["total_break"]),
        round(work_time.total_seconds() / 3600, 2),
        str(message.from_user.id),
        str(message.chat.id),
        format_datetime(now_dt()),
    ])
def get_worker(user_id):
    spreadsheet = get_sheet()
    workers_sheet = spreadsheet.worksheet("Працівники")

    rows = workers_sheet.get_all_records()

    for row in rows:
        if str(row.get("telegram_user_id", "")).strip() == str(user_id).strip():
            active = str(row.get("active", "")).strip().upper()

            if active != "TRUE":
                return None

            return {
                "telegram_user_id": str(row.get("telegram_user_id", "")).strip(),
                "name": str(row.get("ПІБ", "")).strip(),
                "role": str(row.get("роль", "")).strip(),
                "brigade": str(row.get("бригада", "")).strip(),
            }

    return None
users = {}

START_SHIFT_TEXT = "Початок зміни"
START_BREAK_TEXT = "Перерва"
STOP_BREAK_TEXT = "Стоп перерви"
SELECT_CAPTURE_TEXT = "Обрати захватку"
END_SHIFT_TEXT = "Кінець зміни"
STATUS_TEXT = "Мій статус"

SHIFT_HEADERS = [
    "дата",
    "працівник",
    "роль",
    "чистий час",
    "захватка",
    "обʼєкт",
    "бригада",
    "початок зміни",
    "кінець зміни",
    "загальна тривалість",
    "перерви",
    "години",
    "telegram_user_id",
    "telegram_chat_id",
    "timestamp",
]


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
    markup = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    markup.row(
        KeyboardButton(START_SHIFT_TEXT),
        KeyboardButton(START_BREAK_TEXT),
    )
    markup.row(
        KeyboardButton(STOP_BREAK_TEXT),
        KeyboardButton(END_SHIFT_TEXT),
    )
    markup.row(KeyboardButton(STATUS_TEXT), KeyboardButton(SELECT_CAPTURE_TEXT))
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
            "selected_capture": None,
            "shift_capture": None,
        },
    )


def send_with_keyboard(message, text):
    send_options = {"reply_markup": main_keyboard()}
    thread_id = getattr(message, "message_thread_id", None)

    if thread_id is not None:
        send_options["message_thread_id"] = thread_id

    bot.send_message(message.chat.id, text, **send_options)


def choose_capture(message):
    captures = get_active_captures()
    if not captures:
        send_with_keyboard(message, "Немає активних захваток у таблиці «Well Place 2».")
        return

    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for capture in captures:
        markup.row(KeyboardButton(capture["name"]))
    markup.row(KeyboardButton("Скасувати вибір"))

    options = {"reply_markup": markup}
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None:
        options["message_thread_id"] = thread_id
    bot.send_message(message.chat.id, "Оберіть захватку для наступної зміни:", **options)


def select_capture(message, capture_name):
    user = get_user(message.from_user.id, get_user_name(message))
    for capture in get_active_captures():
        if capture["name"] == capture_name:
            user["selected_capture"] = capture
            send_with_keyboard(message, f"Обрано захватку: {capture['name']}\nОбʼєкт: {capture['project']}")
            return True
    return False


def start_shift(message):
    user = get_user(message.from_user.id, get_user_name(message))

    if user["shift_started"]:
        send_with_keyboard(
            message,
            f"{user['full_name']}\nЗміна вже відкрита.\nПочаток: {format_datetime(user['shift_start_time'])}",
        )
        return

    if not user.get("selected_capture"):
        send_with_keyboard(message, "Спершу оберіть захватку кнопкою «Обрати захватку».")
        return

    user["shift_started"] = True
    user["shift_start_time"] = now_dt()
    user["break_active"] = False
    user["break_start_time"] = None
    user["total_break"] = timedelta()
    user["shift_capture"] = user["selected_capture"]

    send_with_keyboard(
        message,
        f"{user['full_name']}\nПочаток зміни зафіксовано.\n"
        f"Захватка: {user['shift_capture']['name']}\n"
        f"Час: {format_datetime(user['shift_start_time'])}",
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
    user["shift_capture"] = None

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
@bot.message_handler(commands=["start"])
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
        SELECT_CAPTURE_TEXT: choose_capture,
    }

    handler = commands_map.get(text)

    if handler:
        handler(message)
        return

    if text == "Скасувати вибір":
        send_with_keyboard(message, "Вибір захватки скасовано.")
        return

    if select_capture(message, text):
        return
    
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
