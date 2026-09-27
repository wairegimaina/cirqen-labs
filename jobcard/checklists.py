"""Work order checklists.

Each equipment type (``EquipmentDescription.checklist_template``) holds an
ordered list of checklist items. When a technician records a work order on a
machine of that type, every item is marked Pass, Fail or N/A, optionally with a
note, and the completed list is stored on the work order (``jobcard.checklist``)
with the item text copied, so later template edits never rewrite a signed
record.
"""

RESULTS = {"pass": "Pass", "fail": "Fail", "na": "N/A"}
MAX_ITEMS = 50
MAX_ITEM_LENGTH = 200
MAX_NOTE_LENGTH = 300


class ChecklistIncomplete(Exception):
    """A required checklist item was left without a result."""


def parse_template(text):
    """One item per non-blank line, trimmed, de-duplicated, capped."""
    items, seen = [], set()
    for line in (text or "").splitlines():
        item = " ".join(line.split())[:MAX_ITEM_LENGTH]
        if item and item.lower() not in seen:
            seen.add(item.lower())
            items.append(item)
    return items[:MAX_ITEMS]


def template_for(equipment):
    """The checklist items for a machine's equipment type (empty when none)."""
    description = getattr(equipment, "description", None)
    items = getattr(description, "checklist_template", None) or []
    return [str(item) for item in items if str(item).strip()]


def completed_from_post(post, items):
    """Build the completed checklist from the submitted form.

    The item texts come from the template on the server, never from the form;
    the form only supplies ``checklist_result_<n>`` and ``checklist_note_<n>``
    for each position. Every item needs a result.
    """
    completed, missing = [], []
    for n, item in enumerate(items):
        result = (post.get(f"checklist_result_{n}") or "").strip().lower()
        if result not in RESULTS:
            missing.append(item)
            continue
        note = " ".join((post.get(f"checklist_note_{n}") or "").split())[:MAX_NOTE_LENGTH]
        completed.append({"item": item, "result": result, "note": note})
    if missing:
        shown = ", ".join(f'"{m}"' for m in missing[:3])
        more = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
        raise ChecklistIncomplete(f"Mark every checklist item Pass, Fail or N/A. Missing: {shown}{more}.")
    return completed


def summary(checklist):
    """Counts for display, e.g. {"pass": 5, "fail": 1, "na": 0, "total": 6}."""
    counts = {key: 0 for key in RESULTS}
    for entry in checklist or []:
        if entry.get("result") in counts:
            counts[entry["result"]] += 1
    counts["total"] = sum(counts.values())
    return counts
