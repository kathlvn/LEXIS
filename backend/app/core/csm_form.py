"""
The Client Satisfaction Measurement (CSM) form.

The questions live in an editable file, app/csm_form.json, not in the code,
so the office can change them without a programmer changing Python or the
database. The current file follows the Legal Office's printed CSM form
(manuscript Appendix E), which is based on the ARTA harmonized CSM.

load_csm_form() reads the file, checks it, and fills in each question's
choices from its scale. It re-reads the file when it changes, so an edit
takes effect without restarting the backend. A broken edit raises
CsmFormError with a plain message; callers show the client a friendly
"not available" message instead of an error page.
"""
import json
import re
from pathlib import Path

CSM_FORM_PATH = Path(__file__).resolve().parents[1] / "csm_form.json"

# Lengths that must fit the CsmResponse table. 50 questions with
# 20-character ids and 10-character values always fit its 2000-character
# answers column.
MAX_VERSION_LENGTH = 50
MAX_VALUE_LENGTH = 10
MAX_COMMENT_LENGTH = 2000
MAX_QUESTIONS = 50
QUESTION_ID = re.compile(r"^[A-Za-z0-9_]{1,20}$")

_cache: tuple[float, dict] | None = None   # (file modified time, checked form)


class CsmFormError(Exception):
    """The CSM form file is missing or not filled in correctly."""


def load_csm_form(path: Path | None = None) -> dict:
    global _cache
    path = path or CSM_FORM_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        raise CsmFormError(f"{path.name} was not found.")
    if path == CSM_FORM_PATH and _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CsmFormError(f"{path.name} is not valid JSON: {exc}")
    form = _check(data)
    if path == CSM_FORM_PATH:
        _cache = (mtime, form)
    return form


def _text(value, where: str, max_length: int = 1000, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > max_length:
        raise CsmFormError(f"{where} must be text of up to {max_length} characters.")
    return value.strip()


def _options(raw, where: str) -> list[dict]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise CsmFormError(f"{where} needs at least 2 choices.")
    options, seen = [], set()
    for i, o in enumerate(raw, 1):
        if not isinstance(o, dict):
            raise CsmFormError(f"{where}, choice {i} must have a value and a label.")
        value = _text(o.get("value"), f"{where}, choice {i} value", MAX_VALUE_LENGTH)
        if value in seen:
            raise CsmFormError(f"{where} uses the choice value {value!r} twice.")
        seen.add(value)
        options.append({"value": value, "label": _text(o.get("label"), f"{where}, choice {i} label", 300)})
    return options


def _value_list(raw, allowed: set[str], where: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(v, str) or v not in allowed for v in raw):
        raise CsmFormError(f"{where} must list values from the choices above.")
    return list(raw)


def _check(data) -> dict:
    if not isinstance(data, dict):
        raise CsmFormError("The CSM form must be a JSON object.")

    raw_scales = data.get("scales") or {}
    if not isinstance(raw_scales, dict):
        raise CsmFormError("scales must be a JSON object of named scales.")
    scales = {}
    for name, raw in raw_scales.items():
        where = f"Scale {name!r}"
        if not isinstance(raw, dict):
            raise CsmFormError(f"{where} must have options.")
        options = _options(raw.get("options"), where)
        values = {o["value"] for o in options}
        scales[name] = {
            "options": options,
            "satisfied_values": _value_list(raw.get("satisfied_values"), values, f"{where} satisfied_values"),
            "not_applicable_values": _value_list(raw.get("not_applicable_values"), values,
                                                 f"{where} not_applicable_values"),
        }

    sections, seen_ids = [], set()
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise CsmFormError("The CSM form needs at least one section.")
    for s_no, raw_section in enumerate(raw_sections, 1):
        if not isinstance(raw_section, dict) or not isinstance(raw_section.get("questions"), list) \
                or not raw_section["questions"]:
            raise CsmFormError(f"Section {s_no} needs at least one question.")
        questions = []
        for q in raw_section["questions"]:
            qid = q.get("id") if isinstance(q, dict) else None
            if not isinstance(qid, str) or not QUESTION_ID.match(qid):
                raise CsmFormError(f"Section {s_no} has a question without a valid id "
                                   "(letters, digits or _, up to 20).")
            if qid in seen_ids:
                raise CsmFormError(f"The question id {qid} is used twice.")
            seen_ids.add(qid)
            where = f"Question {qid}"
            if "scale" in q:
                scale = scales.get(q["scale"]) if isinstance(q["scale"], str) else None
                if not scale:
                    raise CsmFormError(f"{where} uses an unknown scale {q['scale']!r}.")
                choices = {"scale": q["scale"], **scale}
            else:
                options = _options(q.get("options"), where)
                values = {o["value"] for o in options}
                choices = {
                    "scale": None,
                    "options": options,
                    "satisfied_values": _value_list(q.get("satisfied_values"), values, f"{where} satisfied_values"),
                    "not_applicable_values": _value_list(q.get("not_applicable_values"), values,
                                                         f"{where} not_applicable_values"),
                }
            questions.append({"id": qid, "text": _text(q.get("text"), f"{where} text"), **choices})
        sections.append({
            "heading": _text(raw_section.get("heading"), f"Section {s_no} heading", 200),
            "instructions": _text(raw_section.get("instructions"), f"Section {s_no} instructions",
                                  required=False),
            "questions": questions,
        })

    if len(seen_ids) > MAX_QUESTIONS:
        raise CsmFormError(f"The CSM form may have up to {MAX_QUESTIONS} questions.")

    comment = data.get("comment")
    if comment is not None:
        if not isinstance(comment, dict):
            raise CsmFormError("comment must have a label and a max_length.")
        max_length = comment.get("max_length", 1000)
        if not isinstance(max_length, int) or isinstance(max_length, bool) \
                or not 1 <= max_length <= MAX_COMMENT_LENGTH:
            raise CsmFormError(f"comment max_length must be a number from 1 to {MAX_COMMENT_LENGTH}.")
        comment = {"label": _text(comment.get("label"), "comment label", 300), "max_length": max_length}

    return {
        "version": _text(data.get("version"), "version", MAX_VERSION_LENGTH),
        "placeholder": bool(data.get("placeholder", False)),
        "title": _text(data.get("title"), "title", 200),
        "intro": _text(data.get("intro"), "intro", required=False),
        "sections": sections,
        "comment": comment,
    }
