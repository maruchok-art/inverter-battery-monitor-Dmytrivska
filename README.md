# 🔋 Inverter Battery Monitor

> Система моніторингу резервного живлення для багатоквартирного будинку (ОСББ)

Бот автоматично стежить за зарядом акумуляторів інвертора Deye і надсилає сповіщення мешканцям у Telegram. Коли світло зникає — всі одразу знають чи можна користуватись ліфтом.

---

## ✨ Можливості

- 📡 Підключення до інвертора Deye через офіційний хмарний API
- 📲 Автоматичні сповіщення в Telegram-канал при зміні рівня заряду
- 🌐 Веб-дашборд з поточним станом акумулятора
- 📱 Встановлюється як додаток на телефон (PWA)
- ⚡ Запускається кожні 10 хвилин через cron-job.org + GitHub Actions

---

## 🚦 Рівні сповіщень

| Рівень | Заряд | Повідомлення |
|--------|-------|-------------|
| 🟡 | 50–90% | Ліфт працює від акумуляторів, просимо економити |
| 🟠 | 30–50% | Заряд обмежений, по можливості йти сходами |
| 🔴 | < 30% | Критичний заряд — не користуватись ліфтом! |
| ⚠️ | — | Втрачено зв'язок з інвертором |

---

## 📊 Веб-дашборд

👉 **[Відкрити дашборд](https://maruchok-art.github.io/inverter-battery-monitor/dashboard.html)**

Показує поточний заряд і статус в реальному часі. Оновлюється кожні 10 хвилин автоматично.
Можна встановити як додаток на телефон через Chrome → "Додати на головний екран".

---

## 🏗 Як це працює

```
Інвертор Deye
      ↓ (Deye Cloud API)
  Python скрипт (main.py)
      ↓ (кожні 10 хв через cron-job.org + GitHub Actions)
  GitHub Gist (зберігання стану і даних)
      ↓ (при зміні рівня заряду)
  Telegram канал  +  Веб-дашборд
```

---

## ⚙️ Технічний стек

| Компонент | Технологія | Вартість |
|-----------|-----------|---------|
| Виконання скрипту | GitHub Actions | Безкоштовно |
| Точний розклад запусків | cron-job.org | Безкоштовно |
| База даних стану | GitHub Gist | Безкоштовно |
| Хостинг дашборду | GitHub Pages | Безкоштовно |
| API інвертора | Deye Cloud | Безкоштовно |

**Загальна вартість: $0/місяць** 🎉

---

## 🔧 Налаштування

### Крок 1 — GitHub Secrets
Додати в Settings → Secrets and variables → Actions:

```
TG_BOT_TOKEN            — токен Telegram бота (@BotFather)
TG_CHAT_ID              — ID Telegram каналу
SOLARMAN_APP_ID         — App ID з Deye Developer Portal
SOLARMAN_APP_SECRET     — App Secret з Deye Developer Portal
SOLARMAN_EMAIL          — email акаунту Deye
SOLARMAN_PASSWORD       — пароль акаунту Deye
DEVICE_SN               — серійний номер інвертора
GIST_ID                 — ID файлу state.json на gist.github.com
PERSONAL_GITHUB_TOKEN   — особистий токен GitHub з правами gist
```

### Крок 2 — GitHub Gist
Створити новий **secret gist** на [gist.github.com](https://gist.github.com):
- Назва файлу: `state.json`
- Вміст:
```json
{"state": 0, "token": "", "token_time": 0}
```
Скопіювати ID з URL і додати в secrets як `GIST_ID`.

### Крок 3 — GitHub токени
Потрібно два окремих токени (Settings → Developer settings → Tokens (classic)):

| Токен | Права | Використання |
|-------|-------|-------------|
| `cron-job token` | `workflow` | Для cron-job.org — тригер запусків |
| `PERSONAL_GITHUB_TOKEN` | `gist` | Для скрипту — читання і запис стану |

### Крок 4 — cron-job.org
Налаштувати завдання для надійного запуску кожні 10 хвилин:
- **URL:** `https://api.github.com/repos/ТВІЙ_НІК/inverter-battery-monitor/dispatches`
- **Method:** POST
- **Headers:** `Authorization: Bearer CRON_JOB_TOKEN`, `Content-Type: application/json`
- **Body:** `{"event_type": "trigger"}`

---

## 📄 Ліцензія.

Цей проєкт є відкритим. Використовуйте, модифікуйте та покращуйте його для потреб ваших будинків! Світло обов'язково переможе темряву. 🇺🇦
