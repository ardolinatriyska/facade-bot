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
    def __init__(self, text):
        self.text = text


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

    def get_me(self):
        return types.SimpleNamespace(username="test_bot", id=99)

    def message_handler(self, **kwargs):
        return lambda function: function

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))

    def infinity_polling(self, **kwargs):
        return None


def load_bot_module():
    os.environ["BOT_TOKEN"] = "test-token"

    telebot_module = types.ModuleType("telebot")
    telebot_module.TeleBot = FakeTeleBot
    telebot_types = types.ModuleType("telebot.types")
    telebot_types.KeyboardButton = FakeButton
    telebot_types.ReplyKeyboardMarkup = FakeMarkup
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

    def test_events_keyboard_contains_two_distinct_buttons(self):
        rows = self.module.events_keyboard().rows
        self.assertEqual(
            rows,
            [
                ["Питання", "Завдання"],
                ["Актуальні питання", "Актуальні завдання"],
            ],
        )

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
        self.assertNotIn(
            "Прогноз погоди",
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


if __name__ == "__main__":
    unittest.main()
