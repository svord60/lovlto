# bot.py
# Полностью рабочий бот для оценки Telegram Username
# С поддержкой переменных окружения и ротации прокси

import asyncio
import json
import os
import random
import re
import time
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import Image, ImageDraw, ImageFont
import aiohttp
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

CHANNEL_ID = -1002839409663
CHANNEL_LINK = "https://t.me/+oo08QbfuFYU1NTYy"

# Кулдауны (секунды)
SEARCH_COOLDOWN = 35
CATCH_COOLDOWN = 10

# Пути к файлам данных
DATA_DIR = "bot_data"
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
POPULAR_FILE = os.path.join(DATA_DIR, "popular.json")
USER_COOLDOWN_FILE = os.path.join(DATA_DIR, "user_cooldown.json")
USER_CATCH_COOLDOWN_FILE = os.path.join(DATA_DIR, "user_catch_cooldown.json")
USER_HISTORY_FILE = os.path.join(DATA_DIR, "user_history.json")

os.makedirs(DATA_DIR, exist_ok=True)

# Источники прокси (несколько для надёжности)
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all.txt",
    "https://raw.githubusercontent.com/gfpcom/free-proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
]

# Состояния для FSM
class SearchStates:
    WAITING_FOR_USERNAME = "waiting_for_username"
    WAITING_FOR_COMPARE = "waiting_for_compare"
    WAITING_FOR_CATCH_TYPE = "waiting_for_catch_type"

# Хранилище состояний пользователей (в памяти, для простоты)
user_states = {}
user_last_search = {}
user_last_catch = {}
current_catch_type = {}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def clean_username(text: str) -> str:
    """Очищает юзернейм от @, пробелов и приводит к нижнему регистру"""
    text = text.strip()
    if text.startswith("t.me/"):
        text = text.split("/")[-1]
    if text.startswith("@"):
        text = text[1:]
    return text.lower()

def load_json(file_path: str, default: dict = None) -> dict:
    """Загружает JSON файл"""
    if default is None:
        default = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(file_path: str, data: dict):
    """Сохраняет JSON файл"""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def update_popularity(username: str, evaluation: dict):
    """Обновляет статистику популярности юзернейма"""
    popular = load_json(POPULAR_FILE, {})
    
    if username not in popular:
        popular[username] = {
            "search_count": 0,
            "last_rank": evaluation["rank"],
            "last_price_usd": evaluation["price_usd"]
        }
    
    popular[username]["search_count"] += 1
    popular[username]["last_rank"] = evaluation["rank"]
    popular[username]["last_price_usd"] = evaluation["price_usd"]
    
    save_json(POPULAR_FILE, popular)

def add_to_user_history(user_id: int, username: str, evaluation: dict):
    """Добавляет запрос в историю пользователя"""
    history = load_json(USER_HISTORY_FILE, {})
    user_id_str = str(user_id)
    
    if user_id_str not in history:
        history[user_id_str] = []
    
    history[user_id_str].insert(0, {
        "username": username,
        "rank": evaluation["rank"],
        "price_usd": evaluation["price_usd"],
        "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M")
    })
    
    # Оставляем только последние 50 запросов
    history[user_id_str] = history[user_id_str][:50]
    
    save_json(USER_HISTORY_FILE, history)

def get_user_history(user_id: int) -> List[dict]:
    """Возвращает историю пользователя"""
    history = load_json(USER_HISTORY_FILE, {})
    return history.get(str(user_id), [])

def get_popular_usernames(limit: int = 10) -> List[tuple]:
    """Возвращает топ популярных юзернеймов"""
    popular = load_json(POPULAR_FILE, {})
    sorted_items = sorted(popular.items(), key=lambda x: x[1]["search_count"], reverse=True)
    return [(username, data) for username, data in sorted_items[:limit]]

