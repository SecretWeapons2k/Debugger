import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE = Path.home() / "Debugger"
PROFILE = BASE / "brave-agent-profile"
CONFIG_FILE = BASE / "copy_test_config.txt"
CACHE_FILE = BASE / "codeblock_cache.json"

CHAT_URL = "https://chatgpt.com/"
INPUT_SELECTOR = '[role="textbox"][aria-label="Ask ChatGPT"]'
SEND_SELECTOR = 'button[aria-label="Send"]'
BLOCK_SELECTOR = '[data-code-block="true"]'
COPY_SELECTOR = 'button[aria-label="Copy code"]'


def timestamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Konfiguration fehlt: {CONFIG_FILE}")

    with CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config.get("expected_blocks"), list):
        raise ValueError("expected_blocks muss eine Liste sein.")

    if not config["expected_blocks"]:
        raise ValueError("Die Konfiguration enthält keine erwarteten Codeblöcke.")

    if not config.get("prompt"):
        raise ValueError("Der Testprompt fehlt.")

    return config


def load_cache():
    if not CACHE_FILE.exists():
        return {"version": 1, "tests": {}}

    try:
        with CACHE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Der Cache ist kein JSON-Objekt.")

        data.setdefault("version", 1)
        data.setdefault("tests", {})

        if not isinstance(data["tests"], dict):
            raise ValueError("Der Cache-Eintrag 'tests' ist ungültig.")

        return data

    except Exception as exc:
        raise RuntimeError(
            f"Der bestehende Cache konnte nicht sicher gelesen werden: {exc}"
        ) from exc


