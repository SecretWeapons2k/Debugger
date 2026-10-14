from pathlib import Path
import shutil
import sys

ROOT = Path.home() / "Debugger"

# Zuordnung vorhandener Dateien zu den fünf Ordnern.
PLAN = {
    "diagnostics": [
        "diagnose_browser_tabs.py",
        "diagnose_code_language.py",
        "diagnose_copy_code.py",
        "diagnose_send.py",
        "inspect_codeblocks.py",
        "inspect_composer.py",
        "inspect_dom.py",
        "inspect_messages.py",
        "inspect_send.py",
        "observe_browser.py",
        "show_scripts.py",
        "chatgpt-debug.png",
        "chatgpt-inspection.png",
        "send-failed.png",
        "send-inspection.png",
    ],
    "backups": [
        "test_chatgpt.py.bak",
        "test_chatgpt.py.bak2",
        "test_chatgpt.py.before_dom_fix",
        "test_chatgpt.py.before-dom-fix.bak",
        "test_chatgpt.py.before-js-send.bak",
        "test_copy_code.py.bak",
        "test_copy_code.py.bak1",
        "test_copy_code.py.bak2",
        "test_copy_code.py.bak.20261014-023901",
        "test_copy_code.py.bak.20261014-024012",
        "test_copy_code.py.before_assistant_fix",
        "test_copy_code.py.before_wait_fix",
    ],
    "logs": [
        "agent-test.log",
        "agent-test.pid",
    ],
    "docs": [
        "Masterplan.txt",
    ],
    "archive": [
        # Bewusst zunächst leer:
        # Unbekannte Dateien werden nicht automatisch verschoben.
    ],
}

def main():
    if not ROOT.is_dir():
        sys.exit(f"Projektverzeichnis nicht gefunden: {ROOT}")

    # Vollständige Vorprüfung: Bei einem Konflikt wird nichts verschoben.
    moves = []
    missing = []

    for folder, filenames in PLAN.items():
        for filename in filenames:
            source = ROOT / filename
            target = ROOT / folder / filename

            if not source.exists():
                if not source.is_symlink():
                    missing.append(filename)
                continue

            if target.exists() or target.is_symlink():
                sys.exit(
                    f"ABBRUCH: Ziel existiert bereits:\n"
                    f"  {target}\n"
                    "Es wurde nichts verschoben."
                )

            moves.append((source, target))

    print("=== VORSCHAU ===")
    for source, target in moves:
        print(f"{source.name} -> {target.parent.name}/")

    if missing:
        print("\nNicht vorhandene Dateien (werden übersprungen):")
        for name in missing:
            print(f"  {name}")

    answer = input(
        f"\n{len(moves)} Dateien verschieben? "
        "Es wird nichts gelöscht. [ja/NEIN]: "
    ).strip()

    if answer != "ja":
        print("Abgebrochen. Es wurde nichts verändert.")
        return

    # Ordner erstellen und Dateien verschieben.
    created_dirs = []
    completed = []

    try:
        for folder in PLAN:
            directory = ROOT / folder
            if not directory.exists():
                directory.mkdir()
                created_dirs.append(directory)

        for source, target in moves:
            shutil.move(str(source), str(target))
            completed.append((source, target))

    except Exception as exc:
        print(f"\nFehler beim Verschieben: {exc}")
        print("Versuche, bereits verschobene Dateien zurückzulegen.")

        for source, target in reversed(completed):
            try:
                shutil.move(str(target), str(source))
            except Exception as rollback_error:
                print(
                    f"RÜCKVERSCHIEBEN FEHLGESCHLAGEN: "
                    f"{target} -> {source}: {rollback_error}"
                )

        print("Bitte die obigen Meldungen prüfen.")
        sys.exit(1)

    print(f"\nFertig: {len(completed)} Dateien verschoben.")
    print("Es wurden keine Dateien gelöscht oder überschrieben.")
    print("\nIm Hauptverzeichnis verbleiben die aktiven Skripte,")
    print("Konfiguration, Cache sowie venv und Browserprofil.")

if __name__ == "__main__":
    main()
