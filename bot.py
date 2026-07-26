from email.mime import message
import os
from dotenv import load_dotenv

load_dotenv()

import json
import re
import urllib.error
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
MATERIALS_TEXT = "Взяти матеріал"
MATERIAL_CONFIRM_TEXT = "Підтвердити"
MATERIAL_CANCEL_TEXT = "Скасувати матеріал"

MATERIAL_LOG_HEADERS = [
    "дата", "операція", "матеріал", "кількість", "од. виміру",
    "захватка", "працівник", "роль", "примітка", "telegram_user_id",
    "telegram_chat_id", "timestamp",
]

MATERIAL_BALANCE_HEADERS = [
    "матеріал", "од. виміру", "початковий залишок", "надійшло",
    "видано", "поточний залишок",
]


DAILY_MATERIAL_SHEET = "Рух матеріалів"
DAILY_MOVEMENT_COLUMNS = {
    "Видача": "Видано",
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
    return markup


def materials_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    markup.row(KeyboardButton(MATERIALS_TEXT))
    return markup


def is_materials_topic(message):
    """Material operations are permitted only in the configured Telegram topic."""
    thread_id = getattr(message, "message_thread_id", None)
    return (
        bool(MATERIALS_CHAT_ID and MATERIALS_THREAD_ID)
        and str(message.chat.id) == str(MATERIALS_CHAT_ID)
        and str(thread_id) == str(MATERIALS_THREAD_ID)
    )


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
            "pending_material": None,
        },
    )


def send_with_keyboard(message, text):
    keyboard = materials_keyboard() if is_materials_topic(message) else main_keyboard()
    send_options = {"reply_markup": keyboard}
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


def sync_daily_material_movement(spreadsheet, message, material, operation, quantity, worker, timestamp):
    worksheet = spreadsheet.worksheet(DAILY_MATERIAL_SHEET)
    date_values = worksheet.col_values(1)
    row_number = next(
        (index for index, value in enumerate(date_values, start=1) if index >= 5 and date_matches(value, timestamp)),
        None,
    )
    if row_number is None:
        dated_rows = [
            index for index, value in enumerate(date_values, start=1)
            if index >= 5 and str(value or "").strip()
        ]
        row_number = max(dated_rows, default=4) + 1
        row_formulas = worksheet.row_values(row_number, value_render_option="FORMULA")
        if any(str(value).startswith("=SUM(") for value in row_formulas):
            worksheet.insert_row([""] * worksheet.col_count, index=row_number)
            extend_movement_totals(
                worksheet,
                row_number + 1,
                row_number - 1,
                row_number,
            )
        worksheet.update(f"A{row_number}", [[timestamp.strftime("%d.%m.%Y")]])

    details = worksheet.get(f"B{row_number}:D{row_number}")
    details = details[0] if details else ["", "", ""]
    details += [""] * (3 - len(details))
    worker_name = worker.get("name") or get_user_name(message)
    side_label = {
        "Видача": "Видача через RAHUY Bot",
        "Надходження": "Приймання через RAHUY Bot",
        "Замовлення": "Замовлення через RAHUY Bot",
    }[operation]
    details[0] = append_unique_text(details[0], side_label)
    details[1] = append_unique_text(details[1], "RAHUY Bot / склад")
    details[2] = append_unique_text(details[2], worker_name)
    worksheet.update(f"B{row_number}:D{row_number}", [details])

    column = get_daily_material_column(worksheet, material, operation)
    cell = f"{column_letter(column)}{row_number}"
    current_value = worksheet.acell(cell).value
    worksheet.update(cell, [[parse_sheet_number(current_value) + quantity]])
    return row_number


