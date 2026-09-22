from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import CORS_ORIGINS

app = FastAPI(title="Spectral Canvas API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# The call page needs extra packages (reedsolo). If they are missing, the rest
# of the app still starts and /api/tel answers with what to install.
try:
    from app.api.tel_routes import router as tel_router
    app.include_router(tel_router)
except ImportError as _tel_error:
    _tel_detail = (f"The call page is unavailable on this server: {_tel_error}. "
                   "Install the backend requirements and restart it.")
    print(f"WARNING: {_tel_detail}")

    @app.api_route("/api/tel/{path:path}", methods=["GET", "POST"])
    def tel_unavailable(path: str):
        raise HTTPException(503, _tel_detail)


@app.get("/")
def root():
    return {"name": "Spectral Canvas API", "docs": "/docs"}
