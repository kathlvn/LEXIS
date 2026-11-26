"""
Holiday import from the Official Gazette (holiday importer).

Every year the President declares the regular holidays and special
(non-working) days in one proclamation titled "Declaring the regular
holidays and special (non-working) days for the year <year>". The Official
Gazette posts it as a page whose list of dates is only inside a scanned PDF
(a photo of the signed paper, no text layer), so LEXIS:

1. finds that page with the Gazette's own search,
2. downloads the PDF linked from it,
3. reads the scanned pages with Tesseract OCR, and
4. picks out the "Name - day Month" lines under each section.

The result is a list of SUGGESTIONS. Nothing is saved here: the Head
Attorney checks each date against the PDF and ticks what to add (saved
through the existing POST /holidays).

Left out on purpose:
- Special (working) days, e.g. the EDSA anniversary: offices are open, so
  they don't move due dates.
- Eid'l Fitr and Eid'l Adha: declared in their own proclamations later in
  the year (Section 2 of the yearly proclamation). Entered by hand, like
  local holidays, work suspensions, and any later amending proclamation.

Outside calls: a timeout on every request, one
try each (no retry loop), caps on search results, PDF size and pages, and
only https://www.officialgazette.gov.ph links are followed, so a link on a
page can never send LEXIS anywhere else. LEXIS names itself in the
User-Agent and sends no made-up referer.
"""
import calendar
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote_plus

from app.core.config import settings

GAZETTE_ORIGIN = "https://www.officialgazette.gov.ph"
GAZETTE_DOWNLOADS = GAZETTE_ORIGIN + "/downloads/"
REQUEST_HEADERS = {"User-Agent": "LEXIS/1.0 (ESSU Legal Executive Information System; holiday import)"}
PAGE_TIMEOUT_SECONDS = 20
PDF_TIMEOUT_SECONDS = 40
OCR_TIMEOUT_SECONDS = 60       # per page
MAX_CANDIDATE_PAGES = 5        # search results opened to find the right proclamation
MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 6              # the proclamation is 3 pages
OCR_SCALE = 300 / 72           # render at 300 dpi, Tesseract's sweet spot
WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
WEEKDAYS = {name.lower(): number for number, name in enumerate(calendar.day_name)}

# "New Year's Day - 1 January (Friday)". OCR leaves small smudges between the
# day and the month ("21 ~+August", "1. | November"), hence the few
# non-letters allowed there.
HOLIDAY_LINE = re.compile(
    r"^(?P<name>[A-Za-z].*?)\s+[-\u2013\u2014~]+\s*(?P<day>\d{1,2})[^A-Za-z\n]{0,6}?"
    r"(?P<month>" + "|".join(MONTHS) + r")\b(?:\s*\(\s*(?P<weekday>[A-Za-z]+))?",
    re.IGNORECASE,
)
MONTH_WORD = re.compile(r"\b(" + "|".join(MONTHS) + r")\b", re.IGNORECASE)


class HolidayImportError(Exception):
    """A friendly message the Holidays page can show as it is."""


@dataclass
class SuggestedHoliday:
    holiday_date: date
    name: str
    holiday_type: str                 # "regular" | "special" (Holiday.holiday_type)
    check_note: str | None = None     # e.g. the weekday in the PDF doesn't match the date


@dataclass
class ProclamationImport:
    year: int
    page_url: str
    pdf_url: str
    title: str
    holidays: list[SuggestedHoliday] = field(default_factory=list)
    # Lines that look like a holiday but couldn't be read, so the Head
    # Attorney knows to look at the PDF for them.
    unreadable_lines: list[str] = field(default_factory=list)


# --- 1. Find the proclamation ------------------------------------------------

