"""
monitor_pipeline.py
---------------------
Runs on its own cron schedule (separate from pipeline.py itself) and checks
two things:
  1. Did the last run actually succeed, or did it crash with a traceback?
  2. Has a run happened recently enough, given the time of day? (catches
     cron silently not firing at all -- laptop asleep, clock wrong, etc.)

If either check fails, it pushes a free notification straight to your phone
via ntfy.sh -- no signup, no email/SMTP setup. You pick a "topic" name (like
a private channel name) and subscribe to it once in the ntfy app; anything
posted to that topic shows up as a phone notification within seconds.

ONE-TIME SETUP (do this before relying on it):
    1. Install the "ntfy" app from the App Store (free), or use ntfy.sh in
       a browser.
    2. Pick a topic name that's hard for a stranger to guess, since anyone
       who knows your topic name could theoretically post to it too --
       e.g. "battergami-alerts-jc4471" rather than something generic.
    3. In the app, subscribe to that exact topic name.
    4. Set NTFY_TOPIC below (or as an environment variable) to that same name.
    5. Test it once manually: python monitor_pipeline.py --test
       You should get a phone notification within a few seconds.

Usage:
    python monitor_pipeline.py            # normal check, only alerts if something's wrong
    python monitor_pipeline.py --test     # forces a test notification regardless of pipeline state
"""

import os
import re
import sys
from datetime import datetime, timedelta

import requests

LOG_PATH = os.environ.get("BATTERGAMI_LOG", "pipeline.log")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "battergami-alerts-jc4471")

# Cron's active window is 12:00-01:00 (see run_battergami.sh schedule).
# If we're inside that window and no run has happened in the last
# MAX_GAP_MINUTES, something's wrong (cron not firing, laptop asleep, etc.)
MAX_GAP_MINUTES = 90

RUN_STARTED_RE = re.compile(r"=== Run started: (.+?) ===")


def send_notification(title: str, message: str, priority: str = "default", tags: str = "") -> bool:
    """Returns True if the notification was actually sent successfully.

    Important: ntfy delivers emoji via the separate 'Tags' header (comma-
    separated short-codes like 'rotating_light', which ntfy renders as the
    matching emoji on your phone) -- NOT by putting raw unicode emoji
    characters directly in the Title/other headers. HTTP headers only
    support a narrow character set (latin-1), so a raw emoji character in
    a header silently breaks the request. The message BODY supports full
    UTF-8 fine; it's specifically headers that are the constraint.
    """
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        # If the notification itself fails, at least leave a local trace --
        # better than failing completely silently a second time.
        print(f"Failed to send notification: {e}")
        return False


def parse_last_run(log_text: str):
    """Returns (timestamp_str, block_text) for the most recent run, or
    (None, None) if the log is empty or has no recognizable runs yet."""
    matches = list(RUN_STARTED_RE.finditer(log_text))
    if not matches:
        return None, None
    last_match = matches[-1]
    timestamp_str = last_match.group(1)
    block_text = log_text[last_match.start():]
    return timestamp_str, block_text


def check():
    if not os.path.exists(LOG_PATH):
        send_notification(
            "Battergami: no log file found",
            f"{LOG_PATH} doesn't exist at all -- the pipeline may have never run.",
            priority="high",
            tags="rotating_light",
        )
        return

    with open(LOG_PATH) as f:
        log_text = f.read()

    timestamp_str, block_text = parse_last_run(log_text)

    if timestamp_str is None:
        send_notification(
            "Battergami: log exists but has no runs recorded",
            "pipeline.log exists but no '=== Run started ===' entries were found in it.",
            priority="high",
            tags="rotating_light",
        )
        return

    # Check 1: did the most recent run crash?
    if "Traceback (most recent call last)" in block_text:
        error_line = ""
        for line in block_text.splitlines():
            if "Error" in line and "File " not in line:
                error_line = line.strip()
                break
        send_notification(
            "Battergami: last run crashed",
            f"Run at {timestamp_str} failed.\n{error_line}",
            priority="urgent",
            tags="rotating_light",
        )
        return

    # Check 2: has a run happened recently enough, given the time of day?
    try:
        # cron's date format: "Wed Jul  8 23:13:40 EDT 2026" -- strip the
        # timezone name since %Z parsing is unreliable across platforms
        cleaned = re.sub(r"\s+[A-Z]{2,5}\s+(\d{4})$", r" \1", timestamp_str.strip())
        last_run_time = datetime.strptime(cleaned, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        # If we can't parse it, don't false-alarm -- just note it and move on
        print(f"Could not parse timestamp: {timestamp_str!r}")
        return

    now = datetime.now()
    hour = now.hour
    in_active_window = (12 <= hour <= 23) or (0 <= hour <= 1)

    if in_active_window:
        gap = now - last_run_time
        if gap > timedelta(minutes=MAX_GAP_MINUTES):
            send_notification(
                "Battergami: no recent run detected",
                f"Last successful run was at {timestamp_str}, "
                f"{int(gap.total_seconds() // 60)} minutes ago. "
                f"Expected a run within the last {MAX_GAP_MINUTES} minutes. "
                f"Laptop may be asleep, or cron may not be firing.",
                priority="urgent",
                tags="rotating_light",
            )
            return

    print(f"OK -- last run at {timestamp_str}, no issues detected.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        ok = send_notification(
            "Battergami monitor test",
            "If you're seeing this, notifications are working correctly.",
            tags="white_check_mark",
        )
        if ok:
            print("Test notification sent successfully -- check your phone.")
        else:
            print("Test notification FAILED to send -- see error above. Check your NTFY_TOPIC and internet connection.")
    else:
        check()