def save_cache(cache):
    temporary_file = CACHE_FILE.with_suffix(".json.tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(cache, file, ensure_ascii=False, indent=2)

    temporary_file.replace(CACHE_FILE)


def normalize_text(value):
    return value.replace("\r\n", "\n").strip()


def get_block_language(block):
    # 1. Bevorzugt das explizite Sprachattribut des Editors.
    language_element = block.locator("[data-language]").first

    if language_element.count() > 0:
        language = language_element.get_attribute("data-language")
        if language:
            return language.strip().lower()

    # 2. Fallback: sichtbare Sprachbezeichnung in der Kopfzeile.
    header = block.locator(
        '[data-markdown-copy="exclude"] .truncate'
    ).first

    if header.count() > 0:
        label = (header.inner_text() or "").strip().lower()

        aliases = {
            "python": "python",
            "bash": "bash",
            "sh": "bash",
            "shell": "bash",
            "javascript": "javascript",
            "js": "javascript",
            "json": "json",
        }

        return aliases.get(label, label)

    return ""


def get_block_code(block):
    content = block.locator(".cm-content").first

    if content.count() > 0:
        return normalize_text(content.inner_text())

    pre = block.locator("pre").first

    if pre.count() > 0:
        return normalize_text(pre.inner_text())

    return normalize_text(block.inner_text())


def main():
    started = time.monotonic()
    test_id = "COPYTEST-" + timestamp()

    config = load_config()
    cache = load_cache()

    marker = f"{config.get('marker', 'COPYTEST')}-{test_id}"
    prompt = config["prompt"].replace(
        str(config.get("marker", "COPYTEST")),
        marker,
    )
    expected_blocks = config["expected_blocks"]

    entry = {
        "test_id": test_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "marker": marker,
        "config_file": str(CONFIG_FILE),
        "status": "running",
        "events": [],
        "blocks": [],
    }

    cache["tests"][test_id] = entry
    save_cache(cache)

    def record(name, status, details=""):
        event = {
            "name": name,
            "status": status,
            "details": str(details),
            "time": datetime.now().isoformat(timespec="seconds"),
        }

        entry["events"].append(event)
        save_cache(cache)

        suffix = f" — {details}" if details else ""
        print(f"[{status}] {name}{suffix}")

    browser_context = None

    try:
        with sync_playwright() as playwright:
            browser_context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                executable_path="/snap/bin/brave",
                headless=False,
                chromium_sandbox=True,
                no_viewport=True,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--start-maximized",
                ],
            )

            browser_context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://chatgpt.com",
            )

            pages = browser_context.pages
            page = pages[0] if pages else browser_context.new_page()

            if "chatgpt.com" not in page.url:
                page.goto(CHAT_URL, wait_until="domcontentloaded")

            page.wait_for_selector(INPUT_SELECTOR, timeout=60000)
            record("ChatGPT geöffnet", "PASS", page.url)

            initial_block_count = page.locator(BLOCK_SELECTOR).count()
            record(
                "Ausgangszustand erfasst",
                "PASS",
                f"Vorhandene Codeblöcke: {initial_block_count}",
            )

            input_box = page.locator(INPUT_SELECTOR).first
            input_box.fill(prompt)

            send_button = page.locator(SEND_SELECTOR).last
            send_button.wait_for(state="visible", timeout=15000)

            if not send_button.is_enabled():
                raise RuntimeError("Der Senden-Button ist deaktiviert.")

            send_button.click()
            record("Testprompt abgeschickt", "PASS", marker)

            required_count = initial_block_count + len(expected_blocks)
            deadline = time.monotonic() + 120
            last_count = -1
            stable_since = None

            while time.monotonic() < deadline:
                current_count = page.locator(BLOCK_SELECTOR).count()

                if current_count >= required_count:
                    if current_count == last_count:
                        if stable_since is None:
                            stable_since = time.monotonic()
                        elif time.monotonic() - stable_since >= 2.0:
                            break
                    else:
                        stable_since = None

                last_count = current_count
                time.sleep(0.5)

            final_count = page.locator(BLOCK_SELECTOR).count()
            new_count = max(0, final_count - initial_block_count)

            entry["observed_new_blocks"] = new_count
            save_cache(cache)

            if new_count == len(expected_blocks):
                record("Anzahl der Codeblöcke", "PASS", f"{new_count} erkannt")
            else:
                record(
                    "Anzahl der Codeblöcke",
                    "FAIL",
                    f"{len(expected_blocks)} erwartet, {new_count} erkannt",
                )

            blocks = page.locator(BLOCK_SELECTOR)

            for index, expected in enumerate(expected_blocks):
                block_result = {
                    "index": index + 1,
                    "expected_language": expected["language"],
                    "expected_code": expected["code"],
                    "checks": {},
                }

                entry["blocks"].append(block_result)
                save_cache(cache)

                print(f"\n--- Codeblock {index + 1} ---")

                if index >= new_count:
                    block_result["status"] = "FAIL"
                    block_result["error"] = "Der erwartete Codeblock fehlt."
                    record(
                        f"Codeblock {index + 1}",
                        "FAIL",
                        "Nicht vorhanden",
                    )
                    save_cache(cache)
                    continue

                block = blocks.nth(initial_block_count + index)

                try:
                    actual_language = get_block_language(block)
                    actual_code = get_block_code(block)

                    block_result["actual_language"] = actual_language
                    block_result["actual_code"] = actual_code

                    language_ok = (
                        actual_language.lower()
                        == expected["language"].lower()
                    )
                    code_ok = (
                        normalize_text(actual_code)
                        == normalize_text(expected["code"])
                    )

                    block_result["checks"]["language"] = (
                        "PASS" if language_ok else "FAIL"
                    )
                    block_result["checks"]["code_content"] = (
                        "PASS" if code_ok else "FAIL"
                    )

                    record(
                        f"Block {index + 1}: Sprache",
                        "PASS" if language_ok else "FAIL",
                        f"erwartet={expected['language']!r}, "
                        f"erkannt={actual_language!r}",
                    )

                    record(
                        f"Block {index + 1}: Inhalt",
                        "PASS" if code_ok else "FAIL",
                        "Inhalt stimmt überein"
                        if code_ok
                        else "Inhalt weicht ab",
                    )

                    try:
                        copy_button = block.locator(COPY_SELECTOR).first
                        copy_button.wait_for(state="visible", timeout=10000)
                        copy_button.click()

                        time.sleep(0.3)

                        clipboard_text = page.evaluate(
                            "() => navigator.clipboard.readText()"
                        )
                        clipboard_ok = (
                            normalize_text(clipboard_text)
                            == normalize_text(expected["code"])
                        )

                        block_result["clipboard_content"] = clipboard_text
                        block_result["checks"]["clipboard"] = (
                            "PASS" if clipboard_ok else "FAIL"
                        )

                        record(
                            f"Block {index + 1}: Zwischenablage",
                            "PASS" if clipboard_ok else "FAIL",
                            "Inhalt stimmt überein"
                            if clipboard_ok
                            else "Zwischenablage weicht ab",
                        )

                    except Exception as exc:
                        block_result["checks"]["clipboard"] = "ERROR"
                        block_result["clipboard_error"] = str(exc)

                        record(
                            f"Block {index + 1}: Zwischenablage",
                            "ERROR",
                            exc,
                        )

                except Exception as exc:
                    block_result["status"] = "ERROR"
                    block_result["error"] = str(exc)

                    record(
                        f"Block {index + 1}: Prüfung",
                        "ERROR",
                        exc,
                    )

                checks = block_result.get("checks", {}).values()

                if checks and all(value == "PASS" for value in checks):
                    block_result["status"] = "PASS"
                else:
                    block_result["status"] = "FAIL"

                save_cache(cache)

            statuses = [
                block.get("status", "FAIL")
                for block in entry["blocks"]
            ]

            all_blocks_pass = (
                len(statuses) == len(expected_blocks)
                and all(status == "PASS" for status in statuses)
                and new_count == len(expected_blocks)
            )

            entry["status"] = "passed" if all_blocks_pass else "failed"

    except PlaywrightTimeoutError as exc:
        entry["status"] = "failed"
        entry["fatal_error"] = f"Playwright-Timeout: {exc}"
        record("Abbruch durch Timeout", "ERROR", exc)

    except Exception as exc:
        entry["status"] = "failed"
        entry["fatal_error"] = str(exc)
        entry["traceback"] = traceback.format_exc()

        record("Testfehler", "ERROR", exc)

    finally:
        if browser_context is not None:
            try:
                browser_context.close()
            except Exception:
                pass

        entry["duration_seconds"] = round(time.monotonic() - started, 2)
        entry["finished_at"] = datetime.now().isoformat(timespec="seconds")

        save_cache(cache)

        print("\n========== TESTBERICHT ==========")
        print(f"Test-ID:       {test_id}")
        print(f"Status:        {entry['status']}")
        print(f"Dauer:         {entry['duration_seconds']} Sekunden")
        print(f"Ergebnisse:    {CACHE_FILE}")

        for block in entry.get("blocks", []):
            print(
                f"Block {block['index']}: "
                f"{block.get('status', 'FAIL')} "
                f"({block['expected_language']})"
            )

        if entry.get("fatal_error"):
            print(f"Fehler:        {entry['fatal_error']}")

        print("==================================")


if __name__ == "__main__":
    main()