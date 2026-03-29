#!/usr/bin/env python3
import argparse
import csv
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError

DEFAULT_MESSAGE_TEXT = """Beste familie en vrienden van Fred en Nathalie.
Ik zal me even voorstellen ik ben de moeder van Nathalie.
Als het goed is hebben jullie de uitnodiging voor 26 juni 2026 ontvangen.
Daarom heb ik iets bedacht, wat eigenlijk als grap bedoeld is.
Ik wil U graag vragen of U voor Fred en Nathalie een peper en zoutstelletje wil kopen of zelf in elkaar wil knutselen?
Dit hoeft echt niet duur te zijn, want het is bedoeld als grapje uiteraard.
En misschien wilt U daar dan ook een (kaartje of foto of briefje) bijvoegen, wat de connectie is van U met het bruidspaar.
(b.v. een leuke herinnering of gebeurtenis).
Maakt niet uit als het maar leuk is.
Deze peper en zoutstelletjes worden dan bij aankomst in een mand gedaan, en na de ceremonie aan het bruidspaar aangeboden.
Onder het mom van Hartelijk bedankt voor het peper en zoutstel van Andre van Duin en Corry van Gorp.
Dit liedje zal dan gedraaid worden.
Vergeet vooral Uw naam niet te vermelden op het pakje.
Mocht U nog vragen hebben hierover.
Mijn tel.nr. is 0651777287 of mijn emailadres is jg.vanderhorst@telfort.nl
Hopelijk doet U allemaal mee, en krijgen ze heel veel peper en zoutstelletjes.

Alvast bedankt, groetjes José van der Laan."""

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
REQUIRED_COLUMNS = ("name", "phone")
DEFAULT_CONFIG = {
    "min_delay_seconds": 4.0,
    "max_delay_seconds": 8.0,
    "test_first_n": 2,
}


@dataclass
class RecipientRow:
    row_number: int
    name: str
    original_phone: str
    normalized_phone: str
    status: str
    reason: str


@dataclass
class Recipient:
    name: str
    phone: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_config(config_path: Path) -> dict:
    config = DEFAULT_CONFIG.copy()
    if not config_path.exists():
        return config

    with config_path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)

    for key in DEFAULT_CONFIG:
        if key in loaded:
            config[key] = loaded[key]

    if config["min_delay_seconds"] > config["max_delay_seconds"]:
        raise ValueError("config.json has min_delay_seconds greater than max_delay_seconds")

    return config


def normalize_phone(value: str) -> str:
    cleaned = re.sub(r"[\s\-().]", "", value or "")
    return cleaned


def repair_common_mojibake(text: str) -> str:
    # If UTF-8 text was accidentally decoded as cp1252/latin1, repair it.
    suspicious_markers = ("Ã", "â", "ðŸ", "Â")
    if not any(marker in text for marker in suspicious_markers):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return text
    return repaired


def load_message_text(message_path: Path) -> str:
    if not message_path.exists():
        return DEFAULT_MESSAGE_TEXT

    raw = message_path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            decoded = raw.decode(encoding)
            decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")
            decoded = repair_common_mojibake(decoded)
            if decoded.strip():
                return decoded
        except UnicodeDecodeError:
            continue

    raise ValueError(f"Could not decode message file: {message_path}")


def render_message_for_recipient(message_text: str, recipient: Recipient) -> str:
    # Personalization token for Message.txt templates.
    return message_text.replace("{name}", recipient.name)


def validate_e164(phone: str) -> bool:
    return bool(E164_RE.fullmatch(phone))


def load_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV appears empty. Add header row: name,phone")

        lower_fields = [name.strip().lower() for name in reader.fieldnames]
        missing = [col for col in REQUIRED_COLUMNS if col not in lower_fields]
        if missing:
            raise ValueError(
                "CSV must include columns: name,phone. "
                f"Missing: {', '.join(missing)}. "
                "Current header row looks different."
            )

        field_map = {name.strip().lower(): name for name in reader.fieldnames}
        rows: list[dict[str, str]] = []
        for idx, row in enumerate(reader, start=2):
            rows.append(
                {
                    "row_number": str(idx),
                    "name": (row.get(field_map["name"]) or "").strip(),
                    "phone": (row.get(field_map["phone"]) or "").strip(),
                }
            )

    return rows


