"""بنكي — أداة شخصية لتتبّع المصاريف. شغّلها بـ ./run.sh"""

import json
import secrets
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db, fx, parsing
from .security import LoginLimiter, hash_pin, verify_pin

BASE_DIR = Path(__file__).resolve().parent
IMPORT_DIR = db.DATA_DIR / "imports"
PUBLIC_PATHS = ("/login", "/setup", "/static", "/manifest.webmanifest", "/sw.js")

db.init_db()
with db.connect() as _conn:
    _secret = db.get_setting(_conn, "session_secret")
    if not _secret:
        _secret = secrets.token_hex(32)
        db.set_setting(_conn, "session_secret", _secret)

app = FastAPI(title="بنكي", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=BASE_DIR / "templates")
limiter = LoginLimiter()


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not path.startswith(PUBLIC_PATHS) and not request.session.get("auth"):
        with db.connect() as conn:
            has_pin = db.get_setting(conn, "pin_hash") is not None
        return RedirectResponse("/login" if has_pin else "/setup", status_code=303)
    return await call_next(request)


# الترتيب مهم: السيشن لازم يتسجّل بعد (يعني يشتغل قبل) فحص الدخول
app.add_middleware(
    SessionMiddleware, secret_key=_secret, max_age=60 * 60 * 24 * 14, same_site="strict", https_only=False
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


# ---------- أدوات مساعدة ----------

def money(value: float, currency: str = "TRY") -> str:
    symbol = {"TRY": "₺", "USD": "$", "EUR": "€"}.get(currency, currency + " ")
    formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if value < 0 else ''}{symbol}{formatted}"


templates.env.filters["money"] = money


def render(request: Request, name: str, **context) -> HTMLResponse:
    context["flash"] = request.session.pop("flash", None)
    context["path"] = request.url.path
    return templates.TemplateResponse(request, name, context)


def flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def shift_month(month: str, delta: int) -> str:
    year, m = map(int, month.split("-"))
    m += delta
    year += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{year:04d}-{m:02d}"


def valid_month(month: str | None) -> str:
    try:
        year, m = map(int, (month or "").split("-"))
        if 2000 <= year <= 2100 and 1 <= m <= 12:
            return f"{year:04d}-{m:02d}"
    except ValueError:
        pass
    return current_month()


def fx_rates(conn: sqlite3.Connection) -> dict[str, float]:
    """سعر كل عملة مستعملة بالحسابات مقابل الليرة."""
    rates = {}
    for row in conn.execute("SELECT DISTINCT currency FROM accounts"):
        cur = row["currency"]
        rate = fx.rate_to_try(conn, cur)
        if rate is None:
            manual = db.get_setting(conn, f"manual_rate_{cur}")
            rate = float(manual) if manual else 0.0
        rates[cur] = rate
    return rates


