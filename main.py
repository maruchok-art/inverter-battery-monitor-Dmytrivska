import requests
import hashlib
import os
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

API_URL = "https://eu1-developer.deyecloud.com"

# Час життя токена (12 годин)
TOKEN_TTL = 43200

# Мінімальна напруга мережі (В), при якій вважаємо, що мережа є.
# Беремо з запасом вище за межу відключення інвертора: краще зайва тривога, ніж пропущена.
GRID_MIN_V = 180


def send_telegram_message(text, silent=False):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_notification": silent}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Помилка відправки в Telegram: {e}")


# --- РОБОТА З GITHUB GIST ---
def get_state():
    default_state = {"state": 0, "tg_state": 0, "token": "", "token_time": 0}
    try:
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        res = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers, timeout=10)
        if res.status_code == 200:
            content = res.json()["files"]["state.json"]["content"]
            data = json.loads(content)
            if not data:
                return default_state
            return data
    except Exception as e:
        logging.warning(f"Помилка читання Gist: {e}")
    return default_state


def save_state(state_dict):
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


# --- ЛОГІКА API DEYE ---
def fetch_new_token():
    if not SOLARMAN_PASSWORD:
        return None
    pwd_hash = hashlib.sha256(SOLARMAN_PASSWORD.encode('utf-8')).hexdigest()
    auth_url = f"{API_URL}/v1.0/account/token?appId={SOLARMAN_APP_ID}"
    payload = {"appSecret": SOLARMAN_APP_SECRET, "email": SOLARMAN_EMAIL, "password": pwd_hash}
    try:
        res = requests.post(auth_url, json=payload, timeout=10).json()
        if res.get("success"):
            return res.get("accessToken", "")
    except Exception as e:
        logging.error(f"Помилка генерації токена: {e}")
    return None


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
            return "AUTH_ERROR"

        data_list = res.get("deviceDataList", [])
        if not data_list:
            return None
        device_data = data_list[0]
        if str(device_data.get("deviceState", "")) == "2":
            return "OFFLINE"

        soc = None
        grid_vs = []  # напруга по фазах L1, L2, L3

        for item in device_data.get("dataList", []):
            key = str(item.get("key", "")).upper()
            if key in ["SOC", "BATTERY_SOC", "BMS_SOC"]:
                soc = float(item.get("value", 100))
            elif key in ["GRIDVOLTAGEL1", "GRIDVOLTAGEL2", "GRIDVOLTAGEL3"]:
                try:
                    grid_vs.append(float(item.get("value", 0)))
                except (TypeError, ValueError):
                    pass

        if soc is None:
            return None

        # Найменша напруга з трьох фаз. None — якщо інвертор не віддав жодної
        # (тоді мережу вважаємо невідомою, а не наявною).
        grid_v = min(grid_vs) if grid_vs else None
        return {"soc": soc, "grid_v": grid_v}
    except Exception as e:
        logging.error(f"Помилка запиту даних: {e}")
        return None


def get_inverter_data_with_retry(state, max_retries=3, delay=15):
    for attempt in range(max_retries):
        token = state.get("token", "")
        token_time = state.get("token_time", 0)

        if not token or (time.time() - token_time) > TOKEN_TTL:
            logging.info("Отримуємо новий токен...")
            token = fetch_new_token()
            if token:
                state["token"] = token
                state["token_time"] = time.time()

        if token:
            data = fetch_inverter_data(token)
            if data == "AUTH_ERROR":
                logging.info("Токен відхилено. Оновлюємо...")
                state["token"] = ""
                continue
            elif data == "OFFLINE":
                return "OFFLINE"
            elif data is not None:
                return data

        logging.warning(f"API Deye не відповів (спроба {attempt+1}/3). Чекаємо {delay} сек...")
        if attempt < max_retries - 1:
            time.sleep(delay)

    return "OFFLINE"


