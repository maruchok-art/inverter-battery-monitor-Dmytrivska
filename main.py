import requests
import hashlib
import os
import sys
import time
import json
import logging

# Професійне логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- ОТРИМУЄМО НАЛАШТУВАННЯ З GITHUB SECRETS ---
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
SOLARMAN_APP_ID = os.environ.get("SOLARMAN_APP_ID")
SOLARMAN_APP_SECRET = os.environ.get("SOLARMAN_APP_SECRET")
SOLARMAN_EMAIL = os.environ.get("SOLARMAN_EMAIL")
SOLARMAN_PASSWORD = os.environ.get("SOLARMAN_PASSWORD")
DEVICE_SN = os.environ.get("DEVICE_SN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GIST_ID = os.environ.get("GIST_ID")
# Окремий ПРИХОВАНИЙ Gist лише для ключа Deye. Його адреса є тільки в секретах GitHub.
TOKEN_GIST_ID = os.environ.get("TOKEN_GIST_ID")

API_URL = "https://eu1-developer.deyecloud.com"

# Як часто брати в Deye новий ключ (12 годин). Між цим ключ береться з прихованого Gist.
TOKEN_TTL = 43200

# Мінімальна напруга мережі (В), при якій вважаємо, що мережа є.
# Беремо з запасом вище за межу відключення інвертора: краще зайва тривога, ніж пропущена.
GRID_MIN_V = 180

# Скільки запусків поспіль мережа має бути в нормі, щоб скинути тривоги в Telegram.
# Захищає від повторних повідомлень, коли напруга "скаче" біля порогу.
GRID_OK_RUNS_TO_RESET = 2

# Скільки запусків поспіль не має свіжих даних (хмара Deye не відповідає АБО каже,
# що інвертор не на зв'язку), щоб повідомити "втрачено зв'язок".
# Захищає від хибних тривог через разові збої.
NO_DATA_RUNS_TO_ALERT = 2

# Рівні тривоги в Telegram (tg_state):
#   0 — тиша, 1 — 🟡 ≤90%, 2 — 🟠 ≤50%, 3 — 🔴 ≤30%, 5 — 🆘 ≤15%, 4 — немає зв'язку.
# Порядок "серйозності" рівнів заряду (4 сюди не входить — це окремий стан):
LEVEL_RANK = {0: 0, 1: 1, 2: 2, 3: 3, 5: 4}

# Проблеми, які має виправити адміністратор (пароль, токени, Telegram).
# Якщо список не порожній — запуск завершується з помилкою (червоний хрестик у GitHub
# і лист на пошту), щоб про проблему дізнався адміністратор, а не мешканці.
ADMIN_PROBLEMS = []


def send_telegram_message(text, silent=False):
    """Надсилає повідомлення. Повертає True, тільки якщо Telegram його прийняв."""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_notification": silent}
    for attempt in range(3):
        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                return True
            logging.warning(f"Telegram відповів кодом {res.status_code}: {res.text[:200]} (спроба {attempt+1}/3)")
        except Exception as e:
            logging.warning(f"Помилка відправки в Telegram (спроба {attempt+1}/3): {e}")
        time.sleep(3)
    logging.error("Не вдалося надіслати повідомлення в Telegram. Спробуємо наступного запуску.")
    ADMIN_PROBLEMS.append("Telegram не прийняв повідомлення")
    return False


# --- РОБОТА З GITHUB GIST ---
def get_state():
    """Повертає стан бота або None, якщо файл стану прочитати не вдалося.

    Важливо розрізняти "файл порожній" і "файл не прочитався": якщо вдавати, що стан
    порожній, бот "забуде", що вже повідомляв, і почне слати ті самі повідомлення щоразу.
    """
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    for attempt in range(3):
        try:
            res = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers, timeout=10)
            if res.status_code == 200:
                content = res.json()["files"]["state.json"]["content"]
                data = json.loads(content) if content and content.strip() else {}
                if not isinstance(data, dict):
                    raise ValueError("state.json має бути об'єктом {...}")
                return data or {"state": 0, "tg_state": 0}
            logging.warning(f"GitHub не віддав файл стану: код {res.status_code} (спроба {attempt+1}/3)")
        except Exception as e:
            logging.warning(f"Помилка читання файлу стану (спроба {attempt+1}/3): {e}")
        time.sleep(3)
    return None


