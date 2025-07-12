import requests
from bs4 import BeautifulSoup
import time
import logging
from datetime import datetime
import os
from dotenv import load_dotenv
from collections import defaultdict
import pytz  # <-- НОВАЯ БИБЛИОТЕКА ДЛЯ ЧАСОВЫХ ПОЯСОВ

# --- ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ ФАЙЛА .env ---
load_dotenv()

# --- 1. ГЛАВНЫЕ НАСТРОЙКИ ---

# Ваши секретные данные из .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_IDS_STR = os.getenv("TELEGRAM_CHAT_IDS", "")
TELEGRAM_CHAT_IDS = TELEGRAM_CHAT_IDS_STR.split(',') if TELEGRAM_CHAT_IDS_STR else []

# Настройки работы
CHECK_INTERVAL_SECONDS = 3600  # Интервал проверки (3600 = 1 час)
REQUEST_TIMEOUT = 60
DAILY_REPORT_HOUR = 8  # В 8 утра будет приходить ежедневный отчет
MOSCOW_TZ = pytz.timezone('Europe/Moscow')  # <-- ЧАСОВОЙ ПОЯС МОСКВЫ

# --- КОНКРЕТНАЯ ЦЕЛЬ ПОИСКА ---
DEADLINE_DATE = datetime(2025, 9, 1)

# Список городов для мониторинга
TARGET_CITIES = [
    {
        "name": "Астана",
        "office": "ASTANA",
        "calendar_id": "20213868"
    },
    {
        "name": "Москва",
        "office": "MOSKAU",
        "calendar_id": "40044915"
    }
]

# --- 2. СИСТЕМНАЯ ЧАСТЬ (ЛУЧШЕ НЕ ТРОГАТЬ) ---

APPOINTMENT_URL = 'https://appointment.bmeia.gv.at/HomeWeb/Scheduler'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'ru-RU,ru;q=0.9', 'Origin': 'https://appointment.bmeia.gv.at', 'Referer': 'https://appointment.bmeia.gv.at/HomeWeb/Scheduler'}
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s', handlers=[logging.FileHandler("checker.log", encoding='utf-8'), logging.StreamHandler()])

# --- Хранилища данных ---
# Для срочных уведомлений (чтобы не спамить)
REPORTED_URGENT_DATES = set()
# Для ежедневного отчета (всегда актуальный список всех дат)
ALL_AVAILABLE_DATES = set()


def send_telegram_notification(message):
    """Отправляет сообщение через Telegram API."""
    if not TELEGRAM_CHAT_IDS or not TELEGRAM_BOT_TOKEN:
        logging.warning("Токен или Chat ID для Telegram не настроены.")
        return
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        if not chat_id: continue
        payload = {'chat_id': chat_id.strip(), 'text': message, 'parse_mode': 'Markdown'}
        try:
            response = requests.post(api_url, json=payload, timeout=10)
            if response.status_code == 200: logging.info(f"Уведомление успешно отправлено на Chat ID: {chat_id}.")
            else: logging.error(f"Ошибка отправки на Chat ID {chat_id}: {response.status_code}, {response.text}")
        except Exception as e:
            logging.error(f"Ошибка соединения с Telegram API: {e}")