def find_material_candidates(text):
    candidates = []
    for row in get_material_catalog():
        score = material_match_score(text, row)
        if score >= 0.35:
            candidates.append((score, row))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in candidates[:3]]


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
            "operation": {"type": "string", "enum": ["Видача", "Надходження", "Замовлення", "Невідомо"]},
            "quantity": {"type": ["number", "null"]},
            "material": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["operation", "quantity", "material", "confidence"],
    }
    instructions = (
        "Ти розпізнаєш короткі українські повідомлення працівників про рух матеріалів. "
        "Обери material тільки як точну назву з каталогу. Використовуй синоніми лише для зіставлення. "
        "Не вигадуй матеріал, кількість чи дію. Якщо даних недостатньо, поверни null або Невідомо. "
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
        output_text = result.get("output_text", "")
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

    operation = parsed.get("operation")
    if operation not in ("Видача", "Надходження", "Замовлення"):
        operation = detect_material_operation(text)
    return {"quantity": quantity, "operation": operation, "selected": selected}


def detect_material_operation(text):
    normalized = normalize_material_text(text)
    if any(word in normalized for word in ("замовляю", "замовити", "замовив", "замовила", "замовлення")):
        return "Замовлення"
    if any(word in normalized for word in (
        "привіз", "привезли", "надійшло", "отримали на склад",
        "повернув", "повернула", "повернули", "повернення",
    )):
        return "Надходження"
    return "Видача"


def send_material_choice(message, text):
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
        send_with_keyboard(
            message,
            "Не бачу кількості. Напишіть, наприклад: «взяв 5 кутиків» або «клей-піна 1 балон»."
        )
        return

    if not candidates:
        send_with_keyboard(
            message,
            "Не знайшов матеріал у довіднику. Напишіть назву точніше або додамо для нього синонім."
        )
        return

    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = {
        "operation": ai_result["operation"] if ai_result else detect_material_operation(text),
        "quantity": quantity,
        "candidates": candidates,
        "selected": None,
    }

    if len(candidates) == 1:
        select_material_candidate(message, candidates[0].get("Матеріал", ""))
        return

    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for candidate in candidates:
        markup.row(KeyboardButton(str(candidate.get("Матеріал", ""))))
    markup.row(KeyboardButton(MATERIAL_CANCEL_TEXT))
    options = {"reply_markup": markup}
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None:
        options["message_thread_id"] = thread_id
    bot.send_message(message.chat.id, "Уточніть матеріал:", **options)


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
    operation = pending["operation"].lower()
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton(MATERIAL_CONFIRM_TEXT), KeyboardButton(MATERIAL_CANCEL_TEXT))
    options = {"reply_markup": markup}
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None:
        options["message_thread_id"] = thread_id
    bot.send_message(
        message.chat.id,
        f"{operation.capitalize()}: {pending['quantity']:g} {unit}\n"
        f"Матеріал: {selected['Матеріал']}\n\nПідтвердити запис?",
        **options,
    )
    return True


def save_material_operation(message):
    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_material")
    if not pending or not pending.get("selected"):
        send_with_keyboard(message, "Немає операції матеріалу для підтвердження.")
        return

    material = pending["selected"]
    worker = get_worker(message.from_user.id) or {}
    capture = user.get("selected_capture") or {}
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
        str(message.from_user.id),
        str(message.chat.id),
        format_datetime(timestamp),
    ])

    user["pending_material"] = None
    send_with_keyboard(
        message,
        f"Записано: {pending['operation'].lower()} — {pending['quantity']:g} "
        f"{material.get('Од. виміру / примітка', '')}\nМатеріал: {material['Матеріал']}",
    )


def cancel_material_operation(message):
    user = get_user(message.from_user.id, get_user_name(message))
    user["pending_material"] = None
    send_with_keyboard(message, "Операцію з матеріалом скасовано.")


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

    total_time = round_up_to_minute(
        shift_end - user["shift_start_time"],
        minimum_one_minute=True,
    )
    user["total_break"] = round_up_to_minute(user["total_break"])
    work_time = total_time - user["total_break"]
    if work_time <= timedelta():
        work_time = timedelta(minutes=1)
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
    if is_materials_topic(message):
        send_with_keyboard(
            message,
            "Ви у гілці «Матеріали». Напишіть, що взяли або отримали, наприклад: «взяв 5 кутиків».",
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


@bot.message_handler(content_types=["text"])
def handle_text(message):
    text = (message.text or "").strip()

    if is_materials_topic(message):
        handle_materials_text(message, text)
        return

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


def handle_materials_text(message, text):
    if text == MATERIALS_TEXT:
        send_with_keyboard(
            message,
            "Напишіть, що взяли або отримали. Наприклад: «взяв 5 кутиків» або «клей-піна 1 балон».",
        )
        return

    if text == MATERIAL_CANCEL_TEXT:
        cancel_material_operation(message)
        return

    if text == MATERIAL_CONFIRM_TEXT:
        save_material_operation(message)
        return

    user = get_user(message.from_user.id, get_user_name(message))
    pending = user.get("pending_material")
    if pending and select_material_candidate(message, text):
        return

    material_keywords = (
        "взяв", "взяла", "взяли", "видати", "видай", "видав",
        "привіз", "привезли", "надійшло", "отримали",
        "повернув", "повернула", "повернули",
        "замовляю", "замовити", "замовив", "замовила",
    )
    if any(keyword in normalize_material_text(text) for keyword in material_keywords):
        send_material_choice(message, text)
        return

    send_with_keyboard(
        message,
        "Напишіть рух матеріалу, наприклад: «взяв 5 кутиків» або «привезли 10 балонів клей-піни».",
    )


print("Bot is running...")
bot.infinity_polling(skip_pending=True)