def build_preview(rows: Iterable[dict[str, str]]) -> tuple[list[RecipientRow], list[Recipient], int]:
    seen_valid_phones: set[str] = set()
    preview: list[RecipientRow] = []
    valid_unique: list[Recipient] = []
    invalid_count = 0

    for row in rows:
        row_number = int(row["row_number"])
        name = row["name"]
        original_phone = row["phone"]
        normalized_phone = normalize_phone(original_phone)

        if not name:
            invalid_count += 1
            preview.append(
                RecipientRow(
                    row_number=row_number,
                    name=name,
                    original_phone=original_phone,
                    normalized_phone=normalized_phone,
                    status="invalid",
                    reason="name is empty",
                )
            )
            continue

        if not validate_e164(normalized_phone):
            invalid_count += 1
            preview.append(
                RecipientRow(
                    row_number=row_number,
                    name=name,
                    original_phone=original_phone,
                    normalized_phone=normalized_phone,
                    status="invalid",
                    reason="phone is not valid E.164 (+countrycode...) format",
                )
            )
            continue

        if normalized_phone in seen_valid_phones:
            preview.append(
                RecipientRow(
                    row_number=row_number,
                    name=name,
                    original_phone=original_phone,
                    normalized_phone=normalized_phone,
                    status="duplicate",
                    reason="duplicate phone (already listed earlier)",
                )
            )
            continue

        seen_valid_phones.add(normalized_phone)
        valid_unique.append(Recipient(name=name, phone=normalized_phone))
        preview.append(
            RecipientRow(
                row_number=row_number,
                name=name,
                original_phone=original_phone,
                normalized_phone=normalized_phone,
                status="valid",
                reason="",
            )
        )

    return preview, valid_unique, invalid_count


def write_preview(preview_path: Path, preview_rows: list[RecipientRow]) -> None:
    with preview_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "row_number",
                "name",
                "original_phone",
                "normalized_phone",
                "status",
                "reason",
            ]
        )
        for row in preview_rows:
            writer.writerow(
                [
                    row.row_number,
                    row.name,
                    row.original_phone,
                    row.normalized_phone,
                    row.status,
                    row.reason,
                ]
            )


def ensure_send_log(log_path: Path) -> None:
    if log_path.exists():
        return

    with log_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_utc", "name", "phone", "status", "reason"])


def append_send_log(log_path: Path, name: str, phone: str, status: str, reason: str) -> None:
    with log_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([utc_now_iso(), name, phone, status, reason])


def load_previously_sent(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()

    sent: set[str] = set()
    with log_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("status") or "").strip().lower() == "sent":
                phone = (row.get("phone") or "").strip()
                if phone:
                    sent.add(phone)
    return sent


def wait_for_whatsapp_ready(page) -> None:
    page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

    # If already logged in, chat UI appears quickly.
    try:
        page.wait_for_selector("#pane-side", timeout=7000)
        return
    except PlaywrightTimeoutError:
        pass

    print("WhatsApp login required. Scan QR code in the opened browser window...")
    page.wait_for_selector("#pane-side", timeout=120000)


def _chat_not_available(page) -> bool:
    not_available_texts = [
        "Phone number shared via url is invalid",
        "The phone number shared via url is invalid",
        "Deze telefoonnummer is ongeldig",
        "Geen WhatsApp-account",
    ]
    for text in not_available_texts:
        if page.get_by_text(text).count() > 0:
            return True
    return False


def send_message_once(page, recipient: Recipient, message_text: str) -> tuple[bool, str]:
    phone_digits = recipient.phone.lstrip("+")
    url = f"https://web.whatsapp.com/send?phone={phone_digits}"
    page.goto(url, wait_until="domcontentloaded")

    if _chat_not_available(page):
        return False, "chat not available or invalid phone in WhatsApp"

    compose_selector = "footer div[contenteditable='true']"
    page.wait_for_selector(compose_selector, timeout=30000)
    compose_box = page.locator(compose_selector).first
    compose_box.click(timeout=3000)

    # Insert message directly into the compose box to avoid oversized URL issues.
    personalized_message = render_message_for_recipient(message_text, recipient)
    page.keyboard.insert_text(personalized_message)

    send_button = page.locator("button:has(span[data-icon='send'])")
    if send_button.count() > 0:
        send_button.first.click(timeout=6000)
    else:
        page.keyboard.press("Enter")

    # Wait a moment to let UI dispatch the message.
    time.sleep(1.5)
    return True, "sent"


