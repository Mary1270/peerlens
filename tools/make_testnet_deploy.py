#!/usr/bin/env python3
"""Builds contract_testnet_deploy.py from contract_deploy.py by replacing the
time constants only. Usage: make_testnet_deploy.py [--check]"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "contract_deploy.py")
TARGET = os.path.join(ROOT, "contract_testnet_deploy.py")
CHANGES = (
    ("ABSENCE_LAG_SECONDS = 3600", "ABSENCE_LAG_SECONDS = 300"),
    ("MODEL_WINDOW_SECONDS = 3 * 24 * 3600", "MODEL_WINDOW_SECONDS = 3600"),
    ("MIN_DEADLINE_LEAD_SECONDS = 3600", "MIN_DEADLINE_LEAD_SECONDS = 300"),
    ("EVIDENCE_WINDOW_SECONDS = 3 * 24 * 3600", "EVIDENCE_WINDOW_SECONDS = 3600"),
    ("EVIDENCE_GRACE_SECONDS = 24 * 3600", "EVIDENCE_GRACE_SECONDS = 300"),
    ("APPEAL_WINDOW_SECONDS = 2 * 24 * 3600", "APPEAL_WINDOW_SECONDS = 300"),
    ("COMMIT_WINDOW_SECONDS = 2 * 24 * 3600", "COMMIT_WINDOW_SECONDS = 900"),
    ("REVEAL_WINDOW_SECONDS = 1 * 24 * 3600", "REVEAL_WINDOW_SECONDS = 900"),
    ("RESOLUTION_TIMEOUT_SECONDS = 7 * 24 * 3600", "RESOLUTION_TIMEOUT_SECONDS = 3600"),
)


def build():
    text = open(SOURCE).read()
    for old, new in CHANGES:
        if text.count(old) != 1:
            raise SystemExit("constant not found exactly once: " + old)
        text = text.replace(old, new)
    return text


if __name__ == "__main__":
    text = build()
    if "--check" in sys.argv:
        sys.exit(0 if open(TARGET).read() == text else 1)
    open(TARGET, "w").write(text)
    print("wrote", TARGET)
