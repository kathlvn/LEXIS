r"""Checks for app/core/holiday_import.py (task f). Plain Python, no pytest.
Needs the internet (Official Gazette) and Tesseract. From backend/:
    ..\.venv\Scripts\python.exe tests\test_holiday_import.py
The two proclamation PDFs are downloaded into a temporary folder, so the
repo carries no PDF files.
"""
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from app.core import holiday_import as hi  # noqa: E402

PDF_URLS = {
    "proc-1427.pdf": "https://www.officialgazette.gov.ph/downloads/2026/09sep/20260908-PROC-1427-FRM.pdf",
    "proc-1006.pdf": "https://www.officialgazette.gov.ph/downloads/2025/09sep/20250903-PROC-1006-FRM.pdf",
}
PDF_DIR = Path(tempfile.mkdtemp(prefix="lois-proclamations-"))
for filename, url in PDF_URLS.items():
    (PDF_DIR / filename).write_bytes(hi.download_pdf(url))
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def as_set(holidays):
    return {(h.holiday_date, h.holiday_type) for h in holidays}


R, S = "regular", "special"
EXPECTED = {
    2027: ("proc-1427.pdf", {
        (date(2027, 1, 1), R), (date(2027, 3, 25), R), (date(2027, 3, 26), R), (date(2027, 4, 9), R),
        (date(2027, 5, 1), R), (date(2027, 6, 12), R), (date(2027, 8, 30), R), (date(2027, 11, 30), R),
        (date(2027, 12, 25), R), (date(2027, 12, 30), R),
        (date(2027, 2, 6), S), (date(2027, 3, 27), S), (date(2027, 8, 21), S), (date(2027, 11, 1), S),
        (date(2027, 11, 2), S), (date(2027, 12, 8), S), (date(2027, 12, 24), S), (date(2027, 12, 31), S),
    }),
    2026: ("proc-1006.pdf", {
        (date(2026, 1, 1), R), (date(2026, 4, 2), R), (date(2026, 4, 3), R), (date(2026, 4, 9), R),
        (date(2026, 5, 1), R), (date(2026, 6, 12), R), (date(2026, 8, 31), R), (date(2026, 11, 30), R),
        (date(2026, 12, 25), R), (date(2026, 12, 30), R),
        (date(2026, 2, 17), S), (date(2026, 4, 4), S), (date(2026, 8, 21), S), (date(2026, 11, 1), S),
        (date(2026, 11, 2), S), (date(2026, 12, 8), S), (date(2026, 12, 24), S), (date(2026, 12, 31), S),
    }),
}

# 1. Real PDFs, OCR + parser, against the lists read by eye.
for year, (filename, expected) in EXPECTED.items():
    text = hi.ocr_pdf((PDF_DIR / filename).read_bytes())
    holidays, unreadable = hi.parse_holidays(text, year)
    got = as_set(holidays)
    check(f"{year}: all 18 dates with the right type", got == expected,
          f"missing {sorted(expected - got)}, extra {sorted(got - expected)}")
    check(f"{year}: EDSA (special working day) left out", (date(year, 2, 25), R) not in got and (date(year, 2, 25), S) not in got)
    check(f"{year}: no unreadable lines", not unreadable, unreadable)
    check(f"{year}: no weekday warnings", all(h.check_note is None for h in holidays),
          [h for h in holidays if h.check_note])
    names = {h.holiday_date: h.name for h in holidays}
    check(f"{year}: names cleaned (All Saints' Day)", names.get(date(year, 11, 1)) == "All Saints' Day", names.get(date(year, 11, 1)))
    print("     ", "; ".join(f"{h.holiday_date:%b %d} {h.name}" for h in holidays))

# 2. Tricky lines.
sample = """WHEREAS, on 06 February 2027 (Saturday), Chinese nationals - 6 February
SECTION 1. The following regular holidays and special days for the year 2027
A. Regular Holidays
New Year's Day - 1 January (Friday)
Labor Day – 1 May (Sunday)
Bogus Day - 30 February (Monday)
Rizal Day 30 December (Thursday)
National Heroes Day - 30 August (Last Monday of
August)
B. Special (Non-Working) Days
Ninoy Aquino Day - 21 ~+August (Saturday)
C. Special (Working) Day
EDSA People Power Revolution Anniversary - 25 February (Thursday)
SECTION 2. Eidul Fitr - 10 March
"""
holidays, unreadable = hi.parse_holidays(sample, 2027)
got = {h.holiday_date: h for h in holidays}
check("WHEREAS lines before Section 1 ignored", date(2027, 2, 6) not in got)
check("smudge between day and month read", date(2027, 8, 21) in got and got[date(2027, 8, 21)].holiday_type == S)
check("en dash accepted", date(2027, 5, 1) in got)
check("weekday mismatch flagged", got.get(date(2027, 5, 1)) is not None and got[date(2027, 5, 1)].check_note is not None)
check("'(Last Monday of' not treated as a weekday", got.get(date(2027, 8, 30)) is not None and got[date(2027, 8, 30)].check_note is None)
check("impossible date (30 February) reported, not saved", "Bogus Day - 30 February (Monday)" in unreadable)
check("line with no dash reported as unreadable", "Rizal Day 30 December (Thursday)" in unreadable)
check("special working day skipped", date(2027, 2, 25) not in got)
check("lines after Section 2 ignored", date(2027, 3, 10) not in got)
check("sorted by date", [h.holiday_date for h in holidays] == sorted(got))

# 3. Safety: never fetch outside the Gazette.
for bad in ("https://example.com/x.pdf", "https://www.officialgazette.gov.ph.evil.com/x", "http://www.officialgazette.gov.ph/x"):
    try:
        hi._fetch(bad, 5)
        check(f"refuses {bad}", False, "no error raised")
    except hi.HolidayImportError:
        check(f"refuses {bad}", True)

# 4. Live, end to end (network). The Gazette (Cloudflare) briefly refuses
# pages after a few quick requests; that is reported as SKIP, not PASS.
skipped = []


def limited(error):
    if "limiting requests" in str(error):
        skipped.append(1)
        print("SKIP live check: the Official Gazette is limiting requests right now; run again in a few minutes")
        return True
    return False


try:
    result = hi.import_from_gazette(2027)
    check("live 2027: found Proclamation 1427", "1427" in result.page_url and result.pdf_url.startswith(hi.GAZETTE_DOWNLOADS))
    check("live 2027: same 18 dates", as_set(result.holidays) == EXPECTED[2027][1])
except hi.HolidayImportError as e:
    if not limited(e):
        check("live 2027 import", False, str(e))
try:
    hi.import_from_gazette(2030)
    check("live 2030: friendly 'not found yet' error", False, "no error raised")
except hi.HolidayImportError as e:
    if not limited(e):
        check("live 2030: friendly 'not found yet' error", "No holiday proclamation for 2030" in str(e), str(e))

print(f"\n{sum(results)}/{len(results)} passed" + (f", {len(skipped)} live check(s) skipped" if skipped else ""))
sys.exit(0 if all(results) else 1)
