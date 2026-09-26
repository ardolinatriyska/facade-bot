import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path


class FakeWorksheetNotFound(Exception):
    pass


class FakeButton:
    def __init__(self, text, callback_data=None, url=None):
        self.text = text
        self.callback_data = callback_data
        self.url = url


class FakeMarkup:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.rows = []

    def row(self, *buttons):
        self.rows.append([button.text for button in buttons])


class FakeTeleBot:
    def __init__(self, token, **kwargs):
        self.token = token
        self.sent = []
        self.edited = []
        self.answered_callbacks = []
        self.next_message_id = 1000

    def get_me(self):
        return types.SimpleNamespace(username="test_bot", id=99)

    def message_handler(self, **kwargs):
        return lambda function: function

    def callback_query_handler(self, **kwargs):
        return lambda function: function

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        self.next_message_id += 1
        return types.SimpleNamespace(message_id=self.next_message_id)

    def edit_message_text(self, text, **kwargs):
        self.edited.append((text, kwargs))

    def answer_callback_query(self, callback_query_id, text=None, **kwargs):
        self.answered_callbacks.append((callback_query_id, text, kwargs))

    def infinity_polling(self, **kwargs):
        return None


def load_bot_module():
    os.environ["BOT_TOKEN"] = "test-token"
    os.environ["SASHA_RECEIPT_ALLOWED_USER_IDS"] = "1890913278,5665169791"

    telebot_module = types.ModuleType("telebot")
    telebot_module.TeleBot = FakeTeleBot
    telebot_types = types.ModuleType("telebot.types")
    telebot_types.KeyboardButton = FakeButton
    telebot_types.ReplyKeyboardMarkup = FakeMarkup
    telebot_types.InlineKeyboardButton = FakeButton
    telebot_types.InlineKeyboardMarkup = FakeMarkup
    telebot_module.types = telebot_types

    gspread_module = types.ModuleType("gspread")
    gspread_module.WorksheetNotFound = FakeWorksheetNotFound
    gspread_module.authorize = lambda credentials: None

    credentials_module = types.ModuleType("google.oauth2.service_account")

    class FakeCredentials:
        @classmethod
        def from_service_account_file(cls, *args, **kwargs):
            return cls()

        @classmethod
        def from_service_account_info(cls, *args, **kwargs):
            return cls()

    credentials_module.Credentials = FakeCredentials

    google_module = types.ModuleType("google")
    oauth2_module = types.ModuleType("google.oauth2")
    oauth2_module.service_account = credentials_module
    google_module.oauth2 = oauth2_module

    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda: None

    fake_modules = {
        "telebot": telebot_module,
        "telebot.types": telebot_types,
        "gspread": gspread_module,
        "google": google_module,
        "google.oauth2": oauth2_module,
        "google.oauth2.service_account": credentials_module,
        "dotenv": dotenv_module,
    }
    previous = {name: sys.modules.get(name) for name in fake_modules}
    sys.modules.update(fake_modules)
    try:
        module_path = Path(__file__).with_name("bot.py")
        spec = importlib.util.spec_from_file_location("production_bot_for_tests", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


class FakeWorksheet:
    def __init__(self, title, headers=None, records=None):
        self.title = title
        self.headers = list(headers or [])
        self.records = list(records or [])
        self.updated = []
        self.appended = []
        self.cell_updates = []
        self.col_count = len(self.headers)

    def row_values(self, row):
        return list(self.headers) if row == 1 else []

    def get_all_records(self):
        return list(self.records)

    def get_all_values(self):
        return [list(self.headers)] + [
            [str(record.get(header, "")) for header in self.headers]
            for record in self.records
        ]

    def update(self, cell, values):
        self.updated.append((cell, values))
        self.headers = list(values[0])
        self.records = [dict(zip(self.headers, row)) for row in values[1:]]
        self.col_count = max(self.col_count, len(self.headers))

    def add_cols(self, count):
        self.col_count += count

    def append_row(self, values, **kwargs):
        self.appended.append((list(values), kwargs))
        self.records.append(dict(zip(self.headers, values)))

    def update_cell(self, row, column, value):
        self.cell_updates.append((row, column, value))
        self.records[row - 2][self.headers[column - 1]] = value


class FakeSpreadsheet:
    def __init__(self, worksheets=None):
        self.sheets = {worksheet.title: worksheet for worksheet in worksheets or []}
        self.created = []

    def worksheet(self, title):
        try:
            return self.sheets[title]
        except KeyError as error:
            raise FakeWorksheetNotFound(title) from error

    def worksheets(self):
        return list(self.sheets.values())

    def add_worksheet(self, title, rows, cols):
        worksheet = FakeWorksheet(title)
        self.sheets[title] = worksheet
        self.created.append((title, rows, cols))
        return worksheet


class BotEventsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_bot_module()

    def setUp(self):
        self.module.users.clear()
        self.module.bot.sent.clear()
        self.module.bot.edited.clear()
        self.module.bot.answered_callbacks.clear()

    def message(self, text=""):
        return types.SimpleNamespace(
            text=text,
            message_id=321,
            message_thread_id=int(self.module.EVENTS_THREAD_ID),
            chat=types.SimpleNamespace(id=int(self.module.EVENTS_CHAT_ID), type="supergroup"),
            from_user=types.SimpleNamespace(
                id=123,
                first_name="Іван",
                last_name="Петренко",
                username="ivan",
            ),
            reply_to_message=None,
        )

    def bot_topic_message(self, text=""):
        message = self.message(text)
        message.message_thread_id = int(self.module.BOT_TOPIC_THREAD_ID)
        message.chat.id = int(self.module.BOT_TOPIC_CHAT_ID)
        return message

    def materials_topic_message(self, text=""):
        self.module.MATERIALS_CHAT_ID = "-100555000111"
        self.module.MATERIALS_THREAD_ID = "77"
        message = self.message(text)
        message.message_thread_id = int(self.module.MATERIALS_THREAD_ID)
        message.chat.id = int(self.module.MATERIALS_CHAT_ID)
        return message

    def calculations_topic_message(self, text=""):
        self.module.CALCULATIONS_CHAT_ID = "-100555000111"
        self.module.CALCULATIONS_THREAD_ID = "88"
        message = self.message(text)
        message.message_thread_id = int(self.module.CALCULATIONS_THREAD_ID)
        message.chat.id = int(self.module.CALCULATIONS_CHAT_ID)
        return message

    def sasha_receipt_message(self, text="", user_id=1890913278):
        message = self.calculations_topic_message(text)
        message.from_user.id = user_id
        message.from_user.first_name = "Андрій" if user_id == 1890913278 else "Павло"
        message.from_user.last_name = "Резуненко" if user_id == 1890913278 else "Майба"
        return message

    def calculation_sheets(self, payment_records=None, daily_records=None):
        workers = FakeWorksheet(
            "Працівники",
            headers=[
                "telegram_user_id", "ПІБ", "роль", "бригада", "active", "примітка",
                "ставка, грн/год", "початковий баланс, грн",
            ],
            records=[{
                "telegram_user_id": "123",
                "ПІБ": "Іван Петренко",
                "роль": "Бригадир",
                "бригада": "Резуненко",
                "active": "TRUE",
                "ставка, грн/год": 200,
                "початковий баланс, грн": -300,
            }, {
                "telegram_user_id": "5101350452",
                "ПІБ": "Коваль Роман",
                "роль": "Майстер",
                "бригада": "Резуненко",
                "active": "TRUE",
                "ставка, грн/год": 250,
                "початковий баланс, грн": 100,
            }],
        )
        objects = FakeWorksheet(
            "Об’єкти",
            headers=["об’єкт", "active"],
            records=[{"об’єкт": "Well Place", "active": "TRUE"}],
        )
        captures = FakeWorksheet(
            "Захватки",
            headers=["capture_id", "назва", "обʼєкт", "active", "примітка"],
            records=[
                {
                    "capture_id": "capture-2",
                    "назва": "2 низ",
                    "обʼєкт": "Well Place",
                    "active": "TRUE",
                },
                {
                    "capture_id": "other",
                    "назва": "Інший об’єкт",
                    "обʼєкт": "Кротошин",
                    "active": "TRUE",
                },
            ],
        )
        payments = FakeWorksheet(
            "Виплати",
            headers=list(self.module.PAYMENT_HEADERS),
            records=list(payment_records or []),
        )
        daily = FakeWorksheet(
            "Денні дані",
            headers=["Дата", "Працівник", "Роль", "Години"],
            records=list(daily_records or []),
        )
        return FakeSpreadsheet([workers, objects, captures, payments, daily])

    def callback(self, message, data):
        callback_message = types.SimpleNamespace(
            message_id=1001,
            message_thread_id=message.message_thread_id,
            chat=message.chat,
            text="",
        )
        return types.SimpleNamespace(
            id=f"callback-{data}",
            data=data,
            from_user=message.from_user,
            message=callback_message,
        )

    def active_shift_user(self, message, capture=None):
        capture = capture or {
            "capture_id": "capture-2",
            "name": "2 секція",
            "project": "Well Place 2",
        }
        user = self.module.get_user(message.from_user.id, "Іван Петренко")
        user.update({
            "shift_started": True,
            "shift_start_time": self.module.datetime(
                2026, 9, 26, 8, 0, tzinfo=self.module.KYIV_TZ
            ),
            "shift_capture": dict(capture),
            "selected_capture": dict(capture),
            "shift_id": "shift-test-123",
            "shift_worker": {
                "telegram_user_id": "123",
                "name": "Іван Петренко",
                "role": "Майстер",
                "brigade": "Резуненко",
            },
        })
        return user

    def goal_sheet(self, records=None):
        return FakeWorksheet(
            self.module.SHIFT_GOALS_SHEET,
            headers=list(self.module.SHIFT_GOAL_HEADERS),
            records=list(records or []),
        )

    def advance_personal_goal_to_quantity(self, message, process_index=1, category_index=1, subprocess_index=1):
        self.module.start_shift_goal(message)
        self.module.handle_goal_callback(self.callback(message, "g:type:personal"))
        self.module.handle_goal_callback(
            self.callback(message, f"g:process:{process_index}")
        )
        if list(self.module.GOAL_PROCESS_TREE)[process_index] != "🧱 Клінкер":
            self.module.handle_goal_callback(
                self.callback(message, f"g:category:{category_index}")
            )
        self.module.handle_goal_callback(
            self.callback(message, f"g:subprocess:{subprocess_index}")
        )

    def test_events_keyboard_contains_only_approved_operational_buttons(self):
        rows = self.module.events_keyboard().rows
        self.assertEqual(
            rows,
            [
                ["🎯 Ціль зміни", "📊 Мій статус"],
                ["📍 Обрати захватку", "🌦 Прогноз погоди"],
            ],
        )

    def test_general_topic_without_thread_id_is_detected_as_events(self):
        message = self.message("/start")
        message.message_thread_id = None

        self.assertTrue(self.module.is_events_topic(message))

        self.module.start_command(message)

        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            self.module.events_keyboard().rows,
        )

    def test_missing_thread_id_in_another_chat_is_not_events(self):
        message = self.message()
        message.message_thread_id = None
        message.chat.id = -1009999999999

        self.assertFalse(self.module.is_events_topic(message))

    def test_events_menu_does_not_move_time_controls_from_work_menu(self):
        event_buttons = [button for row in self.module.events_keyboard().rows for button in row]
        work_buttons = [button for row in self.module.main_keyboard().rows for button in row]
        for button in (
            self.module.START_SHIFT_TEXT,
            self.module.START_BREAK_TEXT,
            self.module.STOP_BREAK_TEXT,
            self.module.END_SHIFT_TEXT,
        ):
            self.assertNotIn(button, event_buttons)
            self.assertIn(button, work_buttons)

    def test_all_four_events_buttons_route_in_events_topic(self):
        message = self.message()
        user = self.active_shift_user(message)
        calls = []
        original_choose = self.module.choose_capture
        original_weather = self.module.show_remaining_weather_forecast
        original_status = self.module.show_status
        self.module.choose_capture = lambda current: calls.append("capture")
        self.module.show_remaining_weather_forecast = lambda current: calls.append("weather")
        self.module.show_status = lambda current: calls.append("status")
        try:
            for text in (
                self.module.EVENTS_STATUS_TEXT,
                self.module.EVENTS_CAPTURE_TEXT,
                self.module.EVENTS_WEATHER_TEXT,
            ):
                self.assertTrue(self.module.handle_events_text(message, text))
            self.assertTrue(
                self.module.handle_events_text(message, self.module.SHIFT_GOAL_TEXT)
            )
        finally:
            self.module.choose_capture = original_choose
            self.module.show_remaining_weather_forecast = original_weather
            self.module.show_status = original_status

        self.assertEqual(calls, ["status", "capture", "weather"])
        self.assertEqual(user["pending_goal"]["stage"], "type")

    def test_goal_requires_active_shift_and_reuses_capture_selector_when_missing(self):
        message = self.message(self.module.SHIFT_GOAL_TEXT)
        self.module.start_shift_goal(message)
        self.assertIn("Спочатку відкрийте зміну", self.module.bot.sent[-1][1])

        user = self.module.get_user(message.from_user.id, "Іван Петренко")
        user.update({
            "shift_started": True,
            "shift_start_time": self.module.datetime(
                2026, 9, 26, 8, 0, tzinfo=self.module.KYIV_TZ
            ),
        })
        calls = []
        original_choose = self.module.choose_capture
        self.module.choose_capture = lambda current: calls.append(current)
        try:
            self.module.start_shift_goal(message)
        finally:
            self.module.choose_capture = original_choose
        self.assertEqual(calls, [message])
        self.assertEqual(user["pending_goal"]["stage"], "waiting_capture")

    def test_goal_navigation_exact_labels_units_numeric_and_back(self):
        message = self.message(self.module.SHIFT_GOAL_TEXT)
        self.active_shift_user(message)
        self.module.start_shift_goal(message)
        self.assertEqual(self.module.bot.sent[-1][2]["reply_markup"].rows, [
            [self.module.GOAL_PERSONAL_TEXT, self.module.GOAL_TEAM_TEXT],
            [self.module.GOAL_BACK_TEXT],
        ])

        self.module.handle_goal_callback(self.callback(message, "g:type:personal"))
        pending = self.module.get_user(123, "Іван Петренко")["pending_goal"]
        self.assertEqual(pending["stage"], "process")
        self.module.handle_goal_callback(self.callback(message, "g:process:1"))
        self.module.handle_goal_callback(self.callback(message, "g:category:1"))
        rows = self.module.bot.edited[-1][1]["reply_markup"].rows
        labels = [button for row in rows for button in row]
        self.assertIn("Попереднє вирівнювання площини", labels)
        self.assertIn("Молочко", labels)
        self.assertNotIn("Додатковий декоративний шар", labels)

        self.module.handle_goal_callback(self.callback(message, "g:back"))
        self.assertEqual(pending["stage"], "category")
        self.module.handle_goal_callback(self.callback(message, "g:category:1"))
        self.module.handle_goal_callback(self.callback(message, "g:subprocess:2"))
        self.assertEqual(pending["subprocess"], "Молочко")
        self.assertEqual(pending["unit"], "м²")
        self.assertTrue(self.module.handle_goal_text(message, "12,5"))
        self.assertEqual(pending["target"], 12.5)
        self.assertEqual(pending["stage"], "confirm")

        pending["stage"] = "quantity"
        self.assertTrue(self.module.handle_goal_text(message, "0"))
        self.assertEqual(pending["stage"], "quantity")
        self.assertIn("більшим за нуль", self.module.bot.edited[-1][0])
        self.assertEqual(len(self.module.bot.sent), 1)

    def test_goal_process_tree_matches_approved_labels_and_units(self):
        self.assertEqual(self.module.GOAL_PROCESS_TREE, {
            "🧱 Поклейка": {
                "Підготовка": [
                    ("Підготовка основи", "м²"),
                    ("Захист вікон", "шт."),
                    ("Люлька / риштування", "секції"),
                    ("Виставлення жилок", "м.п."),
                    ("Стартовий / цокольний профіль", "м.п."),
                    ("Деформаційний профіль / шов", "м.п."),
                ],
                "Утеплювач": [
                    ("Примикаюча віконна планка", "м.п."),
                    ("Поклейка утеплювача", "м²"),
                    ("Запінення щілин", "м²"),
                    ("Затирання площі", "м²"),
                    ("Дюбелювання", "м²"),
                ],
            },
            "🕸 Сітка": {
                "Підготовка під перетяжку": [
                    ("Запінення відкосів", "м.п."),
                    ("Встановлення кутиків", "м.п."),
                    ("Встановлення крапельників", "м.п."),
                    ("Діагональні косинки", "шт."),
                    ("Герметизація примикань", "м.п."),
                    ("Встановлення відливів", "м.п."),
                    ("Перетяжка відкосів", "м.п."),
                ],
                "Площина": [
                    ("Попереднє вирівнювання площини", "м²"),
                    ("Перетяжка сіткою", "м²"),
                    ("Молочко", "м²"),
                    ("Корекція площі", "м²"),
                ],
            },
            "🎨 Декор": {
                "Баранник": [
                    ("Ґрунтування", "м²"),
                    ("Нанесення баранника", "м²"),
                    ("Фарбування", "м²"),
                    ("Миття вікон", "шт."),
                ],
            },
            "🧱 Клінкер": {
                "": [
                    ("Розмітка площі", "м²"),
                    ("Приклеювання листів", "м²"),
                    ("Фугування", "м²"),
                    ("Обробка гідрофобом", "м²"),
                ],
            },
        })

    def test_multiple_personal_goals_are_saved_for_one_shift(self):
        message = self.message(self.module.SHIFT_GOAL_TEXT)
        self.active_shift_user(message)
        goal_sheet = self.goal_sheet()
        spreadsheet = FakeSpreadsheet([goal_sheet])
        original_get_sheet = self.module.get_sheet
        self.module.get_sheet = lambda: spreadsheet
        try:
            self.advance_personal_goal_to_quantity(message)
            self.module.handle_goal_text(message, "25")
            self.module.handle_goal_callback(self.callback(message, "g:add"))
            pending = self.module.get_user(123, "Іван Петренко")["pending_goal"]
            self.assertEqual(pending["stage"], "process")

            self.module.handle_goal_callback(self.callback(message, "g:process:3"))
            self.module.handle_goal_callback(self.callback(message, "g:subprocess:0"))
            self.module.handle_goal_text(message, "18,5")
            self.module.handle_goal_callback(self.callback(message, "g:done"))
        finally:
            self.module.get_sheet = original_get_sheet

        self.assertEqual(len(goal_sheet.appended), 2)
        first, second = [values for values, _ in goal_sheet.appended]
        self.assertEqual(first[1], "shift-test-123")
        self.assertEqual(first[16:19], ["Перетяжка сіткою", "м²", 25.0])
        self.assertEqual(second[16:19], ["Розмітка площі", "м²", 18.5])
        self.assertIsNone(self.module.get_user(123, "Іван Петренко")["pending_goal"])

    def test_team_goal_saves_one_shared_row_without_duplicates(self):
        message = self.message(self.module.SHIFT_GOAL_TEXT)
        self.active_shift_user(message)
        workers = FakeWorksheet(
            "Працівники",
            headers=["telegram_user_id", "ПІБ", "роль", "бригада", "active"],
            records=[
                {
                    "telegram_user_id": "123", "ПІБ": "Іван Петренко",
                    "роль": "Майстер", "бригада": "Резуненко", "active": "TRUE",
                },
                {
                    "telegram_user_id": "456", "ПІБ": "Роман Коваль",
                    "роль": "Майстер", "бригада": "Резуненко", "active": "TRUE",
                },
            ],
        )
        goal_sheet = self.goal_sheet()
        spreadsheet = FakeSpreadsheet([workers, goal_sheet])
        original_get_sheet = self.module.get_sheet
        self.module.get_sheet = lambda: spreadsheet
        try:
            self.module.start_shift_goal(message)
            self.module.handle_goal_callback(self.callback(message, "g:type:team"))
            self.module.handle_goal_callback(self.callback(message, "g:team:1"))
            self.module.handle_goal_callback(self.callback(message, "g:team:done"))
            self.module.handle_goal_callback(self.callback(message, "g:process:2"))
            self.module.handle_goal_callback(self.callback(message, "g:category:0"))
            self.module.handle_goal_callback(self.callback(message, "g:subprocess:1"))
            self.module.handle_goal_text(message, "40")
            self.module.handle_goal_callback(self.callback(message, "g:done"))
        finally:
            self.module.get_sheet = original_get_sheet

        self.assertEqual(len(goal_sheet.appended), 1)
        row, _ = goal_sheet.appended[0]
        self.assertEqual(row[7], "Командна")
        self.assertEqual(set(row[10].split(";")), {"123", "456"})
        self.assertEqual(row[12], "123")
        self.assertEqual(row[16:19], ["Нанесення баранника", "м²", 40.0])

    def test_my_status_shows_capture_shared_progress_without_double_counting(self):
        message = self.message(self.module.EVENTS_STATUS_TEXT)
        self.active_shift_user(message)
        fixed_now = self.module.datetime(2026, 9, 26, 12, 0, tzinfo=self.module.KYIV_TZ)
        record = dict(zip(self.module.SHIFT_GOAL_HEADERS, [
            "goal-1", "shift-test-123", "26.09.2026", "26.09.2026 08:00:00",
            "capture-2", "2 секція", "Well Place 2", "Командна", "123",
            "Іван Петренко", "123;456", "Іван Петренко; Роман Коваль", "123",
            "Іван Петренко", "🕸 Сітка", "Площина", "Перетяжка сіткою", "м²",
            100, 60, 60, "Частково", "", "",
        ]))
        spreadsheet = FakeSpreadsheet([self.goal_sheet([record])])
        original_get_sheet = self.module.get_sheet
        original_now = self.module.now_dt
        self.module.get_sheet = lambda: spreadsheet
        self.module.now_dt = lambda: fixed_now
        try:
            self.module.show_status(message)
        finally:
            self.module.get_sheet = original_get_sheet
            self.module.now_dt = original_now

        response = self.module.bot.sent[-1][1]
        self.assertIn("Захватка: 2 секція", response)
        self.assertEqual(response.count("Перетяжка сіткою"), 1)
        self.assertIn("██████░░░░ 60%", response)
        self.assertIn("Командна · У процесі", response)

    def test_close_shift_skips_goal_fact_when_none_and_always_asks_cleanup(self):
        message = self.message(self.module.END_SHIFT_TEXT)
        user = self.active_shift_user(message)
        workers = FakeWorksheet(
            "Працівники",
            headers=["telegram_user_id", "ПІБ", "роль", "бригада", "active"],
            records=[{
                "telegram_user_id": "123", "ПІБ": "Іван Петренко", "роль": "Майстер",
                "бригада": "Резуненко", "active": "TRUE",
            }],
        )
        spreadsheet = FakeSpreadsheet([workers])
        original_get_sheet = self.module.get_sheet
        original_now = self.module.now_dt
        self.module.get_sheet = lambda: spreadsheet
        self.module.now_dt = lambda: self.module.datetime(
            2026, 9, 26, 17, 0, tzinfo=self.module.KYIV_TZ
        )
        try:
            self.module.end_shift(message)
        finally:
            self.module.get_sheet = original_get_sheet
            self.module.now_dt = original_now

        pending = user["pending_shift_close"]
        self.assertEqual(pending["stage"], "cleanup")
        self.assertEqual(pending["goals"], [])
        self.assertEqual(self.module.bot.sent[-1][1], "Робоче місце прибрано?")
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            [[self.module.CLEANUP_YES_TEXT, self.module.CLEANUP_NO_TEXT]],
        )

    def test_close_shift_collects_fact_calculates_above_100_and_marks_cleanup_no(self):
        message = self.message(self.module.END_SHIFT_TEXT)
        user = self.active_shift_user(message)
        workers = FakeWorksheet(
            "Працівники",
            headers=["telegram_user_id", "ПІБ", "роль", "бригада", "active"],
            records=[{
                "telegram_user_id": "123", "ПІБ": "Іван Петренко", "роль": "Майстер",
                "бригада": "Резуненко", "active": "TRUE",
            }],
        )
        goal_record = dict(zip(self.module.SHIFT_GOAL_HEADERS, [
            "goal-1", "shift-test-123", "26.09.2026", "26.09.2026 08:00:00",
            "capture-2", "2 секція", "Well Place 2", "Особиста", "123",
            "Іван Петренко", "123", "Іван Петренко", "123", "Іван Петренко",
            "🕸 Сітка", "Площина", "Молочко", "м²", 10, "", "", "Заплановано", "", "",
        ]))
        goals = self.goal_sheet([goal_record])
        shifts = FakeWorksheet("Зміни", headers=list(self.module.SHIFT_HEADERS))
        spreadsheet = FakeSpreadsheet([workers, goals, shifts])
        fixed_now = self.module.datetime(2026, 9, 26, 17, 0, tzinfo=self.module.KYIV_TZ)
        original_get_sheet = self.module.get_sheet
        original_now = self.module.now_dt
        self.module.get_sheet = lambda: spreadsheet
        self.module.now_dt = lambda: fixed_now
        try:
            self.module.end_shift(message)
            self.assertEqual(user["pending_shift_close"]["stage"], "fact")
            self.assertIn("Факт виконання", self.module.bot.sent[-1][1])
            self.assertTrue(self.module.handle_shift_close_text(message, "12,5"))
            self.assertEqual(user["pending_shift_close"]["stage"], "cleanup")
            self.assertTrue(
                self.module.handle_shift_close_text(message, self.module.CLEANUP_NO_TEXT)
            )
        finally:
            self.module.get_sheet = original_get_sheet
            self.module.now_dt = original_now

        self.assertFalse(user["shift_started"])
        self.assertEqual(len(shifts.appended), 1)
        self.assertIn("125%", self.module.bot.sent[-1][1])
        self.assertIn("Робоче місце прибрано: Ні, не виконано", self.module.bot.sent[-1][1])
        updated = {goals.headers[column - 1]: value for _, column, value in goals.cell_updates}
        self.assertEqual(updated["факт"], 12.5)
        self.assertEqual(updated["виконання, %"], 125.0)

    def test_weather_forecast_targets_general_events_without_thread_id(self):
        original_forecast = self.module.get_vynnyky_weather_text
        self.module.get_vynnyky_weather_text = lambda: "Тестовий прогноз"
        try:
            self.module.send_weather_forecast()
        finally:
            self.module.get_vynnyky_weather_text = original_forecast

        chat_id, text, options = self.module.bot.sent[-1]
        self.assertEqual(chat_id, self.module.WEATHER_CHAT_ID)
        self.assertEqual(text, "Тестовий прогноз")
        self.assertNotIn("message_thread_id", options)

    def test_my_id_replies_in_the_same_topic(self):
        message = self.message("/my_id")

        self.module.my_id_command(message)

        chat_id, text, options = self.module.bot.sent[-1]
        self.assertEqual(chat_id, message.chat.id)
        self.assertIn(str(message.from_user.id), text)
        self.assertEqual(options["message_thread_id"], message.message_thread_id)

    def test_chat_id_replies_in_the_same_topic(self):
        message = self.message("/chat_id")

        self.module.chat_id_command(message)

        chat_id, text, options = self.module.bot.sent[-1]
        self.assertEqual(chat_id, message.chat.id)
        self.assertIn(str(message.chat.id), text)
        self.assertEqual(options["message_thread_id"], message.message_thread_id)

    def test_materials_keyboard_adds_order_only_next_to_balance(self):
        self.assertEqual(
            self.module.materials_keyboard().rows,
            [
                [self.module.MATERIALS_TEXT, self.module.MATERIAL_RETURN_TEXT],
                [self.module.INVENTORY_TEXT, self.module.DEFECT_TEXT],
                [self.module.BALANCE_TEXT, self.module.MATERIAL_ORDER_TEXT],
                [self.module.MATERIAL_OTHER_MASTER_TEXT],
            ],
        )

    def test_material_issue_selects_capture_confirms_and_saves_it(self):
        message = self.materials_topic_message(self.module.MATERIALS_TEXT)
        capture = {
            "capture_id": "capture-2",
            "name": "2 низ",
            "project": "Well Place",
        }
        material = {
            "Матеріал": "Клей фасадний",
            "Од. виміру / примітка": "міш.",
        }
        log_sheet = FakeWorksheet(
            "Операції матеріалів",
            headers=self.module.MATERIAL_LOG_HEADERS,
        )
        spreadsheet = FakeSpreadsheet([log_sheet])
        fixed_now = self.module.datetime(
            2026,
            8,
            25,
            10,
            30,
            tzinfo=self.module.KYIV_TZ,
        )

        originals = {
            "get_active_captures": self.module.get_active_captures,
            "get_material_catalog": self.module.get_material_catalog,
            "find_material_candidates": self.module.find_material_candidates,
            "interpret_material_with_ai": self.module.interpret_material_with_ai,
            "get_worker": self.module.get_worker,
            "get_sheet": self.module.get_sheet,
            "get_or_create_worksheet": self.module.get_or_create_worksheet,
            "sync_daily_material_movement": self.module.sync_daily_material_movement,
            "now_dt": self.module.now_dt,
        }
        self.module.get_active_captures = lambda: [capture]
        self.module.get_material_catalog = lambda: [material]
        self.module.find_material_candidates = lambda text: [material]
        self.module.interpret_material_with_ai = lambda text, catalog: None
        self.module.get_worker = lambda user_id: {
            "telegram_user_id": str(user_id),
            "name": "Іван Петренко",
            "role": "Майстер",
        }
        self.module.get_sheet = lambda: spreadsheet
        self.module.get_or_create_worksheet = (
            lambda current_spreadsheet, title, headers: log_sheet
        )
        self.module.sync_daily_material_movement = lambda *args, **kwargs: None
        self.module.now_dt = lambda: fixed_now

        try:
            self.module.handle_text(message)
            actor = self.module.get_user(message.from_user.id, "Іван Петренко")
            self.assertEqual(actor["pending_material"]["kind"], "issue_capture")
            self.assertEqual(
                self.module.bot.sent[-1][2]["reply_markup"].rows,
                [["2 низ"], [self.module.MATERIAL_CANCEL_TEXT]],
            )

            message.text = "2 низ"
            self.module.handle_text(message)
            self.assertEqual(actor["pending_material"]["kind"], "issue_input")
            self.assertEqual(actor["pending_material"]["capture"], capture)
            self.assertIn(
                "Який матеріал беремо та яка кількість?",
                self.module.bot.sent[-1][1],
            )

            message.text = "Клей фасадний 2"
            self.module.handle_text(message)
            self.assertEqual(actor["pending_material"]["kind"], "material")
            self.assertEqual(actor["pending_material"]["capture"], capture)
            self.assertIn("Захватка: 2 низ", self.module.bot.sent[-1][1])
            self.assertEqual(
                self.module.bot.sent[-1][2]["reply_markup"].rows,
                [[self.module.MATERIAL_CONFIRM_TEXT, self.module.MATERIAL_CANCEL_TEXT]],
            )

            message.text = self.module.MATERIAL_CONFIRM_TEXT
            self.module.handle_text(message)
        finally:
            for name, value in originals.items():
                setattr(self.module, name, value)

        self.assertIsNone(actor["pending_material"])
        self.assertEqual(len(log_sheet.appended), 1)
        appended_values, _ = log_sheet.appended[0]
        self.assertEqual(appended_values[5], "2 низ")
        self.assertEqual(self.module.MATERIAL_LOG_HEADERS[5], "Захватка")
        self.assertIn("Захватка: 2 низ", self.module.bot.sent[-1][1])
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            self.module.materials_keyboard().rows,
        )

    def test_material_issue_cancel_returns_materials_keyboard(self):
        message = self.materials_topic_message(self.module.MATERIAL_CANCEL_TEXT)
        actor = self.module.get_user(message.from_user.id, "Іван Петренко")
        actor["pending_material"] = {
            "kind": "issue_capture",
            "captures": [{"name": "2 низ"}],
        }

        self.module.handle_text(message)

        self.assertIsNone(actor["pending_material"])
        self.assertIn("скасовано", self.module.bot.sent[-1][1].lower())
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            self.module.materials_keyboard().rows,
        )

    def test_material_order_keeps_order_mode_confirms_and_saves_it(self):
        message = self.materials_topic_message(self.module.MATERIAL_ORDER_TEXT)
        capture = {
            "capture_id": "capture-5",
            "name": "5 Велика",
            "project": "Well Place",
        }
        material = {
            "Матеріал": "Клей фасадний",
            "Од. виміру / примітка": "міш.",
        }
        log_sheet = FakeWorksheet(
            "Операції матеріалів",
            headers=self.module.MATERIAL_LOG_HEADERS,
        )
        spreadsheet = FakeSpreadsheet([log_sheet])
        fixed_now = self.module.datetime(
            2026,
            8,
            25,
            11,
            45,
            tzinfo=self.module.KYIV_TZ,
        )
        movement_calls = []

        originals = {
            "get_active_captures": self.module.get_active_captures,
            "get_material_catalog": self.module.get_material_catalog,
            "find_material_candidates": self.module.find_material_candidates,
            "interpret_material_with_ai": self.module.interpret_material_with_ai,
            "get_worker": self.module.get_worker,
            "get_sheet": self.module.get_sheet,
            "get_or_create_worksheet": self.module.get_or_create_worksheet,
            "sync_daily_material_movement": self.module.sync_daily_material_movement,
            "now_dt": self.module.now_dt,
        }
        self.module.get_active_captures = lambda: [capture]
        self.module.get_material_catalog = lambda: [material]
        self.module.find_material_candidates = lambda text: [material]
        self.module.interpret_material_with_ai = lambda text, catalog: None
        self.module.get_worker = lambda user_id: {
            "telegram_user_id": str(user_id),
            "name": "Іван Петренко",
            "role": "Майстер",
        }
        self.module.get_sheet = lambda: spreadsheet
        self.module.get_or_create_worksheet = (
            lambda current_spreadsheet, title, headers: log_sheet
        )
        self.module.sync_daily_material_movement = (
            lambda *args, **kwargs: movement_calls.append((args, kwargs))
        )
        self.module.now_dt = lambda: fixed_now

        try:
            self.module.handle_text(message)
            actor = self.module.get_user(message.from_user.id, "Іван Петренко")
            self.assertEqual(actor["pending_material"]["kind"], "issue_capture")
            self.assertEqual(actor["pending_material"]["operation"], "Замовлення")
            self.assertIn("замовляємо матеріал", self.module.bot.sent[-1][1])

            message.text = "5 Велика"
            self.module.handle_text(message)
            self.assertEqual(actor["pending_material"]["kind"], "issue_input")
            self.assertEqual(actor["pending_material"]["operation"], "Замовлення")
            self.assertEqual(actor["pending_material"]["capture"], capture)
            self.assertIn(
                "Який матеріал замовляємо та яка кількість?",
                self.module.bot.sent[-1][1],
            )

            message.text = "Клей фасадний"
            self.module.handle_text(message)
            self.assertEqual(actor["pending_material"]["kind"], "issue_input")
            self.assertEqual(actor["pending_material"]["operation"], "Замовлення")
            self.assertIn("Не бачу кількості", self.module.bot.sent[-1][1])
            self.assertIn("матеріал замовляємо", self.module.bot.sent[-1][1])

            message.text = "Клей фасадний 4"
            self.module.handle_text(message)
            self.assertEqual(actor["pending_material"]["kind"], "material")
            self.assertEqual(actor["pending_material"]["operation"], "Замовлення")
            self.assertEqual(actor["pending_material"]["capture"], capture)
            self.assertIn("Замовлення: 4 міш.", self.module.bot.sent[-1][1])
            self.assertIn("Захватка: 5 Велика", self.module.bot.sent[-1][1])

            message.text = self.module.MATERIAL_CONFIRM_TEXT
            self.module.handle_text(message)
        finally:
            for name, value in originals.items():
                setattr(self.module, name, value)

        self.assertIsNone(actor["pending_material"])
        self.assertEqual(len(movement_calls), 1)
        movement_args, movement_kwargs = movement_calls[0]
        self.assertEqual(movement_args[3], "Замовлення")
        self.assertEqual(movement_args[4], 4)
        self.assertEqual(movement_kwargs, {})
        self.assertEqual(len(log_sheet.appended), 1)
        appended_values, _ = log_sheet.appended[0]
        self.assertEqual(appended_values[1], "Замовлення")
        self.assertEqual(appended_values[2], "Клей фасадний")
        self.assertEqual(appended_values[3], 4)
        self.assertEqual(appended_values[4], "міш.")
        self.assertEqual(appended_values[5], "5 Велика")
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            self.module.materials_keyboard().rows,
        )

    def test_material_order_cancel_returns_materials_keyboard_without_writes(self):
        message = self.materials_topic_message(self.module.MATERIAL_CANCEL_TEXT)
        actor = self.module.get_user(message.from_user.id, "Іван Петренко")
        actor["pending_material"] = {
            "kind": "issue_capture",
            "operation": "Замовлення",
            "captures": [{"name": "5 Велика"}],
        }

        self.module.handle_text(message)

        self.assertIsNone(actor["pending_material"])
        self.assertIn("скасовано", self.module.bot.sent[-1][1].lower())
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            self.module.materials_keyboard().rows,
        )

    def test_order_sync_updates_only_the_material_order_column(self):
        class Cell:
            def __init__(self, value=""):
                self.value = value

        class MovementWorksheet:
            def row_values(self, row):
                if row == 2:
                    return ["", "", "", "", "", "Прийнято", "Видано", "Замовлення"]
                if row == 3:
                    return ["", "", "", "", "", "Клей фасадний", "", ""]
                return []

            def get(self, cell_range):
                if cell_range == "A1:D200":
                    return [[], [], [], [], ["25.08.2026", "", "", ""]]
                if cell_range == "B5:D5":
                    return [["", "", ""]]
                raise AssertionError(f"Unexpected range: {cell_range}")

            def acell(self, cell):
                return Cell({"A5": "25.08.2026", "H5": "2"}.get(cell, ""))

        class MovementSpreadsheet:
            def __init__(self, worksheet):
                self.movement = worksheet
                self.batch_updates = []

            def worksheet(self, title):
                self.assert_title = title
                return self.movement

            def values_batch_update(self, payload):
                self.batch_updates.append(payload)

        worksheet = MovementWorksheet()
        spreadsheet = MovementSpreadsheet(worksheet)
        message = self.materials_topic_message()
        timestamp = self.module.datetime(
            2026,
            8,
            25,
            12,
            0,
            tzinfo=self.module.KYIV_TZ,
        )

        row_number = self.module.sync_daily_material_movement(
            spreadsheet,
            message,
            {"Матеріал": "Клей фасадний", "Синоніми": ""},
            "Замовлення",
            3,
            {"name": "Іван Петренко"},
            timestamp,
        )

        self.assertEqual(row_number, 5)
        self.assertEqual(spreadsheet.assert_title, self.module.DAILY_MATERIAL_SHEET)
        self.assertEqual(len(spreadsheet.batch_updates), 1)
        data = spreadsheet.batch_updates[0]["data"]
        self.assertEqual(
            data,
            [
                {
                    "range": "'Рух матеріалів'!B5:D5",
                    "values": [[
                        "Замовлення через RAHUY Bot",
                        "Іван Петренко",
                        "Іван Петренко",
                    ]],
                },
                {"range": "'Рух матеріалів'!H5", "values": [[5.0]]},
            ],
        )

    def test_exact_foam_50_alias_excludes_other_thicknesses(self):
        foam_50 = {
            "Матеріал": "Пінопласт 50 мм",
            "Група": "Утеплювачі",
            "Од. виміру / примітка": "м.кв.",
            "Синоніми": "пінопласт 50, EPS 50, пінопласт 5 см",
        }
        foam_150 = {
            "Матеріал": "Пінопласт 150 мм",
            "Група": "Утеплювачі",
            "Од. виміру / примітка": "м.кв.",
            "Синоніми": "пінопласт 150, EPS 150, пінопласт 15 см",
        }
        original_get_material_catalog = self.module.get_material_catalog
        self.module.get_material_catalog = lambda: [foam_50, foam_150]
        try:
            candidates = self.module.find_material_candidates("пінопласт 50")
        finally:
            self.module.get_material_catalog = original_get_material_catalog

        self.assertEqual(candidates, [foam_50])

    def test_invoice_foam_cubic_quantity_is_saved_as_square_metres(self):
        material = {
            "Матеріал": "Пінопласт 50 мм",
            "Група": "Утеплювачі",
            "Постачальник": "СУПТзОВ «Термобуд»",
            "Од. виміру / примітка": "м.кв.",
            "Синоніми": "пінопласт 50, EPS 50, пінопласт 5 см",
        }
        ai_output = {
            "supplier": "Термобуд",
            "invoice_number": "N-50",
            "invoice_date": "25.08.2026",
            "items": [{
                "material": "Пінопласт 50 мм",
                "raw_name": "Пінопласт 50 мм",
                "quantity": 6.18,
                "unit": "м³",
                "confidence": 0.99,
            }],
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                payload = {"output_text": json.dumps(ai_output, ensure_ascii=False)}
                return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        log_sheet = FakeWorksheet(
            "Операції матеріалів",
            headers=self.module.MATERIAL_LOG_HEADERS,
        )
        spreadsheet = FakeSpreadsheet([log_sheet])
        fixed_now = self.module.datetime(
            2026,
            8,
            25,
            13,
            20,
            tzinfo=self.module.KYIV_TZ,
        )
        movement_calls = []
        originals = {
            "OPENAI_API_KEY": self.module.OPENAI_API_KEY,
            "urlopen": self.module.urllib.request.urlopen,
            "get_worker": self.module.get_worker,
            "get_sheet": self.module.get_sheet,
            "get_or_create_worksheet": self.module.get_or_create_worksheet,
            "sync_daily_material_movement": self.module.sync_daily_material_movement,
            "now_dt": self.module.now_dt,
        }
        self.module.OPENAI_API_KEY = "test-openai-key"
        self.module.urllib.request.urlopen = lambda request, timeout: FakeResponse()
        self.module.get_worker = lambda user_id: {
            "telegram_user_id": str(user_id),
            "name": "Іван Петренко",
            "role": "Майстер",
        }
        self.module.get_sheet = lambda: spreadsheet
        self.module.get_or_create_worksheet = (
            lambda current_spreadsheet, title, headers: log_sheet
        )
        self.module.sync_daily_material_movement = (
            lambda *args, **kwargs: movement_calls.append((args, kwargs))
        )
        self.module.now_dt = lambda: fixed_now

        try:
            invoice = self.module.interpret_invoice_photo_with_ai(b"fake-image", [material])
            self.assertEqual(invoice["items"][0]["quantity"], 123.6)
            self.assertEqual(invoice["items"][0]["unit"], "м.кв.")
            self.assertEqual(invoice["items"][0]["source_quantity"], 6.18)
            self.assertEqual(invoice["items"][0]["source_unit"], "м³")
            self.assertEqual(invoice["items"][0]["thickness_mm"], 50)

            message = self.materials_topic_message(self.module.INVOICE_CONFIRM_TEXT)
            actor = self.module.get_user(message.from_user.id, "Іван Петренко")
            actor["pending_material"] = {"kind": "invoice", "invoice": invoice}
            self.module.handle_text(message)
        finally:
            self.module.OPENAI_API_KEY = originals["OPENAI_API_KEY"]
            self.module.urllib.request.urlopen = originals["urlopen"]
            for name in (
                "get_worker",
                "get_sheet",
                "get_or_create_worksheet",
                "sync_daily_material_movement",
                "now_dt",
            ):
                setattr(self.module, name, originals[name])

        self.assertIsNone(actor["pending_material"])
        self.assertEqual(len(movement_calls), 1)
        movement_args, movement_kwargs = movement_calls[0]
        self.assertEqual(movement_args[3], "Надходження")
        self.assertEqual(movement_args[4], 123.6)
        self.assertEqual(movement_kwargs, {"source_name": "Термобуд"})

        self.assertEqual(len(log_sheet.appended), 1)
        appended_values, _ = log_sheet.appended[0]
        self.assertEqual(appended_values[1], "Надходження за накладною")
        self.assertEqual(appended_values[2], "Пінопласт 50 мм")
        self.assertEqual(appended_values[3], 123.6)
        self.assertEqual(appended_values[4], "м.кв.")
        self.assertIn(
            "Перерахунок: 6.18 м³ → 123.6 м.кв.; товщина 50 мм",
            appended_values[8],
        )
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            self.module.materials_keyboard().rows,
        )

    def test_bot_topic_keyboard_contains_exactly_three_buttons(self):
        self.assertEqual(
            self.module.bot_topic_keyboard().rows,
            [["Хто на зміні", "Мій статус"], ["Прогноз погоди"]],
        )
        self.assertNotIn(
            "Хто на зміні",
            [button for row in self.module.main_keyboard().rows for button in row],
        )
        self.assertNotIn(
            "Прогноз погоди",
            [button for row in self.module.main_keyboard().rows for button in row],
        )
        self.assertIn(
            "🌦 Прогноз погоди",
            [button for row in self.module.events_keyboard().rows for button in row],
        )

    def test_bot_topic_status_returns_bot_topic_keyboard(self):
        message = self.bot_topic_message(self.module.STATUS_TEXT)

        self.module.handle_text(message)

        self.assertIn("Статус: поза зміною", self.module.bot.sent[-1][1])
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            [["Хто на зміні", "Мій статус"], ["Прогноз погоди"]],
        )

    def test_bot_topic_weather_button_returns_remaining_forecast_in_same_topic(self):
        message = self.bot_topic_message(self.module.WEATHER_FORECAST_TEXT)
        original_forecast = self.module.get_remaining_vynnyky_weather_text
        self.module.get_remaining_vynnyky_weather_text = lambda: "Тестовий прогноз"
        try:
            self.module.handle_text(message)
        finally:
            self.module.get_remaining_vynnyky_weather_text = original_forecast

        self.assertEqual(len(self.module.bot.sent), 1)
        chat_id, text, options = self.module.bot.sent[-1]
        self.assertEqual(chat_id, int(self.module.BOT_TOPIC_CHAT_ID))
        self.assertEqual(text, "Тестовий прогноз")
        self.assertEqual(options["message_thread_id"], int(self.module.BOT_TOPIC_THREAD_ID))
        self.assertEqual(
            options["reply_markup"].rows,
            [["Хто на зміні", "Мій статус"], ["Прогноз погоди"]],
        )

    def test_remaining_weather_starts_with_current_hour_and_ends_today(self):
        fixed_now = self.module.datetime(
            2026,
            8,
            25,
            14,
            35,
            tzinfo=self.module.KYIV_TZ,
        )
        payload = {
            "hourly": {
                "time": [
                    "2026-08-25T13:00",
                    "2026-08-25T14:00",
                    "2026-08-25T23:00",
                    "2026-08-26T00:00",
                ],
                "temperature_2m": [18, 19, 12, 11],
                "wind_speed_10m": [4, 5, 3, 2],
                "precipitation_probability": [10, 20, 30, 40],
            },
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        original_now_dt = self.module.now_dt
        original_urlopen = self.module.urllib.request.urlopen
        self.module.now_dt = lambda: fixed_now
        self.module.urllib.request.urlopen = lambda *args, **kwargs: FakeResponse()
        try:
            forecast = self.module.get_remaining_vynnyky_weather_text()
        finally:
            self.module.now_dt = original_now_dt
            self.module.urllib.request.urlopen = original_urlopen

        self.assertNotIn("13:00", forecast)
        self.assertIn("14:00", forecast)
        self.assertIn("23:00", forecast)
        self.assertNotIn("00:00", forecast)
        self.assertNotIn("26.08", forecast)

    def test_who_is_on_shift_combines_active_and_today_completed_read_only(self):
        message = self.bot_topic_message(self.module.WHO_ON_SHIFT_TEXT)
        fixed_now = self.module.datetime(
            2026,
            8,
            25,
            12,
            0,
            tzinfo=self.module.KYIV_TZ,
        )
        active = self.module.get_user("999", "Майба Павло")
        active.update({
            "shift_started": True,
            "shift_start_time": fixed_now.replace(hour=8, minute=15),
            "break_active": False,
            "shift_worker": {"name": "Майба Павло"},
        })
        shifts = FakeWorksheet(
            "Зміни",
            headers=self.module.SHIFT_HEADERS,
            records=[
                {
                    "дата": "25.08.2026",
                    "працівник": "Корчинський Іван",
                    "початок зміни": "25.08.2026 07:30:00",
                    "кінець зміни": "25.08.2026 11:45:00",
                    "загальна тривалість": "04:15:00",
                    "перерви": "00:20:00",
                },
                {
                    "дата": "24.08.2026",
                    "працівник": "Учорашній Працівник",
                    "початок зміни": "24.08.2026 08:00:00",
                    "кінець зміни": "24.08.2026 17:00:00",
                    "загальна тривалість": "09:00:00",
                    "перерви": "00:30:00",
                },
            ],
        )
        spreadsheet = FakeSpreadsheet([shifts])
        original_get_sheet = self.module.get_sheet
        original_now_dt = self.module.now_dt
        self.module.get_sheet = lambda: spreadsheet
        self.module.now_dt = lambda: fixed_now
        try:
            self.module.handle_text(message)
        finally:
            self.module.get_sheet = original_get_sheet
            self.module.now_dt = original_now_dt

        self.assertEqual(len(self.module.bot.sent), 1)
        response = self.module.bot.sent[-1][1]
        self.assertIn("Майба Павло — початок 08:15 (на зміні)", response)
        self.assertIn("Корчинський Іван", response)
        self.assertIn("Початок: 07:30 · Кінець: 11:45", response)
        self.assertIn("Перерви: 00:20:00 · Тривалість зміни: 04:15:00", response)
        self.assertNotIn("Учорашній Працівник", response)
        self.assertEqual(shifts.updated, [])
        self.assertEqual(shifts.appended, [])
        self.assertEqual(shifts.cell_updates, [])
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            [["Хто на зміні", "Мій статус"], ["Прогноз погоди"]],
        )

    def test_my_status_cancels_same_topic_delegate_and_shows_actor(self):
        message = self.message(self.module.STATUS_TEXT)
        actor = self.module.get_user(message.from_user.id, "Іван Петренко")
        chat_id, thread_id = self.module.message_context(message)
        actor["pending_delegate"] = {
            "kind": "shift",
            "stage": "datetime",
            "worker": {
                "telegram_user_id": "999",
                "name": "Корчинський Іван",
            },
            "action": self.module.START_SHIFT_TEXT,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }

        self.module.handle_text(message)

        self.assertIsNone(actor["pending_delegate"])
        response = self.module.bot.sent[-1][1]
        self.assertIn("Працівник: Іван Петренко", response)
        self.assertNotIn("Корчинський Іван", response)

    def test_delegate_action_offers_current_or_other_time(self):
        message = self.message(self.module.START_SHIFT_TEXT)
        actor = self.module.get_user(message.from_user.id, "Іван Петренко")
        chat_id, thread_id = self.module.message_context(message)
        actor["pending_delegate"] = {
            "kind": "shift",
            "stage": "action",
            "worker": {
                "telegram_user_id": "999",
                "name": "Корчинський Іван",
            },
            "chat_id": chat_id,
            "thread_id": thread_id,
        }

        self.assertTrue(self.module.handle_delegate_text(message, message.text))

        self.assertEqual(actor["pending_delegate"]["stage"], "time_choice")
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            [
                ["Поточний час"],
                ["Вказати інший час"],
                ["Скасувати позначення"],
            ],
        )

    def test_delegated_current_time_shift_returns_main_keyboard_after_confirmation(self):
        message = self.message(self.module.DELEGATE_NOW_TEXT)
        message.message_thread_id = 777777
        actor = self.module.get_user(message.from_user.id, "Іван Петренко")
        chat_id, thread_id = self.module.message_context(message)
        actor["pending_delegate"] = {
            "kind": "shift",
            "stage": "time_choice",
            "worker": {
                "telegram_user_id": "999",
                "name": "Корчинський Іван",
            },
            "action": self.module.START_SHIFT_TEXT,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }
        original_get_captures = self.module.get_active_captures
        self.module.get_active_captures = lambda: [
            {"name": "Well Place", "project": "Well Place 2"}
        ]
        try:
            self.assertTrue(
                self.module.handle_delegate_text(message, self.module.DELEGATE_NOW_TEXT)
            )
            self.assertEqual(actor["pending_delegate"]["stage"], "capture")
            self.assertTrue(self.module.handle_delegate_text(message, "Well Place"))
            self.assertEqual(actor["pending_delegate"]["stage"], "confirm")
            self.assertTrue(
                self.module.handle_delegate_text(
                    message,
                    self.module.DELEGATE_CONFIRM_TEXT,
                )
            )
        finally:
            self.module.get_active_captures = original_get_captures

        self.assertIsNone(actor["pending_delegate"])
        self.assertIn("Початок зміни зафіксовано", self.module.bot.sent[-1][1])
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            self.module.main_keyboard().rows,
        )

    def test_delegate_action_requires_final_confirmation(self):
        message = self.message()
        actor = self.module.get_user(message.from_user.id, "Іван Петренко")
        chat_id, thread_id = self.module.message_context(message)
        worker = {
            "telegram_user_id": "999",
            "name": "Корчинський Іван",
        }
        actor["pending_delegate"] = {
            "kind": "shift",
            "stage": "datetime",
            "worker": worker,
            "action": self.module.START_BREAK_TEXT,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }
        calls = []
        original_start_break = self.module.start_break
        self.module.start_break = lambda *args: calls.append(args)
        try:
            self.assertTrue(self.module.handle_delegate_text(message, "01.01.2026 08:00"))
            self.assertEqual(actor["pending_delegate"]["stage"], "confirm")
            self.assertEqual(calls, [])
            self.assertEqual(
                self.module.bot.sent[-1][2]["reply_markup"].rows,
                [["Підтвердити позначення"], ["Скасувати позначення"]],
            )

            self.assertTrue(
                self.module.handle_delegate_text(
                    message,
                    self.module.DELEGATE_CONFIRM_TEXT,
                )
            )
        finally:
            self.module.start_break = original_start_break

        self.assertIsNone(actor["pending_delegate"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], worker)

    def test_delegated_shift_confirms_after_capture_selection(self):
        message = self.message()
        actor = self.module.get_user(message.from_user.id, "Іван Петренко")
        chat_id, thread_id = self.module.message_context(message)
        worker = {
            "telegram_user_id": "999",
            "name": "Корчинський Іван",
        }
        actor["pending_delegate"] = {
            "kind": "shift",
            "stage": "datetime",
            "worker": worker,
            "action": self.module.START_SHIFT_TEXT,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }
        calls = []
        original_get_captures = self.module.get_active_captures
        original_start_shift = self.module.start_shift
        self.module.get_active_captures = lambda: [{"name": "Well Place"}]
        self.module.start_shift = lambda *args: calls.append(args)
        try:
            self.assertTrue(self.module.handle_delegate_text(message, "01.01.2026 08:00"))
            self.assertEqual(actor["pending_delegate"]["stage"], "capture")
            self.assertTrue(self.module.handle_delegate_text(message, "Well Place"))
            self.assertEqual(actor["pending_delegate"]["stage"], "confirm")
            self.assertEqual(calls, [])
            self.assertTrue(
                self.module.handle_delegate_text(
                    message,
                    self.module.DELEGATE_CONFIRM_TEXT,
                )
            )
        finally:
            self.module.get_active_captures = original_get_captures
            self.module.start_shift = original_start_shift

        self.assertIsNone(actor["pending_delegate"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][3], {"name": "Well Place"})

    def test_stale_delegate_cancel_is_handled_as_command(self):
        message = self.message(self.module.DELEGATE_CANCEL_TEXT)

        self.module.handle_text(message)

        response = self.module.bot.sent[-1][1]
        self.assertEqual(response, "Активного позначення в цій гілці немає.")
        self.assertNotIn("Текст звернення", response)

    def test_delegate_cancel_does_not_clear_another_topic(self):
        message = self.message(self.module.DELEGATE_CANCEL_TEXT)
        actor = self.module.get_user(message.from_user.id, "Іван Петренко")
        actor["pending_delegate"] = {
            "kind": "shift",
            "stage": "datetime",
            "worker": {"telegram_user_id": "999", "name": "Корчинський Іван"},
            "action": self.module.START_SHIFT_TEXT,
            "chat_id": str(message.chat.id),
            "thread_id": "999999",
        }

        self.module.handle_text(message)

        self.assertIsNotNone(actor["pending_delegate"])
        self.assertEqual(
            self.module.bot.sent[-1][1],
            "Активного позначення в цій гілці немає.",
        )

    def test_objects_sheet_is_created_only_when_missing_and_seeded_from_captures(self):
        captures = FakeWorksheet(
            "Захватки",
            headers=["обʼєкт"],
            records=[{"обʼєкт": "Well Place 2"}, {"обʼєкт": "Well Place 2"}],
        )
        spreadsheet = FakeSpreadsheet([captures])
        self.module.get_sheet = lambda: spreadsheet

        objects = self.module.get_active_objects()

        self.assertEqual(objects, [{"name": "Well Place 2"}])
        self.assertEqual(spreadsheet.created, [("Об’єкти", 1000, 2)])
        self.assertEqual(
            spreadsheet.sheets["Об’єкти"].updated,
            [("A1", [["об’єкт", "active"], ["Well Place 2", "TRUE"]])],
        )

    def test_question_flow_appends_to_existing_events_columns(self):
        objects = FakeWorksheet(
            "Об’єкти",
            headers=["об’єкт", "active"],
            records=[{"об’єкт": "Well Place 2", "active": "TRUE"}],
        )
        workers = FakeWorksheet(
            "Працівники",
            headers=["telegram_user_id", "ПІБ", "роль", "бригада", "active"],
            records=[],
        )
        events = FakeWorksheet(
            "Питання ",
            headers=[
                "17.серпня", "Тема ", "Об'єкт ", "Питання ", "", "День Народження ",
                "Адресат / виконавець", "Ініціатор / реєстратор",
            ],
        )
        spreadsheet = FakeSpreadsheet([objects, workers, events])
        self.module.get_sheet = lambda: spreadsheet
        message = self.message()

        self.assertTrue(self.module.handle_events_text(message, "Питання"))
        self.assertTrue(self.module.handle_events_text(message, "Well Place 2"))
        self.assertTrue(self.module.handle_events_text(message, self.module.EVENT_SELF_TEXT))
        self.assertTrue(self.module.handle_events_text(message, "Коли буде готовий фасад?"))

        row, options = events.appended[0]
        self.assertRegex(row[0], r"\d{2}\.\d{2}\.\d{4}")
        self.assertEqual(
            row[1:],
            [
                "Питання", "Well Place 2", "Коли буде готовий фасад?", "Активне", "",
                "Іван Петренко", "Іван Петренко",
            ],
        )
        self.assertEqual(options, {"value_input_option": "RAW"})
        self.assertIsNone(self.module.get_user(123, "Іван Петренко")["pending_event"])
        self.assertEqual(events.updated, [])

        self.assertTrue(self.module.handle_events_text(message, self.module.ACTIVE_QUESTIONS_TEXT))
        active_record = self.module.get_user(123, "Іван Петренко")["pending_event"]["records"][0]
        self.assertTrue(self.module.handle_events_text(message, self.module.event_record_button(active_record)))
        self.assertTrue(self.module.handle_events_text(message, self.module.RESOLVE_QUESTION_TEXT))
        self.assertEqual(events.cell_updates, [(2, 5, "Вирішено")])
        self.assertIsNone(self.module.get_user(123, "Іван Петренко")["pending_event"])

    def test_task_flow_keeps_assignee_and_initiator_separate(self):
        objects = FakeWorksheet(
            "Об’єкти",
            headers=["об’єкт", "active"],
            records=[{"об’єкт": "Кротошин", "active": "TRUE"}],
        )
        workers = FakeWorksheet(
            "Працівники",
            headers=["telegram_user_id", "ПІБ", "роль", "бригада", "active"],
            records=[{
                "telegram_user_id": "5101350452",
                "ПІБ": "Коваль Роман",
                "роль": "Майстер",
                "бригада": "Резуненко",
                "active": "TRUE",
            }],
        )
        events = FakeWorksheet(
            "Питання ",
            headers=[
                "17.серпня", "Тема ", "Об'єкт ", "Питання ", "", "День Народження ",
                "Адресат / виконавець", "Ініціатор / реєстратор",
            ],
        )
        spreadsheet = FakeSpreadsheet([objects, workers, events])
        self.module.get_sheet = lambda: spreadsheet
        message = self.message()

        self.assertTrue(self.module.handle_events_text(message, "Завдання"))
        self.assertTrue(self.module.handle_events_text(message, "Кротошин"))
        self.assertTrue(self.module.handle_events_text(message, "Коваль Роман | 5101350452"))
        self.assertTrue(self.module.handle_events_text(message, "Перевірити риштування"))

        row, _ = events.appended[0]
        self.assertEqual(row[1], "Завдання")
        self.assertEqual(row[2], "Кротошин")
        self.assertEqual(row[3], "Перевірити риштування")
        self.assertEqual(row[4], "Активне")
        self.assertEqual(row[6], "Коваль Роман")
        self.assertEqual(row[7], "Іван Петренко")

        self.assertTrue(self.module.handle_events_text(message, self.module.ACTIVE_TASKS_TEXT))
        active_record = self.module.get_user(123, "Іван Петренко")["pending_event"]["records"][0]
        self.assertTrue(self.module.handle_events_text(message, self.module.event_record_button(active_record)))
        self.assertTrue(self.module.handle_events_text(message, self.module.COMPLETE_TASK_TEXT))
        self.assertEqual(events.cell_updates, [(2, 5, "Виконано")])
        self.assertIsNone(self.module.get_user(123, "Іван Петренко")["pending_event"])

    def test_missing_events_sheet_is_not_created(self):
        spreadsheet = FakeSpreadsheet()
        self.module.get_sheet = lambda: spreadsheet

        with self.assertRaisesRegex(ValueError, "немає вкладки «Події» або «Питання»"):
            self.module.append_question_to_events(
                self.message(),
                "Well Place 2",
                "Тестове питання",
            )

        self.assertEqual(spreadsheet.created, [])

    def test_events_sheet_is_preferred_when_both_targets_exist(self):
        headers = ["тип", "об’єкт", "питання", "unused", "", "адресат", "ініціатор"]
        events = FakeWorksheet("Події", headers=headers)
        questions = FakeWorksheet("Питання ", headers=headers)
        spreadsheet = FakeSpreadsheet([questions, events])
        self.module.get_sheet = lambda: spreadsheet

        title = self.module.append_question_to_events(
            self.message(),
            "Well Place",
            "Тестове питання",
        )

        self.assertEqual(title, "Події")
        self.assertEqual(len(events.appended), 1)
        self.assertEqual(questions.appended, [])

    def test_calculations_keyboard_contains_requested_actions(self):
        self.assertEqual(
            self.module.calculations_keyboard().rows,
            [
                [self.module.CALC_ADVANCE_TEXT, self.module.CALC_FULL_PAYMENT_TEXT],
                [self.module.CALC_ADJUSTMENT_TEXT, self.module.CALC_BALANCE_TEXT],
                [self.module.CALC_HISTORY_TEXT, self.module.CALC_CLOSE_CAPTURE_TEXT],
            ],
        )

    def test_sasha_button_is_visible_only_to_configured_people(self):
        base_rows = self.module.calculations_keyboard(123).rows
        andriy_rows = self.module.calculations_keyboard(1890913278).rows
        pavlo_rows = self.module.calculations_keyboard("5665169791").rows

        self.assertNotIn([self.module.CALC_SASHA_GAVE_TEXT], base_rows)
        self.assertEqual(andriy_rows[-1], [self.module.CALC_SASHA_GAVE_TEXT])
        self.assertEqual(pavlo_rows[-1], [self.module.CALC_SASHA_GAVE_TEXT])
        self.assertEqual(
            self.module.SASHA_RECEIPT_ALLOWED_USER_IDS,
            frozenset({"1890913278", "5665169791"}),
        )

    def test_worker_lookup_supports_live_blank_telegram_id_header(self):
        workers = FakeWorksheet(
            "Працівники",
            headers=["", "ПІБ", "роль", "бригада", "active"],
            records=[{
                "": "123",
                "ПІБ": "Іван Петренко",
                "роль": "Бригадир",
                "бригада": "Резуненко",
                "active": "TRUE",
            }],
        )
        self.module.get_sheet = lambda: FakeSpreadsheet([workers])

        worker = self.module.get_worker(123)

        self.assertEqual(worker["telegram_user_id"], "123")
        self.assertEqual(worker["name"], "Іван Петренко")

    def test_calculations_command_activates_current_topic_without_env(self):
        self.module.CALCULATIONS_THREAD_ID = None
        message = self.message("/calculations")

        self.module.calculations_command(message)

        self.assertTrue(self.module.is_calculations_topic(message))
        self.assertEqual(
            self.module.bot.sent[-1][2]["reply_markup"].rows,
            self.module.calculations_keyboard().rows,
        )

    def test_sasha_receipt_flow_writes_only_column_l_after_confirmation(self):
        spreadsheet = self.calculation_sheets()
        original_get_sheet = self.module.get_sheet
        self.module.get_sheet = lambda: spreadsheet
        message = self.sasha_receipt_message()
        try:
            self.assertTrue(
                self.module.handle_calculations_text(
                    message,
                    self.module.CALC_SASHA_GAVE_TEXT,
                )
            )
            pending = self.module.get_user(
                message.from_user.id,
                "Андрій Резуненко",
            )["pending_calculation"]
            self.assertEqual(pending["stage"], "sasha_kind")
            self.assertEqual(
                self.module.bot.sent[-1][2]["reply_markup"].rows,
                [["Аванс", "Розрахунок"], [self.module.CALC_CANCEL_TEXT]],
            )

            for text in ("Аванс", "Well Place", "2 низ", "2500,50"):
                self.assertTrue(self.module.handle_calculations_text(message, text))

            self.assertEqual(pending["stage"], "confirm")
            self.assertEqual(spreadsheet.sheets["Виплати"].appended, [])
            confirmation = self.module.bot.sent[-1][1]
            self.assertIn("Операція: Саша дав", confirmation)
            self.assertIn("Тип: Аванс", confirmation)
            self.assertNotIn("Коментар", confirmation)

            self.assertTrue(
                self.module.handle_calculations_text(
                    message,
                    self.module.CALC_CONFIRM_TEXT,
                )
            )
        finally:
            self.module.get_sheet = original_get_sheet

        row, options = spreadsheet.sheets["Виплати"].appended[0]
        self.assertEqual(options, {"value_input_option": "USER_ENTERED"})
        self.assertEqual(row[1:], [
            "", 0.0, 0.0, "", "Саша дав — Аванс", "Well Place", "2 низ",
            "Не застосовується", "Андрій Резуненко", "1890913278", 2500.5,
        ])

    def test_sasha_receipt_supports_settlement_subtype(self):
        spreadsheet = self.calculation_sheets()
        original_get_sheet = self.module.get_sheet
        self.module.get_sheet = lambda: spreadsheet
        message = self.sasha_receipt_message(user_id=5665169791)
        try:
            for text in (
                self.module.CALC_SASHA_GAVE_TEXT,
                self.module.CALC_SASHA_SETTLEMENT_TEXT,
                "Well Place",
                "2 низ",
                "10000",
                self.module.CALC_CONFIRM_TEXT,
            ):
                self.assertTrue(self.module.handle_calculations_text(message, text))
        finally:
            self.module.get_sheet = original_get_sheet

        row, _ = spreadsheet.sheets["Виплати"].appended[0]
        self.assertEqual(row[5], "Саша дав — Розрахунок")
        self.assertEqual(row[9:12], ["Павло Майба", "5665169791", 10000.0])

    def test_unauthorized_user_cannot_start_or_forge_sasha_receipt(self):
        message = self.calculations_topic_message(self.module.CALC_SASHA_GAVE_TEXT)

        self.assertTrue(
            self.module.handle_calculations_text(
                message,
                self.module.CALC_SASHA_GAVE_TEXT,
            )
        )
        user = self.module.get_user(message.from_user.id, "Іван Петренко")
        self.assertIsNone(user["pending_calculation"])
        self.assertIn("доступна тільки", self.module.bot.sent[-1][1])

        spreadsheet = self.calculation_sheets()
        original_get_sheet = self.module.get_sheet
        self.module.get_sheet = lambda: spreadsheet
        try:
            with self.assertRaises(PermissionError):
                self.module.append_payment_operation(message, {
                    "worker": None,
                    "amount": 5000,
                    "comment": "",
                    "type": "Саша дав — Аванс",
                    "source": "Саша",
                    "object": {"name": "Well Place"},
                    "capture": {"name": "2 низ"},
                    "capture_status": "Не застосовується",
                })
        finally:
            self.module.get_sheet = original_get_sheet
        self.assertEqual(spreadsheet.sheets["Виплати"].appended, [])

    def test_payments_schema_requires_new_receipt_column_l(self):
        legacy_headers = list(self.module.PAYMENT_HEADERS[:-1])
        worksheet = FakeWorksheet("Виплати", headers=legacy_headers)

        with self.assertRaisesRegex(ValueError, "A:L"):
            self.module.validate_payments_sheet(worksheet)

    def test_decimal_comma_hours_are_not_numericised_as_thousands(self):
        spreadsheet = self.calculation_sheets(daily_records=[
            {"Дата": "04.09.2026", "Працівник": "Коваль Роман", "Години": "9,08"},
            {"Дата": "16.09.2026", "Працівник": "Коваль Роман", "Години": "12,82"},
        ])
        original_get_sheet = self.module.get_sheet
        self.module.get_sheet = lambda: spreadsheet
        try:
            worker = self.module.get_active_payment_workers()[1]
            summary = self.module.get_worker_financial_summary(worker)
        finally:
            self.module.get_sheet = original_get_sheet

        self.assertEqual(summary["hours"], 21.9)
        self.assertEqual(summary["accrued"], 5475.0)

    def test_formatted_decimal_comma_rates_and_payments_keep_their_scale(self):
        spreadsheet = self.calculation_sheets(
            payment_records=[{
                "Дата": "20.09.2026",
                "Працівник": "Коваль Роман",
                "Виплачено, грн": "500,00",
                "Коригування, грн": "0,00",
            }],
            daily_records=[{
                "Дата": "20.09.2026",
                "Працівник": "Коваль Роман",
                "Години": "4,00",
            }],
        )
        spreadsheet.sheets["Працівники"].records[1]["ставка, грн/год"] = "250,00"
        spreadsheet.sheets["Працівники"].records[1]["початковий баланс, грн"] = "100,00"
        original_get_sheet = self.module.get_sheet
        self.module.get_sheet = lambda: spreadsheet
        try:
            worker = self.module.get_active_payment_workers()[1]
            summary = self.module.get_worker_financial_summary(worker)
        finally:
            self.module.get_sheet = original_get_sheet

        self.assertEqual(worker["rate"], 250.0)
        self.assertEqual(worker["initial_balance"], 100.0)
        self.assertEqual(summary["paid"], 500.0)
        self.assertEqual(summary["balance"], 600.0)

    def test_sasha_receipt_amount_never_changes_worker_balance(self):
        spreadsheet = self.calculation_sheets(
            payment_records=[{
                "Дата": "20.09.2026",
                "Працівник": "Коваль Роман",
                "Виплачено, грн": 500,
                "Коригування, грн": 0,
                "Тип операції": "Аванс",
                "Отримано від Саші, грн": 0,
            }, {
                "Дата": "21.09.2026",
                "Працівник": "Коваль Роман",
                "Виплачено, грн": 0,
                "Коригування, грн": 0,
                "Тип операції": "Саша дав — Аванс",
                "Отримано від Саші, грн": 10000,
            }],
            daily_records=[
                {"Дата": "20.09.2026", "Працівник": "Коваль Роман", "Години": "4,0"},
            ],
        )
        original_get_sheet = self.module.get_sheet
        self.module.get_sheet = lambda: spreadsheet
        try:
            worker = self.module.get_active_payment_workers()[1]
            summary = self.module.get_worker_financial_summary(worker)
        finally:
            self.module.get_sheet = original_get_sheet

        self.assertEqual(summary["paid"], 500.0)
        self.assertEqual(summary["balance"], 600.0)

    def test_money_parser_still_accepts_decimal_comma_and_grouped_spaces(self):
        self.assertEqual(
            self.module.parse_calculation_amount("2 500,50 грн", "Аванс"),
            2500.5,
        )

    def test_advance_flow_filters_captures_and_appends_exact_payment_row(self):
        spreadsheet = self.calculation_sheets()
        self.module.get_sheet = lambda: spreadsheet
        message = self.calculations_topic_message()

        for text in (
            self.module.CALC_ADVANCE_TEXT,
            "Коваль Роман | 5101350452",
            "Well Place",
        ):
            self.assertTrue(self.module.handle_calculations_text(message, text))

        pending = self.module.get_user(123, "Іван Петренко")["pending_calculation"]
        self.assertEqual([item["name"] for item in pending["captures"]], ["2 низ"])

        for text in ("2 низ", "2500,50", self.module.CALC_SKIP_COMMENT_TEXT, self.module.CALC_CONFIRM_TEXT):
            self.assertTrue(self.module.handle_calculations_text(message, text))

        row, options = spreadsheet.sheets["Виплати"].appended[0]
        self.assertEqual(options, {"value_input_option": "USER_ENTERED"})
        self.assertEqual(row[1:], [
            "Коваль Роман", 2500.5, 0.0, "", "Аванс", "Well Place", "2 низ",
            "Відкрита", "Іван Петренко", "123", 0.0,
        ])
        self.assertEqual(spreadsheet.created, [])

    def test_signed_adjustment_is_written_only_to_adjustment_column(self):
        spreadsheet = self.calculation_sheets()
        self.module.get_sheet = lambda: spreadsheet
        message = self.calculations_topic_message()
        for text in (
            self.module.CALC_ADJUSTMENT_TEXT,
            "Коваль Роман | 5101350452",
            "Well Place",
            "2 низ",
            "-300",
            "Утримання за інструмент",
            self.module.CALC_CONFIRM_TEXT,
        ):
            self.assertTrue(self.module.handle_calculations_text(message, text))

        row, _ = spreadsheet.sheets["Виплати"].appended[0]
        self.assertEqual(row[2], 0.0)
        self.assertEqual(row[3], -300.0)
        self.assertEqual(row[4], "Утримання за інструмент")
        self.assertEqual(row[5], "Коригування")

    def test_full_payment_button_routes_through_calculations_topic(self):
        spreadsheet = self.calculation_sheets()
        self.module.get_sheet = lambda: spreadsheet
        message = self.calculations_topic_message(self.module.CALC_FULL_PAYMENT_TEXT)

        self.module.handle_text(message)

        pending = self.module.get_user(123, "Іван Петренко")["pending_calculation"]
        self.assertEqual(pending["operation_type"], "Повний розрахунок")
        self.assertEqual(pending["stage"], "worker")
        self.assertIn("Оберіть працівника", self.module.bot.sent[-1][1])

    def test_balance_matches_sheet_formula(self):
        spreadsheet = self.calculation_sheets(
            payment_records=[
                {
                    "Дата": "20.09.2026",
                    "Працівник": "Коваль Роман",
                    "Виплачено, грн": 500,
                    "Коригування, грн": 0,
                },
                {
                    "Дата": "21.09.2026",
                    "Працівник": "Коваль Роман",
                    "Виплачено, грн": 0,
                    "Коригування, грн": -100,
                },
            ],
            daily_records=[
                {"Дата": "20.09.2026", "Працівник": "Коваль Роман", "Години": 8},
                {"Дата": "21.09.2026", "Працівник": "Коваль Роман", "Години": 4},
            ],
        )
        self.module.get_sheet = lambda: spreadsheet
        message = self.calculations_topic_message()

        self.module.handle_calculations_text(message, self.module.CALC_BALANCE_TEXT)
        self.module.handle_calculations_text(message, "Коваль Роман | 5101350452")

        response = self.module.bot.sent[-1][1]
        self.assertIn("Нараховано: 3 000,00 грн", response)
        self.assertIn("Виплачено: 500,00 грн", response)
        self.assertIn("Коригування: -100,00 грн", response)
        self.assertIn("Поточний баланс: 2 500,00 грн", response)

    def test_history_shows_latest_payment_first(self):
        spreadsheet = self.calculation_sheets(payment_records=[
            {
                "Дата": "20.09.2026", "Працівник": "Коваль Роман",
                "Виплачено, грн": 500, "Коригування, грн": 0,
                "Тип операції": "Аванс", "Захватка": "2 низ",
            },
            {
                "Дата": "21.09.2026", "Працівник": "Коваль Роман",
                "Виплачено, грн": 0, "Коригування, грн": 150,
                "Тип операції": "Коригування", "Захватка": "2 низ",
            },
        ])
        self.module.get_sheet = lambda: spreadsheet
        message = self.calculations_topic_message()

        self.module.handle_calculations_text(message, self.module.CALC_HISTORY_TEXT)
        self.module.handle_calculations_text(message, "Коваль Роман | 5101350452")

        response = self.module.bot.sent[-1][1]
        self.assertLess(response.index("21.09.2026"), response.index("20.09.2026"))
        self.assertIn("Коригування | 150,00 грн", response)

    def test_close_capture_appends_zero_operation_and_deactivates_capture(self):
        spreadsheet = self.calculation_sheets()
        self.module.get_sheet = lambda: spreadsheet
        message = self.calculations_topic_message()
        for text in (
            self.module.CALC_CLOSE_CAPTURE_TEXT,
            "Well Place",
            "2 низ",
            self.module.CALC_CONFIRM_TEXT,
        ):
            self.assertTrue(self.module.handle_calculations_text(message, text))

        row, _ = spreadsheet.sheets["Виплати"].appended[0]
        self.assertEqual(row[1], "Іван Петренко")
        self.assertEqual(row[2:4], [0.0, 0.0])
        self.assertEqual(row[5], "Закриття захватки")
        self.assertEqual(row[8], "Закрита")
        self.assertEqual(spreadsheet.sheets["Захватки"].cell_updates, [(2, 4, False)])
        self.assertEqual(self.module.get_active_captures_for_object("Well Place"), [])


if __name__ == "__main__":
    unittest.main()
