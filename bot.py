import telebot
import os
from datetime import datetime

TOKEN = os.getenv("TOKEN")
bot = telebot.TeleBot(TOKEN)

users = {}

@bot.message_handler(commands=['start'])
def start(message):
    users[message.from_user.id] = {
        "working": False,
        "start_time": None,
        "total_time": 0
    }
    bot.send_message(message.chat.id, f"{message.from_user.first_name}, готовий працювати")

@bot.message_handler(commands=['work'])
def start_work(message):
    user = users.get(message.from_user.id)

    if not user:
        start(message)
        user = users[message.from_user.id]

    if not user["working"]:
        user["working"] = True
        user["start_time"] = datetime.now()
        bot.send_message(message.chat.id, "Робота почалась")

@bot.message_handler(commands=['stop'])
def stop_work(message):
    user = users.get(message.from_user.id)

    if user and user["working"]:
        delta = datetime.now() - user["start_time"]
        user["total_time"] += delta.total_seconds()
        user["working"] = False

        hours = user["total_time"] / 3600
        bot.send_message(message.chat.id, f"Зміна завершена: {round(hours,2)} год")

@bot.message_handler(commands=['break'])
def break_time(message):
    bot.send_message(message.chat.id, "Перерва зафіксована")

bot.infinity_polling()