def save_state(state_dict):
    # Ключ Deye ніколи не зберігаємо: файл стану публічно читається дашбордом.
    # (Ці рядки також прибирають старий ключ, який лишився з попередньої версії.)
    state_dict.pop("token", None)
    state_dict.pop("token_time", None)

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json"
    }
    payload = {"files": {"state.json": {"content": json.dumps(state_dict)}}}
    for attempt in range(3):
        try:
            res = requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload, timeout=10)
            res.raise_for_status()
            logging.info("Пам'ять бота успішно оновлено та збережено в Gist!")
            return
        except Exception as e:
            logging.warning(f"Помилка запису в Gist (спроба {attempt+1}/3): {e}")
        time.sleep(3)
    logging.error("КРИТИЧНО: Не вдалося зберегти стан.")
    ADMIN_PROBLEMS.append("не вдалося зберегти файл стану")


# --- ЗБЕРІГАННЯ КЛЮЧА DEYE В ПРИХОВАНОМУ GIST ---
def load_cached_token():
    """Повертає збережений ключ, якщо він ще свіжий, інакше порожній рядок."""
    if not TOKEN_GIST_ID:
        return ""
    try:
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        res = requests.get(f"https://api.github.com/gists/{TOKEN_GIST_ID}", headers=headers, timeout=10)
        if res.status_code == 200:
            data = json.loads(res.json()["files"]["token.json"]["content"] or "{}")
            token = data.get("token", "")
            age = time.time() - data.get("token_time", 0)
            if token and age < TOKEN_TTL:
                logging.info(f"Беремо збережений ключ Deye (вік {int(age // 60)} хв).")
                return token
        else:
            logging.warning(f"Не вдалося прочитати Gist з ключем (код {res.status_code}).")
    except Exception as e:
        logging.warning(f"Помилка читання Gist з ключем: {e}")
    return ""


def save_cached_token(token):
    """Зберігає новий ключ у прихований Gist (не в публічний файл стану!)."""
    if not TOKEN_GIST_ID or not token:
        return
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json"
    }
    payload = {"files": {"token.json": {"content": json.dumps({"token": token, "token_time": int(time.time())})}}}
    try:
        res = requests.patch(f"https://api.github.com/gists/{TOKEN_GIST_ID}", headers=headers, json=payload, timeout=10)
        res.raise_for_status()
        logging.info("Новий ключ Deye збережено в прихованому Gist.")
    except Exception as e:
        logging.warning(f"Не вдалося зберегти ключ у прихований Gist: {e}")


# --- ЛОГІКА API DEYE ---
def fetch_new_token():
    """Повертає (ключ, відмова).
    відмова=True — Deye свідомо не пустив (пароль, App ID/Secret тощо): це справа адміністратора.
    відмова=False і ключ None — просто не вдалося достукатись (мережа), можна спробувати ще."""
    if not SOLARMAN_PASSWORD:
        logging.error("Не задано пароль Deye (секрет SOLARMAN_PASSWORD).")
        return None, True
    pwd_hash = hashlib.sha256(SOLARMAN_PASSWORD.encode('utf-8')).hexdigest()
    auth_url = f"{API_URL}/v1.0/account/token?appId={SOLARMAN_APP_ID}"
    payload = {"appSecret": SOLARMAN_APP_SECRET, "email": SOLARMAN_EMAIL, "password": pwd_hash}
    try:
        res = requests.post(auth_url, json=payload, timeout=10).json()
        if res.get("success") and res.get("accessToken"):
            return res["accessToken"], False
        logging.error(f"Deye не пустив бота: код {res.get('code')}, повідомлення: {res.get('msg')}")
        return None, True
    except Exception as e:
        logging.error(f"Помилка генерації токена: {e}")
    return None, False


