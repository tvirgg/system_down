import requests
from bs4 import BeautifulSoup
import time
import logging
import argparse
from datetime import datetime, time as dt_time
import os
from dotenv import load_dotenv

# --- ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ ФАЙЛА .env ---
load_dotenv()

# --- 1. ГЛАВНЫЕ НАСТРОЙКИ (ЗДЕСЬ МОЖНО РЕДАКТИРОВАТЬ) ---

# Ваши секретные данные, которые скрипт берет из файла .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_IDS_STR = os.getenv("TELEGRAM_CHAT_IDS", "")
TELEGRAM_CHAT_IDS = TELEGRAM_CHAT_IDS_STR.split(',') if TELEGRAM_CHAT_IDS_STR else []

# Общие настройки работы
CHECK_INTERVAL_SECONDS = 3600 # Интервал проверки (3600 = 1 час)
REQUEST_TIMEOUT = 60
DAILY_REPORT_HOUR = 11 # В 11 часов будет приходить ежедневный отчет

# Список городов для мониторинга (можно добавлять новые)
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

# Список СРОЧНЫХ критериев (бот будет искать соответствие ЛЮБОМУ из них)
URGENT_CRITERIA = [
    {"type": "deadline", "value": datetime(2025, 9, 1), "message": "Найдена дата до 1 сентября"},
    {"type": "deadline", "value": datetime(2025, 9, 20), "message": "Найдена дата до 20 сентября"},
    # Можно добавить еще критерии, например, поиск конкретного месяца:
    # {"type": "month", "value": ".08.", "message": "Найдена дата в августе"}
]


# --- 2. СИСТЕМНАЯ ЧАСТЬ (ЛУЧШЕ НЕ ТРОГАТЬ) ---

# Системные константы
APPOINTMENT_URL = 'https://appointment.bmeia.gv.at/HomeWeb/Scheduler'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'ru-RU,ru;q=0.9', 'Origin': 'https://appointment.bmeia.gv.at', 'Referer': 'https://appointment.bmeia.gv.at/HomeWeb/Scheduler'}
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s', handlers=[logging.FileHandler("checker.log", encoding='utf-8'), logging.StreamHandler()])


def send_telegram_notification(message):
    """Отправляет сообщение через Telegram API каждому получателю из списка."""
    if not TELEGRAM_CHAT_IDS or not TELEGRAM_BOT_TOKEN:
        logging.warning("Токен или Chat ID для Telegram не настроены.")
        return
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        if not chat_id: continue
        payload = {'chat_id': chat_id.strip(), 'text': message, 'parse_mode': 'Markdown'}
        try:
            response = requests.post(api_url, json=payload, timeout=10)
            if response.status_code == 200: logging.info(f"Уведомление отправлено на Chat ID: {chat_id}.")
            else: logging.error(f"Ошибка отправки на Chat ID {chat_id}: {response.status_code}, {response.text}")
        except Exception as e:
            logging.error(f"Ошибка соединения с Telegram API: {e}")


def send_urgent_alert(message, city_name, reason):
    """Отправка срочного уведомления о найденной дате."""
    full_message = f"🚨 *СРОЧНО: НАЙДЕНА ДАТА в г. {city_name.upper()}!* 🚨\n\n*Причина:* {reason}\n*Детали:* {message}"
    logging.critical(full_message)
    send_telegram_notification(full_message)


def get_available_dates_for_target(session, target):
    """Запрашивает и парсит все доступные даты для одного города."""
    logging.info(f"Проверка города: {target['name']}...")
    form_data = {'Language': 'ru', 'Office': target['office'], 'CalendarId': target['calendar_id'], 'PersonCount': '1', 'Monday': '', 'Command': ''}
    dates = []
    try:
        response = session.post(APPOINTMENT_URL, data=form_data, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        date_headers = soup.find_all('th')
        for header in date_headers:
            date_text = header.get_text(strip=True)
            if date_text:
                try:
                    date_part = date_text.split(',')[-1].strip()
                    appointment_date = datetime.strptime(date_part, "%d.%m.%Y")
                    dates.append({"city": target['name'], "date_obj": appointment_date, "date_str": date_text})
                except (ValueError, IndexError):
                    continue
    except requests.exceptions.RequestException as e:
        logging.error(f"Сетевая ошибка при проверке г. {target['name']}: {e}.")
    return dates


def run_urgent_check(session):
    """Проверяет все города на соответствие любому из срочных критериев."""
    logging.info("--- Запуск срочной проверки по всем критериям ---")
    for city in TARGET_CITIES:
        dates_found = get_available_dates_for_target(session, city)
        for date_data in dates_found:
            date_obj = date_data['date_obj']
            date_str = date_data['date_str']
            
            # Проверяем на соответствие каждому срочному критерию
            for criterion in URGENT_CRITERIA:
                match = False
                if criterion['type'] == 'deadline' and date_obj < criterion['value']:
                    match = True
                elif criterion['type'] == 'month' and criterion['value'] in date_str:
                    match = True
                
                if match:
                    send_urgent_alert(date_str, city['name'], criterion['message'])
                    return True # Нашли, можно завершать работу всего скрипта
    logging.info("Срочных дат по критериям не найдено.")
    return False


def run_daily_report(session):
    """Собирает даты со всех городов и отправляет отчет о ближайшей."""
    logging.info("--- ЗАПУСК ЕЖЕДНЕВНОГО ОТЧЕТА ---")
    all_dates = []
    for city in TARGET_CITIES:
        all_dates.extend(get_available_dates_for_target(session, city))
    
    if not all_dates:
        message = "📊 *Ежедневный отчет*\n\nНа данный момент свободных дат в Астане и Москве не найдено."
        logging.info("Отчет: свободных дат нет.")
    else:
        closest_date_data = min(all_dates, key=lambda x: x['date_obj'])
        city_name = closest_date_data['city']
        date_str = closest_date_data['date_str']
        message = f"📊 *Ежедневный отчет*\n\nБлижайшая доступная дата: *{date_str}* (г. {city_name})."
        logging.info(f"Отчет: ближайшая дата {date_str} в г. {city_name}.")
    
    send_telegram_notification(message)


def run_production_mode():
    """Главный цикл работы бота."""
    logging.info("--- ЗАПУСК БОТА В РАБОЧЕМ РЕЖИМЕ ---")
    last_report_day = -1
    with requests.Session() as session:
        try:
            logging.info("Инициализация сессии..."); session.get(APPOINTMENT_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT); logging.info("Сессия успешно инициализирована.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Не удалось инициализировать сессию: {e}."); return
        
        while True:
            now = datetime.now()
            if now.hour == DAILY_REPORT_HOUR and now.day != last_report_day:
                run_daily_report(session)
                last_report_day = now.day

            if run_urgent_check(session):
                logging.info("Срочная дата найдена! Скрипт завершает работу."); break
            
            logging.info(f"Следующая проверка через {CHECK_INTERVAL_SECONDS / 3600} час(а)."); time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Скрипт мониторинга визовых дат.")
    parser.add_argument('--force-report', action='store_true', help='ТЕСТ: принудительно запустить и отправить ежедневный отчет.')
    parser.add_argument('--run', action='store_true', help='Запустить бота в рабочем режиме (режим по умолчанию).')
    args = parser.parse_args()

    if args.force_report:
        with requests.Session() as s:
            try:
                s.get(APPOINTMENT_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                run_daily_report(s)
            except Exception as e:
                logging.error(f"Не удалось запустить тестовый отчет: {e}")
    else:
        # Любой запуск без флага --force-report будет рабочим
        run_production_mode()