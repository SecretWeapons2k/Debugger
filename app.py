from fastapi import FastAPI

app = FastAPI(title="Debugger Agent")

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "debugger-agent",
        "terminal_control": False
    }
