from email.mime import message
import os
from dotenv import load_dotenv

load_dotenv()

import json
import re
import base64
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MATERIALS_CHAT_ID = os.getenv("MATERIALS_CHAT_ID")
MATERIALS_THREAD_ID = os.getenv("MATERIALS_THREAD_ID")

# "Події" у WellPlaceBOT. Ця тема має ідентифікатор 1 у посиланні Telegram.
WEATHER_CHAT_ID = -1004258418040
WEATHER_THREAD_ID = 1
EVENTS_CHAT_ID = os.getenv("EVENTS_CHAT_ID", str(WEATHER_CHAT_ID))
EVENTS_THREAD_ID = os.getenv("EVENTS_THREAD_ID", str(WEATHER_THREAD_ID))
BOT_TOPIC_CHAT_ID = os.getenv("BOT_TOPIC_CHAT_ID", str(WEATHER_CHAT_ID))
BOT_TOPIC_THREAD_ID = os.getenv("BOT_TOPIC_THREAD_ID", "82")
CALCULATIONS_CHAT_ID = os.getenv("CALCULATIONS_CHAT_ID", str(WEATHER_CHAT_ID))
CALCULATIONS_THREAD_ID = os.getenv("CALCULATIONS_THREAD_ID")
WEATHER_LATITUDE = 49.8157
WEATHER_LONGITUDE = 24.1346

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
    thread_id = getattr(message, "message_thread_id", None)
    options = {"message_thread_id": thread_id} if thread_id is not None else {}
    bot.send_message(
        message.chat.id,
        f"chat_id цієї групи:\n{message.chat.id}",
        **options,
    )
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@bot.message_handler(commands=["my_id"])
def my_id_command(message):
    thread_id = getattr(message, "message_thread_id", None)
    options = {"message_thread_id": thread_id} if thread_id is not None else {}
    bot.send_message(
        message.chat.id,
        f"Твій user_id:\n{message.from_user.id}",
        **options,
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
    if not SHEET_ID:
        raise ValueError("SHEET_ID не задано")

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


def normalize_sheet_header(value):
    value = str(value or "").strip().lower()
    return " ".join(
        value.replace("’", "'").replace("ʼ", "'").replace("`", "'").replace("_", " ").split()
    )


def get_record_value(record, aliases):
    normalized_aliases = {normalize_sheet_header(alias) for alias in aliases}
    for key, value in record.items():
        if normalize_sheet_header(key) in normalized_aliases:
            return value
    return ""


def get_capture_objects(spreadsheet):
    """Read unique object names from the existing capture directory without changing it."""
    try:
        captures_sheet = spreadsheet.worksheet("Захватки")
    except gspread.WorksheetNotFound:
        return []

    objects = []
    seen = set()
    for row in captures_sheet.get_all_records():
        name = str(
            get_record_value(row, {"об'єкт", "проєкт", "проект", "object", "project"}) or ""
        ).strip()
        key = name.casefold()
        if name and key not in seen:
            objects.append(name)
            seen.add(key)
    return objects


def get_or_create_objects_worksheet(spreadsheet):
    """Create only the explicitly approved object directory when it is absent."""
    try:
        return spreadsheet.worksheet(OBJECTS_SHEET)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=OBJECTS_SHEET,
            rows=1000,
            cols=len(OBJECTS_HEADERS),
        )
        values = [OBJECTS_HEADERS]
        values.extend([[name, "TRUE"] for name in get_capture_objects(spreadsheet)])
        worksheet.update("A1", values)
        return worksheet


def get_active_objects(create_if_missing=True):
    spreadsheet = get_sheet()
    if create_if_missing:
        worksheet = get_or_create_objects_worksheet(spreadsheet)
    else:
        worksheet = spreadsheet.worksheet(OBJECTS_SHEET)
    headers = worksheet.row_values(1)
    if not any(
        normalize_sheet_header(header) in {"об'єкт", "назва", "object", "name", "project", "проєкт"}
        for header in headers
    ):
        raise ValueError("Вкладка «Об’єкти» повинна містити колонку «об’єкт» або «назва».")

    objects = []
    seen = set()
    for row in worksheet.get_all_records():
        active = str(get_record_value(row, {"active", "активний", "активна"}) or "TRUE").strip().upper()
        if active in {"FALSE", "0", "НІ", "NO", "CLOSED", "INACTIVE"}:
            continue
        name = str(
            get_record_value(row, {"об'єкт", "назва", "object", "name", "project", "проєкт"}) or ""
        ).strip()
        key = name.casefold()
        if name and key not in seen:
            objects.append({"name": name})
            seen.add(key)
    return objects


def event_field_for_header(header):
    normalized = normalize_sheet_header(header)
    for field, aliases in EVENT_FIELD_ALIASES.items():
        if normalized in {normalize_sheet_header(alias) for alias in aliases}:
            return field
    return None


def event_fields_from_headers(headers):
    fields = [event_field_for_header(header) for header in headers]
    if "date" not in fields and "event_type" in fields:
        event_type_index = fields.index("event_type")
        if event_type_index > 0 and fields[event_type_index - 1] is None:
            fields[event_type_index - 1] = "date"
    if "status" not in fields and len(fields) > 4 and fields[4] is None:
        fields[4] = "status"
    return fields


def get_question_worksheet(spreadsheet):
    worksheets = spreadsheet.worksheets()
    by_title = {worksheet.title.strip(): worksheet for worksheet in worksheets}
    for title in (EVENTS_SHEET, QUESTIONS_SHEET):
        if title in by_title:
            return by_title[title]
    raise ValueError("У налаштованій таблиці немає вкладки «Події» або «Питання».")


def append_event_to_sheet(message, event_type, object_name, content, target_worker=None):
    """Append a question or task while keeping target and initiator separate."""
    spreadsheet = get_sheet()
    worksheet = get_question_worksheet(spreadsheet)

    headers = worksheet.row_values(1)
    fields = event_fields_from_headers(headers)
    missing_fields = {
        required
        for required in ("event_type", "object", "question", "status", "target", "initiator")
        if required not in fields
    }
    if missing_fields:
        raise ValueError(
            "Цільова вкладка повинна мати колонки для типу, об’єкта, тексту, "
            "адресата/виконавця та ініціатора/реєстратора."
        )

    timestamp = now_dt()
    initiator_name = get_user_name(message)
    target_name = (target_worker or {}).get("name") or initiator_name
    values = {
        "date": timestamp.strftime("%d.%m.%Y"),
        "time": timestamp.strftime("%H:%M:%S"),
        "event_type": event_type,
        "object": object_name,
        "question": content,
        "target": target_name,
        "initiator": initiator_name,
        "author": initiator_name,
        "telegram_user_id": str(message.from_user.id),
        "telegram_chat_id": str(message.chat.id),
        "telegram_message_id": str(message.message_id),
        "telegram_thread_id": str(getattr(message, "message_thread_id", "") or ""),
        "status": ACTIVE_EVENT_STATUS,
        "timestamp": format_datetime(timestamp),
    }
    worksheet.append_row(
        [values.get(field, "") for field in fields],
        value_input_option="RAW",
    )
    return worksheet.title.strip()


def get_event_records(event_type, active_only=False):
    spreadsheet = get_sheet()
    worksheet = get_question_worksheet(spreadsheet)
    rows = worksheet.get_all_values()
    if not rows:
        return []

    fields = event_fields_from_headers(rows[0])
    records = []
    for row_number, row in enumerate(rows[1:], start=2):
        padded = list(row) + [""] * max(0, len(fields) - len(row))
        record = {
            field: padded[index]
            for index, field in enumerate(fields)
            if field is not None
        }
        if str(record.get("event_type", "")).strip() != event_type:
            continue
        status = str(record.get("status", "")).strip() or ACTIVE_EVENT_STATUS
        if active_only and status.casefold() not in {
            ACTIVE_EVENT_STATUS.casefold(),
            "active",
            "нове",
        }:
            continue
        record["status"] = status
        record["row_number"] = row_number
        records.append(record)
    return records


def set_event_final_status(row_number, event_type):
    records = get_event_records(event_type)
    record = next((item for item in records if item["row_number"] == row_number), None)
    if not record:
        raise ValueError("Запис не знайдено або його тип змінився.")
    if record["status"].casefold() not in {
        ACTIVE_EVENT_STATUS.casefold(),
        "active",
        "нове",
    }:
        raise ValueError("Цей запис уже не активний.")

    new_status = (
        RESOLVED_QUESTION_STATUS
        if event_type == QUESTION_TEXT
        else COMPLETED_TASK_STATUS
    )
    worksheet = get_question_worksheet(get_sheet())
    worksheet.update_cell(row_number, 5, new_status)
    return new_status


def append_question_to_events(message, object_name, question, target_worker=None):
    return append_event_to_sheet(
        message,
        QUESTION_TEXT,
        object_name,
        question,
        target_worker,
    )


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


def is_active_sheet_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().upper() in {
        "TRUE", "1", "ТАК", "YES", "ACTIVE",
    }


def sheet_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return 0.0
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def get_active_payment_workers():
    worksheet = get_sheet().worksheet("Працівники")
    workers = []
    for row in worksheet.get_all_records():
        name = str(get_record_value(row, {"піб", "працівник", "name"}) or "").strip()
        active = get_record_value(row, {"active", "активний", "активна"})
        if not name or not is_active_sheet_value(active):
            continue
        workers.append({
            "telegram_user_id": str(
                get_record_value(row, {"telegram_user_id", "telegram id", "user id", ""}) or ""
            ).strip(),
            "name": name,
            "rate": sheet_number(
                get_record_value(row, {"ставка, грн/год", "ставка грн/год", "ставка"})
            ),
            "initial_balance": sheet_number(
                get_record_value(
                    row,
                    {"початковий баланс, грн", "початковий баланс грн", "початковий баланс"},
                )
            ),
        })
    return workers


def get_active_captures_for_object(object_name):
    worksheet = get_sheet().worksheet("Захватки")
    captures = []
    expected_object = str(object_name or "").strip().casefold()
    for row_number, row in enumerate(worksheet.get_all_records(), start=2):
        active = get_record_value(row, {"active", "активна", "активний"})
        name = str(get_record_value(row, {"назва", "захватка", "name"}) or "").strip()
        project = str(
            get_record_value(row, {"об'єкт", "проєкт", "проект", "object", "project"}) or ""
        ).strip()
        if not name or not is_active_sheet_value(active):
            continue
        if expected_object and project.casefold() != expected_object:
            continue
        captures.append({
            "capture_id": str(get_record_value(row, {"capture_id", "id"}) or "").strip(),
            "name": name,
            "project": project,
            "row_number": row_number,
        })
    return captures


def validate_payments_sheet(worksheet):
    headers = worksheet.row_values(1)[:len(PAYMENT_HEADERS)]
    if [normalize_sheet_header(value) for value in headers] != [
        normalize_sheet_header(value) for value in PAYMENT_HEADERS
    ]:
        raise ValueError(
            "Вкладка «Виплати» повинна мати колонки A:K у погодженому порядку."
        )


def append_payment_operation(message, operation):
    worksheet = get_sheet().worksheet(PAYMENTS_SHEET)
    validate_payments_sheet(worksheet)
    amount = operation.get("amount", 0.0)
    paid = amount if operation["type"] in {"Аванс", "Повний розрахунок"} else 0.0
    adjustment = amount if operation["type"] == "Коригування" else 0.0
    worksheet.append_row(
        [
            now_dt().strftime("%d.%m.%Y"),
            operation["worker"]["name"],
            paid,
            adjustment,
            operation.get("comment", ""),
            operation["type"],
            operation.get("object", {}).get("name", ""),
            operation.get("capture", {}).get("name", ""),
            operation.get("capture_status", "Відкрита"),
            get_user_name(message),
            str(message.from_user.id),
        ],
        value_input_option="USER_ENTERED",
    )


def deactivate_capture(capture):
    worksheet = get_sheet().worksheet("Захватки")
    headers = worksheet.row_values(1)
    active_column = next(
        (
            index
            for index, header in enumerate(headers, start=1)
            if normalize_sheet_header(header) in {"active", "активна", "активний"}
        ),
        None,
    )
    if active_column is None:
        raise ValueError("У вкладці «Захватки» немає колонки active.")
    worksheet.update_cell(capture["row_number"], active_column, False)


def get_worker_financial_summary(worker):
    spreadsheet = get_sheet()
    worker_name = worker["name"]
    daily_sheet = spreadsheet.worksheet("Денні дані")
    total_hours = sum(
        sheet_number(get_record_value(row, {"години", "hours"}))
        for row in daily_sheet.get_all_records()
        if str(get_record_value(row, {"працівник", "піб", "worker"}) or "").strip().casefold()
        == worker_name.casefold()
    )
    payments_sheet = spreadsheet.worksheet(PAYMENTS_SHEET)
    validate_payments_sheet(payments_sheet)
    payment_rows = [
        row
        for row in payments_sheet.get_all_records()
        if str(get_record_value(row, {"працівник"}) or "").strip().casefold()
        == worker_name.casefold()
    ]
    paid = sum(sheet_number(get_record_value(row, {"виплачено, грн"})) for row in payment_rows)
    adjustment = sum(
        sheet_number(get_record_value(row, {"коригування, грн"})) for row in payment_rows
    )
    accrued = round(total_hours * worker.get("rate", 0.0), 2)
    balance = round(worker.get("initial_balance", 0.0) + accrued - paid + adjustment, 2)
    return {
        "initial_balance": worker.get("initial_balance", 0.0),
        "hours": round(total_hours, 2),
        "rate": worker.get("rate", 0.0),
        "accrued": accrued,
        "paid": round(paid, 2),
        "adjustment": round(adjustment, 2),
        "balance": balance,
        "payments": payment_rows,
    }


def save_shift_to_sheet(
    message,
    user,
    shift_end,
    total_time,
    work_time,
    worker=None,
    chat_id=None,
):
    capture = user.get("shift_capture")
    if not capture:
        raise ValueError("Не вибрано захватку для зміни")

    message_user_id = message.from_user.id if message is not None else ""
    worker = worker or get_worker(message_user_id) or {}
    worker_user_id = worker.get("telegram_user_id") or message_user_id
    chat_id = chat_id if chat_id is not None else message.chat.id
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
        str(worker_user_id),
        str(chat_id),
        format_datetime(now_dt()),
    ])


def get_worker(user_id):
    spreadsheet = get_sheet()
    workers_sheet = spreadsheet.worksheet("Працівники")

    rows = workers_sheet.get_all_records()

    for row in rows:
        row_user_id = str(
            get_record_value(row, {"telegram_user_id", "telegram id", "user id", ""}) or ""
        ).strip()
        if row_user_id == str(user_id).strip():
            active = str(get_record_value(row, {"active", "активний", "активна"}) or "").strip().upper()

            if active != "TRUE":
                return None

            return {
                "telegram_user_id": row_user_id,
                "name": str(get_record_value(row, {"піб", "працівник", "name"}) or "").strip(),
                "role": str(get_record_value(row, {"роль", "role"}) or "").strip(),
                "brigade": str(get_record_value(row, {"бригада", "brigade"}) or "").strip(),
            }

    return None


