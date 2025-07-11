import requests
from bs4 import BeautifulSoup
import time
import logging
import argparse
from datetime import datetime
import os # <-- ДОБАВЛЕНА БИБЛИОТЕКА ДЛЯ РАБОТЫ С ОКРУЖЕНИЕМ

# --- НАСТРОЙКИ, ЧИТАЕМЫЕ ИЗ ОКРУЖЕНИЯ ---
# Скрипт будет брать эти значения из настроек Render.
# Значения в кавычках - это ЗАПАСНОЙ ВАРИАНТ для локального запуска.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7737740147:AAGi46pwB6ampuT2PSOYKUSARo7OTOgkTck")
# На Render переменная окружения будет строкой "id1,id2". Мы ее разбиваем на список.
TELEGRAM_CHAT_IDS_STR = os.getenv("TELEGRAM_CHAT_IDS", "465422780,385947658")
TELEGRAM_CHAT_IDS = TELEGRAM_CHAT_IDS_STR.split(',')

# --- ОБЩИЕ НАСТРОЙКИ ---
CHECK_INTERVAL_SECONDS = 3600
REQUEST_TIMEOUT = 60

# --- СИСТЕМНЫЕ КОНСТАНТЫ ---
APPOINTMENT_URL = 'https://appointment.bmeia.gv.at/HomeWeb/Scheduler'
FORM_DATA = {'Language': 'ru', 'Office': 'ASTANA', 'CalendarId': '20213868', 'PersonCount': '1', 'Monday': '', 'Command': ''}
TEST_AUGUST_FILE = "test_august.html"
TEST_SEPTEMBER_FILE = "test_september.html"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'ru-RU,ru;q=0.9', 'Origin': 'https://appointment.bmeia.gv.at', 'Referer': 'https://appointment.bmeia.gv.at/HomeWeb/Scheduler'}

# --- КОНФИГУРАЦИЯ ЛОГГИРОВАНИЯ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s', handlers=[logging.FileHandler("checker.log", encoding='utf-8'), logging.StreamHandler()])

# Остальная часть кода остается без изменений...
def send_telegram_notification(message):
    """Отправляет сообщение через Telegram API каждому получателю из списка."""
    if not TELEGRAM_CHAT_IDS or TELEGRAM_BOT_TOKEN is None:
        logging.warning("Токен или список Chat ID для Telegram не настроены. Уведомление не будет отправлено.")
        return
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        if not chat_id: continue # Пропускаем пустые значения на всякий случай
        payload = {'chat_id': chat_id.strip(), 'text': message, 'parse_mode': 'Markdown'}
        try:
            response = requests.post(api_url, json=payload, timeout=10)
            if response.status_code == 200:
                logging.info(f"Уведомление в Telegram успешно отправлено на Chat ID: {chat_id}.")
            else:
                logging.error(f"Не удалось отправить уведомление на Chat ID {chat_id}. Код ответа: {response.status_code}, Ответ: {response.text}")
        except Exception as e:
            logging.error(f"Ошибка при отправке уведомления на Chat ID {chat_id}: {e}")

def send_notification(message):
    logging.critical("="*60)
    logging.critical("!!! ВНИМАНИЕ: НАЙДЕНА ПОДХОДЯЩАЯ ДАТА ЗАПИСИ !!!")
    logging.critical(f"!!! {message} !!!")
    logging.critical("="*60)
    send_telegram_notification(f"🔥 *Найдена дата!* 🔥\n\nДетали: *{message}*")

