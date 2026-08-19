import importlib.util
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

    def test_events_keyboard_contains_two_distinct_buttons(self):
        rows = self.module.events_keyboard().rows
        self.assertEqual(
            rows,
            [
                ["Питання", "Завдання"],
                ["Актуальні питання", "Актуальні завдання"],
            ],
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