def get_active_workers():
    spreadsheet = get_sheet()
    workers_sheet = spreadsheet.worksheet("Працівники")
    workers = []

    for row in workers_sheet.get_all_records():
        user_id = str(
            get_record_value(row, {"telegram_user_id", "telegram id", "user id", ""}) or ""
        ).strip()
        name = str(get_record_value(row, {"піб", "працівник", "name"}) or "").strip()
        active = str(get_record_value(row, {"active", "активний", "активна"}) or "").strip().upper()
        if active != "TRUE" or not user_id or not name:
            continue
        workers.append({
            "telegram_user_id": user_id,
            "name": name,
            "role": str(get_record_value(row, {"роль", "role"}) or "").strip(),
            "brigade": str(get_record_value(row, {"бригада", "brigade"}) or "").strip(),
        })

    return workers


users = {}

AUTO_SHIFT_END_HOUR = 18

START_SHIFT_TEXT = "Початок зміни"
START_BREAK_TEXT = "Перерва"
STOP_BREAK_TEXT = "Стоп перерви"
SELECT_CAPTURE_TEXT = "Обрати захватку"
END_SHIFT_TEXT = "Кінець зміни"
STATUS_TEXT = "Мій статус"
WHO_ON_SHIFT_TEXT = "Хто на зміні"
WEATHER_FORECAST_TEXT = "Прогноз погоди"
MATERIALS_TEXT = "Взяв"
MATERIAL_RETURN_TEXT = "Повернув"
INVENTORY_TEXT = "Інвентаризація"
BALANCE_TEXT = "Перевірити залишок"
MATERIAL_ORDER_TEXT = "Замовити"
DEFECT_TEXT = "Дефектний акт"
MATERIAL_CONFIRM_TEXT = "Підтвердити"
MATERIAL_CANCEL_TEXT = "Скасувати матеріал"
INVENTORY_CONFIRM_TEXT = "Підтвердити інвентаризацію"
INVOICE_CONFIRM_TEXT = "Підтвердити накладну"
DEFECT_CONFIRM_TEXT = "Підтвердити дефектний акт"
MATERIAL_OTHER_MASTER_TEXT = "Взяв інший майстер"
MARK_FOR_MASTER_TEXT = "Позначити за майстра"
DELEGATE_CANCEL_TEXT = "Скасувати позначення"
DELEGATE_CONFIRM_TEXT = "Підтвердити позначення"
DELEGATE_NOW_TEXT = "Поточний час"
DELEGATE_OTHER_TIME_TEXT = "Вказати інший час"
QUESTION_TEXT = "Питання"
TASK_TEXT = "Завдання"
EVENT_CANCEL_TEXT = "Скасувати"
EVENT_SELF_TEXT = "Не обирати — собі"
ACTIVE_QUESTIONS_TEXT = "Актуальні питання"
ACTIVE_TASKS_TEXT = "Актуальні завдання"
EVENT_LIST_BACK_TEXT = "Назад до подій"
RESOLVE_QUESTION_TEXT = "Позначити вирішеним"
COMPLETE_TASK_TEXT = "Позначити виконаним"

CALC_ADVANCE_TEXT = "💵 Видати аванс"
CALC_FULL_PAYMENT_TEXT = "✅ Повний розрахунок"
CALC_ADJUSTMENT_TEXT = "✏️ Коригування"
CALC_BALANCE_TEXT = "📊 Баланс працівника"
CALC_HISTORY_TEXT = "📜 Історія виплат"
CALC_CLOSE_CAPTURE_TEXT = "🏁 Закрити захватку"
CALC_CONFIRM_TEXT = "Підтвердити"
CALC_CANCEL_TEXT = "Скасувати розрахунок"
CALC_SKIP_COMMENT_TEXT = "Пропустити"

PAYMENTS_SHEET = "Виплати"
PAYMENT_HEADERS = [
    "Дата", "Працівник", "Виплачено, грн", "Коригування, грн", "Коментар",
    "Тип операції", "Об’єкт", "Захватка", "Статус захватки", "Хто вніс",
    "Telegram ID",
]

ACTIVE_EVENT_STATUS = "Активне"
RESOLVED_QUESTION_STATUS = "Вирішено"
COMPLETED_TASK_STATUS = "Виконано"

OBJECTS_SHEET = "Об’єкти"
EVENTS_SHEET = "Події"
QUESTIONS_SHEET = "Питання"
OBJECTS_HEADERS = ["об’єкт", "active"]

EVENT_FIELD_ALIASES = {
    "date": {"дата", "date"},
    "time": {"час", "time"},
    "event_type": {"тип", "тип події", "тема", "подія", "категорія", "topic", "event", "event type"},
    "object": {"об'єкт", "проєкт", "проект", "object", "project"},
    "question": {
        "питання", "текст питання", "опис", "опис події", "текст", "коментар", "question",
    },
    "target": {
        "адресат / виконавець", "адресат/виконавець", "адресат", "виконавець",
        "призначений", "recipient", "assignee",
    },
    "initiator": {
        "ініціатор / реєстратор", "ініціатор/реєстратор", "ініціатор", "реєстратор",
        "створив", "initiator", "created by",
    },
    "author": {"автор", "працівник", "користувач", "піб", "user", "user name"},
    "telegram_user_id": {"telegram user id", "telegram_user_id", "user id", "user_id"},
    "telegram_chat_id": {"telegram chat id", "telegram_chat_id", "chat id", "chat_id"},
    "telegram_message_id": {
        "telegram message id", "telegram_message_id", "message id", "message_id",
    },
    "telegram_thread_id": {
        "telegram thread id", "telegram_thread_id", "thread id", "thread_id",
    },
    "status": {"статус", "status"},
    "timestamp": {"timestamp", "created at", "created_at", "дата створення"},
}

MATERIAL_LOG_HEADERS = [
    "дата", "операція", "матеріал", "кількість", "од. виміру",
    "Захватка", "працівник", "роль", "примітка", "telegram_user_id",
    "telegram_chat_id", "timestamp",
]

MATERIAL_BALANCE_HEADERS = [
    "матеріал", "од. виміру", "початковий залишок", "надійшло",
    "видано", "поточний залишок",
]

DEFECT_HEADERS = [
    "дата", "номер акта", "матеріал", "кількість", "од. виміру",
    "захватка", "працівник", "роль", "опис", "telegram_file_id",
    "telegram_user_id", "telegram_chat_id", "telegram_message_id", "timestamp",
]

DAILY_MATERIAL_SHEET = "Рух матеріалів"
DAILY_MOVEMENT_COLUMNS = {
    "Видача": "Видано",
    "Повернення": "Прийнято",
    "Дефект": "Видано",
    "Надходження": "Прийнято",
    "Замовлення": "Замовлення",
}

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


def parse_operation_datetime(value):
    text = str(value or "").strip()
    for date_format in ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=KYIV_TZ)
        except ValueError:
            pass

    try:
        entered_time = datetime.strptime(text, "%H:%M").time()
        return datetime.combine(now_dt().date(), entered_time, tzinfo=KYIV_TZ)
    except ValueError as error:
        raise ValueError("Невірний формат дати й часу") from error


