"""
Расходы — серверная часть (Flask).

Подключение в боте:
    from expenses import bp as expenses_bp
    app.register_blueprint(expenses_bp)        # страница будет на /expenses/

Переменные окружения:
    EXPENSES_PASSWORD   пароль для входа (обязательно)
    ANTHROPIC_API_KEY   тот же ключ, что у бота (читает библиотека anthropic)
    EXPENSES_DATA_DIR   куда писать данные (по умолчанию ./data рядом с модулем)
    EXPENSES_MODEL      модель для чата (по умолчанию claude-haiku-5-5)
    EXPENSES_CHAT_PER_DAY  лимит вопросов к ИИ в день (по умолчанию 200)
"""
import os, re, io, csv, json, glob, time, shutil, hmac, hashlib, threading
import datetime as dt
from flask import (Blueprint, request, jsonify, Response, redirect, url_for,
                   send_from_directory, make_response)

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA_DIR = os.environ.get("EXPENSES_DATA_DIR", os.path.join(HERE, "data"))
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
DB = os.path.join(DATA_DIR, "expenses.json")
KEEP_BACKUPS = 60
MODEL = os.environ.get("EXPENSES_MODEL", "claude-haiku-5-5")
CHAT_LIMIT = int(os.environ.get("EXPENSES_CHAT_PER_DAY", "200"))
TZ = dt.timezone(dt.timedelta(hours=5))  # Ташкент

bp = Blueprint("expenses", __name__, url_prefix="/expenses")
lock = threading.Lock()
PUBLIC = {"expenses.login", "expenses.manifest", "expenses.icon"}


def today():
    return dt.datetime.now(TZ).date()


# ---------------------------------------------------------------- доступ
def _password():
    return os.environ.get("EXPENSES_PASSWORD", "")


def _token():
    return hmac.new(_password().encode(), b"expenses-cookie-v1", hashlib.sha256).hexdigest()


def _ip():
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "?")


_fails = {}


def _too_many_fails():
    now = time.time()
    lst = [t for t in _fails.get(_ip(), []) if now - t < 900]
    _fails[_ip()] = lst
    return len(lst) >= 10


@bp.before_request
def guard():
    if request.endpoint in PUBLIC:
        return None
    if not _password():
        return Response("EXPENSES_PASSWORD не задан на сервере", 503)
    c = request.cookies.get("exp_auth", "")
    if c and hmac.compare_digest(c, _token()):
        return None
    if request.path.startswith("/expenses/api/"):
        return jsonify(error="auth"), 401
    return redirect(url_for("expenses.login"))