def _fetch(url: str, timeout: int):
    """One GET to the Gazette. Imported here so the parser can be tested
    without the network packages loaded."""
    from scrapling.fetchers import Fetcher

    if not url.startswith(GAZETTE_ORIGIN + "/"):
        raise HolidayImportError("LEXIS only reads holiday proclamations from the Official Gazette.")
    try:
        page = Fetcher.get(
            url, timeout=timeout, retries=1, stealthy_headers=False,
            headers=REQUEST_HEADERS, max_redirects=3,
        )
    except Exception:
        raise HolidayImportError(
            "The Official Gazette couldn't be reached. Try again later, or enter the dates by hand."
        )
    if not str(page.url).startswith(GAZETTE_ORIGIN + "/"):  # redirected somewhere else
        raise HolidayImportError("LEXIS only reads holiday proclamations from the Official Gazette.")
    if page.status in (403, 429):
        # The Gazette is behind Cloudflare, which briefly refuses pages after
        # a few quick requests. LEXIS doesn't try to get around it.
        raise HolidayImportError(
            "The Official Gazette is limiting requests from LEXIS right now. "
            "Wait a few minutes and try again, or enter the dates by hand."
        )
    if page.status != 200:
        raise HolidayImportError(
            f"The Official Gazette answered with an error ({page.status}). "
            "Try again later, or enter the dates by hand."
        )
    return page


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().upper()


def find_proclamation(year: int) -> tuple[str, str, str]:
    """(page URL, PDF URL, title) of the yearly holiday proclamation."""
    search = _fetch(
        f"{GAZETTE_ORIGIN}/?s={quote_plus(f'regular holidays special days {year}')}",
        PAGE_TIMEOUT_SECONDS,
    )
    candidates: list[str] = []
    for link in search.css("a"):
        href = link.attrib.get("href", "")
        if href.startswith(GAZETTE_ORIGIN + "/") and "/proclamation-no-" in href and href not in candidates:
            candidates.append(href)

    wanted = f"DECLARING THE REGULAR HOLIDAYS AND SPECIAL (NON-WORKING) DAYS FOR THE YEAR {year}"
    for url in candidates[:MAX_CANDIDATE_PAGES]:
        page = _fetch(url, PAGE_TIMEOUT_SECONDS)
        if wanted not in _squash(" ".join(page.css("body ::text").getall())):
            continue
        for el in page.css("a[href$='.pdf'], embed[src$='.pdf']"):
            pdf_url = el.attrib.get("href") or el.attrib.get("src") or ""
            if pdf_url.startswith(GAZETTE_DOWNLOADS):
                title = (page.css("title::text").get() or "").split("|")[0].strip()
                return url, pdf_url, title or "Holiday proclamation"
        raise HolidayImportError(
            "The proclamation was found, but its PDF link wasn't. Open it on the Official Gazette "
            f"and enter the dates by hand: {url}"
        )
    raise HolidayImportError(
        f"No holiday proclamation for {year} was found on the Official Gazette yet. "
        "It usually comes out a few months before the year starts."
    )


# --- 2 and 3. Download the PDF and read it with OCR --------------------------

def _tesseract_cmd() -> str:
    for cmd in (settings.tesseract_cmd, shutil.which("tesseract"), WINDOWS_TESSERACT):
        if cmd and os.path.exists(cmd):
            return cmd
    raise HolidayImportError(
        "OCR isn't set up on this server (Tesseract wasn't found), so the PDF can't be read. "
        "Enter the dates by hand, or ask the system administrator."
    )


def download_pdf(pdf_url: str) -> bytes:
    body = _fetch(pdf_url, PDF_TIMEOUT_SECONDS).body
    if not body.startswith(b"%PDF"):
        raise HolidayImportError("The proclamation's file isn't a PDF, so it can't be read.")
    if len(body) > MAX_PDF_BYTES:
        raise HolidayImportError("The proclamation's PDF is larger than expected, so it wasn't read.")
    return body