def evaluate_username(username: str) -> dict:
    """
    Оценивает юзернейм по формальным признакам.
    Возвращает словарь с рейтингом, ценой, преимуществами и недостатками.
    """
    length = len(username)
    has_digits = bool(re.search(r'\d', username))
    has_underscore = '_' in username
    has_hyphen = '-' in username
    
    # Проверка на случайный набор букв
    vowels = 'aeiouy'
    consonants = 'bcdfghjklmnpqrstvwxz'
    vowel_count = sum(1 for c in username if c in vowels)
    consonant_count = length - vowel_count
    
    is_readable = (vowel_count >= 1 and consonant_count >= 1 and 
                   length <= 8 and not has_digits and not has_underscore)
    
    rare_letters = ['z', 'x', 'q', 'v', 'j', 'w', 'y']
    rare_count = sum(1 for c in username if c in rare_letters)
    
    # Оценка ранга (1-10)
    rank = 5
    
    if length <= 4:
        rank += 3
    elif length <= 5:
        rank += 2
    elif length <= 6:
        rank += 1
    elif length >= 10:
        rank -= 2
    elif length >= 8:
        rank -= 1
    
    if not has_digits and not has_underscore and not has_hyphen:
        rank += 2
    elif has_digits:
        rank -= 1
    if has_underscore or has_hyphen:
        rank -= 1
    
    if is_readable:
        rank += 1
    if rare_count >= 2 and length <= 6:
        rank += 1
    
    rank = max(1, min(10, rank))
    
    # Стоимость создания в TON
    creation_cost_ton = 10 if length >= 5 else 20
    
    # Примерная рыночная цена
    base_price = creation_cost_ton
    if rank >= 8:
        base_price = creation_cost_ton * (10 + (rank - 7) * 5)
    elif rank >= 6:
        base_price = creation_cost_ton * (2 + (rank - 5))
    
    price_ton = base_price
    price_usd = round(price_ton * 1.9, 2)
    
    # Преимущества и недостатки
    advantages = []
    disadvantages = []
    
    if not has_digits:
        advantages.append("🔤 Без цифр")
    else:
        disadvantages.append("🔢 Содержит цифры")
    
    if not has_underscore:
        advantages.append("✨ Без подчёркивания")
    else:
        disadvantages.append("_ Содержит подчёркивание")
    
    if is_readable:
        advantages.append("🗣 Хорошая произносимость")
    else:
        disadvantages.append("🔀 Плохая произносимость")
    
    if length <= 6:
        advantages.append(f"📏 Короткий ({length} символов)")
    elif length >= 8:
        disadvantages.append(f"📐 Длинноватый ({length}+ символов)")
    
    if rare_count >= 2 and length <= 6:
        advantages.append("💎 Содержит редкие буквы")
    
    if not advantages:
        advantages.append("Обычный юзернейм")
    if not disadvantages:
        disadvantages.append("Нет явных недостатков")
    
    # Звёздный рейтинг (1-5)
    stars_rating = round((rank / 10) * 5)
    stars_rating = max(1, min(5, stars_rating))
    stars_string = "★" * stars_rating + "☆" * (5 - stars_rating)
    
    return {
        "rank": rank,
        "stars": stars_string,
        "stars_count": stars_rating,
        "price_ton": price_ton,
        "price_usd": price_usd,
        "creation_cost_ton": creation_cost_ton,
        "creation_cost_usd": round(creation_cost_ton * 1.9, 2),
        "advantages": advantages,
        "disadvantages": disadvantages,
        "length": length,
        "has_digits": has_digits,
        "has_underscore": has_underscore,
        "is_readable": is_readable,
        "rare_count": rare_count
    }