def load_rules(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    return [(r["keyword"], r["category_id"]) for r in conn.execute("SELECT keyword, category_id FROM rules")]


def account_balances(conn: sqlite3.Connection, rates: dict[str, float]) -> list[dict]:
    rows = conn.execute(
        """SELECT a.*, a.opening_balance + COALESCE(SUM(t.amount), 0) AS balance, COUNT(t.id) AS tx_count
           FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id
           GROUP BY a.id ORDER BY a.id"""
    ).fetchall()
    return [dict(r) | {"balance_try": r["balance"] * rates.get(r["currency"], 0)} for r in rows]


# ---------- الدخول ----------

@app.get("/setup")
def setup_page(request: Request):
    with db.connect() as conn:
        if db.get_setting(conn, "pin_hash"):
            return redirect("/login")
    return render(request, "setup.html")


@app.post("/setup")
def setup_submit(request: Request, pin: str = Form(...), pin2: str = Form(...)):
    with db.connect() as conn:
        if db.get_setting(conn, "pin_hash"):
            return redirect("/login")
        if len(pin) < 4:
            return render(request, "setup.html", error="الرمز لازم يكون 4 خانات أو أكثر.")
        if pin != pin2:
            return render(request, "setup.html", error="الرمزين مش متطابقين.")
        db.set_setting(conn, "pin_hash", hash_pin(pin))
    request.session["auth"] = True
    return redirect("/")


@app.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@app.post("/login")
def login_submit(request: Request, pin: str = Form(...)):
    key = request.client.host if request.client else "local"
    wait = limiter.locked_for(key)
    if wait:
        return render(request, "login.html", error=f"محاولات كثير غلط. استنّى {wait // 60 + 1} دقايق.")
    with db.connect() as conn:
        stored = db.get_setting(conn, "pin_hash")
    if stored and verify_pin(pin, stored):
        limiter.reset(key)
        request.session["auth"] = True
        return redirect("/")
    limiter.fail(key)
    return render(request, "login.html", error="الرمز غلط.")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# ---------- اللوحة الرئيسية ----------

@app.get("/")
def dashboard(request: Request, month: str | None = None):
    month = valid_month(month)
    with db.connect() as conn:
        rates = fx_rates(conn)
        accounts = account_balances(conn, rates)
        rows = conn.execute(
            """SELECT t.*, a.currency, a.name AS account_name, c.name AS category_name, c.color, c.kind
               FROM transactions t
               JOIN accounts a ON a.id = t.account_id
               LEFT JOIN categories c ON c.id = t.category_id
               WHERE substr(t.date, 1, 7) = ?
               ORDER BY t.date DESC, t.id DESC""",
            (month,),
        ).fetchall()

    income = expense = 0.0
    by_category: dict[str, dict] = {}
    daily: dict[int, float] = defaultdict(float)
    for r in rows:
        amount_try = r["amount"] * rates.get(r["currency"], 0)
        if r["category_name"] == "تحويلات":
            continue  # التحويل بين حساباتك مش مصروف حقيقي
        if amount_try >= 0:
            income += amount_try
        else:
            expense += -amount_try
            name = r["category_name"] or "بدون تصنيف"
            entry = by_category.setdefault(name, {"name": name, "color": r["color"] or "#bdbdbd", "total": 0.0})
            entry["total"] += -amount_try
            daily[int(r["date"][8:10])] += -amount_try

    categories = sorted(by_category.values(), key=lambda c: c["total"], reverse=True)
    year, m = map(int, month.split("-"))
    days_in_month = (date(year + (m == 12), m % 12 + 1, 1) - date(year, m, 1)).days
    chart = {
        "categories": {
            "labels": [c["name"] for c in categories],
            "values": [round(c["total"], 2) for c in categories],
            "colors": [c["color"] for c in categories],
        },
        "daily": {
            "labels": list(range(1, days_in_month + 1)),
            "values": [round(daily.get(d, 0), 2) for d in range(1, days_in_month + 1)],
        },
    }
    return render(
        request,
        "dashboard.html",
        month=month,
        prev_month=shift_month(month, -1),
        next_month=shift_month(month, 1),
        income=income,
        expense=expense,
        net=income - expense,
        net_worth=sum(a["balance_try"] for a in accounts),
        accounts=accounts,
        categories=categories,
        recent=rows[:8],
        chart_json=json.dumps(chart, ensure_ascii=False),
        usd_rate=rates.get("USD"),
        missing_rates=[cur for cur, rate in rates.items() if not rate],
    )


# ---------- المعاملات ----------

@app.get("/transactions")
def transactions_list(
    request: Request, month: str | None = None, account: str = "", category: str = "", q: str = ""
):
    month = valid_month(month)
    sql = """SELECT t.*, a.currency, a.name AS account_name, c.name AS category_name, c.color
             FROM transactions t
             JOIN accounts a ON a.id = t.account_id
             LEFT JOIN categories c ON c.id = t.category_id
             WHERE substr(t.date, 1, 7) = ?"""
    params: list = [month]
    if account.isdigit():
        sql += " AND t.account_id = ?"
        params.append(int(account))
    if category == "none":
        sql += " AND t.category_id IS NULL"
    elif category.isdigit():
        sql += " AND t.category_id = ?"
        params.append(int(category))
    sql += " ORDER BY t.date DESC, t.id DESC"
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        accounts = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        categories = conn.execute("SELECT * FROM categories ORDER BY kind DESC, id").fetchall()
    if q:
        needle = parsing.normalize(q)
        rows = [r for r in rows if needle in parsing.normalize(r["description"] + " " + r["note"])]
    return render(
        request,
        "transactions.html",
        rows=rows,
        accounts=accounts,
        categories=categories,
        month=month,
        prev_month=shift_month(month, -1),
        next_month=shift_month(month, 1),
        f_account=account,
        f_category=category,
        q=q,
    )


def _form_context(conn: sqlite3.Connection) -> dict:
    return {
        "accounts": conn.execute("SELECT * FROM accounts ORDER BY id").fetchall(),
        "categories": conn.execute("SELECT * FROM categories ORDER BY kind DESC, id").fetchall(),
    }


def _parse_tx_form(kind: str, amount: str) -> float | None:
    value = parsing.parse_amount(amount)
    if value is None or value == 0:
        return None
    return abs(value) if kind == "income" else -abs(value)


@app.get("/transactions/new")
def transaction_new(request: Request):
    with db.connect() as conn:
        ctx = _form_context(conn)
    return render(request, "transaction_form.html", tx=None, today=date.today().isoformat(), **ctx)


@app.post("/transactions/new")
def transaction_create(
    request: Request,
    account_id: int = Form(...),
    tx_date: str = Form(...),
    kind: str = Form("expense"),
    amount: str = Form(...),
    description: str = Form(""),
    category_id: str = Form(""),
    note: str = Form(""),
):
    value = _parse_tx_form(kind, amount)
    tx_date = parsing.parse_date(tx_date)
    with db.connect() as conn:
        if value is None or not tx_date:
            ctx = _form_context(conn)
            return render(request, "transaction_form.html", tx=None, today=date.today().isoformat(),
                          error="تأكّد من المبلغ والتاريخ.", **ctx)
        cat = int(category_id) if category_id.isdigit() else parsing.categorize(description, load_rules(conn))
        conn.execute(
            "INSERT INTO transactions (account_id, date, description, amount, category_id, note) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, tx_date, description.strip(), value, cat, note.strip()),
        )
    flash(request, "انضافت المعاملة ✓")
    return redirect(f"/transactions?month={tx_date[:7]}")


@app.get("/transactions/{tx_id}/edit")
def transaction_edit(request: Request, tx_id: int):
    with db.connect() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not tx:
            return redirect("/transactions")
        ctx = _form_context(conn)
    suggestion = parsing.normalize(tx["description"]).split(" ")[0] if tx["description"] else ""
    return render(request, "transaction_form.html", tx=tx, today=tx["date"], suggestion=suggestion, **ctx)


@app.post("/transactions/{tx_id}/edit")
def transaction_update(
    request: Request,
    tx_id: int,
    account_id: int = Form(...),
    tx_date: str = Form(...),
    kind: str = Form("expense"),
    amount: str = Form(...),
    description: str = Form(""),
    category_id: str = Form(""),
    note: str = Form(""),
    remember_keyword: str = Form(""),
):
    value = _parse_tx_form(kind, amount)
    tx_date = parsing.parse_date(tx_date)
    if value is None or not tx_date:
        flash(request, "تأكّد من المبلغ والتاريخ.")
        return redirect(f"/transactions/{tx_id}/edit")
    cat = int(category_id) if category_id.isdigit() else None
    with db.connect() as conn:
        conn.execute(
            """UPDATE transactions SET account_id = ?, date = ?, description = ?, amount = ?,
               category_id = ?, note = ? WHERE id = ?""",
            (account_id, tx_date, description.strip(), value, cat, note.strip(), tx_id),
        )
        keyword = parsing.normalize(remember_keyword)
        if keyword and cat:
            conn.execute(
                "INSERT INTO rules (keyword, category_id) VALUES (?, ?) "
                "ON CONFLICT(keyword) DO UPDATE SET category_id = excluded.category_id",
                (keyword, cat),
            )
            # نطبّق القاعدة الجديدة على المعاملات غير المصنّفة
            rules = [(keyword, cat)]
            for row in conn.execute("SELECT id, description FROM transactions WHERE category_id IS NULL").fetchall():
                if parsing.categorize(row["description"], rules):
                    conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?", (cat, row["id"]))
    flash(request, "انحفظ التعديل ✓")
    return redirect(f"/transactions?month={tx_date[:7]}")


@app.post("/transactions/{tx_id}/delete")
def transaction_delete(request: Request, tx_id: int):
    with db.connect() as conn:
        row = conn.execute("SELECT date FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    flash(request, "انحذفت المعاملة.")
    return redirect(f"/transactions?month={row['date'][:7]}" if row else "/transactions")


# ---------- استيراد كشف حساب ----------

def _import_file(token: str) -> Path:
    if not token.isalnum():
        raise ValueError("bad token")
    return IMPORT_DIR / f"{token}.json"


@app.get("/import")
def import_page(request: Request):
    with db.connect() as conn:
        accounts = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    return render(request, "import.html", accounts=accounts)


@app.post("/import")
async def import_upload(request: Request, account_id: int = Form(...), file: UploadFile = File(...)):
    content = await file.read()
    try:
        rows = parsing.read_table(file.filename or "", content)
    except Exception as exc:  # noqa: BLE001 — أي خطأ بالملف بنعرضه للمستخدم
        flash(request, f"ما قدرت أقرأ الملف: {exc}")
        return redirect("/import")
    rows = [[c.isoformat() if hasattr(c, "isoformat") else c for c in row] for row in rows]
    header_row, mapping = parsing.detect_columns(rows)
    token = secrets.token_hex(12)
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    _import_file(token).write_text(
        json.dumps({"filename": file.filename, "account_id": account_id, "rows": rows,
                    "header_row": header_row, "mapping": mapping}, ensure_ascii=False, default=str)
    )
    return redirect(f"/import/{token}")


def _mapping_from_request(data: dict, params) -> tuple[int | None, dict[str, int]]:
    if "header_row" not in params:
        return data["header_row"], data["mapping"]
    header = params.get("header_row")
    header_row = int(header) if str(header).isdigit() else None
    mapping = {}
    for role in ("date", "description", "amount", "debit", "credit"):
        value = params.get(role, "")
        if str(value).isdigit():
            mapping[role] = int(value)
    return header_row, mapping


@app.get("/import/{token}")
def import_preview(request: Request, token: str):
    try:
        data = json.loads(_import_file(token).read_text())
    except (ValueError, FileNotFoundError):
        flash(request, "انتهت صلاحية الاستيراد، ارفع الملف من جديد.")
        return redirect("/import")
    header_row, mapping = _mapping_from_request(data, request.query_params)
    rows = data["rows"]
    txs = parsing.extract_transactions(rows, header_row, mapping) if header_row is not None else []
    with db.connect() as conn:
        rules = load_rules(conn)
        hashes = parsing.import_hashes(data["account_id"], txs)
        existing = {
            r["import_hash"]
            for r in conn.execute(
                f"SELECT import_hash FROM transactions WHERE import_hash IN ({','.join('?' * len(hashes))})", hashes
            )
        } if hashes else set()
        cat_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM categories")}
        account = conn.execute("SELECT * FROM accounts WHERE id = ?", (data["account_id"],)).fetchone()
    for tx, h in zip(txs, hashes):
        tx["duplicate"] = h in existing
        cat = parsing.categorize(tx["description"], rules)
        tx["category"] = cat_names.get(cat, "")
    header_cells = rows[header_row] if header_row is not None else (rows[0] if rows else [])
    return render(
        request,
        "import_preview.html",
        token=token,
        filename=data["filename"],
        account=account,
        txs=txs,
        new_count=sum(1 for t in txs if not t["duplicate"]),
        header_row=header_row,
        mapping=mapping,
        header_cells=header_cells,
        sample_rows=rows[:15],
        roles={"date": "التاريخ", "description": "الوصف", "amount": "المبلغ (بإشارة)",
               "debit": "مدين / طالع (Borç)", "credit": "دائن / داخل (Alacak)"},
    )


@app.post("/import/{token}/confirm")
async def import_confirm(request: Request, token: str):
    form = await request.form()
    try:
        path = _import_file(token)
        data = json.loads(path.read_text())
    except (ValueError, FileNotFoundError):
        flash(request, "انتهت صلاحية الاستيراد، ارفع الملف من جديد.")
        return redirect("/import")
    header_row, mapping = _mapping_from_request(data, form)
    if header_row is None:
        flash(request, "اختار صف العناوين والأعمدة أول.")
        return redirect(f"/import/{token}")
    txs = parsing.extract_transactions(data["rows"], header_row, mapping)
    hashes = parsing.import_hashes(data["account_id"], txs)
    added = 0
    with db.connect() as conn:
        rules = load_rules(conn)
        for tx, h in zip(txs, hashes):
            cur = conn.execute(
                """INSERT OR IGNORE INTO transactions
                   (account_id, date, description, amount, category_id, source, import_hash)
                   VALUES (?, ?, ?, ?, ?, 'import', ?)""",
                (data["account_id"], tx["date"], tx["description"], tx["amount"],
                 parsing.categorize(tx["description"], rules), h),
            )
            added += cur.rowcount
    path.unlink(missing_ok=True)
    skipped = len(txs) - added
    flash(request, f"انضافت {added} معاملة ✓" + (f" (تجاهلت {skipped} مكرّرة)" if skipped else ""))
    month = max((t["date"] for t in txs), default=date.today().isoformat())[:7]
    return redirect(f"/transactions?month={month}")


# ---------- الحسابات ----------

@app.get("/accounts")
def accounts_page(request: Request):
    with db.connect() as conn:
        rates = fx_rates(conn)
        accounts = account_balances(conn, rates)
        manual_usd = db.get_setting(conn, "manual_rate_USD") or ""
    return render(request, "accounts.html", accounts=accounts, rates=rates, manual_usd=manual_usd,
                  net_worth=sum(a["balance_try"] for a in accounts))


@app.post("/accounts")
def account_create(
    request: Request, name: str = Form(...), bank: str = Form(""), currency: str = Form("TRY"),
    opening_balance: str = Form("0"),
):
    currency = currency if currency in ("TRY", "USD", "EUR") else "TRY"
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO accounts (name, bank, currency, opening_balance) VALUES (?, ?, ?, ?)",
            (name.strip(), bank.strip() or None, currency, parsing.parse_amount(opening_balance) or 0),
        )
    flash(request, "انضاف الحساب ✓")
    return redirect("/accounts")


@app.post("/accounts/{account_id}/edit")
def account_update(request: Request, account_id: int, name: str = Form(...), opening_balance: str = Form("0")):
    with db.connect() as conn:
        conn.execute(
            "UPDATE accounts SET name = ?, opening_balance = ? WHERE id = ?",
            (name.strip(), parsing.parse_amount(opening_balance) or 0, account_id),
        )
    flash(request, "انحفظ ✓")
    return redirect("/accounts")


@app.post("/accounts/{account_id}/delete")
def account_delete(request: Request, account_id: int):
    with db.connect() as conn:
        used = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)).fetchone()[0]
        if used:
            flash(request, "ما بقدر أحذف حساب فيه معاملات.")
        else:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            flash(request, "انحذف الحساب.")
    return redirect("/accounts")


