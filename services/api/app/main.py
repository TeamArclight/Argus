from fastapi import FastAPI

app = FastAPI(title="ARGUS API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "argus-api"}