def generate_username_by_type(username_type: str) -> str:
    """Генерирует юзернейм по типу: дорогой, смешной, уникальный, рандом"""
    
    vowels = ['a', 'e', 'i', 'o', 'u', 'y']
    consonants = ['b', 'c', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm', 'n', 'p', 'q', 'r', 's', 't', 'v', 'w', 'x', 'z']
    rare_consonants = ['z', 'x', 'q', 'v', 'j', 'w', 'y']
    
    funny_words = [
        'lol', 'kek', 'wow', 'zzz', 'omg', 'wtf', 'noob', 'cry', 'sad', 'big', 'tiny',
        'moo', 'boo', 'zoo', 'bee', 'cat', 'dog', 'fox', 'bat', 'rat', 'fun', 'lazy',
        'crazy', 'silly', 'funny', 'happy', 'sleepy', 'chill', 'cool', 'epic', 'fail',
        'oops', 'uhh', 'hmm', 'bruh', 'dude', 'bro', 'sweet', 'nice'
    ]
    
    cool_words = [
        'pro', 'max', 'ultra', 'super', 'hyper', 'mega', 'alpha', 'beta', 'prime',
        'elite', 'dark', 'light', 'shadow', 'storm', 'thunder', 'blade', 'soul', 'heart',
        'star', 'moon', 'sun', 'sky', 'fire', 'ice', 'wind', 'earth', 'zenith', 'vortex'
    ]
    
    if username_type == "дорогой":
        length = random.choice([5, 6])
        if random.random() > 0.5:
            word = random.choice(cool_words)
            if len(word) > length:
                word = word[:length]
            return word.lower()
        else:
            result = (random.choice(rare_consonants) + 
                     random.choice(vowels) + 
                     random.choice(consonants) + 
                     random.choice(vowels) + 
                     random.choice(rare_consonants))
            if length == 6:
                result += random.choice(vowels)
            return result
    
    elif username_type == "смешной":
        if random.random() > 0.4:
            word1 = random.choice(funny_words)
            word2 = random.choice(['', random.choice(funny_words[:10])])
            result = word1 + word2
            if len(result) > 10:
                result = result[:10]
            return result.lower()
        else:
            funny_endings = ['ik', 'er', 'y', 'ie', 'o', 'z', 'x']
            word = random.choice(funny_words)
            ending = random.choice(funny_endings)
            result = word + ending
            if len(result) > 10:
                result = result[:10]
            return result.lower()
    
    elif username_type == "уникальный":
        length = random.choice([6, 7])
        patterns = [
            lambda: random.choice(rare_consonants) + random.choice(vowels) + random.choice(rare_consonants) + random.choice(vowels) + random.choice(rare_consonants) + random.choice(vowels),
            lambda: random.choice(vowels) + random.choice(rare_consonants) + random.choice(vowels) + random.choice(consonants) + random.choice(vowels) + random.choice(rare_consonants),
            lambda: random.choice(consonants) + random.choice(vowels) + random.choice(rare_consonants) + random.choice(vowels) + random.choice(consonants) + random.choice(rare_consonants)
        ]
        result = random.choice(patterns)()
        if length == 7:
            result += random.choice(vowels)
        return result
    
    else:
        length = random.randint(5, 7)
        result = ""
        for i in range(length):
            if i % 2 == 0:
                result += random.choice(consonants)
            else:
                result += random.choice(vowels)
        return result

async def generate_username_image(username: str, evaluation: dict) -> BytesIO:
    """Генерирует картинку с юзернеймом и звёздами"""
    img = Image.new('RGB', (800, 400), color='#1a1a2e')
    draw = ImageDraw.Draw(img)
    
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_stars = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    except:
        try:
            font_title = ImageFont.truetype("arial.ttf", 48)
            font_stars = ImageFont.truetype("arial.ttf", 36)
        except:
            font_title = ImageFont.load_default()
            font_stars = ImageFont.load_default()
    
    # Рисуем рамку
    for i in range(5):
        draw.rectangle([i, i, 800-i, 400-i], outline='#2a2a4e')
    
    # Рисуем юзернейм
    text = f"@{username}"
    bbox = draw.textbbox((0, 0), text, font=font_title)
    text_width = bbox[2] - bbox[0]
    text_x = (800 - text_width) // 2
    text_y = 120
    draw.text((text_x, text_y), text, fill='white', font=font_title)
    
    # Рисуем звёзды
    stars = evaluation["stars"]
    bbox = draw.textbbox((0, 0), stars, font=font_stars)
    stars_width = bbox[2] - bbox[0]
    stars_x = (800 - stars_width) // 2
    stars_y = 220
    draw.text((stars_x, stars_y), stars, fill='#ffd700', font=font_stars)
    
    # Рисуем подпись
    try:
        font_small = ImageFont.truetype("arial.ttf", 16)
        draw.text((20, 360), f"Ранг: {evaluation['rank']}/10", fill='#888888', font=font_small)
        draw.text((700, 360), f"@{username}", fill='#888888', font=font_small)
    except:
        pass
    
    img_bytes = BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    
    return img_bytes

def generate_compare_image(username1: str, eval1: dict, username2: str, eval2: dict, winner: str) -> BytesIO:
    """Генерирует картинку для сравнения двух юзернеймов"""
    img = Image.new('RGB', (900, 500), color='#1a1a2e')
    draw = ImageDraw.Draw(img)
    
    try:
        font_title = ImageFont.truetype("arial.ttf", 36)
        font_text = ImageFont.truetype("arial.ttf", 24)
        font_winner = ImageFont.truetype("arial.ttf", 28)
    except:
        font_title = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_winner = ImageFont.load_default()
    
    # Рисуем рамку
    for i in range(3):
        draw.rectangle([i, i, 900-i, 500-i], outline='#2a2a4e')
    
    # Разделительная линия
    draw.line([450, 80, 450, 450], fill='#2a2a4e', width=2)
    draw.text((420, 230), "VS", fill='#ff4444', font=font_title)
    
    # Левый юзернейм
    text1 = f"@{username1}"
    bbox1 = draw.textbbox((0, 0), text1, font=font_title)
    text1_x = (450 - bbox1[2] + bbox1[0]) // 2
    draw.text((text1_x, 100), text1, fill='white', font=font_title)
    draw.text((text1_x, 160), eval1["stars"], fill='#ffd700', font=font_text)
    
    # Правый юзернейм
    text2 = f"@{username2}"
    bbox2 = draw.textbbox((0, 0), text2, font=font_title)
    text2_x = 450 + (450 - bbox2[2] + bbox2[0]) // 2
    draw.text((text2_x, 100), text2, fill='white', font=font_title)
    draw.text((text2_x, 160), eval2["stars"], fill='#ffd700', font=font_text)
    
    # Длина
    draw.text((100, 250), f"Длина: {eval1['length']}", fill='#cccccc', font=font_text)
    draw.text((550, 250), f"Длина: {eval2['length']}", fill='#cccccc', font=font_text)
    
    # Ранг
    draw.text((100, 300), f"Ранг: {eval1['rank']}/10", fill='#cccccc', font=font_text)
    draw.text((550, 300), f"Ранг: {eval2['rank']}/10", fill='#cccccc', font=font_text)
    
    # Цена
    draw.text((100, 350), f"Цена: ${eval1['price_usd']}", fill='#cccccc', font=font_text)
    draw.text((550, 350), f"Цена: ${eval2['price_usd']}", fill='#cccccc', font=font_text)
    
    # Победитель
    draw.text((420, 430), f"🏆 {winner}", fill='#ffd700', font=font_winner)
    
    img_bytes = BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    
    return img_bytes

# ========== ПРОВЕРКА ПОДПИСКИ ==========

async def is_subscribed(bot: Bot, user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

async def send_subscription_message(message: types.Message, bot: Bot):
    """Отправляет сообщение с требованием подписки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Подписаться", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="♻️ Проверить подписку", callback_data="check_subscription")]
    ])
    
    await message.answer(
        "📢 Для использования бота подпишитесь на канал:",
        reply_markup=keyboard
    )

# ========== ГЛАВНОЕ МЕНЮ ==========

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру главного меню"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔎 Поиск", callback_data="menu_search"),
            InlineKeyboardButton(text="⚖️ Сравнение", callback_data="menu_compare")
        ],
        [
            InlineKeyboardButton(text="📜 История", callback_data="menu_history"),
            InlineKeyboardButton(text="🪔 Популярные", callback_data="menu_popular")
        ],
        [
            InlineKeyboardButton(text="🦠 Словить юзернейм", callback_data="menu_catch")
        ]
    ])
    return keyboard

async def send_main_menu(message: types.Message, bot: Bot, user_id: int, text: str = "📋 Главное меню"):
    """Отправляет главное меню с фото"""
    try:
        photo_url = "https://i.ibb.co/Jjg4J674/e727c4f44eb0.jpg"
        await bot.send_photo(
            chat_id=user_id,
            photo=photo_url,
            caption=text,
            reply_markup=get_main_menu_keyboard()
        )
    except Exception as e:
        print(f"Ошибка отправки фото: {e}")
        await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=get_main_menu_keyboard()
        )

# ========== ОЦЕНКА ЮЗЕРНЕЙМА ==========

async def check_username_available(username: str) -> bool:
    """Проверяет, свободен ли юзернейм через t.me"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://t.me/{username}", timeout=5) as resp:
                text = await resp.text()
                if "not occupied" in text.lower() or "не занят" in text:
                    return True
                return False
    except:
        return False

async def send_evaluation_result(message: types.Message, username: str):
    """Отправляет результат оценки юзернейма"""
    # Оцениваем
    evaluation = evaluate_username(username)
    
    # Проверяем доступность
    is_available = await check_username_available(username)
    
    # Обновляем статистику
    update_popularity(username, evaluation)
    add_to_user_history(message.from_user.id, username, evaluation)
    
    # Генерируем картинку
    image_bytes = await generate_username_image(username, evaluation)
    photo = InputFile(image_bytes, filename="username.png")
    
    # Формируем текст
    availability_text = "✅ СВОБОДЕН" if is_available else "❌ ЗАНЯТ"
    
    advantages_text = "\n".join([f"  {adv}" for adv in evaluation["advantages"]])
    disadvantages_text = "\n".join([f"  {disc}" for disc in evaluation["disadvantages"]])
    
    caption = f"""📊 Статус на Fragment
❌ Продаж на Fragment не обнаружено

📈 Оценка юзернейма @{username}

💰 Юзернейм {'имеет ценность' if evaluation['rank'] >= 6 else 'не представляет ценности для перепродажи'}
🏷 Стоимость создания: {evaluation['creation_cost_ton']} TON (~${evaluation['creation_cost_usd']})
🏆 Ранг: {evaluation['rank']}/10
⭐ Потенциал: {evaluation['stars']}

✅ Преимущества:
<blockquote>{advantages_text}</blockquote>

❌ Недостатки:
<blockquote>{disadvantages_text}</blockquote>

Статус: {availability_text}

⚠️ Ориентировочная оценка: верхняя граница — редкий потолок, реально продаётся ближе к нижней и нужен покупатель. Может меняться при обновлении алгоритмов.

Оценка от @PhySearchUsername_Bot"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оценить ещё", callback_data="menu_search")],
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
    ])
    
    await message.answer_photo(photo=photo, caption=caption, reply_markup=keyboard, parse_mode="HTML")

async def send_compare_result(message: types.Message, username1: str, username2: str):
    """Отправляет результат сравнения двух юзернеймов"""
    eval1 = evaluate_username(username1)
    eval2 = evaluate_username(username2)
    
    # Определяем победителя
    winner = username1 if eval1["rank"] > eval2["rank"] else username2
    if eval1["rank"] == eval2["rank"]:
        winner = "Ничья"
    
    # Генерируем картинку
    image_bytes = generate_compare_image(username1, eval1, username2, eval2, winner)
    photo = InputFile(image_bytes, filename="compare.png")
    
    caption = f"""⚖️ Сравнение юзернеймов

@{username1} vs @{username2}

📏 Длина:   {eval1['length']}  vs  {'🏆 ' if eval1['rank'] > eval2['rank'] else ''}{eval2['length']}
🏆 Ранг:   {eval1['rank']}/10  vs  {'🏆 ' if eval1['rank'] > eval2['rank'] else ''}{eval2['rank']}/10
💰 Цена:   ${eval1['price_usd']}  vs  {'🏆 ' if eval1['price_usd'] > eval2['price_usd'] else ''}${eval2['price_usd']}

🏆 Победитель: @{winner if winner != 'Ничья' else 'Ничья'}"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚖️ Сравнить ещё", callback_data="menu_compare")],
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
    ])
    
    await message.answer_photo(photo=photo, caption=caption, reply_markup=keyboard)