def check_for_appointments(session, search_mode, search_value, html_content=None):
    try:
        if html_content:
            html_to_parse = html_content
        else:
            response = session.post(APPOINTMENT_URL, data=FORM_DATA, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            html_to_parse = response.text
        soup = BeautifulSoup(html_to_parse, 'html.parser')
        date_headers = soup.find_all('th')
        if not date_headers: return False
        found_target_date = False
        for header in date_headers:
            date_text = header.get_text(strip=True)
            if not date_text: continue
            if search_mode == 'month':
                if search_value in date_text:
                    send_notification(f"ОБНАРУЖЕНА ДАТА В НУЖНОМ МЕСЯЦЕ: {date_text}")
                    found_target_date = True
            elif search_mode == 'deadline':
                try:
                    date_part = date_text.split(',')[-1].strip()
                    appointment_date = datetime.strptime(date_part, "%d.%m.%Y")
                    if appointment_date < search_value:
                        send_notification(f"ОБНАРУЖЕНА РАННЯЯ ДАТА: {date_text}")
                        found_target_date = True
                except (ValueError, IndexError):
                    logging.warning(f"Не удалось распознать дату в строке: '{date_text}'.")
        if not found_target_date:
            logging.info("Подходящих дат на этой неделе не найдено.")
        return found_target_date
    except requests.exceptions.RequestException as e:
        logging.error(f"Сетевая ошибка: {e}.")
        return False
    except Exception as e:
        logging.error(f"Непредвиденная ошибка в логике анализа: {e}")
        return False

def run_test_mode(file_to_test, month_to_find_for_test, test_name):
    logging.info(f"--- ЗАПУСК В РЕЖИМЕ ТЕСТИРОВАНИЯ: '{test_name}' ---")
    try:
        with open(file_to_test, 'r', encoding='utf-8') as f: test_html = f.read()
        soup = BeautifulSoup(test_html, 'html.parser')
        header = soup.find('th')
        if header and month_to_find_for_test in header.get_text():
            send_notification(f"ТЕСТОВАЯ ПРОВЕРКА: {header.get_text(strip=True)}")
            logging.info(f"Тест '{test_name}' пройден.")
        else:
            logging.warning(f"Тест '{test_name}' не пройден.")
    except FileNotFoundError:
        logging.error(f"Ошибка теста: не найден файл '{file_to_test}'.")

def run_production_mode(args):
    if args.before_sept1:
        search_mode = 'deadline'
        search_value = datetime(2025, 9, 1)
        log_message = f"любая дата до {search_value.strftime('%d.%m.%Y')}"
    elif args.before_sept20:
        search_mode = 'deadline'
        search_value = datetime(2025, 9, 20)
        log_message = f"любая дата до {search_value.strftime('%d.%m.%Y')}"
    else:
        search_mode = 'month'
        search_value = ".08."
        log_message = "любая дата в августе"
    logging.info(f"--- ЗАПУСК В РАБОЧЕМ РЕЖИМЕ (Цель: {log_message}) ---")
    with requests.Session() as session:
        try:
            logging.info("Инициализация сессии и получение cookies...")
            session.get(APPOINTMENT_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            logging.info("Сессия успешно инициализирована.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Не удалось инициализировать сессию: {e}.")
            return
        while True:
            if check_for_appointments(session, search_mode, search_value):
                logging.info("Целевая дата найдена! Скрипт завершает работу.")
                break
            logging.info(f"Следующая проверка через {CHECK_INTERVAL_SECONDS / 3600} час(а).")
            time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Скрипт для мониторинга сайта записи на визу.", formatter_class=argparse.RawTextHelpFormatter)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--find-august', action='store_true', help='Искать любую дату в августе (режим по умолчанию).')
    mode_group.add_argument('--before-sept1', action='store_true', help='Искать любую дату СТРОГО ДО 1 сентября 2025.')
    mode_group.add_argument('--before-sept20', action='store_true', help='Искать любую дату СТРОГО ДО 20 сентября 2025.')
    parser.add_argument('--test-august', action='store_true', help='ТЕСТ: проверить уведомление для августа.')
    parser.add_argument('--test-september', action='store_true', help='ТЕСТ: проверить уведомление для сентября.')
    args = parser.parse_args()
    if args.test_august:
        run_test_mode(TEST_AUGUST_FILE, ".08.", "Проверка уведомлений для августа")
    elif args.test_september:
        run_test_mode(TEST_SEPTEMBER_FILE, ".09.", "Проверка уведомлений для сентября")
    else:
        run_production_mode(args)