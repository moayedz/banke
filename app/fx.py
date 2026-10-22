"""سعر صرف الدولار مقابل الليرة من البنك المركزي التركي (TCMB)، ببلاش وبدون مفتاح."""

import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

TCMB_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"


def fetch_tcmb() -> dict[str, float]:
    with urllib.request.urlopen(TCMB_URL, timeout=5) as resp:
        root = ET.fromstring(resp.read())
    rates = {}
    for cur in root.findall("Currency"):
        code = cur.get("CurrencyCode")
        selling = cur.findtext("ForexSelling")
        unit = float(cur.findtext("Unit") or 1)
        if code and selling:
            rates[code] = float(selling) / unit
    return rates


def rate_to_try(conn: sqlite3.Connection, currency: str) -> float | None:
    """بيرجّع كم ليرة بتسوى وحدة من العملة. بيحفظ السعر مرة باليوم."""
    if currency == "TRY":
        return 1.0
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT rate FROM fx_rates WHERE currency = ? AND date = ?", (currency, today)
    ).fetchone()
    if row:
        return row["rate"]
    try:
        rates = fetch_tcmb()
        if currency in rates:
            conn.execute(
                "INSERT OR REPLACE INTO fx_rates (currency, date, rate) VALUES (?, ?, ?)",
                (currency, today, rates[currency]),
            )
            conn.commit()
            return rates[currency]
    except Exception:
        pass  # بدون إنترنت؟ بنستعمل آخر سعر محفوظ
    row = conn.execute(
        "SELECT rate FROM fx_rates WHERE currency = ? ORDER BY date DESC LIMIT 1", (currency,)
    ).fetchone()
    return row["rate"] if row else None