def fetch_inverter_data(token):
    if not token:
        return None
    url = f"{API_URL}/v1.0/device/latest?appId={SOLARMAN_APP_ID}"
    auth_header = token if token.lower().startswith("bearer") else f"Bearer {token}"
    headers = {"Authorization": auth_header, "Content-Type": "application/json"}
    payload = {"deviceList": [DEVICE_SN]}

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10).json()
        if not res.get("success"):
            # Записуємо, що саме сказав Deye, — без цього неможливо зрозуміти причину
            logging.warning(f"Deye відхилив запит даних: код {res.get('code')}, повідомлення: {res.get('msg')}")
            return "AUTH_ERROR"

        data_list = res.get("deviceDataList") or []
        if not data_list:
            logging.warning("Deye не повернув жодного пристрою (перевірте серійний номер DEVICE_SN).")
            return None
        device_data = data_list[0]
        if str(device_data.get("deviceState", "")) == "2":
            return "OFFLINE"

        soc = None
        grid_vs = []  # напруга по фазах L1, L2, L3

        for item in device_data.get("dataList") or []:
            key = str(item.get("key", "")).upper()
            if key in ["SOC", "BATTERY_SOC", "BMS_SOC"]:
                try:
                    # Ціле число, щоб у повідомленнях було "85%", а не "85.0%".
                    # Якщо значення немає — заряд НЕВІДОМИЙ (а не 100%).
                    soc = round(float(item.get("value")))
                except (TypeError, ValueError):
                    pass
            elif key in ["GRIDVOLTAGEL1", "GRIDVOLTAGEL2", "GRIDVOLTAGEL3"]:
                try:
                    grid_vs.append(float(item.get("value")))
                except (TypeError, ValueError):
                    pass

        if soc is None:
            logging.warning("Deye не віддав рівень заряду (SOC).")
            return None

        if not grid_vs:
            logging.warning("Deye не віддав напругу мережі — вважаємо, що мережі немає (обережний варіант).")

        # Найменша напруга з трьох фаз. None — якщо інвертор не віддав жодної
        # (тоді мережу вважаємо невідомою, а не наявною).
        grid_v = min(grid_vs) if grid_vs else None
        return {"soc": soc, "grid_v": grid_v}
    except Exception as e:
        logging.error(f"Помилка запиту даних: {e}")
        return None


def get_inverter_data_with_retry(max_retries=3, delay=15):
    """Повертає дані, або "OFFLINE" (Deye каже: інвертор не на зв'язку),
    або "API_ERROR" (не вдалося отримати дані від хмари Deye)."""
    # Спершу пробуємо збережений ключ (з прихованого Gist). Новий у Deye беремо,
    # лише якщо збереженого немає, він старший за 12 годин або Deye його відхилив.
    # За один запуск беремо НЕ БІЛЬШЕ ОДНОГО нового ключа: якщо Deye відхиляє і свіжий,
    # то справа не в ключі, і "смикати" Deye далі немає сенсу.
    # Якщо TOKEN_GIST_ID не налаштовано — просто беремо новий ключ на кожному запуску.
    token = load_cached_token()
    new_tokens = 0
    for attempt in range(max_retries):
        if not token:
            if new_tokens >= 1:
                logging.error("Deye відхиляє запит навіть зі свіжим ключем — справа не в ключі.")
                ADMIN_PROBLEMS.append("Deye відхиляє запит даних навіть зі свіжим ключем")
                return "API_ERROR"
            logging.info("Отримуємо новий токен...")
            token, rejected = fetch_new_token()
            if rejected:
                ADMIN_PROBLEMS.append("Deye не приймає вхід (пароль / App ID / App Secret)")
                return "API_ERROR"
            if token:
                new_tokens += 1
                save_cached_token(token)

        if token:
            data = fetch_inverter_data(token)
            if data == "AUTH_ERROR":
                logging.info("Deye відхилив запит із цим ключем. Пробуємо новий...")
                token = ""
                continue
            elif data == "OFFLINE":
                return "OFFLINE"
            elif data is not None:
                return data

        logging.warning(f"API Deye не відповів (спроба {attempt+1}/{max_retries}). Чекаємо {delay} сек...")
        if attempt < max_retries - 1:
            time.sleep(delay)

    return "API_ERROR"


