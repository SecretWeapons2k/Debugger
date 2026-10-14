from pathlib import Path
from time import monotonic
from playwright.sync_api import sync_playwright

PROFILE = Path.home() / "Debugger" / "brave-agent-profile"
URL = "https://chatgpt.com/"
DURATION = 120

START = monotonic()

def log(message):
    elapsed = monotonic() - START
    print(f"[{elapsed:7.2f}s] {message}", flush=True)

OBSERVER_JS = r"""
(() => {
    if (window.__agentObserverInstalled) return;
    window.__agentObserverInstalled = true;

    const sendSelector = [
        'button[data-testid*="send"]',
        'button[aria-label*="Send"]',
        'button[aria-label*="send"]',
        'button[aria-label*="Senden"]'
    ].join(',');

    const composerSelector = [
        '#pending-home-input',
        'textarea',
        '[contenteditable="true"]'
    ].join(',');

    function describe(el) {
        if (!el) return null;

        return {
            tag: el.tagName,
            id: el.id || null,
            testid: el.getAttribute('data-testid'),
            ariaLabel: el.getAttribute('aria-label'),
            role: el.getAttribute('role'),
            disabled: !!el.disabled,
            ariaDisabled: el.getAttribute('aria-disabled'),
            visible: !!(el.getClientRects().length),
            textLength: typeof el.value === 'string'
                ? el.value.length
                : (el.innerText || '').length
        };
    }

    function report(type, details) {
        console.log('[AGENT-OBS] ' + JSON.stringify({
            type,
            time: Math.round(performance.now()),
            ...details
        }));
    }

    // Protokolliert Klicks auf Buttons, ohne Nachrichtentexte zu speichern.
    document.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button) return;

        report('CLICK', {
            button: describe(button),
            target: event.target.tagName
        });
    }, true);

    // Protokolliert Tastaturereignisse nur für Enter.
    // Andere gedrückte Tasten und eingegebener Text werden nicht gespeichert.
    document.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;

        const target = event.target;
        if (!target.matches(composerSelector)) return;

        report('ENTER', {
            target: describe(target),
            shiftKey: event.shiftKey,
            ctrlKey: event.ctrlKey,
            metaKey: event.metaKey,
            defaultPreventedBefore: event.defaultPrevented
        });

        setTimeout(() => {
            report('ENTER-AFTER', {
                defaultPrevented: event.defaultPrevented
            });
        }, 0);
    }, true);

    // Erkennt Formularübermittlungen.
    document.addEventListener('submit', event => {
        report('FORM-SUBMIT', {
            form: describe(event.target),
            submitter: describe(event.submitter)
        });
    }, true);

    // Protokolliert nur die Länge, niemals den eingegebenen Inhalt.
    document.addEventListener('input', event => {
        if (!event.target.matches(composerSelector)) return;

        report('INPUT', {
            target: describe(event.target)
        });
    }, true);

    function snapshot(reason) {
        const composers = [
            ...document.querySelectorAll(composerSelector)
        ].filter(el => el.getClientRects().length);

        const buttons = [
            ...document.querySelectorAll(sendSelector)
        ].filter(el => el.getClientRects().length);

        report('SNAPSHOT', {
            reason,
            url: location.origin,
            composers: composers.map(describe),
            sendButtons: buttons.map(describe)
        });
    }

    // Erkennt Veränderungen am DOM, insbesondere am Senden-Button.
    let lastButtonState = '';

    const observer = new MutationObserver(() => {
        const buttons = [
            ...document.querySelectorAll(sendSelector)
        ].filter(el => el.getClientRects().length);

        const state = JSON.stringify(buttons.map(describe));

        if (state !== lastButtonState) {
            lastButtonState = state;
            report('SEND-BUTTON-CHANGE', {
                buttons: buttons.map(describe)
            });
        }
    });

    observer.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: [
            'disabled',
            'aria-disabled',
            'aria-label',
            'data-testid',
            'class'
        ]
    });

    window.__agentObserverSnapshot = snapshot;
    snapshot('Observer installiert');
})();
"""

with sync_playwright() as p:
    log("Starte Brave mit dem bestehenden Agent-Profil.")
    log("Falls Brave mit diesem Profil bereits läuft, schließe es zuerst.")

    try:
        context = p.chromium.launch_persistent_context(
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
    except Exception as exc:
        log(f"FEHLER beim Starten von Brave: {type(exc).__name__}: {exc}")
        log(
            "Falls Brave bereits mit demselben Profil läuft, "
            "schließe es vollständig und starte erneut."
        )
        raise SystemExit(1)

    try:
        page = context.pages[0] if context.pages else context.new_page()

        page.on(
            "console",
            lambda msg: print(msg.text, flush=True)
            if "[AGENT-OBS]" in msg.text
            else None
        )

        page.add_init_script(OBSERVER_JS)

        log("Öffne ChatGPT.")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        # Auch dann installieren, wenn die Seite bereits geöffnet war.
        page.evaluate(OBSERVER_JS)

        log("=" * 60)
        log("BEOBACHTUNG AKTIV – du bist jetzt dran.")
        log("1. Tippe eine kurze Testnachricht in ChatGPT.")
        log("2. Drücke selbst auf den Senden-Button.")
        log("3. Warte, bis die Nachricht im Chat erscheint.")
        log(f"Das Skript beobachtet insgesamt {DURATION} Sekunden.")
        log("=" * 60)

        deadline = monotonic() + DURATION

        while monotonic() < deadline:
            page.wait_for_timeout(500)

        page.evaluate("""
            () => window.__agentObserverSnapshot?.(
                'Ende der Beobachtung'
            )
        """)

        log("Beobachtung beendet.")

    except KeyboardInterrupt:
        log("Beobachtung manuell abgebrochen.")

    except Exception as exc:
        log(f"FEHLER: {type(exc).__name__}: {exc}")

    finally:
        context.close()
        log("Brave geschlossen.")
