from app import parsing


def test_parse_amount_turkish_and_english_formats():
    assert parsing.parse_amount("1.234,56") == 1234.56
    assert parsing.parse_amount("-1.234,56 TL") == -1234.56
    assert parsing.parse_amount("1,234.56") == 1234.56
    assert parsing.parse_amount("45,90") == 45.90
    assert parsing.parse_amount("12.50") == 12.50
    assert parsing.parse_amount("1.250") == 1250
    assert parsing.parse_amount("250,00-") == -250
    assert parsing.parse_amount(-99.5) == -99.5
    assert parsing.parse_amount("") is None
    assert parsing.parse_amount("TL") is None


def test_parse_date():
    assert parsing.parse_date("22.10.2026") == "2026-10-22"
    assert parsing.parse_date("22/10/2026 14:33") == "2026-10-22"
    assert parsing.parse_date("2026-10-22T00:00:00") == "2026-10-22"
    assert parsing.parse_date("Tarih") is None


def test_normalize_turkish():
    assert parsing.normalize("İŞLEM AÇIKLAMASI") == "islem aciklamasi"
    assert parsing.normalize("Kâr Payı") == "kar payi"


def test_detect_and_extract_debit_credit_layout():
    rows = [
        ["KUVEYT TÜRK KATILIM BANKASI", None, None, None, None],
        ["Hesap Hareketleri", None, None, None, None],
        [],
        ["İşlem Tarihi", "Açıklama", "Borç", "Alacak", "Bakiye"],
        ["01.10.2026", "MIGROS KADIKOY", "350,75", "", "4.649,25"],
        ["03.10.2026", "GELEN FAST - AHMET", "", "5.000,00", "9.649,25"],
        ["", "Toplam", "350,75", "5.000,00", ""],
    ]
    header, mapping = parsing.detect_columns(rows)
    assert header == 3
    assert mapping == {"date": 0, "description": 1, "debit": 2, "credit": 3}
    txs = parsing.extract_transactions(rows, header, mapping)
    assert txs == [
        {"date": "2026-10-01", "description": "MIGROS KADIKOY", "amount": -350.75},
        {"date": "2026-10-03", "description": "GELEN FAST - AHMET", "amount": 5000.0},
    ]


def test_detect_signed_amount_layout():
    rows = [["Tarih", "Açıklama", "Tutar (TL)", "Bakiye (TL)"], ["05.10.2026", "Netflix", "-229,99", "100"]]
    header, mapping = parsing.detect_columns(rows)
    assert mapping == {"date": 0, "description": 1, "amount": 2}
    assert parsing.extract_transactions(rows, header, mapping)[0]["amount"] == -229.99


def test_read_csv_semicolon_cp1254():
    content = "Tarih;Açıklama;Tutar\n01.10.2026;ŞOK MARKET;-45,50\n".encode("cp1254")
    rows = parsing.read_table("kesit.csv", content)
    assert rows[1] == ["01.10.2026", "ŞOK MARKET", "-45,50"]


def test_import_hashes_keep_identical_same_day_rows_distinct():
    txs = [{"date": "2026-10-01", "description": "Kahve", "amount": -80.0}] * 2
    h = parsing.import_hashes(1, txs)
    assert h[0] != h[1]
    assert h == parsing.import_hashes(1, txs)  # نفس الملف = نفس البصمات


def test_categorize_prefers_longest_keyword():
    rules = [("migros", 1), ("sok market", 2), ("getir", 3), ("getir yemek", 4)]
    assert parsing.categorize("MIGROS KADIKÖY", rules) == 1
    assert parsing.categorize("ŞOK MARKET 123", rules) == 2
    assert parsing.categorize("GETIR YEMEK ISTANBUL", rules) == 4
    assert parsing.categorize("Maaş", rules) is None