def get_vynnyky_weather_text():
    """Return a compact hourly ECMWF forecast for the working part of the day."""
    query = urllib.parse.urlencode({
        "latitude": WEATHER_LATITUDE,
        "longitude": WEATHER_LONGITUDE,
        "hourly": "temperature_2m,wind_speed_10m,precipitation_probability",
        "models": "ecmwf_ifs025",
        "timezone": "Europe/Kyiv",
        "forecast_days": 1,
    })
    request = urllib.request.Request(
        f"https://api.open-meteo.com/v1/forecast?{query}",
        headers={"User-Agent": "RAHUY-Bot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))

    hourly = data.get("hourly", {})
    today = now_dt().date().isoformat()
    lines = []
    for timestamp, temperature, wind, precipitation in zip(
        hourly.get("time", []),
        hourly.get("temperature_2m", []),
        hourly.get("wind_speed_10m", []),
        hourly.get("precipitation_probability", []),
    ):
        if not timestamp.startswith(today):
            continue
        hour = int(timestamp[11:13])
        if 6 <= hour <= 21:
            lines.append(
                f"{hour:02d}:00 — {temperature:+.0f}°C | вітер {wind:.0f} км/год | опади {precipitation:.0f}%"
            )

    if not lines:
        raise RuntimeError("Прогноз на сьогодні не отримано")

    return (
        "Доброго ранку! ☀️\n\n"
        f"Погода у Винниках на {now_dt():%d.%m}:\n"
        + "\n".join(lines)
        + "\n\nВсім гарного робочого дня!"
    )


def get_remaining_vynnyky_weather_text():
    """Return hourly weather from the current Kyiv hour through today's end."""
    current = now_dt()
    query = urllib.parse.urlencode({
        "latitude": WEATHER_LATITUDE,
        "longitude": WEATHER_LONGITUDE,
        "hourly": "temperature_2m,wind_speed_10m,precipitation_probability",
        "models": "ecmwf_ifs025",
        "timezone": "Europe/Kyiv",
        "forecast_days": 1,
    })
    request = urllib.request.Request(
        f"https://api.open-meteo.com/v1/forecast?{query}",
        headers={"User-Agent": "RAHUY-Bot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))

    hourly = data.get("hourly", {})
    today = current.date().isoformat()
    lines = []
    for timestamp, temperature, wind, precipitation in zip(
        hourly.get("time", []),
        hourly.get("temperature_2m", []),
        hourly.get("wind_speed_10m", []),
        hourly.get("precipitation_probability", []),
    ):
        if not timestamp.startswith(today):
            continue
        hour = int(timestamp[11:13])
        if hour >= current.hour:
            lines.append(
                f"{hour:02d}:00 — {temperature:+.0f}°C | вітер {wind:.0f} км/год | опади {precipitation:.0f}%"
            )

    if not lines:
        raise RuntimeError("Прогноз до кінця дня не отримано")

    return (
        f"Погода у Винниках до кінця дня — {current:%d.%m}:\n\n"
        + "\n".join(lines)
    )


def send_weather_forecast():
    """Send the forecast to the General topic renamed to «Події»."""
    bot.send_message(
        WEATHER_CHAT_ID,
        get_vynnyky_weather_text(),
    )


def weather_scheduler():
    """Send one weekday forecast at 06:00 Kyiv time while the bot is running."""
    last_sent_date = None
    while True:
        current = now_dt()
        if (
            current.weekday() != 6
            and current.hour == 6
            and current.minute < 5
            and last_sent_date != current.date()
        ):
            try:
                send_weather_forecast()
                last_sent_date = current.date()
            except Exception as error:
                print(f"Weather forecast failed: {error}")
        time.sleep(30)


def show_remaining_weather_forecast(message):
    try:
        forecast_text = get_remaining_vynnyky_weather_text()
    except Exception as error:
        print(f"Manual weather forecast failed: {error}")
        forecast_text = "Не вдалося отримати прогноз погоди. Спробуйте ще раз пізніше."

    send_with_keyboard(message, forecast_text)


def format_datetime(value):
    if value is None:
        return "-"
    return value.strftime("%d.%m.%Y %H:%M:%S")


def format_duration(duration):
    total_seconds = int(duration.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def round_up_to_minute(duration, minimum_one_minute=False):
    total_seconds = max(0, int(duration.total_seconds()))
    if total_seconds == 0 and not minimum_one_minute:
        return timedelta()

    total_minutes = max(1, (total_seconds + 59) // 60)
    return timedelta(minutes=total_minutes)


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
    markup.row(KeyboardButton(MARK_FOR_MASTER_TEXT))
    return markup


def bot_topic_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    markup.row(KeyboardButton(WHO_ON_SHIFT_TEXT), KeyboardButton(STATUS_TEXT))
    markup.row(KeyboardButton(WEATHER_FORECAST_TEXT))
    return markup


def events_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    markup.row(KeyboardButton(QUESTION_TEXT), KeyboardButton(TASK_TEXT))
    markup.row(KeyboardButton(ACTIVE_QUESTIONS_TEXT), KeyboardButton(ACTIVE_TASKS_TEXT))
    return markup


def materials_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    markup.row(KeyboardButton(MATERIALS_TEXT), KeyboardButton(MATERIAL_RETURN_TEXT))
    markup.row(KeyboardButton(INVENTORY_TEXT), KeyboardButton(DEFECT_TEXT))
    markup.row(KeyboardButton(BALANCE_TEXT), KeyboardButton(MATERIAL_ORDER_TEXT))
    markup.row(KeyboardButton(MATERIAL_OTHER_MASTER_TEXT))
    return markup


def calculations_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    markup.row(KeyboardButton(CALC_ADVANCE_TEXT), KeyboardButton(CALC_FULL_PAYMENT_TEXT))
    markup.row(KeyboardButton(CALC_ADJUSTMENT_TEXT), KeyboardButton(CALC_BALANCE_TEXT))
    markup.row(KeyboardButton(CALC_HISTORY_TEXT), KeyboardButton(CALC_CLOSE_CAPTURE_TEXT))
    return markup


def is_materials_topic(message):
    """Material operations are permitted only in the configured Telegram topic."""
    thread_id = getattr(message, "message_thread_id", None)
    return (
        bool(MATERIALS_CHAT_ID and MATERIALS_THREAD_ID)
        and str(message.chat.id) == str(MATERIALS_CHAT_ID)
        and str(thread_id) == str(MATERIALS_THREAD_ID)
    )


def is_events_topic(message):
    """Event actions are shown only in the configured «Події» topic."""
    thread_id = getattr(message, "message_thread_id", None)
    return (
        bool(EVENTS_CHAT_ID and EVENTS_THREAD_ID)
        and str(message.chat.id) == str(EVENTS_CHAT_ID)
        and str(thread_id) == str(EVENTS_THREAD_ID)
    )


def is_bot_topic(message):
    """Summary actions are shown only in the configured «БОТ» topic."""
    thread_id = getattr(message, "message_thread_id", None)
    return (
        bool(BOT_TOPIC_CHAT_ID and BOT_TOPIC_THREAD_ID)
        and str(message.chat.id) == str(BOT_TOPIC_CHAT_ID)
        and str(thread_id) == str(BOT_TOPIC_THREAD_ID)
    )


def configured_calculations_topic(message):
    thread_id = getattr(message, "message_thread_id", None)
    return (
        bool(CALCULATIONS_CHAT_ID and CALCULATIONS_THREAD_ID)
        and str(message.chat.id) == str(CALCULATIONS_CHAT_ID)
        and str(thread_id) == str(CALCULATIONS_THREAD_ID)
    )


def is_calculations_topic(message):
    if configured_calculations_topic(message):
        return True
    user = users.get(str(message.from_user.id), {})
    fallback_context = user.get("calculations_context") or {}
    chat_id, thread_id = message_context(message)
    return (
        str(fallback_context.get("chat_id", "")) == chat_id
        and str(fallback_context.get("thread_id", "")) == thread_id
    )


def message_context(message):
    thread_id = getattr(message, "message_thread_id", None)
    return str(message.chat.id), "" if thread_id is None else str(thread_id)


def pending_delegate_matches_message(pending, message):
    chat_id, thread_id = message_context(message)
    return (
        str(pending.get("chat_id", "")) == chat_id
        and str(pending.get("thread_id", "")) == thread_id
    )


def get_user_name(message):
    first = message.from_user.first_name or ""
    last = message.from_user.last_name or ""
    full_name = f"{first} {last}".strip()
    if not full_name:
        full_name = message.from_user.username or str(message.from_user.id)
    return full_name


def get_user(user_id, full_name):
    user_key = str(user_id)
    return users.setdefault(
        user_key,
        {
            "full_name": full_name,
            "shift_started": False,
            "shift_start_time": None,
            "break_active": False,
            "break_start_time": None,
            "total_break": timedelta(),
            "selected_capture": None,
            "shift_capture": None,
            "shift_worker": None,
            "shift_chat_id": None,
            "shift_thread_id": None,
            "auto_close_in_progress": False,
            "pending_material": None,
            "pending_delegate": None,
            "material_for_worker": None,
            "pending_event": None,
            "pending_calculation": None,
            "calculations_context": None,
        },
    )


def send_with_markup(message, text, markup):
    send_options = {"reply_markup": markup}
    thread_id = getattr(message, "message_thread_id", None)

    if thread_id is not None:
        send_options["message_thread_id"] = thread_id

    bot.send_message(message.chat.id, text, **send_options)


def send_with_keyboard(message, text):
    if is_calculations_topic(message):
        keyboard = calculations_keyboard()
    elif is_materials_topic(message):
        keyboard = materials_keyboard()
    elif is_events_topic(message):
        keyboard = events_keyboard()
    elif is_bot_topic(message):
        keyboard = bot_topic_keyboard()
    else:
        keyboard = main_keyboard()
    send_with_markup(message, text, keyboard)


def shift_clock_text(value):
    if isinstance(value, datetime):
        return value.strftime("%H:%M")

    text = str(value or "").strip()
    match = re.search(r"(?:^|\s)(\d{1,2}:\d{2})(?::\d{2})?(?:$|\s)", text)
    return match.group(1) if match else (text or "-")


def get_completed_shifts_for_date(target_date):
    """Read completed shifts without changing the existing «Зміни» worksheet."""
    worksheet = get_sheet().worksheet("Зміни")
    expected_date = target_date.strftime("%d.%m.%Y")
    return [
        row
        for row in worksheet.get_all_records()
        if str(get_record_value(row, {"дата"}) or "").strip() == expected_date
    ]


def show_who_is_on_shift(message):
    current = now_dt()
    active_shifts = []
    for user in users.values():
        shift_start = user.get("shift_start_time")
        if not user.get("shift_started") or shift_start is None:
            continue
        worker = user.get("shift_worker") or {}
        active_shifts.append({
            "name": worker.get("name") or user.get("full_name") or "Працівник",
            "start": shift_start,
            "on_break": bool(user.get("break_active")),
        })
    active_shifts.sort(key=lambda item: item["start"])

    completed_shifts = []
    completed_error = False
    try:
        completed_shifts = get_completed_shifts_for_date(current.date())
    except Exception as error:
        completed_error = True
        print(f"Completed shift summary failed: {error}")

    lines = [f"Хто на зміні — {current.strftime('%d.%m.%Y')}", "", "🟢 Зараз працюють:"]
    if active_shifts:
        for item in active_shifts:
            status = "на перерві" if item["on_break"] else "на зміні"
            lines.append(
                f"• {item['name']} — початок {shift_clock_text(item['start'])} ({status})"
            )
    else:
        lines.append("— Нікого.")

    lines.extend(["", "✅ Закрили зміну сьогодні:"])
    if completed_error:
        lines.append("— Не вдалося отримати дані про завершені зміни.")
    elif completed_shifts:
        for row in completed_shifts:
            name = str(get_record_value(row, {"працівник"}) or "Працівник").strip()
            start = shift_clock_text(get_record_value(row, {"початок зміни"}))
            end = shift_clock_text(get_record_value(row, {"кінець зміни"}))
            breaks = str(get_record_value(row, {"перерви"}) or "-").strip()
            duration = str(
                get_record_value(row, {"загальна тривалість"}) or "-"
            ).strip()
            lines.extend([
                f"• {name}",
                f"  Початок: {start} · Кінець: {end}",
                f"  Перерви: {breaks} · Тривалість зміни: {duration}",
            ])
    else:
        lines.append("— Ніхто.")

    send_with_keyboard(message, "\n".join(lines))


def event_noun(event_type):
    return "питання" if event_type == QUESTION_TEXT else "завдання"


def event_target_label(event_type):
    return "Адресат" if event_type == QUESTION_TEXT else "Виконавець"


def send_event_object_selection(message, event_type, objects, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for item in objects:
        markup.row(KeyboardButton(item["name"]))
    markup.row(KeyboardButton(EVENT_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Оберіть об’єкт для {event_noun(event_type)}:",
        markup,
    )


def send_event_worker_selection(message, pending, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(EVENT_SELF_TEXT))
    for worker in pending["workers"]:
        markup.row(KeyboardButton(worker_button_text(worker)))
    markup.row(KeyboardButton(EVENT_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    target_word = "адресата питання" if pending["event_type"] == QUESTION_TEXT else "виконавця завдання"
    send_with_markup(
        message,
        f"{prefix}Необов’язково оберіть {target_word}. "
        f"Якщо нікого не обирати, запис буде призначено вам:",
        markup,
    )


def send_event_content_prompt(message, pending):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(EVENT_CANCEL_TEXT))
    target_worker = pending.get("target_worker")
    target_name = (target_worker or {}).get("name") or get_user_name(message)
    prompt = (
        "Напишіть питання одним повідомленням:"
        if pending["event_type"] == QUESTION_TEXT
        else "Опишіть завдання одним повідомленням:"
    )
    send_with_markup(
        message,
        f"Об’єкт: {pending['object']}\n"
        f"{event_target_label(pending['event_type'])}: {target_name}\n\n"
        f"{prompt}",
        markup,
    )


def start_event(message, event_type):
    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_event"] = None
    try:
        objects = get_active_objects()
    except Exception as error:
        print(f"Object directory loading failed: {error}")
        send_with_keyboard(message, f"Не вдалося завантажити об’єкти. {error}")
        return

    if not objects:
        send_with_keyboard(
            message,
            "У вкладці «Об’єкти» немає активних об’єктів. Додайте об’єкт і повторіть спробу.",
        )
        return

    user["pending_event"] = {
        "kind": "question" if event_type == QUESTION_TEXT else "task",
        "event_type": event_type,
        "stage": "object",
        "objects": objects,
    }
    send_event_object_selection(message, event_type, objects)


def start_question(message):
    start_event(message, QUESTION_TEXT)


def start_task(message):
    start_event(message, TASK_TEXT)


def event_record_button(record):
    prefix = f"#{record['row_number']} {record.get('object') or 'Без об’єкта'}: "
    content = str(record.get("question") or "Без тексту").strip()
    return (prefix + content)[:60]


def send_active_event_list(message, event_type, prefix=""):
    user = get_user(message.from_user.id, get_user_name(message))
    try:
        records = get_event_records(event_type, active_only=True)
    except Exception as error:
        print(f"Active event list loading failed: {error}")
        user["pending_event"] = None
        send_with_keyboard(message, f"Не вдалося завантажити активні записи. {error}")
        return

    if not records:
        user["pending_event"] = None
        message_text = f"Активних {event_noun(event_type)} немає."
        if prefix:
            message_text = f"{prefix}\n\n{message_text}"
        send_with_keyboard(message, message_text)
        return

    user["pending_event"] = {
        "kind": "manage",
        "event_type": event_type,
        "stage": "list",
        "records": records,
    }
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for record in records[:30]:
        markup.row(KeyboardButton(event_record_button(record)))
    markup.row(KeyboardButton(EVENT_LIST_BACK_TEXT))
    message_text = f"Оберіть активне {event_noun(event_type)}:"
    if prefix:
        message_text = f"{prefix}\n\n{message_text}"
    send_with_markup(message, message_text, markup)


def send_event_record_details(message, pending, error_text=""):
    record = pending["record"]
    action_text = (
        RESOLVE_QUESTION_TEXT
        if pending["event_type"] == QUESTION_TEXT
        else COMPLETE_TASK_TEXT
    )
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(action_text))
    markup.row(KeyboardButton(EVENT_LIST_BACK_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}#{record['row_number']} — {pending['event_type']}\n"
        f"Об’єкт: {record.get('object') or '-'}\n"
        f"{event_target_label(pending['event_type'])}: {record.get('target') or '-'}\n"
        f"Ініціатор: {record.get('initiator') or '-'}\n"
        f"Статус: {record.get('status') or ACTIVE_EVENT_STATUS}\n\n"
        f"{record.get('question') or '-'}",
        markup,
    )


def handle_events_text(message, text):
    user = get_user(message.from_user.id, get_user_name(message))

    if text == ACTIVE_QUESTIONS_TEXT:
        send_active_event_list(message, QUESTION_TEXT)
        return True

    if text == ACTIVE_TASKS_TEXT:
        send_active_event_list(message, TASK_TEXT)
        return True

    if text == QUESTION_TEXT:
        start_question(message)
        return True

    if text == TASK_TEXT:
        start_task(message)
        return True

    if text == EVENT_LIST_BACK_TEXT:
        user["pending_event"] = None
        send_with_keyboard(message, "Меню подій.")
        return True

    if text == EVENT_CANCEL_TEXT:
        if user.get("pending_event"):
            user["pending_event"] = None
            send_with_keyboard(message, "Створення запису скасовано.")
            return True
        return False

    pending = user.get("pending_event")
    if not pending:
        return False

    if pending.get("kind") == "manage" and pending.get("stage") == "list":
        selected = next(
            (
                record
                for record in pending["records"][:30]
                if event_record_button(record) == text
            ),
            None,
        )
        if not selected:
            send_active_event_list(
                message,
                pending["event_type"],
                "Запис не знайдено. Оберіть його кнопкою.",
            )
            return True
        pending["record"] = selected
        pending["stage"] = "details"
        send_event_record_details(message, pending)
        return True

    if pending.get("kind") == "manage" and pending.get("stage") == "details":
        expected_action = (
            RESOLVE_QUESTION_TEXT
            if pending["event_type"] == QUESTION_TEXT
            else COMPLETE_TASK_TEXT
        )
        if text != expected_action:
            send_event_record_details(message, pending, "Оберіть дію кнопкою.")
            return True
        try:
            new_status = set_event_final_status(
                pending["record"]["row_number"],
                pending["event_type"],
            )
        except Exception as error:
            print(f"Event status update failed: {error}")
            send_event_record_details(message, pending, f"Не вдалося змінити статус. {error}")
            return True
        event_type = pending["event_type"]
        send_active_event_list(
            message,
            event_type,
            f"Статус змінено на «{new_status}».",
        )
        return True

    main_actions = {
        START_SHIFT_TEXT,
        START_BREAK_TEXT,
        STOP_BREAK_TEXT,
        END_SHIFT_TEXT,
        STATUS_TEXT,
        SELECT_CAPTURE_TEXT,
        MARK_FOR_MASTER_TEXT,
    }
    if text in main_actions:
        user["pending_event"] = None
        return False

    if pending.get("stage") == "object":
        selected = next(
            (
                item
                for item in pending["objects"]
                if item["name"].casefold() == text.casefold()
            ),
            None,
        )
        if not selected:
            send_event_object_selection(
                message,
                pending["event_type"],
                pending["objects"],
                "Об’єкт не знайдено. Оберіть його кнопкою.",
            )
            return True

        pending["object"] = selected["name"]
        pending["stage"] = "worker"
        try:
            pending["workers"] = get_active_workers()
            error_text = "" if pending["workers"] else "Активних працівників не знайдено."
        except Exception as error:
            print(f"Worker directory loading failed: {error}")
            pending["workers"] = []
            error_text = "Не вдалося завантажити працівників."
        send_event_worker_selection(message, pending, error_text)
        return True

    if pending.get("stage") == "worker":
        if text == EVENT_SELF_TEXT:
            pending["target_worker"] = None
        else:
            selected_worker = find_worker_option(text, pending["workers"])
            if not selected_worker:
                send_event_worker_selection(
                    message,
                    pending,
                    "Працівника не знайдено. Оберіть його кнопкою або призначте запис собі.",
                )
                return True
            pending["target_worker"] = selected_worker
        pending["stage"] = "content"
        send_event_content_prompt(message, pending)
        return True

    if pending.get("stage") == "content":
        if not text:
            markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.row(KeyboardButton(EVENT_CANCEL_TEXT))
            send_with_markup(
                message,
                f"{pending['event_type']} не може бути порожнім. Введіть текст одним повідомленням:",
                markup,
            )
            return True
        try:
            sheet_title = append_event_to_sheet(
                message,
                pending["event_type"],
                pending["object"],
                text,
                pending.get("target_worker"),
            )
        except Exception as error:
            print(f"Event saving failed: {error}")
            markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.row(KeyboardButton(EVENT_CANCEL_TEXT))
            send_with_markup(message, f"Не вдалося записати {event_noun(pending['event_type'])}. {error}", markup)
            return True

        object_name = pending["object"]
        event_type = pending["event_type"]
        target_name = (pending.get("target_worker") or {}).get("name") or get_user_name(message)
        user["pending_event"] = None
        send_with_keyboard(
            message,
            f"{event_type} записано у вкладку «{sheet_title}».\n"
            f"Об’єкт: {object_name}\n"
            f"{event_target_label(event_type)}: {target_name}",
        )
        return True

    user["pending_event"] = None
    return False


def worker_button_text(worker):
    return f"{worker['name']} | {worker['telegram_user_id']}"


def find_worker_option(text, workers):
    normalized = str(text or "").strip().lower()
    for worker in workers:
        if normalized in {
            worker_button_text(worker).lower(),
            str(worker["telegram_user_id"]).lower(),
            worker["name"].lower(),
        }:
            return worker
    return None


def send_worker_selection(message, kind):
    workers = get_active_workers()
    if not workers:
        send_with_keyboard(message, "У таблиці «Працівники» немає активних працівників із Telegram ID.")
        return

    user = get_user(message.from_user.id, get_user_name(message))
    chat_id, thread_id = message_context(message)
    user["pending_delegate"] = {
        "kind": kind,
        "stage": "worker",
        "workers": workers,
        "chat_id": chat_id,
        "thread_id": thread_id,
    }
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for worker in workers:
        markup.row(KeyboardButton(worker_button_text(worker)))
    markup.row(KeyboardButton(DELEGATE_CANCEL_TEXT))
    prompt = (
        "Оберіть, хто взяв матеріал:"
        if kind == "material"
        else "Оберіть майстра або працівника, за якого потрібно позначити подію:"
    )
    send_with_markup(message, prompt, markup)


def delegate_action_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(START_SHIFT_TEXT), KeyboardButton(END_SHIFT_TEXT))
    markup.row(KeyboardButton(START_BREAK_TEXT), KeyboardButton(STOP_BREAK_TEXT))
    markup.row(KeyboardButton(DELEGATE_CANCEL_TEXT))
    return markup


def send_material_for_worker_prompt(message, worker, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(DELEGATE_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Введіть матеріал і кількість, які взяв «{worker['name']}».\n"
        "Наприклад: 5 кутиків або клей-піна 1 балон.",
        markup,
    )


def start_material_issue(message, error_text="", operation="Видача"):
    try:
        captures = get_active_captures()
    except Exception as error:
        print(f"Material capture selection failed: {error}")
        get_user(message.from_user.id, get_user_name(message))["pending_material"] = None
        send_with_keyboard(message, "Не вдалося отримати список захваток. Спробуйте ще раз пізніше.")
        return

    if not captures:
        get_user(message.from_user.id, get_user_name(message))["pending_material"] = None
        send_with_keyboard(message, "Немає активних захваток у таблиці «Захватки».")
        return

    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = {
        "kind": "issue_capture",
        "captures": captures,
        "operation": operation,
    }
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for capture in captures:
        markup.row(KeyboardButton(capture["name"]))
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    capture_question = (
        "Для якої захватки замовляємо матеріал?"
        if operation == "Замовлення"
        else "Для якої захватки беремо матеріал?"
    )
    send_with_markup(
        message,
        f"{prefix}{capture_question}",
        markup,
    )


def send_material_issue_input_prompt(message, capture, error_text="", operation="Видача"):
    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = {
        "kind": "issue_input",
        "capture": capture,
        "operation": operation,
    }
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    input_question = (
        "Який матеріал замовляємо та яка кількість?"
        if operation == "Замовлення"
        else "Який матеріал беремо та яка кількість?"
    )
    send_with_markup(
        message,
        f"{prefix}Захватка: {capture['name']}\n\n"
        f"{input_question}",
        markup,
    )


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


def normalize_material_text(value):
    value = str(value or "").lower().replace("ʼ", "'")
    value = value.replace("-", " ")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def material_aliases(row):
    aliases = str(row.get("Синоніми", "") or "")
    aliases = aliases.replace("Синоніми:", "").replace("синоніми:", "")
    return [normalize_material_text(alias) for alias in aliases.split(",") if alias.strip()]


def get_material_catalog():
    spreadsheet = get_sheet()
    worksheet = spreadsheet.worksheet("Довідник матеріалів")
    return [row for row in worksheet.get_all_records() if row.get("Матеріал")]


def extract_material_quantity(text):
    numeric_match = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if numeric_match:
        return float(numeric_match.group(1).replace(",", "."))

    word_numbers = {
        "один": 1, "одна": 1, "одне": 1, "два": 2, "дві": 2,
        "одну": 1,
        "три": 3, "чотири": 4, "пять": 5, "п'ять": 5,
        "пятьох": 5, "шість": 6, "сім": 7, "вісім": 8,
        "девять": 9, "дев'ять": 9, "десять": 10,
    }
    normalized = normalize_material_text(text)
    for word, number in word_numbers.items():
        if word in normalized.split():
            return float(number)
    return None


def material_match_score(text, row):
    normalized = normalize_material_text(text)
    material_name = normalize_material_text(row.get("Матеріал", ""))
    aliases = material_aliases(row)

    if material_name and material_name in normalized:
        return 1.0
    if any(alias and alias in normalized for alias in aliases):
        return 0.95

    query_tokens = set(normalized.split())
    name_tokens = set(material_name.split())
    alias_tokens = set(" ".join(aliases).split())
    meaningful_tokens = query_tokens - {
        "взяв", "взяла", "взяли", "видати", "видай", "видав", "мені",
        "будь", "ласка", "штук", "шт", "штуки", "балон", "балони",
        "один", "одна", "два", "дві", "три", "чотири", "пять", "п'ять",
    }
    material_tokens = name_tokens | alias_tokens
    overlap = meaningful_tokens & material_tokens
    stem_overlap = {
        query_token
        for query_token in meaningful_tokens
        for material_token in material_tokens
        if len(query_token) >= 4 and len(material_token) >= 4
        and query_token[:3] == material_token[:3]
    }
    if not overlap and not stem_overlap:
        return 0.0
    token_score = len(overlap | stem_overlap) / max(1, len(meaningful_tokens))
    similarity = SequenceMatcher(None, normalized, material_name).ratio()
    return max(token_score, similarity * 0.5)


def column_letter(column):
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(65 + remainder) + result
    return result


def parse_sheet_number(value):
    try:
        return float(str(value or "0").replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def is_cubic_unit(value):
    normalized = normalize_material_text(str(value or "").replace("³", "3"))
    compact = re.sub(r"[\s._-]+", "", normalized)
    return any(marker in compact for marker in ("м3", "мкуб", "куб"))


def insulation_thickness_mm(material):
    source = " ".join([
        str(material.get("Матеріал", "")),
        str(material.get("Синоніми", "")),
    ]).lower().replace(",", ".")
    matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*(мм|см)(?!\w)", source)
    for raw_value, unit in matches:
        value = float(raw_value)
        thickness = value * 10 if unit == "см" else value
        if 5 <= thickness <= 500:
            return thickness
    return None


def convert_invoice_quantity(material, quantity, source_unit):
    material_name = str(material.get("Матеріал", ""))
    is_insulation = normalize_material_text(material.get("Група", "")) == "утеплювачі"
    if not is_insulation or not (is_cubic_unit(source_unit) or is_cubic_unit(material_name)):
        return {
            "quantity": quantity,
            "unit": material.get("Од. виміру / примітка", "") or source_unit,
            "source_quantity": quantity,
            "source_unit": source_unit,
            "thickness_mm": None,
        }

    thickness_mm = insulation_thickness_mm(material)
    if not thickness_mm:
        return None

    return {
        "quantity": round(quantity * 1000 / thickness_mm, 3),
        "unit": "м.кв.",
        "source_quantity": quantity,
        "source_unit": source_unit or "м.куб.",
        "thickness_mm": thickness_mm,
    }


def append_unique_text(existing, value):
    entries = [part.strip() for part in str(existing or "").split(",") if part.strip()]
    if value and value not in entries:
        entries.append(value)
    return ", ".join(entries)


def date_matches(value, timestamp):
    value = str(value or "").strip()
    return value in {
        timestamp.strftime("%d.%m.%Y"),
        timestamp.strftime("%d.%m.%y"),
    }


def legacy_material_score(material, legacy_name):
    legacy = normalize_material_text(legacy_name)
    variants = [normalize_material_text(material.get("Матеріал", ""))]
    variants.extend(material_aliases(material))
    best_score = 0.0
    for variant in variants:
        if not variant or not legacy:
            continue
        if variant in legacy or legacy in variant:
            return 1.0
        variant_tokens = set(variant.split())
        legacy_tokens = set(legacy.split())
        overlap = variant_tokens & legacy_tokens
        if overlap:
            best_score = max(best_score, len(overlap) / max(1, len(variant_tokens)))
        best_score = max(best_score, SequenceMatcher(None, variant, legacy).ratio() * 0.6)
    return best_score


def get_daily_material_column(worksheet, material, operation):
    headers = worksheet.row_values(2)
    material_names = worksheet.row_values(3)
    best_match = (0.0, None)
    for index, legacy_name in enumerate(material_names, start=1):
        if not legacy_name:
            continue
        score = legacy_material_score(material, legacy_name)
        if score > best_match[0]:
            best_match = (score, index)

    if best_match[0] < 0.5 or best_match[1] is None:
        raise ValueError(f"Не знайшов колонку для матеріалу «{material['Матеріал']}» у «Рух матеріалів»")

    start_column = best_match[1]
    next_material = len(headers) + 1
    for index in range(start_column + 1, len(material_names) + 1):
        if material_names[index - 1]:
            next_material = index
            break

    target_header = DAILY_MOVEMENT_COLUMNS[operation]
    for index in range(start_column, next_material):
        if str(headers[index - 1]).strip() == target_header:
            return index

    raise ValueError(
        f"Для «{material['Матеріал']}» немає поля «{target_header}» у «Рух матеріалів»"
    )


def get_book_material_balance(worksheet, material):
    """Read the calculated on-book balance shown above a material's three columns."""
    received_column = get_daily_material_column(worksheet, material, "Надходження")
    return parse_sheet_number(worksheet.acell(f"{column_letter(received_column)}4").value)


def extend_movement_totals(worksheet, total_row, previous_last_data_row, new_last_data_row):
    formulas = worksheet.row_values(total_row, value_render_option="FORMULA")
    updates = []
    old_end = f":{previous_last_data_row})"
    new_end = f":{new_last_data_row})"
    for column, formula in enumerate(formulas, start=1):
        formula = str(formula or "")
        if formula.startswith("=SUM(") and formula.endswith(old_end):
            updates.append({
                "range": f"{column_letter(column)}{total_row}",
                "values": [[formula[:-len(old_end)] + new_end]],
            })
    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")


def get_daily_movement_row(worksheet, timestamp):
    """Return the visible daily row immediately after the existing records."""
    # Only the identity columns define the end of the journal. Material cells
    # contain helper formulas, so they must not influence the destination row.
    data_limit = 200
    rows = worksheet.get("A1:D200")
    details = [
        (index, row[:4])
        for index, row in enumerate(rows[4:data_limit], start=5)
    ]

    def row_has_data(row):
        return any(str(value or "").strip() for value in row)

    def is_bot_row(row):
        return any("RAHUY Bot" in str(value or "") for value in row[1:4])

    human_rows = [index for index, row in details if row_has_data(row) and not is_bot_row(row)]
    last_used = max(human_rows, default=4)
    detail_rows = dict(details)
    while last_used + 1 <= data_limit:
        next_row = detail_rows.get(last_used + 1, [])
        if row_has_data(next_row) and is_bot_row(next_row):
            last_used += 1
        else:
            break

    for index in range(5, last_used + 1):
        row = detail_rows.get(index, [])
        if row and date_matches(row[0], timestamp):
            return index

    return last_used + 1


def sync_daily_material_movement(
    spreadsheet,
    message,
    material,
    operation,
    quantity,
    worker,
    timestamp,
    source_name="",
    movement_label="",
):
    worksheet = spreadsheet.worksheet(DAILY_MATERIAL_SHEET)
    pending_updates = []
    row_number = get_daily_movement_row(worksheet, timestamp)
    if not worksheet.acell(f"A{row_number}").value:
        pending_updates.append({
            "range": f"'{DAILY_MATERIAL_SHEET}'!A{row_number}",
            "values": [[timestamp.strftime("%d.%m.%Y")]],
        })

    details = worksheet.get(f"B{row_number}:D{row_number}")
    details = details[0] if details else ["", "", ""]
    details += [""] * (3 - len(details))
    worker_name = worker.get("name") or get_user_name(message)
    if operation == "Видача":
        actor_worker = get_worker(message.from_user.id) or {}
        actor_id = str(actor_worker.get("telegram_user_id") or message.from_user.id)
        target_id = str(worker.get("telegram_user_id") or message.from_user.id)
        issued_by = "RAHUY"
        if actor_id != target_id:
            issued_by = actor_worker.get("name") or get_user_name(message)
        details[0] = append_unique_text(details[0], movement_label or "Видача через RAHUY Bot")
        details[1] = append_unique_text(details[1], issued_by)
        details[2] = append_unique_text(details[2], worker_name)
    elif operation == "Повернення":
        details[0] = append_unique_text(details[0], "Повернення через RAHUY Bot")
        details[1] = append_unique_text(details[1], worker_name)
        details[2] = append_unique_text(details[2], "RAHUY / склад")
    elif operation == "Дефект":
        details[0] = append_unique_text(details[0], "Дефектний акт через RAHUY Bot")
        details[1] = append_unique_text(details[1], worker_name)
        details[2] = append_unique_text(details[2], "RAHUY / склад (дефект)")
    else:
        side_label = movement_label or (
            "Надходження за накладною" if operation == "Надходження" else "Замовлення через RAHUY Bot"
        )
        details[0] = append_unique_text(details[0], side_label)
        details[1] = append_unique_text(
            details[1], source_name or ("Постачальник" if operation == "Надходження" else worker_name),
        )
        details[2] = append_unique_text(details[2], worker_name)
    column = get_daily_material_column(worksheet, material, operation)
    cell = f"{column_letter(column)}{row_number}"
    current_value = worksheet.acell(cell).value
    pending_updates.extend([
        {
            "range": f"'{DAILY_MATERIAL_SHEET}'!B{row_number}:D{row_number}",
            "values": [details],
        },
        {
            "range": f"'{DAILY_MATERIAL_SHEET}'!{cell}",
            "values": [[parse_sheet_number(current_value) + quantity]],
        },
    ])
    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": pending_updates,
    })
    return row_number


def find_material_candidates(text):
    candidates = []
    for row in get_material_catalog():
        score = material_match_score(text, row)
        if score >= 0.35:
            candidates.append((score, row))

    candidates.sort(key=lambda item: item[0], reverse=True)
    normalized = normalize_material_text(text)
    exact_candidates = [
        row
        for _, row in candidates
        if normalized == normalize_material_text(row.get("Матеріал", ""))
        or normalized in material_aliases(row)
    ]
    if exact_candidates:
        return exact_candidates[:3]
    return [row for _, row in candidates[:3]]


def response_output_text(response):
    """Return text from a raw Responses API payload (not the SDK convenience property)."""
    direct_text = str(response.get("output_text") or "").strip()
    if direct_text:
        return direct_text
    for output in response.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"])
    raise ValueError("Відповідь OpenAI не містить тексту")


def interpret_material_with_ai(text, catalog):
    """Turns a colloquial material request into a safe, structured suggestion.

    The result is only a suggestion. The Telegram confirmation step still decides
    whether anything is written to the spreadsheet.
    """
    if not OPENAI_API_KEY or not catalog:
        return None

    materials = [
        {
            "name": str(row.get("Матеріал", "")),
            "aliases": str(row.get("Синоніми", "")),
            "unit": str(row.get("Од. виміру / примітка", "")),
        }
        for row in catalog
        if row.get("Матеріал")
    ]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "quantity": {"type": ["number", "null"]},
            "material": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["quantity", "material", "confidence"],
    }
    instructions = (
        "Ти розпізнаєш короткі українські повідомлення працівників про матеріали та кількість. "
        "Обери material тільки як точну назву з каталогу. Використовуй синоніми лише для зіставлення. "
        "Не визначай тип операції. Не вигадуй матеріал або кількість. Якщо даних недостатньо, поверни null. "
        "Це лише пропозиція для подальшого підтвердження працівником."
    )
    payload = {
        "model": OPENAI_MODEL,
        "instructions": instructions,
        "input": json.dumps({"message": text, "catalog": materials}, ensure_ascii=False),
        "text": {"format": {"type": "json_schema", "name": "material_operation", "strict": True, "schema": schema}},
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        output_text = response_output_text(result)
        parsed = json.loads(output_text)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError):
        return None

    material_name = parsed.get("material")
    selected = next((row for row in catalog if row.get("Матеріал") == material_name), None)
    if not selected or float(parsed.get("confidence", 0) or 0) < 0.65:
        return None

    quantity = parsed.get("quantity")
    try:
        quantity = float(quantity) if quantity is not None else None
    except (TypeError, ValueError):
        quantity = None
    if quantity is None or quantity <= 0:
        return None

    return {"quantity": quantity, "selected": selected}


def interpret_invoice_photo_with_ai(image_bytes, catalog):
    """Extract a delivery invoice into only catalog-backed material arrivals."""
    if not OPENAI_API_KEY or not catalog:
        return None

    materials = [
        {
            "name": str(row.get("Матеріал", "")),
            "aliases": str(row.get("Синоніми", "")),
            "unit": str(row.get("Од. виміру / примітка", "")),
        }
        for row in catalog
        if row.get("Матеріал")
    ]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "supplier": {"type": ["string", "null"]},
            "invoice_number": {"type": ["string", "null"]},
            "invoice_date": {"type": ["string", "null"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "material": {"type": ["string", "null"]},
                        "raw_name": {"type": "string"},
                        "quantity": {"type": ["number", "null"]},
                        "unit": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["material", "raw_name", "quantity", "unit", "confidence"],
                },
            },
        },
        "required": ["supplier", "invoice_number", "invoice_date", "items"],
    }
    instructions = (
        "Розпізнай українську накладну з фото. Витягни постачальника, номер, дату та всі позиції. "
        "Для кожної позиції поверни одиницю саме з накладної, зокрема м³ для утеплювача в кубах. "
        "Для material використовуй лише точну назву з каталогу; зіставляй за назвою, одиницею й синонімами. "
        "Якщо відповідника немає або кількість нечитабельна, поверни material або quantity як null. "
        "Не вигадуй дані. Результат є лише чернеткою, яку людина підтвердить окремо."
    )
    image_data = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": OPENAI_MODEL,
        "instructions": instructions,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": json.dumps({"catalog": materials}, ensure_ascii=False)},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_data}", "detail": "high"},
            ],
        }],
        "text": {"format": {"type": "json_schema", "name": "invoice_delivery", "strict": True, "schema": schema}},
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        parsed = json.loads(response_output_text(result))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"OpenAI API: {error.code}") from error
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("OpenAI API не повернув структуроване розпізнавання") from error

    catalog_by_name = {str(row.get("Матеріал", "")): row for row in catalog}
    items = []
    unmatched = []
    for item in parsed.get("items", []):
        material = catalog_by_name.get(str(item.get("material") or ""))
        try:
            quantity = float(item.get("quantity"))
        except (TypeError, ValueError):
            quantity = None
        if material and quantity and quantity > 0 and float(item.get("confidence", 0) or 0) >= 0.65:
            source_unit = str(item.get("unit") or "").strip()
            converted = convert_invoice_quantity(material, quantity, source_unit)
            if converted:
                items.append({"material": material, **converted})
            else:
                unmatched.append(
                    f"{item.get('raw_name') or material['Матеріал']} (не визначено товщину утеплювача)"
                )
        else:
            unmatched.append(str(item.get("raw_name") or "невідома позиція"))

    return {
        "supplier": str(parsed.get("supplier") or "").strip(),
        "invoice_number": str(parsed.get("invoice_number") or "").strip(),
        "invoice_date": str(parsed.get("invoice_date") or "").strip(),
        "items": items,
        "unmatched": unmatched,
    }