# --- ГОЛОВНА ЛОГІКА ---
def main():
    state = get_state()
    # Міграція: якщо tg_state ще немає в пам'яті, беремо старе значення state
    current_tg_state = state.get("tg_state", state.get("state", 0))

    inverter_data = get_inverter_data_with_retry(state)

    if inverter_data == "OFFLINE":
        soc = state.get("last_soc", "Невідомо")
        logging.info(f"Статус: OFFLINE. Поточний рівень тривоги TG: {current_tg_state}")
        new_tg_state = 4
        dash_state = 4
    else:
        soc = inverter_data["soc"]
        grid_v = inverter_data["grid_v"]
        grid_ok = grid_v is not None and grid_v >= GRID_MIN_V
        logging.info(
            f"SOC: {soc}%, Мін. напруга мережі: {grid_v}V "
            f"(мережа {'є' if grid_ok else 'немає або невідомо'}), Тривога TG: {current_tg_state}"
        )

        # --- 1. Логіка для ТЕЛЕГРАМУ ---
        # Поки мережа є, ліфт живиться від неї, тому тривог за зарядом немає.
        if grid_ok:
            new_tg_state = 0
        elif soc <= 30:
            new_tg_state = 3
        elif soc <= 50:
            new_tg_state = 2
        elif soc <= 90:
            new_tg_state = 1
        else:
            new_tg_state = 0

        # --- 2. Логіка для ДАШБОРДА (відображає реальну напругу) ---
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
               f"Поточний заряд акумулятора ліфта: <b>{soc}%</b>")
        send_telegram_message(msg)
        current_tg_state = 0
        state["tg_state"] = 0

    # 4. Якщо стан змінився — реагуємо
    if new_tg_state != current_tg_state:

        if new_tg_state == 4:
            msg = (f"⚠️ <b>Увага! Втрачено зв'язок з інвертором ліфта.</b>\n\n"
                   f"Дані про заряд не оновлюються (можливо, зник інтернет або живлення роутера). "
                   f"Будь ласка, будьте обережні з ліфтом!")
            send_telegram_message(msg)

        elif new_tg_state == 3 and current_tg_state < 3:
            msg = (f"🔴 ⛔️ <b>КРИТИЧНИЙ ЗАРЯД ({soc}%)! НЕ СІДАЙТЕ В ЛІФТ!</b> ⛔️\n\n"
                   f"Є високий ризик зупинки кабіни між поверхами.")
            send_telegram_message(msg, silent=False)

        elif new_tg_state == 2 and current_tg_state < 2:
            msg = (f"🟠 <b>Заряд акумулятора ліфта: {soc}%</b>\n\n"
                   f"Запас ходу обмежений. Просимо максимально скоротити "
                   f"використання ліфта і за можливості йти сходами.")
            send_telegram_message(msg, silent=False)

        elif new_tg_state == 1 and current_tg_state < 1:
            msg = (f"🟡 <b>Увага! Ліфт працює від акумуляторів (Заряд: {soc}%).</b>\n\n"
                   f"Будь ласка, користуйтеся ним лише за крайньої потреби. Економте заряд!")
            send_telegram_message(msg, silent=True)

        elif new_tg_state == 0:
            logging.info("Мережа є або батарея заряджена. Стан скинуто на 0 (тихо).")

        else:
            logging.info(f"Батарея заряджається. Тихий перехід стану: {current_tg_state} -> {new_tg_state}")

        state["tg_state"] = new_tg_state

    else:
        logging.info("Стан Telegram не змінився. Дій не потрібно.")

    # 5. Зберігаємо завжди — щоб не втратити оновлений токен і дати дані для Дашборда
    state["state"] = dash_state  # Дашборд читає саме цей ключ!
    if inverter_data != "OFFLINE":
        state["last_soc"] = soc
        state["last_update"] = int(time.time())
    save_state(state)


if __name__ == "__main__":
    main()