@app.post("/settings/rate")
def manual_rate(request: Request, usd: str = Form("")):
    value = parsing.parse_amount(usd)
    with db.connect() as conn:
        if value:
            db.set_setting(conn, "manual_rate_USD", str(value))
        else:
            conn.execute("DELETE FROM settings WHERE key = 'manual_rate_USD'")
    flash(request, "انحفظ ✓")
    return redirect("/accounts")


# ---------- الفئات والقواعد ----------

@app.get("/categories")
def categories_page(request: Request):
    with db.connect() as conn:
        categories = conn.execute("SELECT * FROM categories ORDER BY kind DESC, id").fetchall()
        rules = conn.execute(
            "SELECT r.*, c.name AS category_name FROM rules r JOIN categories c ON c.id = r.category_id ORDER BY c.id, r.keyword"
        ).fetchall()
        uncategorized = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0]
    grouped = defaultdict(list)
    for r in rules:
        grouped[r["category_id"]].append(r)
    return render(request, "categories.html", categories=categories, rules=grouped, uncategorized=uncategorized)


@app.post("/categories")
def category_create(request: Request, name: str = Form(...), kind: str = Form("expense"), color: str = Form("#888888")):
    with db.connect() as conn:
        try:
            conn.execute(
                "INSERT INTO categories (name, kind, color) VALUES (?, ?, ?)",
                (name.strip(), "income" if kind == "income" else "expense", color),
            )
            flash(request, "انضافت الفئة ✓")
        except sqlite3.IntegrityError:
            flash(request, "في فئة بنفس الاسم.")
    return redirect("/categories")