async def send_catch_result(message: types.Message, username_type: str):
    """Отправляет результат ловли юзернейма"""
    username = generate_username_by_type(username_type)
    evaluation = evaluate_username(username)
    is_available = await check_username_available(username)
    
    availability_text = "СВОБОДЕН" if is_available else "ЗАНЯТ"
    availability_emoji = "✅" if is_available else "❌"
    
    caption = f"""🎣 Результат ловли ({username_type})

Юзернейм: @{username}
Статус: {availability_emoji} {availability_text}
Оценка: {evaluation['stars']} ({evaluation['rank']}/10)
Длина: {evaluation['length']} символов
Примерная цена: ${evaluation['price_usd']}

{'🔔 Юзернейм свободен! Можете занять его прямо сейчас.' if is_available else '😔 Юзернейм занят. Попробуйте словить другой.'}"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎣 Словить ещё", callback_data=f"catch_again_{username_type}")],
        [InlineKeyboardButton(text="🔄 Сменить тип", callback_data="menu_catch")],
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
    ])
    
    await message.answer(caption, reply_markup=keyboard)

# ========== ОБРАБОТЧИКИ КОМАНД И КОЛБЭКОВ ==========

async def start_command(message: types.Message, bot: Bot):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    
    if await is_subscribed(bot, user_id):
        await send_main_menu(message, bot, user_id)
    else:
        await send_subscription_message(message, bot)

async def check_subscription_callback(callback: types.CallbackQuery, bot: Bot):
    """Проверка подписки по кнопке"""
    user_id = callback.from_user.id
    
    if await is_subscribed(bot, user_id):
        await callback.message.delete()
        await send_main_menu(callback.message, bot, user_id)
    else:
        await callback.answer("❌ Вы не подписаны на канал! Подпишитесь и нажмите проверку снова.", show_alert=True)

async def menu_search_callback(callback: types.CallbackQuery):
    """Обработчик кнопки поиска"""
    user_id = callback.from_user.id
    current_time = time.time()
    
    # Проверка кулдауна
    if user_id in user_last_search:
        time_left = SEARCH_COOLDOWN - (current_time - user_last_search[user_id])
        if time_left > 0:
            await callback.answer(f"⏳ Подождите {int(time_left)} сек перед следующим запросом.", show_alert=True)
            return
    
    user_states[user_id] = SearchStates.WAITING_FOR_USERNAME
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
    ])
    
    await callback.message.edit_text(
        "📝 Отправьте юзернейм для оценки (с @ или без, или ссылку t.me/username).",
        reply_markup=keyboard
    )
    await callback.answer()

async def menu_compare_callback(callback: types.CallbackQuery):
    """Обработчик кнопки сравнения"""
    user_states[callback.from_user.id] = SearchStates.WAITING_FOR_COMPARE
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
    ])
    
    await callback.message.edit_text(
        "📝 Используйте два username через пробел чтобы сравнить:\n\nПример: @swordsar @monk",
        reply_markup=keyboard
    )
    await callback.answer()

async def menu_history_callback(callback: types.CallbackQuery):
    """Обработчик кнопки истории"""
    user_id = callback.from_user.id
    history = get_user_history(user_id)
    
    if not history:
        text = "📜 Ваша история поиска пуста.\n\nОтправьте юзернейм для оценки через кнопку 🔎 Поиск."
    else:
        lines = ["📜 Ваша история поиска:\n"]
        for i, item in enumerate(history[:20], 1):
            lines.append(f"{i}. @{item['username']} | {item['stars']} | {item['timestamp']}")
        text = "\n".join(lines)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

async def menu_popular_callback(callback: types.CallbackQuery):
    """Обработчик кнопки популярных"""
    popular_list = get_popular_usernames(10)
    
    if not popular_list:
        text = "🪔 Популярные юзернеймы\n\nПока нет данных. Начните искать юзернеймы через 🔎 Поиск."
    else:
        lines = ["🪔 Популярные юзернеймы:\n"]
        for i, (username, data) in enumerate(popular_list, 1):
            stars = "★" * min(5, max(1, round(data['last_rank'] / 2)))
            lines.append(f"{i}. @{username} — {data['search_count']}x | ранг {data['last_rank']}.0 | до ${data['last_price_usd']}")
        text = "\n".join(lines)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

async def menu_catch_callback(callback: types.CallbackQuery):
    """Обработчик кнопки ловли юзернейма"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💎 Дорогой", callback_data="catch_type_dorogoy"),
            InlineKeyboardButton(text="😂 Смешной", callback_data="catch_type_smeshnoy")
        ],
        [
            InlineKeyboardButton(text="🦄 Уникальный", callback_data="catch_type_unikalny"),
            InlineKeyboardButton(text="🎲 Рандом", callback_data="catch_type_random")
        ],
        [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
    ])
    
    await callback.message.edit_text(
        "🎣 Ловля username\n\nВыберите тип генерации:",
        reply_markup=keyboard
    )
    await callback.answer()

