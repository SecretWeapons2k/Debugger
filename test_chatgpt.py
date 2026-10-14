from pathlib import Path
from datetime import datetime
from time import monotonic, sleep
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

PROFILE = Path.home() / "Debugger" / "brave-agent-profile"
MARKER = datetime.now().strftime("DEBUGGER-%Y%m%d-%H%M%S")
TEST_MESSAGE = (
    "Antworte exakt mit AGENT_TEST_OK und sonst nichts. "
    f"Testkennung: {MARKER}"
)

START = monotonic()


def log(message):
    print(f"[{monotonic() - START:6.2f}s] {message}", flush=True)


# Nur Metadaten untersuchen, keine Chat-Inhalte oder Zugangsdaten ausgeben.
STORAGE_INSPECTION = r"""
async () => {
    const result = {
        origin: location.origin,
        localStorage: [],
        sessionStorage: [],
        indexedDB: [],
        cacheStorage: []
    };

    for (const [name, storage] of [
        ["localStorage", localStorage],
        ["sessionStorage", sessionStorage]
    ]) {
        try {
            const entries = [];
            for (let i = 0; i < storage.length; i++) {
                const key = storage.key(i);
                entries.push({
                    key: key,
                    valueLength: (storage.getItem(key) || "").length
                });
            }
            result[name] = entries;
        } catch (e) {
            result[name] = {error: String(e)};
        }
    }

    try {
        if (indexedDB.databases) {
            const databases = await indexedDB.databases();

            for (const info of databases) {
                if (!info.name) continue;

                const details = {
                    name: info.name,
                    version: info.version,
                    stores: []
                };

                try {
                    const db = await new Promise((resolve, reject) => {
                        const request = indexedDB.open(info.name);
                        request.onsuccess = () => resolve(request.result);
                        request.onerror = () => reject(request.error);
                        request.onblocked = () => reject(
                            new Error("Datenbankzugriff blockiert")
                        );
                    });

                    const storeNames = [...db.objectStoreNames];

                    for (const storeName of storeNames) {
                        try {
                            const count = await new Promise((resolve, reject) => {
                                const transaction =
                                    db.transaction(storeName, "readonly");
                                const request =
                                    transaction.objectStore(storeName).count();

                                request.onsuccess = () => resolve(request.result);
                                request.onerror = () => reject(request.error);
                            });

                            details.stores.push({
                                name: storeName,
                                recordCount: count
                            });
                        } catch (e) {
                            details.stores.push({
                                name: storeName,
                                error: String(e)
                            });
                        }
                    }

                    db.close();
                } catch (e) {
                    details.error = String(e);
                }

                result.indexedDB.push(details);
            }
        } else {
            result.indexedDB = {
                note: "indexedDB.databases() nicht verfügbar"
            };
        }
    } catch (e) {
        result.indexedDB = {error: String(e)};
    }

    try {
        if ("caches" in window) {
            for (const name of await caches.keys()) {
                try {
                    const cache = await caches.open(name);
                    result.cacheStorage.push({
                        name: name,
                        requestCount: (await cache.keys()).length
                    });
                } catch (e) {
                    result.cacheStorage.push({
                        name: name,
                        error: String(e)
                    });
                }
            }
        }
    } catch (e) {
        result.cacheStorage = {error: String(e)};
    }

    return result;
}
"""

# Nachrichten anhand der aktuellen ChatGPT-DOM-Struktur auslesen.
FIND_MESSAGES = r"""
marker => {
    const headings = [...document.querySelectorAll("h4")];

    const label = heading => (heading.innerText || "").trim();

    // Liest den Inhalt nach einer Überschrift bis zur nächsten h4-Überschrift.
    const extract = heading => {
        let node = heading.nextElementSibling;
        const parts = [];

        while (node && !node.matches("h4")) {
            const value = (node.innerText || "").trim();
            if (value) parts.push(value);
            node = node.nextElementSibling;
        }

        return parts.join("\\n").trim();
    };

    const users = headings.filter(h => label(h) === "You said:");
    const assistants = headings.filter(h => label(h) === "ChatGPT said:");

    const userIndex = users.findIndex(h => extract(h).includes(marker));

    let answer = "";
    let followingAssistantCount = 0;

    if (userIndex >= 0) {
        const headingIndex = headings.indexOf(users[userIndex]);

        const following = headings
            .slice(headingIndex + 1)
            .filter(h => label(h) === "ChatGPT said:");

        followingAssistantCount = following.length;

        if (following.length) {
            answer = extract(following[0]);
        }
    }

    return {
        userCount: users.length,
        assistantCount: assistants.length,
        markerFound: userIndex >= 0,
        answer: answer,
        followingAssistantCount: followingAssistantCount
    };
}
"""