def send_with_retry(context, page, recipient: Recipient, message_text: str) -> tuple[bool, str, object]:
    for attempt in range(1, 3):
        try:
            ok, reason = send_message_once(page, recipient, message_text)
            if ok:
                return True, reason, page
            if attempt == 2:
                return False, reason, page
        except PlaywrightTimeoutError as exc:
            if attempt == 2:
                return False, f"timeout: {exc}", page
            time.sleep(2)
        except Exception as exc:  # noqa: BLE001
            exc_text = str(exc)
            if "Page crashed" in exc_text and attempt < 2:
                # Recover by opening a fresh tab in the same logged-in context.
                try:
                    if not page.is_closed():
                        page.close()
                except Exception:
                    pass
                page = context.new_page()
                wait_for_whatsapp_ready(page)
                time.sleep(1)
                continue
            if attempt == 2:
                return False, f"unexpected error: {exc}", page
            time.sleep(2)

    return False, "unknown failure", page


def select_recipients(mode: str, pending: list[Recipient], test_first_n: int) -> list[Recipient]:
    if mode == "test-send":
        return pending[:test_first_n]
    if mode == "full-send":
        return pending
    return []


def send_recipients_batch(
    context, page, recipients: list[Recipient], config: dict, log_path: Path, message_text: str
) -> tuple[int, int, object]:
    sent_count = 0
    failed_count = 0
    min_delay = float(config["min_delay_seconds"])
    max_delay = float(config["max_delay_seconds"])

    for idx, recipient in enumerate(recipients, start=1):
        print(f"[{idx}/{len(recipients)}] Sending to {recipient.name} ({recipient.phone})...")
        ok, reason, page = send_with_retry(context, page, recipient, message_text)
        if ok:
            sent_count += 1
            append_send_log(log_path, recipient.name, recipient.phone, "sent", reason)
            print("  SENT")
        else:
            failed_count += 1
            append_send_log(log_path, recipient.name, recipient.phone, "failed", reason)
            print(f"  FAILED: {reason}")

        if idx < len(recipients):
            delay = random.uniform(min_delay, max_delay)
            print(f"  Waiting {delay:.1f}s...")
            time.sleep(delay)

    return sent_count, failed_count, page


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch WhatsApp sender with strict safety checks.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate CSV and generate preview.csv only")
    mode.add_argument("--test-send", action="store_true", help="Send first N recipients only")
    mode.add_argument("--full-send", action="store_true", help="Send all pending recipients")
    parser.add_argument("--csv", default="recipients.csv", help="Path to recipients CSV (default: recipients.csv)")
    parser.add_argument("--preview", default="preview.csv", help="Path to output preview CSV")
    parser.add_argument("--log", default="send_log.csv", help="Path to send log CSV")
    parser.add_argument("--config", default="config.json", help="Optional config JSON path")
    parser.add_argument(
        "--message-file",
        default="Message.txt",
        help="Path to message text file (default: Message.txt). If missing, built-in fallback text is used.",
    )
    parser.add_argument(
        "--profile-dir",
        default=".whatsapp_profile",
        help="Persistent browser profile directory (default: .whatsapp_profile)",
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "msedge"],
        default="chromium",
        help="Browser engine for Playwright persistent context",
    )
    return parser.parse_args()


def determine_mode(args: argparse.Namespace) -> str:
    if args.dry_run:
        return "dry-run"
    if args.test_send:
        return "test-send"
    return "full-send"


