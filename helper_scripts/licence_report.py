#!/usr/bin/env python3
"""Print the licence of every package in requirements.txt, from installed metadata.

    python helper_scripts/licence_report.py > /tmp/licences.md

Run it in the build environment before each release and compare with
docs/legal/THIRD_PARTY_LICENCES.md. Any GPL or AGPL package, or one with no
licence, must not ship in the desktop build without a decision recorded there.
"""
import importlib.metadata as md
import re
import sys
from pathlib import Path

COPYLEFT = re.compile(r"\b(A?GPL|General Public)", re.I)
LESSER = re.compile(r"\b(LGPL|Lesser|Library)", re.I)


def licence(meta):
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    text = (meta.get("License") or "").strip()
    return text.splitlines()[0][:60] if text else "NOT DECLARED"


def main():
    requirements = Path(__file__).resolve().parent.parent / "requirements.txt"
    problems = 0
    print("| Package | Version | Licence |\n|---|---|---|")
    for line in requirements.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=\[;! ]", line)[0]
        try:
            meta = md.metadata(name)
        except md.PackageNotFoundError:
            print(f"| {name} | not installed | ? |")
            continue
        text = licence(meta)
        flag = ""
        if text == "NOT DECLARED" or (COPYLEFT.search(text) and not LESSER.search(text)):
            flag, problems = " **(review)**", problems + 1
        print(f"| {name} | {meta['Version']} | {text}{flag} |")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