# --- ГОЛОВНА ЛОГІКА ---
def main():
    ADMIN_PROBLEMS.clear()

    state = get_state()
    if state is None:
        # Не знаємо, що вже повідомляли, — тому мовчимо, щоб не слати повтори всім мешканцям.
        logging.error("КРИТИЧНО: не вдалося прочитати файл стану. Повідомлення не надсилаємо, "
                      "щоб не дублювати їх. Перевірте секрети GIST_ID і PERSONAL_GITHUB_TOKEN.")
        sys.exit(1)

    # Міграція: якщо tg_state ще немає в пам'яті, беремо старе значення state
    current_tg_state = state.get("tg_state", state.get("state", 0))

    inverter_data = get_inverter_data_with_retry()

    # 0. Немає свіжих даних (хмара Deye не відповіла АБО каже, що інвертор офлайн).
    #    Разовий збій — ще не привід лякати мешканців: чекаємо ще один запуск.
    #    (Старий ключ лічильника "api_fails" лишаємо, щоб не скидати збережений стан.)
    if inverter_data in ("API_ERROR", "OFFLINE"):
        fails = state.get("api_fails", 0) + 1
        state["api_fails"] = fails
        reason = "хмара Deye не відповіла" if inverter_data == "API_ERROR" else "Deye каже, що інвертор не на зв'язку"
        if fails < NO_DATA_RUNS_TO_ALERT:
            logging.warning(f"Немає свіжих даних ({reason}), {fails} раз поспіль. "
                            f"Нічого не змінюємо, чекаємо наступного запуску.")
            save_state(state)
            finish()
            return
        logging.warning(f"Немає свіжих даних ({reason}) {fails} рази поспіль. Вважаємо, що зв'язку немає.")
        inverter_data = "OFFLINE"
    else:
        state["api_fails"] = 0

    if inverter_data == "OFFLINE":
        soc = state.get("last_soc", "Невідомо")
        logging.info(f"Статус: OFFLINE. Поточний рівень тривоги TG: {current_tg_state}")
        new_tg_state = 4
        dash_state = 4
    else:
        soc = inverter_data["soc"]
        grid_v = inverter_data["grid_v"]
        grid_ok = grid_v is not None and grid_v >= GRID_MIN_V

        # Рахуємо, скільки запусків поспіль мережа в нормі
        grid_ok_runs = state.get("grid_ok_runs", 0) + 1 if grid_ok else 0
        state["grid_ok_runs"] = grid_ok_runs

        logging.info(
            f"SOC: {soc}%, Мін. напруга мережі: {grid_v}V "
            f"(мережа {'є' if grid_ok else 'немає або невідомо'}), Тривога TG: {current_tg_state}"
        )

        # --- 1. Логіка для ТЕЛЕГРАМУ ---
        # Поки мережа є, споживачі живляться від неї, тому тривог за зарядом немає.
        if grid_ok:
            if grid_ok_runs >= GRID_OK_RUNS_TO_RESET or current_tg_state in (0, 4):
                new_tg_state = 0
            else:
                # Світло щойно повернулось — чекаємо ще один запуск, щоб переконатися,
                # що воно не зникне знову (інакше буде повторне повідомлення).
                new_tg_state = current_tg_state
        else:
            if soc <= 15:
                new_tg_state = 5  # дуже критично: інвертор вимикається на 10%
            elif soc <= 30:
                new_tg_state = 3
            elif soc <= 50:
                new_tg_state = 2
            elif soc <= 90:
                new_tg_state = 1
            else:
                new_tg_state = 0

            # Під час відключення рівень тривоги лише зростає. Якщо заряд на мить
            # "підскочить" на 1% (90→91→90), повторного повідомлення не буде.
            # Скидається рівень тільки після повернення світла (див. вище).
            if current_tg_state in (1, 2, 3, 5):
                new_tg_state = max(new_tg_state, current_tg_state, key=LEVEL_RANK.get)

        # --- 2. Логіка для ДАШБОРДА (відображає реальну напругу, без затримок) ---
        if grid_ok:  # Світло є
            dash_state = 0
        else:  # Світла немає
            if soc <= 30:
                dash_state = 3
            elif soc <= 50:
                dash_state = 2
            else:
                dash_state = 1

    # 3. Якщо були в стані OFFLINE, а зараз отримали реальні дані — повідомляємо про відновлення
    if current_tg_state == 4 and new_tg_state != 4:
        msg = (f"✅ <b>Зв'язок з інвертором відновлено!</b>\n\n"
               f"Поточний заряд акумуляторів: <b>{soc}%</b>")
        if send_telegram_message(msg):
            current_tg_state = 0
            state["tg_state"] = 0
        else:
            new_tg_state = 4  # не дійшло — спробуємо ще раз наступного запуску

    # 4. Якщо стан змінився — реагуємо
    if new_tg_state != current_tg_state:
        sent = True  # якщо повідомлення не потрібне, вважаємо, що все гаразд

        if new_tg_state == 4:
            msg = ("⚠️ <b>Увага! Втрачено зв'язок з інвертором.</b>\n\n"
                   "Дані про заряд не оновлюються (можливо, зник інтернет або живлення роутера). "
                   "Актуальний заряд невідомий, тому будьте обережні з ліфтом.")
            sent = send_telegram_message(msg)

        elif new_tg_state == 5 and current_tg_state < 5:
            msg = f"🔴🆘 <b>Критичний заряд акумуляторів: (Заряд: {soc}%)</b> — інвертор вимкнеться при 10%."
            sent = send_telegram_message(msg, silent=False)

        elif new_tg_state == 3 and current_tg_state < 3:
            msg = f"🔴 <b>Низький заряд акумуляторів: (Заряд: {soc}%)</b>"
            sent = send_telegram_message(msg, silent=False)

        elif new_tg_state == 2 and current_tg_state < 2:
            msg = f"🟠 <b>Заряд акумуляторів обмежений: (Заряд: {soc}%)</b>"
            sent = send_telegram_message(msg, silent=False)

        elif new_tg_state == 1 and current_tg_state < 1:
            msg = f"🟡 <b>Будинок працює від акумуляторів: (Заряд: {soc}%)</b>"
            sent = send_telegram_message(msg, silent=True)  # перше повідомлення — тихе, без звуку

        elif new_tg_state == 0:
            logging.info("Мережа є або батарея заряджена. Стан скинуто на 0 (тихо).")

        else:
            logging.info(f"Тихий перехід стану: {current_tg_state} -> {new_tg_state}")

        # Запам'ятовуємо новий рівень, тільки якщо повідомлення справді дійшло.
        # Інакше наступний запуск спробує надіслати його знову.
        if sent:
            state["tg_state"] = new_tg_state

    else:
        logging.info("Стан Telegram не змінився. Дій не потрібно.")

    # 5. Зберігаємо завжди — щоб дати дані для Дашборда
    state["state"] = dash_state  # Дашборд читає саме цей ключ!
    if inverter_data != "OFFLINE":
        state["last_soc"] = soc
        state["last_update"] = int(time.time())
    save_state(state)
    finish()


def finish():
    """Якщо є проблеми для адміністратора — завершуємо запуск з помилкою,
    щоб GitHub показав червоний хрестик і надіслав лист на пошту."""
    if ADMIN_PROBLEMS:
        logging.error("ПОТРІБНА УВАГА АДМІНІСТРАТОРА: " + "; ".join(ADMIN_PROBLEMS))
        sys.exit(1)


if __name__ == "__main__":
    main()