def send_material_choice(message, text, forced_operation=None):
    user = get_user(message.from_user.id, get_user_name(message))
    current_pending = user.get("pending_material") or {}
    capture = current_pending.get("capture")
    operation = forced_operation or current_pending.get("operation") or "Видача"
    target_worker = user.get("material_for_worker")
    quantity = extract_material_quantity(text)
    catalog = get_material_catalog()
    candidates = []
    if quantity is not None and quantity > 0:
        candidates = find_material_candidates(text)

    ai_result = None
    if not candidates or len(candidates) > 1 or quantity is None:
        ai_result = interpret_material_with_ai(text, catalog)
        if ai_result:
            quantity = ai_result["quantity"]
            candidates = [ai_result["selected"]]

    if quantity is None or quantity <= 0:
        error_text = "Не бачу кількості."
        if target_worker:
            send_material_for_worker_prompt(message, target_worker, error_text)
        elif capture:
            send_material_issue_input_prompt(
                message,
                capture,
                error_text,
                operation=operation,
            )
        else:
            send_with_keyboard(
                message,
                f"{error_text} Напишіть, наприклад: «взяв 5 кутиків» або «клей-піна 1 балон»."
            )
        return False

    if not candidates:
        error_text = "Не знайшов матеріал у довіднику. Напишіть назву точніше."
        if target_worker:
            send_material_for_worker_prompt(message, target_worker, error_text)
        elif capture:
            send_material_issue_input_prompt(
                message,
                capture,
                error_text,
                operation=operation,
            )
        else:
            send_with_keyboard(message, error_text + " Або додамо для нього синонім.")
        return False

    user["pending_material"] = {
        "kind": "material",
        "operation": operation,
        "quantity": quantity,
        "candidates": candidates,
        "selected": None,
        "worker": target_worker,
        "capture": capture,
    }
    user["material_for_worker"] = None

    if len(candidates) == 1:
        select_material_candidate(message, candidates[0].get("Матеріал", ""))
        return True

    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for candidate in candidates:
        markup.row(KeyboardButton(str(candidate.get("Матеріал", ""))))
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    options = {"reply_markup": markup}
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None:
        options["message_thread_id"] = thread_id
    visible_options = "\n".join(
        f"• {candidate.get('Матеріал', '')}" for candidate in candidates
    )
    bot.send_message(
        message.chat.id,
        f"Уточніть матеріал — оберіть кнопку або надішліть точну назву:\n{visible_options}",
        **options,
    )
    return True