with sync_playwright() as p:
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

    try:
        page = context.pages[0] if context.pages else context.new_page()

        # Vorhandenen Chat beibehalten, wenn bereits ChatGPT geöffnet ist.
        if "chatgpt.com" not in (page.url or ""):
            log("Öffne ChatGPT.")
            page.goto(
                "https://chatgpt.com/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
        else:
            log(f"Bestehende ChatGPT-Seite beibehalten: {page.url}")

        textbox = page.locator(
            '[role="textbox"][aria-label="Ask ChatGPT"]'
        )
        textbox.wait_for(state="visible", timeout=60000)

        # 1. Lokalen Webspeicher untersuchen.
        log("Prüfe lokale Speicher-Metadaten vor dem Test.")
        storage_before = page.evaluate(STORAGE_INSPECTION)
        print("\n--- LOKALE SPEICHER VOR DEM TEST ---")
        print(storage_before)

        before = page.evaluate(FIND_MESSAGES, MARKER)
        log(
            "DOM-Ausgangszustand: "
            f"{before['userCount']} Nutzernachrichten, "
            f"{before['assistantCount']} Assistentennachrichten."
        )

        # 2. Testnachricht senden.
        log(f"Sende Testnachricht mit Kennung {MARKER}.")
        textbox.fill(TEST_MESSAGE)

        send_button = page.locator('button[aria-label="Send"]').last
        send_button.wait_for(state="visible", timeout=10000)

        if send_button.is_disabled():
            raise RuntimeError("Senden-Button ist deaktiviert.")

        send_button.click()

        # 3. Bestätigen, dass die Nachricht tatsächlich im Chat steht.
        # Die aktuelle Oberfläche verwendet "You said:" statt
        # data-message-author-role="user".
        log("Warte auf die Nachricht im DOM.")

        try:
            page.wait_for_function(
                """marker => {
                    const headings = [...document.querySelectorAll("h4")];

                    const extract = heading => {
                        let node = heading.nextElementSibling;
                        const parts = [];

                        while (node && !node.matches("h4")) {
                            const value = (node.innerText || "").trim();
                            if (value) parts.push(value);
                            node = node.nextElementSibling;
                        }

                        return parts.join("\\n");
                    };

                    return headings.some(heading =>
                        (heading.innerText || "").trim() === "You said:" &&
                        extract(heading).includes(marker)
                    );
                }""",
                arg=MARKER,
                timeout=15000,
            )
        except PlaywrightTimeout:
            log("DOM-Bestätigung fehlgeschlagen.")
            log(
                "Kennung irgendwo auf der Seite: "
                f"{page.get_by_text(MARKER, exact=False).count() > 0}"
            )
            log("Die Kennung wurde nicht unter einer 'You said:'-Überschrift gefunden.")
            raise

        log("VERSAND BESTÄTIGT: Kennung in einer Nutzernachricht gefunden.")

        # 4. Auf eine Antwort nach genau dieser Nutzernachricht warten.
        log("Warte auf eine zugehörige Assistentenantwort.")

        page.wait_for_function(
            """marker => {
                const headings = [...document.querySelectorAll("h4")];

                const label = heading =>
                    (heading.innerText || "").trim();

                const extract = heading => {
                    let node = heading.nextElementSibling;
                    const parts = [];

                    while (node && !node.matches("h4")) {
                        const value = (node.innerText || "").trim();
                        if (value) parts.push(value);
                        node = node.nextElementSibling;
                    }

                    return parts.join("\\n").trim();
                };

                const userIndex = headings.findIndex(heading =>
                    label(heading) === "You said:" &&
                    extract(heading).includes(marker)
                );

                if (userIndex < 0) return false;

                const answerHeading = headings
                    .slice(userIndex + 1)
                    .find(heading => label(heading) === "ChatGPT said:");

                return Boolean(answerHeading && extract(answerHeading));
            }""",
            arg=MARKER,
            timeout=45000,
        )

        log("Eine Assistentenantwort zur Testnachricht wurde gefunden.")

        # Auf stabilen Antworttext warten, damit möglichst keine
        # unvollständige Streaming-Antwort ausgelesen wird.
        log("Assistentenantwort gefunden; prüfe, ob sie fertig ist.")
        last_answer = ""
        stable_polls = 0
        answer = ""

        for _ in range(60):
            state = page.evaluate(FIND_MESSAGES, MARKER)
            answer = state["answer"]

            if answer and answer == last_answer:
                stable_polls += 1
            else:
                stable_polls = 0

            last_answer = answer

            stop_visible = page.locator(
                'button[aria-label*="Stop"], '
                'button[data-testid="stop-button"]'
            ).count() > 0

            if stable_polls >= 4 and not stop_visible:
                break

            sleep(0.75)

        print("\n--- ZUGEHÖRIGE CHATGPT-ANTWORT ---")
        print(answer or "(Kein Antworttext ausgelesen.)")

        if "AGENT_TEST_OK" in answer:
            log("ERFOLG: Antwortinhalt erkannt.")
        elif answer:
            log("Antwort erkannt, aber der erwartete Antwortmarker fehlt.")
        else:
            log("Keine auslesbare Antwort gefunden.")

        # 5. Speicher erneut untersuchen, nur Metadaten ausgeben.
        log("Prüfe lokale Speicher-Metadaten nach dem Test.")
        storage_after = page.evaluate(STORAGE_INSPECTION)

        print("\n--- LOKALE SPEICHER NACH DEM TEST ---")
        print(storage_after)

        log(
            "DOM-Endzustand: "
            f"{state['userCount']} Nutzernachrichten, "
            f"{state['assistantCount']} Assistentennachrichten."
        )

        input("\nPrüfung beendet. Enter schließt Brave.")

    except Exception as exc:
        log(f"FEHLER: {type(exc).__name__}: {exc}")
        input("Enter schließt Brave.")

    finally:
        context.close()
