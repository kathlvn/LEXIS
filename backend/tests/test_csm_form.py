r"""Checks for the CSM form file and its loader (checklist #5). Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_csm_form.py
Loads the real app/csm_form.json, then broken copies written to a temp
folder (the real file is never changed).
"""
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from app.core import csm_form  # noqa: E402
from app.core.csm_form import CsmFormError, load_csm_form  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


# --- The real file -------------------------------------------------------
form = load_csm_form()
questions = [q for s in form["sections"] for q in s["questions"]]
ids = [q["id"] for q in questions]
check("real file loads", True)
check("12 questions CC1-CC3 + SQD0-SQD8",
      ids == ["CC1", "CC2", "CC3"] + [f"SQD{i}" for i in range(9)], ids)
check("no longer a placeholder (follows the printed form)", form["placeholder"] is False)
texts = {q["id"]: q["text"] for q in questions}
check("SQD3 as printed", texts["SQD3"] == "The steps (including payment) I needed to do for my transaction were easy and simple.")
check("SQD5 as printed", texts["SQD5"] == "I paid a reasonable amount of fees for my transaction.")
check("SQD8 as printed", texts["SQD8"] == "I got what I needed from the government office or (if denied) denial of request "
                                          "was sufficiently explained to me.")
check("version fits the table", 0 < len(form["version"]) <= csm_form.MAX_VERSION_LENGTH)
sqd0 = questions[3]
check("SQD questions get the agreement scale's 6 choices",
      [o["value"] for o in sqd0["options"]] == ["1", "2", "3", "4", "5", "NA"], sqd0["options"])
check("satisfied = Agree + Strongly agree", sqd0["satisfied_values"] == ["4", "5"])
check("N/A is not counted", sqd0["not_applicable_values"] == ["NA"])
check("CC questions keep their own choices", len(questions[0]["options"]) == 4 and questions[0]["scale"] is None)
check("comment box up to 1000 characters", form["comment"]["max_length"] == 1000)
check("second load uses the cache (same object)", load_csm_form() is form)

# --- Broken copies are refused with a plain message -----------------------
original = json.loads(csm_form.CSM_FORM_PATH.read_text(encoding="utf-8"))
tmp = Path(tempfile.mkdtemp())


def refused(name, change, expect):
    data = copy.deepcopy(original)
    change(data)
    path = tmp / "csm_form.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    try:
        load_csm_form(path)
        check(name, False, "was accepted")
    except CsmFormError as exc:
        check(name, expect in str(exc), str(exc))
    except Exception as exc:  # anything else would be a crash on the client's page
        check(name, False, f"crashed: {type(exc).__name__}: {exc}")


def set_in(path, value):
    def change(data):
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return change


try:
    load_csm_form(tmp / "missing.json")
    check("missing file refused", False, "was accepted")
except CsmFormError as exc:
    check("missing file refused", "was not found" in str(exc), str(exc))

path = tmp / "csm_form.json"
path.write_text('{"version": "x",', encoding="utf-8")
try:
    load_csm_form(path)
    check("invalid JSON refused", False, "was accepted")
except CsmFormError as exc:
    check("invalid JSON refused", "not valid JSON" in str(exc), str(exc))

refused("duplicate question id", set_in(["sections", 1, "questions", 1, "id"], "SQD0"), "used twice")
refused("bad question id", set_in(["sections", 0, "questions", 0, "id"], "CC 1!"), "valid id")
refused("unknown scale", set_in(["sections", 1, "questions", 0, "scale"], "stars"), "unknown scale")
refused("scale given as a list", set_in(["sections", 1, "questions", 0, "scale"], ["agreement"]), "unknown scale")
refused("only one choice", set_in(["sections", 0, "questions", 0, "options"], [{"value": "1", "label": "Yes"}]),
        "at least 2 choices")
refused("duplicate choice value", set_in(["sections", 0, "questions", 0, "options", 1, "value"], "1"), "twice")
refused("choice value too long", set_in(["sections", 0, "questions", 0, "options", 0, "value"], "x" * 11),
        "up to 10")
refused("satisfied value not a choice", set_in(["scales", "agreement", "satisfied_values"], ["9"]),
        "values from the choices")
refused("satisfied values not text", set_in(["scales", "agreement", "satisfied_values"], [{}]),
        "values from the choices")
refused("empty question text", set_in(["sections", 1, "questions", 0, "text"], "  "), "SQD0 text")
refused("version too long", set_in(["version"], "v" * 51), "version")
refused("no sections", set_in(["sections"], []), "at least one section")
refused("comment box too long", set_in(["comment", "max_length"], 5000), "max_length")
refused("scales not an object", set_in(["scales"], ["agreement"]), "scales must be")
refused("more than 50 questions",
        set_in(["sections", 1, "questions"],
               [{"id": f"Q{i}", "scale": "agreement", "text": f"Statement {i}"} for i in range(48)]),
        "up to 50 questions")
path.write_text('["just", "a", "list"]', encoding="utf-8")
try:
    load_csm_form(path)
    check("top level not an object refused", False, "was accepted")
except CsmFormError as exc:
    check("top level not an object refused", "JSON object" in str(exc), str(exc))

# --- An edit to the real file is picked up without a restart ---------------
# Simulated on a copy: point the loader at a temp file, edit it, load again.
saved_path = csm_form.CSM_FORM_PATH
try:
    live = tmp / "live.json"
    live.write_text(json.dumps(original), encoding="utf-8")
    csm_form.CSM_FORM_PATH = live
    csm_form._cache = None
    first = load_csm_form()
    edited = copy.deepcopy(original)
    edited["version"] = "office-2026-1"
    time.sleep(0.05)
    live.write_text(json.dumps(edited), encoding="utf-8")
    os.utime(live, (time.time() + 2, time.time() + 2))  # make sure the modified time changes
    second = load_csm_form()
    check("edited file is re-read", first["version"] != second["version"] == "office-2026-1",
          (first["version"], second["version"]))
finally:
    csm_form.CSM_FORM_PATH = saved_path
    csm_form._cache = None

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