def launch_context_with_fallback(playwright, profile_dir: Path, browser: str):
    launch_attempts = []

    channel = "msedge" if browser == "msedge" else None
    launch_attempts.append(
        {
            "name": f"{browser} default",
            "kwargs": {
                "user_data_dir": str(profile_dir),
                "headless": False,
                "viewport": {"width": 1280, "height": 900},
                **({"channel": channel} if channel else {}),
            },
        }
    )

    launch_attempts.append(
        {
            "name": f"{browser} safe-flags",
            "kwargs": {
                "user_data_dir": str(profile_dir),
                "headless": False,
                "viewport": {"width": 1280, "height": 900},
                "args": [
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                **({"channel": channel} if channel else {}),
            },
        }
    )

    # Final fallback: clean profile directory with safe flags.
    clean_profile = profile_dir.parent / f"{profile_dir.name}_clean"
    clean_profile.mkdir(exist_ok=True)
    launch_attempts.append(
        {
            "name": f"{browser} clean-profile safe-flags",
            "kwargs": {
                "user_data_dir": str(clean_profile),
                "headless": False,
                "viewport": {"width": 1280, "height": 900},
                "args": [
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                **({"channel": channel} if channel else {}),
            },
        }
    )

    errors: list[str] = []
    for attempt in launch_attempts:
        try:
            context = playwright.chromium.launch_persistent_context(**attempt["kwargs"])
            print(f"Browser launch strategy: {attempt['name']}")
            return context
        except PlaywrightError as exc:
            errors.append(f"{attempt['name']}: {exc}")

    raise RuntimeError("Failed to launch a stable browser context.\n" + "\n".join(errors))


def main() -> int:
    args = parse_args()
    mode = determine_mode(args)

    csv_path = Path(args.csv)
    preview_path = Path(args.preview)
    log_path = Path(args.log)
    config_path = Path(args.config)
    message_path = Path(args.message_file)

    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        return 1

    try:
        config = read_config(config_path)
        rows = load_csv_rows(csv_path)
        message_text = load_message_text(message_path)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 1

    preview_rows, valid_unique, invalid_count = build_preview(rows)
    write_preview(preview_path, preview_rows)

    duplicate_count = sum(1 for r in preview_rows if r.status == "duplicate")
    print(f"Preview written: {preview_path}")
    print(f"Total rows: {len(preview_rows)}")
    print(f"Valid unique recipients: {len(valid_unique)}")
    print(f"Invalid rows: {invalid_count}")
    print(f"Duplicates skipped: {duplicate_count}")

    if invalid_count > 0:
        print("Validation failed. Fix invalid rows in CSV and rerun.")
        return 1

    if mode == "dry-run":
        print("Dry-run complete. No messages were sent.")
        return 0

    ensure_send_log(log_path)
    already_sent = load_previously_sent(log_path)

    pending: list[Recipient] = []
    for recipient in valid_unique:
        if recipient.phone in already_sent:
            append_send_log(log_path, recipient.name, recipient.phone, "skipped", "already sent previously")
        else:
            pending.append(recipient)

    to_send = select_recipients(mode, pending, int(config["test_first_n"]))

    if not to_send:
        print("Nothing to send. All valid recipients are already marked as sent.")
        return 0

    user_data_dir = Path(args.profile_dir)
    user_data_dir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        try:
            context = launch_context_with_fallback(p, user_data_dir, args.browser)
        except Exception as exc:  # noqa: BLE001
            print("ERROR: Could not launch a stable browser for Playwright.")
            print("Run: python -m playwright install chromium")
            print("Optional fallback: run with --browser msedge if installed.")
            print(f"Details: {exc}")
            return 1

        page = context.pages[0] if context.pages else context.new_page()

        try:
            wait_for_whatsapp_ready(page)
        except PlaywrightTimeoutError:
            print("ERROR: WhatsApp did not become ready in time (QR login timeout).")
            context.close()
            return 1

        print(f"Sending mode: {mode}")
        print(f"Recipients in this run: {len(to_send)}")
        sent_count, failed_count, page = send_recipients_batch(
            context, page, to_send, config, log_path, message_text
        )

        if mode == "test-send":
            remaining_recipients = pending[len(to_send):]
            if remaining_recipients:
                response = input(
                    f"Test send completed for {len(to_send)} contacts. "
                    f"Type CONTINUE to send remaining {len(remaining_recipients)} now: "
                ).strip()
                if response == "CONTINUE":
                    print(f"Continuing with remaining {len(remaining_recipients)} recipients...")
                    add_sent, add_failed, page = send_recipients_batch(
                        context, page, remaining_recipients, config, log_path, message_text
                    )
                    sent_count += add_sent
                    failed_count += add_failed
                else:
                    print("Stopped after test-send as requested.")

        context.close()

    print(f"Run complete. Sent={sent_count}, Failed={failed_count}, Log={log_path}")
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