def select_material_candidate(message, material_name):
    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_material")
    if not pending:
        return False

    selected = next(
        (row for row in pending["candidates"] if row.get("Матеріал") == material_name),
        None,
    )
    if not selected:
        return False

    pending["selected"] = selected
    unit = selected.get("Од. виміру / примітка", "") or "од."
    worker_line = ""
    if pending.get("worker"):
        worker_line = f"\nПрацівник: {pending['worker']['name']}"
    capture_line = ""
    if pending.get("capture"):
        capture_line = f"\nЗахватка: {pending['capture']['name']}"
    if pending.get("kind") == "inventory":
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.row(KeyboardButton(INVENTORY_CONFIRM_TEXT), KeyboardButton(MATERIAL_CANCEL_TEXT))
        options = {"reply_markup": markup}
        thread_id = getattr(message, "message_thread_id", None)
        if thread_id is not None:
            options["message_thread_id"] = thread_id
        bot.send_message(
            message.chat.id,
            f"Інвентаризація: фактичний залишок {pending['quantity']:g} {unit}\n"
            f"Матеріал: {selected['Матеріал']}{worker_line}\n\nПідтвердити запис?",
            **options,
        )
        return True

    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(MATERIAL_CONFIRM_TEXT), KeyboardButton(MATERIAL_CANCEL_TEXT))
    options = {"reply_markup": markup}
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None:
        options["message_thread_id"] = thread_id
    if pending.get("worker"):
        confirmation_text = (
            f"Майстер: {pending['worker']['name']}\n"
            f"Матеріал: {selected['Матеріал']}\n"
            f"Кількість: {pending['quantity']:g} {unit}\n"
            f"Операція: {pending['operation']}{capture_line}\n\n"
            "Підтвердити запис?"
        )
    else:
        confirmation_text = (
            f"{pending['operation']}: {pending['quantity']:g} {unit}\n"
            f"Матеріал: {selected['Матеріал']}{capture_line}\n\nПідтвердити запис?"
        )
    bot.send_message(message.chat.id, confirmation_text, **options)
    return True


def save_material_operation(message):
    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_material")
    if not pending or not pending.get("selected"):
        send_with_keyboard(message, "Немає операції матеріалу для підтвердження.")
        return

    material = pending["selected"]
    worker = pending.get("worker") or get_worker(message.from_user.id) or {}
    target_user = get_user(
        worker.get("telegram_user_id") or message.from_user.id,
        worker.get("name") or user["full_name"],
    )
    capture = (
        pending.get("capture")
        or target_user.get("selected_capture")
        or user.get("selected_capture")
        or {}
    )
    spreadsheet = get_sheet()
    timestamp = now_dt()
    sync_daily_material_movement(
        spreadsheet,
        message,
        material,
        pending["operation"],
        pending["quantity"],
        worker,
        timestamp,
    )
    log_sheet = get_or_create_worksheet(spreadsheet, "Операції матеріалів", MATERIAL_LOG_HEADERS)
    log_sheet.append_row([
        timestamp.strftime("%d.%m.%Y"),
        pending["operation"],
        material["Матеріал"],
        pending["quantity"],
        material.get("Од. виміру / примітка", ""),
        capture.get("name", ""),
        worker.get("name") or user["full_name"],
        worker.get("role", ""),
        "",
        str(worker.get("telegram_user_id") or message.from_user.id),
        str(message.chat.id),
        format_datetime(timestamp),
    ])

    user["pending_material"] = None
    capture_line = f"\nЗахватка: {capture['name']}" if capture.get("name") else ""
    send_with_keyboard(
        message,
        f"Записано: {pending['operation'].lower()} — {pending['quantity']:g} "
        f"{material.get('Од. виміру / примітка', '')}\nМатеріал: {material['Матеріал']}\n"
        f"Працівник: {worker.get('name') or user['full_name']}{capture_line}",
    )


def save_inventory_record(message):
    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_material")
    if not pending or pending.get("kind") != "inventory" or not pending.get("selected"):
        send_with_keyboard(message, "Немає інвентаризації для підтвердження.")
        return

    material = pending["selected"]
    worker = get_worker(message.from_user.id) or {}
    capture = user.get("selected_capture") or {}
    timestamp = now_dt()
    spreadsheet = get_sheet()
    movement_sheet = spreadsheet.worksheet(DAILY_MATERIAL_SHEET)
    book_balance = get_book_material_balance(movement_sheet, material)
    actual_balance = pending["quantity"]
    adjustment = actual_balance - book_balance
    adjustment_operation = "Надходження" if adjustment > 0 else "Видача"

    if adjustment:
        sync_daily_material_movement(
            spreadsheet,
            message,
            material,
            adjustment_operation,
            abs(adjustment),
            worker,
            timestamp,
            source_name="RAHUY / інвентаризація",
            movement_label="Коригування інвентаризації",
        )

    log_sheet = get_or_create_worksheet(spreadsheet, "Операції матеріалів", MATERIAL_LOG_HEADERS)
    log_sheet.append_row([
        timestamp.strftime("%d.%m.%Y"),
        "Інвентаризація" if not adjustment else f"Інвентаризація → {adjustment_operation}",
        material["Матеріал"],
        abs(adjustment),
        material.get("Од. виміру / примітка", ""),
        capture.get("name", ""),
        worker.get("name") or user["full_name"],
        worker.get("role", ""),
        f"Облік: {book_balance:g}; фактично: {actual_balance:g}; коригування: {adjustment:+g}",
        str(message.from_user.id),
        format_datetime(timestamp),
    ])
    user["pending_material"] = None
    send_with_keyboard(
        message,
        f"Інвентаризацію записано: {material['Матеріал']}. "
        f"Облік {book_balance:g}, фактично {actual_balance:g}; "
        f"коригування {adjustment:+g} {material.get('Од. виміру / примітка', '')}.",
    )


def start_inventory(message, text):
    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = {
        "kind": "inventory_material",
        "candidates": [],
        "selected": None,
    }
    send_with_keyboard(message, "Вкажіть матеріал для інвентаризації, наприклад: «малярний скотч».")


def choose_inventory_material(message, text, pending):
    selected = next(
        (row for row in pending.get("candidates", []) if row.get("Матеріал") == text),
        None,
    )
    if selected:
        pending["selected"] = selected
        pending["kind"] = "inventory_quantity"
        movement_sheet = get_sheet().worksheet(DAILY_MATERIAL_SHEET)
        book_balance = get_book_material_balance(movement_sheet, selected)
        unit = selected.get("Од. виміру / примітка", "") or "од."
        send_with_keyboard(
            message,
            f"Матеріал: {selected['Матеріал']}\n"
            f"За обліком на складі: {book_balance:g} {unit}.\n"
            "Напишіть фактичну кількість.",
        )
        return True

    candidates = find_material_candidates(text)
    if not candidates:
        send_with_keyboard(message, "Не знайшов матеріал у довіднику. Напишіть назву точніше або додайте синонім.")
        return True
    pending["candidates"] = candidates
    if len(candidates) == 1:
        return choose_inventory_material(message, candidates[0].get("Матеріал", ""), pending)

    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for candidate in candidates:
        markup.row(KeyboardButton(str(candidate.get("Матеріал", ""))))
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    bot.send_message(message.chat.id, "Уточніть матеріал для інвентаризації.", reply_markup=markup)
    return True


def set_inventory_quantity(message, text, pending):
    quantity = extract_material_quantity(text)
    if quantity is None or quantity < 0:
        send_with_keyboard(message, "Не бачу кількості. Напишіть фактичний залишок числом.")
        return True
    pending["quantity"] = quantity
    pending["kind"] = "inventory"
    return select_material_candidate(message, pending["selected"].get("Матеріал", ""))


def send_defect_material_prompt(message, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Введіть дефектний матеріал і кількість.\n"
        "Наприклад: 4 кутики або клей-піна 1 балон.",
        markup,
    )


def send_defect_photo_prompt(message, pending, error_text=""):
    material = pending["selected"]
    unit = material.get("Од. виміру / примітка", "") or "од."
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Дефектний матеріал: {material['Матеріал']}\n"
        f"Кількість: {pending['quantity']:g} {unit}\n\n"
        "Надішліть фото дефекту. Опис причини можна додати в підписі до фото.",
        markup,
    )