def update_and_check_dates(session):
    """
    Получает все даты, обновляет общий список и проверяет, нет ли новых срочных дат.
    """
    global REPORTED_URGENT_DATES, ALL_AVAILABLE_DATES
    logging.info("--- Начинаю плановую проверку дат ---")
    
    current_dates_this_cycle = set()
    
    for city in TARGET_CITIES:
        form_data = {'Language': 'ru', 'Office': city['office'], 'CalendarId': city['calendar_id'], 'PersonCount': '1', 'Monday': '', 'Command': ''}
        try:
            response = session.post(APPOINTMENT_URL, data=form_data, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            date_headers = soup.find_all('th')

            for header in date_headers:
                date_text = header.get_text(strip=True)
                if not date_text: continue
                
                try:
                    date_part = date_text.split(',')[-1].strip()
                    appointment_date = datetime.strptime(date_part, "%d.%m.%Y")
                    
                    # 1. Проверяем на срочность (до 1 сентября)
                    if appointment_date < DEADLINE_DATE and date_text not in REPORTED_URGENT_DATES:
                        logging.critical(f"!!! НАЙДЕНА НОВАЯ ЦЕЛЕВАЯ ДАТА: {date_text} в г. {city['name']} !!!")
                        message = (f"🚨 *Найдена дата до 1 сентября!* 🚨\n\n"
                                   f"📍 Город: *{city['name']}*\n"
                                   f"🗓️ Дата: `{date_text}`")
                        send_telegram_notification(message)
                        REPORTED_URGENT_DATES.add(date_text)
                    
                    # 2. Добавляем ЛЮБУЮ найденную дату в общий список для отчета
                    current_dates_this_cycle.add((city['name'], appointment_date, date_text))
                        
                except (ValueError, IndexError):
                    continue
        except requests.exceptions.RequestException as e:
            logging.error(f"Сетевая ошибка при проверке г. {city['name']}. Пропускаю обновление общих дат.")
            # В случае ошибки не сбрасываем общий список, чтобы не потерять данные
            return 
    
    # Обновляем общий список всех доступных дат
    ALL_AVAILABLE_DATES = current_dates_this_cycle
    logging.info(f"Проверка завершена. Всего найдено дат: {len(ALL_AVAILABLE_DATES)}.")


def send_daily_summary():
    """Формирует и отправляет сводку о всех доступных датах."""
    logging.info("--- Формирую ежедневный отчет... ---")

    if not ALL_AVAILABLE_DATES:
        message = f"📊 *Ежедневный отчет ({DAILY_REPORT_HOUR}:00 МСК)*\n\nНа данный момент свободных дат не найдено."
    else:
        # Группируем даты по городам
        dates_by_city = defaultdict(list)
        # Сортируем все известные даты от ближайшей к дальней
        for city_name, _, date_str in sorted(list(ALL_AVAILABLE_DATES), key=lambda x: x[1]):
            dates_by_city[city_name].append(f"  - `{date_str}`")
        
        message_parts = [f"📊 *Ежедневный отчет о доступных датах ({DAILY_REPORT_HOUR}:00 МСК)*\n"]
        for city, date_strings in dates_by_city.items():
            message_parts.append(f"*{city.upper()}:*")
            message_parts.extend(date_strings)
        
        message = "\n".join(message_parts)
    
    send_telegram_notification(message)


def run_production_mode(session):
    """Главный цикл работы бота: проверка -> пауза -> проверка."""
    last_report_day = -1
    
    try:
        # --- ПЕРВАЯ ПРОВЕРКА СРАЗУ ПРИ ЗАПУСКЕ ---
        update_and_check_dates(session)

        while True:
            now_moscow = datetime.now(MOSCOW_TZ)
            
            # --- ПРОВЕРКА УСЛОВИЯ ДЛЯ ЕЖЕДНЕВНОГО ОТЧЕТА ---
            if now_moscow.hour == DAILY_REPORT_HOUR and now_moscow.day != last_report_day:
                send_daily_summary()
                last_report_day = now_moscow.day

            # --- ПАУЗА ---
            logging.info(f"Следующая проверка через {CHECK_INTERVAL_SECONDS / 60:.0f} минут.")
            time.sleep(CHECK_INTERVAL_SECONDS)
            
            # --- ПЛАНОВАЯ ЕЖЕЧАСНАЯ ПРОВЕРКА ---
            update_and_check_dates(session)
            
    finally:
        send_telegram_notification("⏹️ *Бот остановлен*.")


if __name__ == "__main__":
    try:
        with requests.Session() as s:
            logging.info("Инициализация сессии...")
            s.get(APPOINTMENT_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            
            send_telegram_notification(f"✅ *Бот запущен.*\n- Срочные уведомления: даты до 01.09.2025\n- Ежедневный отчет: в {DAILY_REPORT_HOUR}:00 по МСК")
            run_production_mode(s)

    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную.")
    except Exception as e:
        error_message = f"❌ *Критическая ошибка!* Бот аварийно завершил работу.\n\n*Ошибка:* `{e}`"
        send_telegram_notification(error_message)