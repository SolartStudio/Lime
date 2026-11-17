РАСХОДЫ — установка на сервер с ботом (Python/Flask)

1. Скопируй папку expenses/ рядом с кодом бота.
2. В приложении бота (там, где создаётся Flask app) добавь две строки:
       from expenses import bp as expenses_bp
       app.register_blueprint(expenses_bp)
3. Задай переменные окружения (ключ Anthropic у бота уже есть):
       EXPENSES_PASSWORD=придумай_пароль
       (по желанию) EXPENSES_DATA_DIR=/путь/где/хранить   — по умолчанию expenses/data
4. pip install flask anthropic   (если ещё нет) и перезапусти бота.
5. На iPhone открой в Safari https://ТВОЙ-ДОМЕН/expenses/ , введи пароль один раз,
   затем «Поделиться» -> «На экран “Домой”».

Данные: expenses/data/expenses.json. Копия каждый день: expenses/data/backups/ (хранится 60 дней).
Выгрузка в таблицу: https://ТВОЙ-ДОМЕН/expenses/api/export.csv
Если бот не на Flask (FastAPI и т.п.) — запусти отдельно:  python -m expenses  (порт 8099)
и проксируй /expenses/ на 127.0.0.1:8099.