def ocr_pdf(pdf_bytes: bytes) -> str:
    """The text of every page, in order."""
    import pypdfium2 as pdfium
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd()
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception:
        raise HolidayImportError("The proclamation's PDF couldn't be opened.")
    try:
        texts = []
        for index in range(min(len(pdf), MAX_PDF_PAGES)):
            image = pdf[index].render(scale=OCR_SCALE).to_pil().convert("L")
            # psm 6 = "one block of text": keeps each "Name - day Month" on one line.
            texts.append(pytesseract.image_to_string(
                image, lang="eng", config="--psm 6", timeout=OCR_TIMEOUT_SECONDS,
            ))
        return "\n".join(texts)
    except RuntimeError:  # pytesseract's timeout
        raise HolidayImportError("Reading the PDF took too long. Try again, or enter the dates by hand.")
    finally:
        pdf.close()


# --- 4. Pick out the dates ---------------------------------------------------

def _section_of(line: str) -> str | None:
    """'regular', 'special' or 'skip' for a section heading, else None.
    "A. Regular Holidays", "B. Special (Non-Working) Days",
    "C. Special (Working) Day", "Additional Special (Non-Working) Days"."""
    letters = re.sub(r"[^a-z]", "", line.lower())
    if len(letters) > 45:  # a sentence or a holiday line, not a heading
        return None
    if "regularholiday" in letters:
        return "regular"
    if "specialnonworking" in letters:
        return "special"
    if "specialworking" in letters:
        return "skip"
    return None


def _clean_name(name: str) -> str:
    name = name.replace("\u2019", "'").replace("\u2018", "'")
    name = re.sub(r"[^A-Za-z0-9' .,()\-]", "", name)
    return re.sub(r"\s+", " ", name).strip(" .-")[:150]  # Holiday.name max_length


def parse_holidays(text: str, year: int) -> tuple[list[SuggestedHoliday], list[str]]:
    """The holidays in the OCR text of a yearly proclamation, plus the lines
    that looked like holidays but couldn't be read."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Only Section 1 lists the dates; the WHEREAS clauses before it mention
    # dates in sentences. If OCR garbled "SECTION 1", read everything.
    start = next((i for i, l in enumerate(lines) if re.match(r"SECTION\s*1\b", l, re.I)), 0)
    end = next((i for i, l in enumerate(lines) if i > start and re.match(r"SECTION\s*2\b", l, re.I)), len(lines))

    holidays: list[SuggestedHoliday] = []
    unreadable: list[str] = []
    seen: set[date] = set()
    section = None
    for line in lines[start:end]:
        heading = _section_of(line)
        if heading:
            section = heading
            continue
        if section not in ("regular", "special"):
            continue
        match = HOLIDAY_LINE.match(line)
        if not match:
            if MONTH_WORD.search(line) and re.search(r"\d", line):
                unreadable.append(line[:200])
            continue
        try:
            day = date(year, MONTHS[match["month"].lower()], int(match["day"]))
        except ValueError:
            unreadable.append(line[:200])
            continue
        if day in seen:
            continue
        seen.add(day)
        note = None
        weekday = (match["weekday"] or "").lower()
        if weekday in WEEKDAYS and WEEKDAYS[weekday] != day.weekday():
            note = (f"The PDF says {weekday.capitalize()}, but this date is a "
                    f"{calendar.day_name[day.weekday()]}. Check the date in the PDF.")
        holidays.append(SuggestedHoliday(day, _clean_name(match["name"]), section, note))
    holidays.sort(key=lambda h: h.holiday_date)
    return holidays, unreadable


# --- All four steps ----------------------------------------------------------

def import_from_gazette(year: int) -> ProclamationImport:
    """Find, download, read and parse the year's proclamation. Raises
    HolidayImportError with a message fit to show the Head Attorney."""
    page_url, pdf_url, title = find_proclamation(year)
    holidays, unreadable = parse_holidays(ocr_pdf(download_pdf(pdf_url)), year)
    if not holidays:
        raise HolidayImportError(
            "The proclamation was found, but no dates could be read from its PDF. Open it on the "
            f"Official Gazette and enter the dates by hand: {page_url}"
        )
    return ProclamationImport(year, page_url, pdf_url, title, holidays, unreadable)
