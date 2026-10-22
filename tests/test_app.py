import importlib
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BANKE_DATA_DIR", str(tmp_path))
    from app import db, main
    importlib.reload(db)
    importlib.reload(main)
    return TestClient(main.app)


def test_full_flow(client):
    # أول مرة: لازم يختار PIN
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/setup"
    r = client.post("/setup", data={"pin": "1234", "pin2": "1234"})
    assert r.status_code == 200 and "صافي الثروة" in r.text

    # إضافة مصروف يدوي، والتصنيف التلقائي بيشتغل
    r = client.post("/transactions/new", data={
        "account_id": 1, "tx_date": "2026-10-05", "kind": "expense", "amount": "120,50", "description": "Migros Kadıköy"})
    assert "Migros" in r.text and "سوبرماركت" in r.text

    # استيراد Excel مرتين: المرة الثانية ما في تكرار
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["İşlem Tarihi", "Açıklama", "Tutar", "Bakiye"])
    ws.append(["06.10.2026", "TURKCELL FATURA", "-300,00", "1000"])
    ws.append(["07.10.2026", "Kâr Payı", "12,34", "1012,34"])
    buf = io.BytesIO()
    wb.save(buf)
    for expected in ("انضافت 2", "انضافت 0"):
        r = client.post("/import", data={"account_id": 1},
                        files={"file": ("k.xlsx", buf.getvalue(), "application/octet-stream")})
        assert "معاينة" in r.text
        token = str(r.url).rsplit("/", 1)[1]
        r = client.post(f"/import/{token}/confirm", data={})
        assert expected in r.text

    r = client.get("/?month=2026-10")
    assert "اتصالات وإنترنت" in r.text

    # الخروج والدخول بالرمز
    client.post("/logout")
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert "الرمز غلط" in client.post("/login", data={"pin": "0000"}).text
    assert "صافي الثروة" in client.post("/login", data={"pin": "1234"}).text

    backup = client.get("/backup")
    assert backup.content.startswith(b"SQLite format 3")