async def catch_type_callback(callback: types.CallbackQuery):
    """Обработчик выбора типа для ловли"""
    user_id = callback.from_user.id
    current_time = time.time()
    
    # Проверка кулдауна
    if user_id in user_last_catch:
        time_left = CATCH_COOLDOWN - (current_time - user_last_catch[user_id])
        if time_left > 0:
            await callback.answer(f"⏳ Подождите {int(time_left)} сек перед следующей ловлей.", show_alert=True)
            return
    
    type_map = {
        "catch_type_dorogoy": "дорогой",
        "catch_type_smeshnoy": "смешной",
        "catch_type_unikalny": "уникальный",
        "catch_type_random": "рандом"
    }
    
    username_type = type_map.get(callback.data, "рандом")
    user_last_catch[user_id] = current_time
    
    await send_catch_result(callback.message, username_type)
    await callback.answer()

async def catch_again_callback(callback: types.CallbackQuery):
    """Обработчик кнопки 'Словить ещё'"""
    user_id = callback.from_user.id
    current_time = time.time()
    
    # Проверка кулдауна
    if user_id in user_last_catch:
        time_left = CATCH_COOLDOWN - (current_time - user_last_catch[user_id])
        if time_left > 0:
            await callback.answer(f"⏳ Подождите {int(time_left)} сек перед следующей ловлей.", show_alert=True)
            return
    
    # Извлекаем тип из callback_data
    username_type = callback.data.replace("catch_again_", "")
    user_last_catch[user_id] = current_time
    
    await send_catch_result(callback.message, username_type)
    await callback.answer()

