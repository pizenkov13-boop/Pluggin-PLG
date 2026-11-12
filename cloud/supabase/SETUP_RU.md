# Supabase — настройка PLG (с нуля)

## Что храним

| Данные | Таблицы |
|--------|---------|
| Аккаунты, подписка, квота | `profiles`, `user_devices` |
| Платежи | `payment_events` |
| Сгенерированные биты (метаданные + pattern JSON) | `beats`, `beat_patterns`, `beat_events`, `beat_ratings` |
| Фидбек, аналитика | `feedback_submissions`, `analytics_events` |
| Флаги, waitlist | `feature_flags`, `waitlist_entries` |

Аудио / FLP / stems **не** кладём в Storage — только JSON-паттерны и метаданные (см. `009_data_moat.sql`).

---

## Вариант A — Supabase CLI (рекомендуется)

Нужен **Node.js 20+**. Локальный `supabase start` требует Docker; без Docker — только **облачный** проект.

```bat
npx supabase login
npx supabase link --project-ref YOUR_PROJECT_REF
scripts\push_supabase_schema.ps1
scripts\setup_supabase.bat
```

Миграции лежат в `supabase/migrations/` (синхрон с `cloud/supabase/migrations/`).

---

## Вариант B — SQL Editor (без CLI)

## 1. Создай проект

1. [supabase.com/dashboard](https://supabase.com/dashboard) → **New project**
2. Имя: `plg-dev` (или любое)
3. Регион: **EU (Frankfurt)** — для GDPR
4. Пароль БД — сохрани (для бэкапов)

## 2. Прогони SQL

1. **SQL Editor** → **New query**
2. Скопируй весь файл [`setup_all.sql`](./setup_all.sql)
3. **Run** — без ошибок внизу

Это создаёт таблицы: `profiles`, `user_devices`, `payment_events`, `feature_flags`, `feedback_submissions` и т.д.

## 3. Auth (для локальной разработки)

**Authentication → Providers → Email** — включён

**Authentication → Settings:**

- **Confirm email** — выключи для dev (иначе регистрация не пустит без письма)
- JWT expiry — 3600 сек (1 час), как в README

## 4. Ключи API

**Project Settings → API:**

| Поле в `.env` | Откуда |
|---------------|--------|
| `SUPABASE_URL` | Project URL |
| `SUPABASE_ANON_KEY` | `anon` `public` |
| `SUPABASE_SERVICE_KEY` | `service_role` `secret` (только сервер!) |
| `SUPABASE_JWT_SECRET` | JWT Settings → JWT Secret |

## 5. Автоматическая настройка `.env`

Из корня репозитория:

```bat
scripts\setup_supabase.bat
```

Скрипт спросит ключи, запишет `cloud/.env` и обновит корневой `.env`, затем проверит подключение.

Или вручную:

**Корневой `.env`** (десктоп):

```env
PLG_CLOUD_MODE=true
PLG_CLOUD_URL=http://127.0.0.1:8787
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
PLG_ENV=development
```

**`cloud/.env`** (API-сервер) — скопируй из `cloud/.env.example` и заполни `SUPABASE_*` + `GEMINI_API_KEY`.

## 6. Запуск

```bat
pip install -r cloud\requirements.txt
python cloud\run.py
```

Проверка: http://127.0.0.1:8787/health

Перезапусти `run_plg.bat` → экран входа → **Аккаунт** с полным профилем.

## 7. Проверка

```bat
python scripts\verify_supabase.py
```

## Безопасность

- `service_role` и `JWT Secret` — **никогда** в клиент / GitHub
- `anon` key — только в десктопе (корневой `.env`)
- Для продакшена: включи Confirm email. CAPTCHA сейчас отключена по умолчанию
  (`PLG_CAPTCHA_PROVIDER=none`) — см. the project issue log (not included) §0aj: Turnstile
  исключён по 152-ФЗ, Yandex SmartCaptcha технически ненадёжна для части сетей.
  От спама защищают rate-limits и блокировка перебора паролей.