def start_defect_report(message):
    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = {"kind": "defect_input", "candidates": []}
    send_defect_material_prompt(message)


def select_defect_candidate(message, material_name, pending):
    selected = next(
        (row for row in pending.get("candidates", []) if row.get("Матеріал") == material_name),
        None,
    )
    if not selected:
        return False
    pending["selected"] = selected
    pending["kind"] = "defect_photo"
    send_defect_photo_prompt(message, pending)
    return True


def choose_defect_material(message, text, pending):
    if pending.get("kind") == "defect_choice":
        if select_defect_candidate(message, text, pending):
            return True

    quantity = extract_material_quantity(text)
    catalog = get_material_catalog()
    candidates = find_material_candidates(text) if quantity and quantity > 0 else []
    if not candidates or len(candidates) > 1 or quantity is None:
        ai_result = interpret_material_with_ai(text, catalog)
        if ai_result:
            quantity = ai_result["quantity"]
            candidates = [ai_result["selected"]]

    if quantity is None or quantity <= 0:
        send_defect_material_prompt(message, "Не бачу кількості.")
        return True
    if not candidates:
        send_defect_material_prompt(message, "Не знайшов матеріал у довіднику.")
        return True

    pending.update({"quantity": quantity, "candidates": candidates})
    if len(candidates) == 1:
        return select_defect_candidate(message, candidates[0].get("Матеріал", ""), pending)

    pending["kind"] = "defect_choice"
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for candidate in candidates:
        markup.row(KeyboardButton(str(candidate.get("Матеріал", ""))))
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    send_with_markup(message, "Уточніть дефектний матеріал.", markup)
    return True


def handle_defect_photo(message, pending):
    pending["photo_file_id"] = message.photo[-1].file_id
    pending["photo_message_id"] = message.message_id
    pending["description"] = str(message.caption or "").strip()
    pending["kind"] = "defect_confirm"
    material = pending["selected"]
    unit = material.get("Од. виміру / примітка", "") or "од."
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(DEFECT_CONFIRM_TEXT), KeyboardButton(MATERIAL_CANCEL_TEXT))
    description = pending["description"] or "не вказано"
    send_with_markup(
        message,
        f"Дефектний акт:\n"
        f"Матеріал: {material['Матеріал']}\n"
        f"Кількість: {pending['quantity']:g} {unit}\n"
        f"Опис: {description}\n"
        "Фото додано. Підтвердити списання дефектного матеріалу?",
        markup,
    )


def save_defect_report(message):
    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_material")
    if not pending or pending.get("kind") != "defect_confirm":
        send_with_keyboard(message, "Немає дефектного акта для підтвердження.")
        return

    material = pending["selected"]
    quantity = pending["quantity"]
    worker = get_worker(message.from_user.id) or {}
    capture = user.get("selected_capture") or {}
    timestamp = now_dt()
    act_number = f"DEF-{timestamp:%Y%m%d-%H%M%S}-{message.from_user.id}"
    spreadsheet = get_sheet()
    sync_daily_material_movement(
        spreadsheet, message, material, "Дефект", quantity, worker, timestamp,
    )

    unit = material.get("Од. виміру / примітка", "")
    description = pending.get("description", "")
    defect_sheet = get_or_create_worksheet(spreadsheet, "Дефектні акти", DEFECT_HEADERS)
    defect_sheet.append_row([
        timestamp.strftime("%d.%m.%Y"),
        act_number,
        material["Матеріал"],
        quantity,
        unit,
        capture.get("name", ""),
        worker.get("name") or user["full_name"],
        worker.get("role", ""),
        description,
        pending.get("photo_file_id", ""),
        str(message.from_user.id),
        str(message.chat.id),
        str(pending.get("photo_message_id", "")),
        format_datetime(timestamp),
    ])
    log_sheet = get_or_create_worksheet(spreadsheet, "Операції матеріалів", MATERIAL_LOG_HEADERS)
    log_sheet.append_row([
        timestamp.strftime("%d.%m.%Y"),
        "Дефектний акт",
        material["Матеріал"],
        quantity,
        unit,
        capture.get("name", ""),
        worker.get("name") or user["full_name"],
        worker.get("role", ""),
        "; ".join(part for part in (act_number, description) if part),
        str(message.from_user.id),
        str(message.chat.id),
        format_datetime(timestamp),
    ])
    user["pending_material"] = None
    send_with_keyboard(
        message,
        f"Дефектний акт {act_number} записано.\n"
        f"З придатного залишку списано: {quantity:g} {unit or 'од.'} — {material['Матеріал']}.",
    )


def start_balance_check(message):
    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = {
        "kind": "balance_material",
        "candidates": [],
    }
    send_with_keyboard(message, "Напишіть назву матеріалу, залишок якого потрібно перевірити.")


def choose_balance_material(message, text, pending):
    selected = next(
        (row for row in pending.get("candidates", []) if row.get("Матеріал") == text),
        None,
    )
    if selected:
        movement_sheet = get_sheet().worksheet(DAILY_MATERIAL_SHEET)
        balance = get_book_material_balance(movement_sheet, selected)
        unit = selected.get("Од. виміру / примітка", "") or "од."
        get_user(message.from_user.id, get_user_name(message))["pending_material"] = None
        send_with_keyboard(
            message,
            f"Залишок за обліком:\n{selected['Матеріал']} — {balance:g} {unit}.",
        )
        return True

    candidates = find_material_candidates(text)
    if not candidates:
        send_with_keyboard(message, "Не знайшов матеріал у довіднику. Напишіть назву точніше або додайте синонім.")
        return True
    pending["candidates"] = candidates
    if len(candidates) == 1:
        return choose_balance_material(message, candidates[0].get("Матеріал", ""), pending)

    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for candidate in candidates:
        markup.row(KeyboardButton(str(candidate.get("Матеріал", ""))))
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    options = {"reply_markup": markup}
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None:
        options["message_thread_id"] = thread_id
    bot.send_message(message.chat.id, "Уточніть матеріал для перевірки залишку.", **options)
    return True


def send_invoice_preview(message, invoice):
    lines = ["Чернетка надходження з накладної:"]
    if invoice.get("supplier"):
        lines.append(f"Постачальник: {invoice['supplier']}")
    if invoice.get("invoice_number"):
        lines.append(f"Накладна: {invoice['invoice_number']}")
    if invoice.get("invoice_date"):
        lines.append(f"Дата накладної: {invoice['invoice_date']}")
    lines.append("")
    for item in invoice["items"]:
        material = item["material"]
        unit = item.get("unit") or material.get("Од. виміру / примітка", "") or "од."
        if item.get("thickness_mm"):
            lines.append(
                f"• {material['Матеріал']}: {item['source_quantity']:g} "
                f"{item['source_unit']} → {item['quantity']:g} {unit} "
                f"(товщина {item['thickness_mm']:g} мм)"
            )
        else:
            lines.append(f"• {material['Матеріал']} — {item['quantity']:g} {unit}")
    if invoice.get("unmatched"):
        lines.append("\nНе додано без уточнення: " + ", ".join(invoice["unmatched"]))
    lines.append("\nПідтвердити надходження?")

    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(INVOICE_CONFIRM_TEXT), KeyboardButton(MATERIAL_CANCEL_TEXT))
    options = {"reply_markup": markup}
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None:
        options["message_thread_id"] = thread_id
    bot.send_message(message.chat.id, "\n".join(lines), **options)


def handle_invoice_photo(message):
    if not is_materials_topic(message):
        return
    if not OPENAI_API_KEY:
        send_with_keyboard(message, "Розпізнавання накладних ще не налаштоване: потрібен ключ OpenAI API.")
        return

    try:
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        image_bytes = bot.download_file(file_info.file_path)
        invoice = interpret_invoice_photo_with_ai(image_bytes, get_material_catalog())
    except Exception as error:
        print(f"Invoice recognition failed: {type(error).__name__}: {error}")
        send_with_keyboard(
            message,
            "Не вдалося обробити фото накладної. Деталь помилки є в логах Railway; перевірю її за наступним записом.",
        )
        return

    if not invoice or not invoice.get("items"):
        unmatched = ", ".join((invoice or {}).get("unmatched", []))
        send_with_keyboard(
            message,
            "Фото прочитано, але жодну позицію не вдалося зіставити з довідником матеріалів. "
            + (f"Розпізнано: {unmatched}. " if unmatched else "")
            + "Додай відповідні назви або синоніми в довідник і надішли фото ще раз.",
        )
        return

    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = {"kind": "invoice", "invoice": invoice}
    send_invoice_preview(message, invoice)


def save_invoice_delivery(message):
    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_material")
    if not pending or pending.get("kind") != "invoice":
        send_with_keyboard(message, "Немає накладної для підтвердження.")
        return

    invoice = pending["invoice"]
    worker = get_worker(message.from_user.id) or {}
    capture = user.get("selected_capture") or {}
    timestamp = now_dt()
    spreadsheet = get_sheet()
    note_parts = []
    if invoice.get("supplier"):
        note_parts.append(f"Постачальник: {invoice['supplier']}")
    if invoice.get("invoice_number"):
        note_parts.append(f"Накладна: {invoice['invoice_number']}")
    if invoice.get("invoice_date"):
        note_parts.append(f"Дата: {invoice['invoice_date']}")
    note = "; ".join(note_parts)
    log_sheet = get_or_create_worksheet(spreadsheet, "Операції матеріалів", MATERIAL_LOG_HEADERS)

    for item in invoice["items"]:
        material = item["material"]
        quantity = item["quantity"]
        sync_daily_material_movement(
            spreadsheet,
            message,
            material,
            "Надходження",
            quantity,
            worker,
            timestamp,
            source_name=invoice.get("supplier") or "Постачальник",
        )
        item_note = note
        if item.get("thickness_mm"):
            conversion_note = (
                f"Перерахунок: {item['source_quantity']:g} {item['source_unit']} → "
                f"{quantity:g} м.кв.; товщина {item['thickness_mm']:g} мм"
            )
            item_note = "; ".join(part for part in (note, conversion_note) if part)
        log_sheet.append_row([
            timestamp.strftime("%d.%m.%Y"),
            "Надходження за накладною",
            material["Матеріал"],
            quantity,
            item.get("unit") or material.get("Од. виміру / примітка", ""),
            capture.get("name", ""),
            worker.get("name") or user["full_name"],
            worker.get("role", ""),
            item_note,
            str(message.from_user.id),
            str(message.chat.id),
            format_datetime(timestamp),
        ])

    user["pending_material"] = None
    send_with_keyboard(
        message,
        f"Накладну записано: {len(invoice['items'])} позицій додано в «Прийнято» та «Операції матеріалів».",
    )


def cancel_material_operation(message):
    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = None
    user["material_for_worker"] = None
    pending_delegate = user.get("pending_delegate") or {}
    if pending_delegate.get("kind") == "material":
        user["pending_delegate"] = None
    send_with_keyboard(message, "Операцію з матеріалом скасовано.")


def cancel_delegate(message):
    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_delegate") or {}
    if pending_delegate_matches_message(pending, message):
        user["pending_delegate"] = None
        user["material_for_worker"] = None
        send_with_keyboard(message, "Позначення за іншого працівника скасовано.")
        return
    send_with_keyboard(message, "Активного позначення в цій гілці немає.")