LOGIN_HTML = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">
<title>Limes</title><style>
body{margin:0;background:#f2f2f7;font:17px -apple-system,system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center}
form{background:#fff;border-radius:16px;padding:22px;width:min(88vw,340px);box-shadow:0 2px 12px #0001}
h1{margin:0 0 14px;font-size:22px}input{width:100%;box-sizing:border-box;font:inherit;padding:13px;border:1px solid #e5e5ea;border-radius:12px;margin-bottom:12px}
button{width:100%;font:inherit;font-weight:600;color:#fff;background:#34c759;border:0;border-radius:12px;padding:13px}
.e{color:#ff3b30;font-size:14px;margin-bottom:10px}</style></head><body>
<form method="post"><h1>Limes</h1>%ERR%<input type="password" name="password" placeholder="Пароль" autofocus autocomplete="current-password">
<button>Войти</button></form></body></html>"""


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not _password():
        return Response("EXPENSES_PASSWORD не задан на сервере", 503)
    err = ""
    if request.method == "POST":
        if _too_many_fails():
            return Response("Слишком много попыток, подожди 15 минут", 429)
        given = (request.form.get("password") or "").encode()
        if hmac.compare_digest(given, _password().encode()):
            r = make_response(redirect(url_for("expenses.index")))
            secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
            r.set_cookie("exp_auth", _token(), max_age=365 * 24 * 3600, httponly=True,
                         secure=secure, samesite="Lax", path="/expenses")
            return r
        _fails.setdefault(_ip(), []).append(time.time())
        err = '<div class="e">Неверный пароль</div>'
    return Response(LOGIN_HTML.replace("%ERR%", err), mimetype="text/html")


# ---------------------------------------------------------------- страница
@bp.route("/")
def index():
    r = send_from_directory(STATIC, "index.html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@bp.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(STATIC, "manifest.webmanifest", mimetype="application/manifest+json")


@bp.route("/<path:name>.png")
def icon(name):
    return send_from_directory(STATIC, name + ".png")


# ---------------------------------------------------------------- данные
def _read():
    try:
        with open(DB, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        d = {}
    d.setdefault("entries", {})
    d.setdefault("config", {})
    return d


def _write(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DB + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DB)


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clean(e):
    """Оставляем только известные поля, проверяем типы."""
    if not isinstance(e, dict):
        return None
    eid = str(e.get("id", ""))[:40]
    date = str(e.get("date", ""))
    if not eid or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return None
    out = {
        "id": eid, "date": date,
        "desc": str(e.get("desc", ""))[:300],
        "amount": max(0, _int(e.get("amount"))),
        "category": str(e.get("category", "Другие расходы"))[:80],
        "manual": bool(e.get("manual")),
        "created": _int(e.get("created")),
        "updated": _int(e.get("updated")),
    }
    if e.get("deleted"):
        out["deleted"] = True
    return out


def backup_if_needed():
    if not os.path.exists(DB):
        return
    dst = os.path.join(BACKUP_DIR, f"expenses-{today().isoformat()}.json")
    if os.path.exists(dst):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copy2(DB, dst)
    for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "expenses-*.json")))[:-KEEP_BACKUPS]:
        os.remove(old)


@bp.record_once
def _start_backup_thread(state):
    def loop():
        while True:
            try:
                with lock:
                    backup_if_needed()
            except Exception:
                pass
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True).start()


@bp.post("/api/sync")
def sync():
    body = request.get_json(silent=True) or {}
    changed = False
    with lock:
        d = _read()
        ents = d["entries"]
        for raw in body.get("entries") or []:
            e = _clean(raw)
            if not e:
                continue
            cur = ents.get(e["id"])
            if not cur or e["updated"] > _int(cur.get("updated")):
                ents[e["id"]] = e
                changed = True
        cfg = body.get("config")
        if isinstance(cfg, dict) and _int(cfg.get("updated")) > _int(d["config"].get("updated")):
            d["config"] = {"cats": cfg.get("cats", []), "learned": cfg.get("learned", {}),
                           "updated": _int(cfg.get("updated"))}
            changed = True
        if changed:
            _write(d)
        backup_if_needed()
    return jsonify(entries=list(ents.values()), config=d["config"])


@bp.get("/api/export.csv")
def export_csv():
    with lock:
        d = _read()
    rows = sorted((e for e in d["entries"].values() if not e.get("deleted")), key=lambda e: e["date"])
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Дата", "Сумма", "Категория", "Описание"])
    for e in rows:
        w.writerow([e["date"], e["amount"], e["category"], e["desc"]])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=expenses.csv"})


# ---------------------------------------------------------------- чат с ИИ
SYSTEM = (
    "Ты помощник по учёту расходов. Отвечай по-русски, коротко и по делу. "
    "Суммы пиши с точкой как разделителем тысяч и словом «сум», например 1.250.000 сум. "
    "Проценты округляй до одного знака. Используй ТОЛЬКО данные ниже; если данных не хватает — так и скажи. "
    "Для сумм бери готовые итоги из таблиц, а не складывай вручную, если итог уже есть. "
    "Сегодняшняя дата указана ниже. «За месяц» без уточнения — текущий календарный месяц."
)
_chat_count = {}


def build_context(entries):
    t = today()
    live = [e for e in entries if not e.get("deleted")]
    by_month = {}
    by_day = {}
    for e in live:
        m = e["date"][:7]
        by_month.setdefault(m, {}).setdefault(e["category"], 0)
        by_month[m][e["category"]] += e["amount"]
        by_day[e["date"]] = by_day.get(e["date"], 0) + e["amount"]
    lines = [f"Сегодня: {t.isoformat()} ({['пн','вт','ср','чт','пт','сб','вс'][t.weekday()]})", ""]
    lines.append("ИТОГИ ПО МЕСЯЦАМ И КАТЕГОРИЯМ:")
    for m in sorted(by_month):
        tot = sum(by_month[m].values())
        cats = "; ".join(f"{k}: {v}" for k, v in sorted(by_month[m].items(), key=lambda x: -x[1]))
        lines.append(f"{m}: всего {tot} | {cats}")
    lines.append("")
    lines.append("ИТОГИ ПО ДНЯМ (последние 62 дня):")
    for d_ in sorted(by_day)[-62:]:
        lines.append(f"{d_}: {by_day[d_]}")
    lines.append("")
    lines.append("ПОСЛЕДНИЕ ЗАПИСИ (дата | категория | сумма | описание):")
    for e in sorted(live, key=lambda e: (e["date"], e["created"]))[-250:]:
        lines.append(f"{e['date']} | {e['category']} | {e['amount']} | {e['desc']}")
    return "\n".join(lines)


def ask_claude(question, context):
    import anthropic  # ключ берётся из ANTHROPIC_API_KEY
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=700, system=SYSTEM,
        messages=[{"role": "user", "content": context + "\n\nВОПРОС: " + question}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


@bp.post("/api/chat")
def chat():
    q = str((request.get_json(silent=True) or {}).get("question", "")).strip()[:500]
    if not q:
        return jsonify(error="empty"), 400
    key = today().isoformat()
    if key not in _chat_count:
        _chat_count.clear()
    _chat_count[key] = _chat_count.get(key, 0) + 1
    if _chat_count[key] > CHAT_LIMIT:
        return jsonify(error="limit"), 429
    with lock:
        ents = list(_read()["entries"].values())
    try:
        answer = ask_claude(q, build_context(ents))
    except Exception:
        return jsonify(error="ai"), 502
    return jsonify(answer=answer or "Не получилось ответить, попробуй переформулировать.")
