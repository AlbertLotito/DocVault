import configparser, os
from fastapi import FastAPI

# Set by run.py before server.run() — lets routes trigger graceful shutdown.
_server = None
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from api.routes import catalog, search, query, workers, utils, settings as settings_routes
from api.routes.vaults  import router as vaults_router
from api.routes.monitor import router as monitor_router
from api.routes.identity import router as identity_router

_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
DB_PATH = _cfg.get('database', 'sqlite_path',
                    fallback=os.path.join(os.path.dirname(__file__),
                                          '..', 'docvault.db'))

app = FastAPI(title="DocVault", version="1.0.0")

app.include_router(catalog.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(workers.router, prefix="/api")
app.include_router(utils.router, prefix="/api")
app.include_router(settings_routes.router, prefix="/api")
app.include_router(vaults_router)
app.include_router(monitor_router)
app.include_router(identity_router)

FRONTEND = os.path.join(os.path.dirname(__file__), '..', 'frontend')
app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND, 'static')),
          name="static")


# ── HTML page routes ────────────────────────────────────
@app.get("/", include_in_schema=False)
@app.get("/status", include_in_schema=False)
@app.get("/vault", include_in_schema=False)
def vault_page():
    return FileResponse(os.path.join(FRONTEND, 'vault.html'))

@app.get("/catalog", include_in_schema=False)
def catalog_redirect():
    return RedirectResponse(url="/vault", status_code=301)

@app.get("/search", include_in_schema=False)
def search_page():
    return FileResponse(os.path.join(FRONTEND, 'search.html'))

@app.get("/utils", include_in_schema=False)
def utils_page():
    return FileResponse(os.path.join(FRONTEND, 'utils.html'))

@app.get("/optimizer", include_in_schema=False)
def optimizer_page():
    return FileResponse(os.path.join(FRONTEND, 'optimizer.html'))

@app.get("/settings", include_in_schema=False)
def settings_page():
    return FileResponse(os.path.join(FRONTEND, 'settings.html'))

@app.get("/lab", include_in_schema=False)
@app.get("/lab.html", include_in_schema=False)
def lab_page():
    return FileResponse(os.path.join(FRONTEND, 'lab.html'))

@app.get("/telemetry", include_in_schema=False)
@app.get("/telemetry.html", include_in_schema=False)
def telemetry_page():
    return FileResponse(os.path.join(FRONTEND, 'telemetry.html'))

@app.get("/identity", include_in_schema=False)
def identity_page():
    return FileResponse(os.path.join(FRONTEND, 'identity.html'))

@app.get("/theme", include_in_schema=False)
def theme_page():
    return FileResponse(os.path.join(FRONTEND, 'theme.html'))