def send_operation_time_choice(message, worker, action, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(DELEGATE_NOW_TEXT))
    markup.row(KeyboardButton(DELEGATE_OTHER_TIME_TEXT))
    markup.row(KeyboardButton(DELEGATE_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Працівник: {worker['name']}\n"
        f"Подія: {action}\n\n"
        "Оберіть поточний час або вкажіть інший фактичний час події.",
        markup,
    )


def send_operation_time_prompt(message, worker, action, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(DELEGATE_NOW_TEXT))
    markup.row(KeyboardButton(DELEGATE_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Працівник: {worker['name']}\n"
        f"Подія: {action}\n\n"
        "Введіть фактичні дату й час події.\n"
        "Формати: 08.08.2026 08:00, 2026-08-08 08:00 або 08:00 для сьогодні.\n"
        "Або натисніть «Поточний час».",
        markup,
    )


def send_delegate_capture_prompt(message, pending, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for capture in pending["captures"]:
        markup.row(KeyboardButton(capture["name"]))
    markup.row(KeyboardButton(DELEGATE_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Оберіть захватку для цієї зміни:",
        markup,
    )


def send_delegate_confirmation(message, pending, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(DELEGATE_CONFIRM_TEXT))
    markup.row(KeyboardButton(DELEGATE_CANCEL_TEXT))
    capture = pending.get("capture") or {}
    capture_line = f"\nЗахватка: {capture.get('name')}" if capture.get("name") else ""
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Перевірте позначення:\n"
        f"Працівник: {pending['worker']['name']}\n"
        f"Подія: {pending['action']}\n"
        f"Дата й час: {format_datetime(pending['operation_time'])}"
        f"{capture_line}\n\n"
        "Підтвердьте або скасуйте позначення.",
        markup,
    )


def complete_delegate_operation(message, pending):
    action = pending["action"]
    worker = pending["worker"]
    operation_time = pending["operation_time"]
    if action == START_SHIFT_TEXT:
        start_shift(message, worker, operation_time, pending["capture"])
    elif action == END_SHIFT_TEXT:
        end_shift(message, worker, operation_time)
    elif action == START_BREAK_TEXT:
        start_break(message, worker, operation_time)
    else:
        stop_break(message, worker, operation_time)


def continue_delegate_with_time(message, actor, pending, operation_time):
    if operation_time > now_dt() + timedelta(minutes=1):
        pending["stage"] = "datetime"
        send_operation_time_prompt(
            message,
            pending["worker"],
            pending["action"],
            "Не можна вказати час у майбутньому.",
        )
        return

    pending["operation_time"] = operation_time
    if pending["action"] == START_SHIFT_TEXT:
        captures = get_active_captures()
        if not captures:
            actor["pending_delegate"] = None
            send_with_keyboard(message, "Немає активних захваток у таблиці «Захватки».")
            return
        pending["captures"] = captures
        pending["stage"] = "capture"
        send_delegate_capture_prompt(message, pending)
        return

    pending["stage"] = "confirm"
    send_delegate_confirmation(message, pending)


def handle_delegate_text(message, text):
    actor = get_user(message.from_user.id, get_user_name(message))
    pending = actor.get("pending_delegate")
    if not pending:
        return False

    if not pending_delegate_matches_message(pending, message):
        return False

    if text == DELEGATE_CANCEL_TEXT:
        cancel_delegate(message)
        return True

    if pending["stage"] == "worker":
        worker = find_worker_option(text, pending["workers"])
        if not worker:
            send_worker_selection(message, pending["kind"])
            return True
        pending["worker"] = worker
        if pending["kind"] == "material":
            pending["stage"] = "material"
            actor["material_for_worker"] = worker
            send_material_for_worker_prompt(message, worker)
            return True

        pending["stage"] = "action"
        send_with_markup(
            message,
            f"Що потрібно позначити для {worker['name']}?",
            delegate_action_keyboard(),
        )
        return True

    if pending["kind"] == "material" and pending["stage"] == "material":
        material_started = send_material_choice(message, text, forced_operation="Видача")
        if material_started:
            actor["pending_delegate"] = None
        return True

    if pending["stage"] == "action":
        actions = {START_SHIFT_TEXT, END_SHIFT_TEXT, START_BREAK_TEXT, STOP_BREAK_TEXT}
        if text not in actions:
            send_with_markup(message, "Оберіть подію кнопкою.", delegate_action_keyboard())
            return True
        pending["action"] = text
        pending["stage"] = "time_choice"
        send_operation_time_choice(message, pending["worker"], text)
        return True

    if pending["stage"] == "time_choice":
        if text == DELEGATE_NOW_TEXT:
            continue_delegate_with_time(message, actor, pending, now_dt())
            return True
        if text == DELEGATE_OTHER_TIME_TEXT:
            pending["stage"] = "datetime"
            send_operation_time_prompt(message, pending["worker"], pending["action"])
            return True
        send_operation_time_choice(
            message,
            pending["worker"],
            pending["action"],
            "Оберіть час кнопкою.",
        )
        return True

    if pending["stage"] == "datetime":
        if text == DELEGATE_NOW_TEXT:
            operation_time = now_dt()
        else:
            try:
                operation_time = parse_operation_datetime(text)
            except ValueError:
                send_operation_time_prompt(
                    message,
                    pending["worker"],
                    pending["action"],
                    "Не вдалося розпізнати дату й час.",
                )
                return True
        continue_delegate_with_time(message, actor, pending, operation_time)
        return True

    if pending["stage"] == "capture":
        capture = next(
            (item for item in pending["captures"] if item["name"] == text),
            None,
        )
        if not capture:
            send_delegate_capture_prompt(
                message,
                pending,
                "Захватку не знайдено. Оберіть її кнопкою.",
            )
            return True
        pending["capture"] = capture
        pending["stage"] = "confirm"
        send_delegate_confirmation(message, pending)
        return True

    if pending["stage"] == "confirm":
        if text != DELEGATE_CONFIRM_TEXT:
            send_delegate_confirmation(message, pending, "Оберіть дію кнопкою.")
            return True
        actor["pending_delegate"] = None
        complete_delegate_operation(message, pending)
        return True

    return False


def get_shift_subject(message, target_worker=None):
    worker = target_worker or get_worker(message.from_user.id) or {
        "telegram_user_id": str(message.from_user.id),
        "name": get_user_name(message),
        "role": "",
        "brigade": "",
    }
    user = get_user(
        worker.get("telegram_user_id") or message.from_user.id,
        worker.get("name") or get_user_name(message),
    )
    return worker, user


def start_shift(message, target_worker=None, operation_time=None, capture=None):
    worker, user = get_shift_subject(message, target_worker)

    if user["shift_started"]:
        send_with_keyboard(
            message,
            f"{user['full_name']}\nЗміна вже відкрита.\nПочаток: {format_datetime(user['shift_start_time'])}",
        )
        return

    if capture:
        user["selected_capture"] = capture
    if not user.get("selected_capture"):
        send_with_keyboard(message, "Спершу оберіть захватку кнопкою «Обрати захватку».")
        return

    shift_start = operation_time or now_dt()
    if shift_start > now_dt() + timedelta(minutes=1):
        send_with_keyboard(message, "Початок зміни не може бути в майбутньому.")
        return

    user["shift_started"] = True
    user["shift_start_time"] = shift_start
    user["break_active"] = False
    user["break_start_time"] = None
    user["total_break"] = timedelta()
    user["shift_capture"] = user["selected_capture"]
    user["shift_worker"] = dict(worker)
    user["shift_chat_id"] = message.chat.id
    user["shift_thread_id"] = getattr(message, "message_thread_id", None)

    send_with_keyboard(
        message,
        f"{user['full_name']}\nПочаток зміни зафіксовано.\n"
        f"Захватка: {user['shift_capture']['name']}\n"
        f"Час: {format_datetime(user['shift_start_time'])}",
    )


def start_break(message, target_worker=None, operation_time=None):
    worker, user = get_shift_subject(message, target_worker)

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

    break_start = operation_time or now_dt()
    if break_start < user["shift_start_time"]:
        send_with_keyboard(message, "Початок перерви не може бути раніше початку зміни.")
        return
    if break_start > now_dt() + timedelta(minutes=1):
        send_with_keyboard(message, "Початок перерви не може бути в майбутньому.")
        return

    user["break_active"] = True
    user["break_start_time"] = break_start

    send_with_keyboard(
        message,
        f"{user['full_name']}\nПерерва почалась.\nЧас: {format_datetime(user['break_start_time'])}",
    )


def stop_break(message, target_worker=None, operation_time=None):
    worker, user = get_shift_subject(message, target_worker)

    if not user["shift_started"]:
        send_with_keyboard(message, "Зміна ще не розпочата.")
        return

    if not user["break_active"] or user["break_start_time"] is None:
        send_with_keyboard(message, "Активної перерви зараз немає.")
        return

    break_end = operation_time or now_dt()
    if break_end < user["break_start_time"]:
        send_with_keyboard(message, "Кінець перерви не може бути раніше її початку.")
        return
    if break_end > now_dt() + timedelta(minutes=1):
        send_with_keyboard(message, "Кінець перерви не може бути в майбутньому.")
        return
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


def calculate_shift_duration(user, shift_end):
    if user["break_active"] and user["break_start_time"] is not None:
        if shift_end < user["break_start_time"]:
            raise ValueError("Кінець зміни не може бути раніше початку перерви.")
        user["total_break"] += shift_end - user["break_start_time"]
        user["break_active"] = False
        user["break_start_time"] = None

    total_time = round_up_to_minute(
        shift_end - user["shift_start_time"],
        minimum_one_minute=True,
    )
    user["total_break"] = round_up_to_minute(user["total_break"])
    work_time = total_time - user["total_break"]
    if work_time <= timedelta():
        work_time = timedelta(minutes=1)
    return total_time, work_time


def clear_active_shift(user):
    user["shift_started"] = False
    user["shift_start_time"] = None
    user["break_active"] = False
    user["break_start_time"] = None
    user["total_break"] = timedelta()
    user["shift_capture"] = None
    user["shift_worker"] = None
    user["shift_chat_id"] = None
    user["shift_thread_id"] = None
    user["auto_close_in_progress"] = False


def end_shift(message, target_worker=None, operation_time=None):
    worker, user = get_shift_subject(message, target_worker)

    if not user["shift_started"] or user["shift_start_time"] is None:
        send_with_keyboard(message, "Немає активної зміни для завершення.")
        return
    if user.get("auto_close_in_progress"):
        send_with_keyboard(message, "Зміна вже закривається автоматично. Зачекайте кілька секунд.")
        return

    shift_end = operation_time or now_dt()
    if shift_end < user["shift_start_time"]:
        send_with_keyboard(message, "Кінець зміни не може бути раніше її початку.")
        return
    if shift_end > now_dt() + timedelta(minutes=1):
        send_with_keyboard(message, "Кінець зміни не може бути в майбутньому.")
        return

    try:
        total_time, work_time = calculate_shift_duration(user, shift_end)
    except ValueError as error:
        send_with_keyboard(message, str(error))
        return
    save_shift_to_sheet(
        message,
        user,
        shift_end,
        total_time,
        work_time,
        worker,
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

    clear_active_shift(user)

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


def auto_close_overdue_shift(user_id, user):
    shift_start = user.get("shift_start_time")
    scheduled_end = shift_start.replace(
        hour=AUTO_SHIFT_END_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    shift_end = max(scheduled_end, shift_start)
    worker = user.get("shift_worker") or {
        "telegram_user_id": str(user_id),
        "name": user.get("full_name", str(user_id)),
        "role": "",
        "brigade": "",
    }
    chat_id = user.get("shift_chat_id")
    thread_id = user.get("shift_thread_id")

    user["auto_close_in_progress"] = True
    if (
        user.get("break_active")
        and user.get("break_start_time") is not None
        and user["break_start_time"] > shift_end
    ):
        user["break_active"] = False
        user["break_start_time"] = None

    try:
        total_time, work_time = calculate_shift_duration(user, shift_end)
        save_shift_to_sheet(
            None,
            user,
            shift_end,
            total_time,
            work_time,
            worker,
            chat_id,
        )
    except Exception:
        user["auto_close_in_progress"] = False
        raise

    summary = (
        f"{user['full_name']}\n"
        "Зміну автоматично закрито, оскільки її не закрили до півночі.\n\n"
        f"Початок: {format_datetime(shift_start)}\n"
        f"Кінець: {format_datetime(shift_end)}\n"
        f"Перерви: {format_duration(user['total_break'])}\n"
        f"Чистий робочий час: {format_duration(work_time)}"
    )
    clear_active_shift(user)

    if chat_id is not None:
        options = {"reply_markup": main_keyboard()}
        if thread_id is not None:
            options["message_thread_id"] = thread_id
        try:
            bot.send_message(chat_id, summary, **options)
        except Exception as error:
            print(f"Automatic shift closure notification failed for {user_id}: {error}")


def automatic_shift_closure_scheduler():
    """Close shifts from previous calendar days at 18:00 Kyiv time."""
    while True:
        current = now_dt()
        for user_id, user in list(users.items()):
            shift_start = user.get("shift_start_time")
            if (
                not user.get("shift_started")
                or shift_start is None
                or shift_start.date() >= current.date()
                or user.get("auto_close_in_progress")
            ):
                continue
            try:
                auto_close_overdue_shift(user_id, user)
            except Exception as error:
                print(f"Automatic shift closure failed for {user_id}: {error}")
        time.sleep(30)


def format_money(value):
    return f"{float(value):,.2f}".replace(",", " ").replace(".", ",")


def calculation_context_matches(pending, message):
    chat_id, thread_id = message_context(message)
    return (
        str(pending.get("chat_id", "")) == chat_id
        and str(pending.get("thread_id", "")) == thread_id
    )


def calculation_cancel_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(CALC_CANCEL_TEXT))
    return markup


def calculation_worker_button(worker):
    if worker.get("telegram_user_id"):
        return f"{worker['name']} | {worker['telegram_user_id']}"
    return worker["name"]


def find_calculation_worker(text, workers):
    normalized = str(text or "").strip().casefold()
    for worker in workers:
        options = {worker["name"].casefold(), calculation_worker_button(worker).casefold()}
        if worker.get("telegram_user_id"):
            options.add(str(worker["telegram_user_id"]).casefold())
        if normalized in options:
            return worker
    return None


def send_calculation_worker_prompt(message, pending, error_text=""):
    workers = pending["workers"]
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for worker in workers:
        markup.row(KeyboardButton(calculation_worker_button(worker)))
    markup.row(KeyboardButton(CALC_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(message, f"{prefix}Оберіть працівника:", markup)


def send_calculation_object_prompt(message, pending, error_text=""):
    objects = pending["objects"]
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for item in objects:
        markup.row(KeyboardButton(item["name"]))
    markup.row(KeyboardButton(CALC_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(message, f"{prefix}Оберіть активний об’єкт:", markup)


def send_calculation_capture_prompt(message, pending, error_text=""):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for capture in pending["captures"]:
        markup.row(KeyboardButton(capture["name"]))
    markup.row(KeyboardButton(CALC_CANCEL_TEXT))
    prefix = f"{error_text}\n\n" if error_text else ""
    send_with_markup(
        message,
        f"{prefix}Об’єкт: {pending['object']['name']}\nОберіть активну захватку:",
        markup,
    )


def send_calculation_amount_prompt(message, pending, error_text=""):
    prefix = f"{error_text}\n\n" if error_text else ""
    sign_hint = (
        "Для коригування використовуйте знак: наприклад, +500 або -300."
        if pending["operation_type"] == "Коригування"
        else "Введіть додатну суму, наприклад: 2500."
    )
    send_with_markup(
        message,
        f"{prefix}Працівник: {pending['worker']['name']}\n"
        f"Об’єкт: {pending['object']['name']}\n"
        f"Захватка: {pending['capture']['name']}\n\n{sign_hint}",
        calculation_cancel_keyboard(),
    )


def send_calculation_comment_prompt(message, pending):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(CALC_SKIP_COMMENT_TEXT))
    markup.row(KeyboardButton(CALC_CANCEL_TEXT))
    send_with_markup(message, "Додайте коментар або натисніть «Пропустити»:", markup)


def calculation_confirmation_text(pending):
    lines = [
        f"Операція: {pending['operation_type']}",
        f"Працівник: {pending['worker']['name']}",
        f"Об’єкт: {pending['object']['name']}",
        f"Захватка: {pending['capture']['name']}",
    ]
    if pending["operation_type"] != "Закриття захватки":
        lines.append(f"Сума: {format_money(pending['amount'])} грн")
    lines.append(f"Коментар: {pending.get('comment') or '—'}")
    return "\n".join(lines) + "\n\nПідтвердити операцію?"


def send_calculation_confirmation(message, pending):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(CALC_CONFIRM_TEXT), KeyboardButton(CALC_CANCEL_TEXT))
    send_with_markup(message, calculation_confirmation_text(pending), markup)


def start_calculation_flow(message, action):
    user = get_user(message.from_user.id, get_user_name(message))
    chat_id, thread_id = message_context(message)
    pending = {"action": action, "chat_id": chat_id, "thread_id": thread_id}

    if action == CALC_CLOSE_CAPTURE_TEXT:
        worker = get_worker(message.from_user.id)
        if not worker:
            send_with_keyboard(
                message,
                "Закрити захватку може зареєстрований активний працівник із Telegram ID.",
            )
            return
        objects = get_active_objects(create_if_missing=False)
        if not objects:
            send_with_keyboard(message, "У таблиці немає активних об’єктів.")
            return
        pending.update({
            "stage": "object",
            "worker": worker,
            "operation_type": "Закриття захватки",
            "objects": objects,
            "amount": 0.0,
            "comment": "Захватку закрито через бот.",
        })
        user["pending_calculation"] = pending
        send_calculation_object_prompt(message, pending)
        return

    workers = get_active_payment_workers()
    if not workers:
        send_with_keyboard(message, "У вкладці «Працівники» немає активних працівників.")
        return
    pending.update({"stage": "worker", "workers": workers})
    if action == CALC_ADVANCE_TEXT:
        pending["operation_type"] = "Аванс"
    elif action == CALC_FULL_PAYMENT_TEXT:
        pending["operation_type"] = "Повний розрахунок"
    elif action == CALC_ADJUSTMENT_TEXT:
        pending["operation_type"] = "Коригування"
    user["pending_calculation"] = pending
    send_calculation_worker_prompt(message, pending)


def show_worker_balance(message, worker):
    summary = get_worker_financial_summary(worker)
    send_with_keyboard(
        message,
        f"Баланс працівника: {worker['name']}\n\n"
        f"Початковий баланс: {format_money(summary['initial_balance'])} грн\n"
        f"Відпрацьовано: {summary['hours']:g} год\n"
        f"Ставка: {format_money(summary['rate'])} грн/год\n"
        f"Нараховано: {format_money(summary['accrued'])} грн\n"
        f"Виплачено: {format_money(summary['paid'])} грн\n"
        f"Коригування: {format_money(summary['adjustment'])} грн\n"
        f"Поточний баланс: {format_money(summary['balance'])} грн",
    )


def show_worker_payment_history(message, worker):
    summary = get_worker_financial_summary(worker)
    records = summary["payments"][-10:]
    if not records:
        send_with_keyboard(message, f"Для працівника {worker['name']} виплат ще немає.")
        return
    lines = [f"Останні операції: {worker['name']}"]
    for row in reversed(records):
        operation_type = str(get_record_value(row, {"тип операції"}) or "Операція").strip()
        paid = sheet_number(get_record_value(row, {"виплачено, грн"}))
        adjustment = sheet_number(get_record_value(row, {"коригування, грн"}))
        amount = adjustment if operation_type == "Коригування" else paid
        date = str(get_record_value(row, {"дата"}) or "—").strip()
        capture = str(get_record_value(row, {"захватка"}) or "—").strip()
        lines.append(f"• {date} | {operation_type} | {format_money(amount)} грн | {capture}")
    send_with_keyboard(message, "\n".join(lines))


def parse_calculation_amount(text, operation_type):
    normalized = str(text or "").strip().replace("\u00a0", "").replace(" ", "")
    normalized = normalized.replace("грн", "").replace("₴", "").replace(",", ".")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d{1,2})?", normalized):
        raise ValueError("Введіть суму числом, не більше двох знаків після коми.")
    amount = float(normalized)
    if operation_type == "Коригування":
        if amount == 0:
            raise ValueError("Коригування не може дорівнювати нулю.")
    elif amount <= 0:
        raise ValueError("Сума повинна бути більшою за нуль.")
    return amount


def handle_calculations_text(message, text):
    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_calculation") or {}
    if pending and not calculation_context_matches(pending, message):
        pending = {}

    if text == CALC_CANCEL_TEXT:
        if pending:
            user["pending_calculation"] = None
            send_with_keyboard(message, "Операцію скасовано.")
        else:
            send_with_keyboard(message, "Активної операції немає.")
        return True

    actions = {
        CALC_ADVANCE_TEXT,
        CALC_FULL_PAYMENT_TEXT,
        CALC_ADJUSTMENT_TEXT,
        CALC_BALANCE_TEXT,
        CALC_HISTORY_TEXT,
        CALC_CLOSE_CAPTURE_TEXT,
    }
    if text in actions:
        try:
            start_calculation_flow(message, text)
        except Exception as error:
            print(f"Calculation flow start failed: {error}")
            send_with_keyboard(message, f"Не вдалося почати операцію. {error}")
        return True

    if not pending:
        send_with_keyboard(message, "Оберіть дію в меню «Розрахунки».")
        return True

    if pending["stage"] == "worker":
        worker = find_calculation_worker(text, pending["workers"])
        if not worker:
            send_calculation_worker_prompt(message, pending, "Працівника не знайдено.")
            return True
        pending["worker"] = worker
        if pending["action"] == CALC_BALANCE_TEXT:
            user["pending_calculation"] = None
            try:
                show_worker_balance(message, worker)
            except Exception as error:
                print(f"Worker balance failed: {error}")
                send_with_keyboard(message, f"Не вдалося розрахувати баланс. {error}")
            return True
        if pending["action"] == CALC_HISTORY_TEXT:
            user["pending_calculation"] = None
            try:
                show_worker_payment_history(message, worker)
            except Exception as error:
                print(f"Worker payment history failed: {error}")
                send_with_keyboard(message, f"Не вдалося отримати історію. {error}")
            return True
        objects = get_active_objects(create_if_missing=False)
        if not objects:
            user["pending_calculation"] = None
            send_with_keyboard(message, "У таблиці немає активних об’єктів.")
            return True
        pending.update({"stage": "object", "objects": objects})
        send_calculation_object_prompt(message, pending)
        return True

    if pending["stage"] == "object":
        selected = next(
            (item for item in pending["objects"] if item["name"].casefold() == text.casefold()),
            None,
        )
        if not selected:
            send_calculation_object_prompt(message, pending, "Об’єкт не знайдено.")
            return True
        captures = get_active_captures_for_object(selected["name"])
        if not captures:
            send_calculation_object_prompt(
                message,
                pending,
                f"Для об’єкта «{selected['name']}» немає активних захваток.",
            )
            return True
        pending.update({"stage": "capture", "object": selected, "captures": captures})
        send_calculation_capture_prompt(message, pending)
        return True

    if pending["stage"] == "capture":
        capture = next(
            (item for item in pending["captures"] if item["name"].casefold() == text.casefold()),
            None,
        )
        if not capture:
            send_calculation_capture_prompt(message, pending, "Захватку не знайдено.")
            return True
        pending["capture"] = capture
        if pending["operation_type"] == "Закриття захватки":
            pending["stage"] = "confirm"
            send_calculation_confirmation(message, pending)
        else:
            pending["stage"] = "amount"
            send_calculation_amount_prompt(message, pending)
        return True

    if pending["stage"] == "amount":
        try:
            pending["amount"] = parse_calculation_amount(text, pending["operation_type"])
        except ValueError as error:
            send_calculation_amount_prompt(message, pending, str(error))
            return True
        pending["stage"] = "comment"
        send_calculation_comment_prompt(message, pending)
        return True

    if pending["stage"] == "comment":
        pending["comment"] = "" if text == CALC_SKIP_COMMENT_TEXT else text
        pending["stage"] = "confirm"
        send_calculation_confirmation(message, pending)
        return True

    if pending["stage"] == "confirm":
        if text != CALC_CONFIRM_TEXT:
            send_calculation_confirmation(message, pending)
            return True
        try:
            operation = {
                "worker": pending["worker"],
                "amount": pending.get("amount", 0.0),
                "comment": pending.get("comment", ""),
                "type": pending["operation_type"],
                "object": pending["object"],
                "capture": pending["capture"],
                "capture_status": (
                    "Закрита"
                    if pending["operation_type"] == "Закриття захватки"
                    else "Відкрита"
                ),
            }
            if pending["operation_type"] == "Закриття захватки":
                if not pending.get("payment_saved"):
                    append_payment_operation(message, operation)
                    pending["payment_saved"] = True
                deactivate_capture(pending["capture"])
            else:
                append_payment_operation(message, operation)
        except Exception as error:
            print(f"Calculation operation saving failed: {error}")
            send_with_markup(
                message,
                f"Не вдалося записати операцію. {error}",
                calculation_cancel_keyboard(),
            )
            return True
        user["pending_calculation"] = None
        amount_line = (
            ""
            if pending["operation_type"] == "Закриття захватки"
            else f"\nСума: {format_money(pending['amount'])} грн"
        )
        send_with_keyboard(
            message,
            f"Записано: {pending['operation_type']}\n"
            f"Працівник: {pending['worker']['name']}\n"
            f"Об’єкт: {pending['object']['name']}\n"
            f"Захватка: {pending['capture']['name']}{amount_line}",
        )
        return True

    return True


@bot.message_handler(commands=["calculations", "rozrahunky"])
def calculations_command(message):
    user = get_user(message.from_user.id, get_user_name(message))
    chat_id, thread_id = message_context(message)
    user["calculations_context"] = {"chat_id": chat_id, "thread_id": thread_id}
    user["pending_calculation"] = None
    send_with_markup(
        message,
        "Меню «Розрахунки» активовано в цій гілці.",
        calculations_keyboard(),
    )


@bot.message_handler(commands=["start"])
def start_command(message):
    if is_calculations_topic(message):
        send_with_keyboard(message, "Ви у гілці «Розрахунки». Оберіть потрібну дію.")
        return
    if is_materials_topic(message):
        send_with_keyboard(
            message,
            "Ви у гілці «Матеріали». Напишіть матеріал і кількість, наприклад: «5 кутиків». "
            "Будь-який такий запис означає видачу матеріалу.",
        )
        return

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
    if is_materials_topic(message):
        handle_materials_text(message, MATERIALS_TEXT)
        return
    start_shift(message)


@bot.message_handler(commands=["break"])
def break_command(message):
    if is_materials_topic(message):
        handle_materials_text(message, MATERIALS_TEXT)
        return
    start_break(message)


@bot.message_handler(commands=["stop_break"])
def stop_break_command(message):
    if is_materials_topic(message):
        handle_materials_text(message, MATERIALS_TEXT)
        return
    stop_break(message)


@bot.message_handler(commands=["stop"])
def stop_command(message):
    if is_materials_topic(message):
        handle_materials_text(message, MATERIALS_TEXT)
        return
    end_shift(message)


@bot.message_handler(commands=["status"])
def status_command(message):
    if is_materials_topic(message):
        handle_materials_text(message, MATERIALS_TEXT)
        return
    show_status(message)


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    if is_materials_topic(message):
        user = get_user(message.from_user.id, get_user_name(message))
        pending = user.get("pending_material") or {}
        if pending.get("kind") == "defect_photo":
            handle_defect_photo(message, pending)
            return
        if pending.get("kind") in {"defect_input", "defect_choice"}:
            send_defect_material_prompt(message, "Спочатку введіть матеріал і кількість.")
            return
        if pending.get("kind") == "defect_confirm":
            send_with_keyboard(message, "Фото вже додано. Підтвердьте дефектний акт кнопкою або скасуйте його.")
            return
    handle_invoice_photo(message)


@bot.message_handler(content_types=["text"])
def handle_text(message):
    text = (message.text or "").strip()

    if is_calculations_topic(message):
        try:
            handle_calculations_text(message, text)
        except Exception as error:
            print(f"Calculation handler failed: {error}")
            send_with_keyboard(message, f"Не вдалося виконати операцію. {error}")
        return

    if is_materials_topic(message):
        handle_materials_text(message, text)
        return

    if is_events_topic(message) and handle_events_text(message, text):
        return

    if is_bot_topic(message) and text == WHO_ON_SHIFT_TEXT:
        show_who_is_on_shift(message)
        return

    if is_bot_topic(message) and text == WEATHER_FORECAST_TEXT:
        show_remaining_weather_forecast(message)
        return

    if text == STATUS_TEXT:
        user = get_user(message.from_user.id, get_user_name(message))
        pending_delegate = user.get("pending_delegate") or {}
        if pending_delegate_matches_message(pending_delegate, message):
            user["pending_delegate"] = None
            user["material_for_worker"] = None
        show_status(message)
        return

    if handle_delegate_text(message, text):
        return

    if text == DELEGATE_CANCEL_TEXT:
        cancel_delegate(message)
        return

    commands_map = {
        START_SHIFT_TEXT: start_shift,
        START_BREAK_TEXT: start_break,
        STOP_BREAK_TEXT: stop_break,
        END_SHIFT_TEXT: end_shift,
        STATUS_TEXT: show_status,
        SELECT_CAPTURE_TEXT: choose_capture,
        MARK_FOR_MASTER_TEXT: lambda current_message: send_worker_selection(current_message, "shift"),
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


def handle_materials_text(message, text):
    if handle_delegate_text(message, text):
        return

    if text == DELEGATE_CANCEL_TEXT:
        cancel_delegate(message)
        return

    if text == MATERIAL_OTHER_MASTER_TEXT:
        send_worker_selection(message, "material")
        return

    if text == BALANCE_TEXT:
        start_balance_check(message)
        return

    if text == MATERIAL_ORDER_TEXT:
        start_material_issue(message, operation="Замовлення")
        return

    if text == INVENTORY_TEXT:
        start_inventory(message, text)
        return

    if text == DEFECT_TEXT:
        start_defect_report(message)
        return

    if text in {MATERIALS_TEXT, "Взяти матеріал"}:
        start_material_issue(message)
        return

    if text == MATERIAL_RETURN_TEXT:
        user = get_user(message.from_user.id, get_user_name(message))
        user["pending_material"] = {"kind": "return_input"}
        send_with_keyboard(
            message,
            "Напишіть матеріал і кількість, які повертаєте на склад. "
            "Наприклад: «3 кутики» або «клей-піна 1 балон».",
        )
        return

    if text == MATERIAL_CANCEL_TEXT:
        cancel_material_operation(message)
        return

    if text == MATERIAL_CONFIRM_TEXT:
        save_material_operation(message)
        return

    if text == INVENTORY_CONFIRM_TEXT:
        save_inventory_record(message)
        return

    if text == INVOICE_CONFIRM_TEXT:
        save_invoice_delivery(message)
        return


    if text == DEFECT_CONFIRM_TEXT:
        save_defect_report(message)
        return

    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_material")
    if pending and pending.get("kind") == "issue_capture":
        operation = pending.get("operation") or "Видача"
        capture = next(
            (item for item in pending.get("captures", []) if item.get("name") == text),
            None,
        )
        if not capture:
            start_material_issue(
                message,
                "Захватку не знайдено. Оберіть її кнопкою.",
                operation=operation,
            )
            return
        send_material_issue_input_prompt(message, capture, operation=operation)
        return
    if pending and pending.get("kind") == "issue_input":
        send_material_choice(
            message,
            text,
            forced_operation=pending.get("operation") or "Видача",
        )
        return
    if pending and pending.get("kind") in {"defect_input", "defect_choice"}:
        choose_defect_material(message, text, pending)
        return
    if pending and pending.get("kind") == "defect_photo":
        send_defect_photo_prompt(message, pending, "Очікую фото дефекту, а не текст.")
        return
    if pending and pending.get("kind") == "defect_confirm":
        send_with_keyboard(message, "Підтвердьте дефектний акт кнопкою або скасуйте його.")
        return
    if pending and pending.get("kind") == "return_input":
        send_material_choice(message, text, forced_operation="Повернення")
        return
    if pending and pending.get("kind") == "inventory_material":
        choose_inventory_material(message, text, pending)
        return
    if pending and pending.get("kind") == "inventory_quantity":
        set_inventory_quantity(message, text, pending)
        return
    if pending and pending.get("kind") == "balance_material":
        choose_balance_material(message, text, pending)
        return
    if pending and select_material_candidate(message, text):
        return

    send_material_choice(
        message,
        text,
        forced_operation=(pending or {}).get("operation") or "Видача",
    )


def main():
    print("Bot is running...")
    threading.Thread(target=weather_scheduler, daemon=True).start()
    threading.Thread(target=automatic_shift_closure_scheduler, daemon=True).start()
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