@app.post("/rules")
def rule_create(request: Request, keyword: str = Form(...), category_id: int = Form(...)):
    keyword = parsing.normalize(keyword)
    if keyword:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO rules (keyword, category_id) VALUES (?, ?) "
                "ON CONFLICT(keyword) DO UPDATE SET category_id = excluded.category_id",
                (keyword, category_id),
            )
        flash(request, "انضافت القاعدة ✓")
    return redirect("/categories")


@app.post("/rules/{rule_id}/delete")
def rule_delete(request: Request, rule_id: int):
    with db.connect() as conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return redirect("/categories")


@app.post("/rules/apply")
def rules_apply(request: Request):
    """بيطبّق القواعد على المعاملات اللي لسا بدون تصنيف."""
    changed = 0
    with db.connect() as conn:
        rules = load_rules(conn)
        for row in conn.execute("SELECT id, description FROM transactions WHERE category_id IS NULL").fetchall():
            cat = parsing.categorize(row["description"], rules)
            if cat:
                conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?", (cat, row["id"]))
                changed += 1
    flash(request, f"تصنّفت {changed} معاملة ✓")
    return redirect("/categories")


# ---------- نسخة احتياطية و PWA ----------

@app.get("/backup")
def backup():
    target = sqlite3.connect(":memory:")
    with db.connect() as conn:
        conn.backup(target)
    data = target.serialize()
    target.close()
    filename = f"banke-backup-{date.today().isoformat()}.db"
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/manifest.webmanifest")
def manifest():
    return Response((BASE_DIR / "static" / "manifest.webmanifest").read_text(), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return Response((BASE_DIR / "static" / "sw.js").read_text(), media_type="application/javascript")
