import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import telebot
from telebot.types import KeyboardButton, ReplyKeyboardMarkup


TOKEN = os.getenv("TOKEN")
KYIV_TZ = ZoneInfo("Europe/Kyiv")

if not TOKEN:
    raise ValueError("TOKEN is not set")

bot = telebot.TeleBot(TOKEN)

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
            f"Працівник: {user['full_name']}\nСтатус: поза зміною",
        )
        return

    current_break = timedelta()
    if user["break_active"] and user["break_start_time"] is not None:
        current_break = now_dt() - user["break_start_time"]

    status_text = (
        f"Працівник: {user['full_name']}\n"
        f"Початок зміни: {format_datetime(user['shift_start_time'])}\n"
        f"Статус: {'перерва' if user['break_active'] else 'у зміні'}\n"
        f"Накопичені перерви: {format_duration(user['total_break'] + current_break)}"
    )

    if user["break_active"]:
        status_text += f"\nПочаток перерви: {format_datetime(user['break_start_time'])}"

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

    if text == START_SHIFT_TEXT:
        start_shift(message)
    elif text == START_BREAK_TEXT:
        start_break(message)
    elif text == STOP_BREAK_TEXT:
        stop_break(message)
    elif text == END_SHIFT_TEXT:
        end_shift(message)
    elif text == STATUS_TEXT:
        show_status(message)
    elif text.startswith("/start"):
        start_command(message)
    elif text.startswith("/work"):
        start_shift(message)
    elif text.startswith("/break"):
        start_break(message)
    elif text.startswith("/stop_break"):
        stop_break(message)
    elif text.startswith("/stop"):
        end_shift(message)
    elif text.startswith("/status"):
        show_status(message)
    else:
        send_with_keyboard(message, "Використайте кнопки нижче або команду /start.")


print("Bot is running...")
bot.infinity_polling(skip_pending=True)