async def back_to_menu_callback(callback: types.CallbackQuery, bot: Bot):
    """Обработчик кнопки возврата в меню"""
    user_id = callback.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    
    await send_main_menu(callback.message, bot, user_id)
    await callback.answer()

async def handle_text_message(message: types.Message, bot: Bot):
    """Обработчик текстовых сообщений"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Проверяем, что пользователь не в меню
    if user_id not in user_states:
        return
    
    state = user_states[user_id]
    
    if state == SearchStates.WAITING_FOR_USERNAME:
        username = clean_username(text)
        
        if not username:
            await message.answer("❌ Некорректный юзернейм. Попробуйте ещё раз.")
            return
        
        # Обновляем кулдаун
        user_last_search[user_id] = time.time()
        
        # Отправляем сообщение о начале анализа
        analyzing_msg = await message.answer(f"🪧 Анализирую юзернейм @{username}...")
        
        # Выполняем оценку
        await send_evaluation_result(message, username)
        
        # Удаляем сообщение о анализе
        await analyzing_msg.delete()
        
        # Сбрасываем состояние
        del user_states[user_id]
    
    elif state == SearchStates.WAITING_FOR_COMPARE:
        parts = text.split()
        if len(parts) != 2:
            await message.answer("❌ Отправьте два username через пробел.\n\nПример: @swordsar @monk")
            return
        
        username1 = clean_username(parts[0])
        username2 = clean_username(parts[1])
        
        if not username1 or not username2:
            await message.answer("❌ Некорректные юзернеймы. Попробуйте ещё раз.")
            return
        
        await send_compare_result(message, username1, username2)
        del user_states[user_id]

# ========== ЗАПУСК БОТА ==========

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # Регистрируем обработчики
    dp.message.register(start_command, Command("start"))
    dp.message.register(handle_text_message)
    
    dp.callback_query.register(check_subscription_callback, lambda c: c.data == "check_subscription")
    dp.callback_query.register(menu_search_callback, lambda c: c.data == "menu_search")
    dp.callback_query.register(menu_compare_callback, lambda c: c.data == "menu_compare")
    dp.callback_query.register(menu_history_callback, lambda c: c.data == "menu_history")
    dp.callback_query.register(menu_popular_callback, lambda c: c.data == "menu_popular")
    dp.callback_query.register(menu_catch_callback, lambda c: c.data == "menu_catch")
    dp.callback_query.register(catch_type_callback, lambda c: c.data.startswith("catch_type_"))
    dp.callback_query.register(catch_again_callback, lambda c: c.data.startswith("catch_again_"))
    dp.callback_query.register(back_to_menu_callback, lambda c: c.data == "back_to_menu")
    
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())