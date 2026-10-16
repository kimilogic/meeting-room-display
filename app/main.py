"""Room-specific displays with shared authentication and isolated polling."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import ConfigError, Settings, load_credentials, load_settings
from app.graph import GraphClient, MsalTokenProvider
from app.store import ScheduleStore
from app.ad_hoc import AdHoc, AdHocError, DEFAULT_PATH

STATIC = Path(__file__).resolve().parent / "static"

def create_app(settings=None, client=None, clients=None, ad_hoc_path=None):
    @asynccontextmanager
    async def lifespan(application):
        configured = settings or Settings()
        error = ""
        token_provider = None
        try:
            configured = settings if settings is not None else load_settings()
            if configured.data_source == "graph" and client is None and clients is None:
                token_provider = MsalTokenProvider(load_credentials())
        except ConfigError as exc:
            error = str(exc)
        stores = {}
        for room in configured.room_definitions():
            graph_client = (clients or {}).get(room.id) if clients is not None else client
            if token_provider is not None:
                graph_client = GraphClient(room.upn, token_provider)
            room_settings = Settings(configured.data_source, room.name, room.upn)
            stores[room.id] = ScheduleStore(room_settings, graph_client, error)
        application.state.stores = stores
        application.state.store = stores["idoutai"]  # Existing integrations/tests.
        application.state.ad_hoc = AdHoc(stores["idoutai"], ad_hoc_path)
        for store in stores.values():
            store.start()
        application.state.ad_hoc.start_monitor()
        try:
            yield
        finally:
            application.state.ad_hoc.close()
            for store in stores.values():
                store.stop.set()
            for store in stores.values():
                store.close()

    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.mount("/static", StaticFiles(directory=STATIC), name="static")

    def get_store(room_id):
        store = application.state.stores.get(room_id)
        if store is None:
            raise HTTPException(status_code=404, detail="会議室が見つかりません")
        return store

    @application.get("/")
    def index():
        controls = '<div id="ad-hoc-controls" style="display:none"><button type="button" id="ad-hoc-start" style="display:none">今から使用</button><button type="button" id="ad-hoc-stop" style="display:none">使用終了</button></div>'
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("<!-- AD_HOC_CONTROLS -->", controls)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @application.get("/room/{room_id}")
    def room_index(room_id: str):
        get_store(room_id)
        if room_id == "idoutai":
            return index()
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @application.get("/api/room/{room_id}/status")
    def room_status(room_id: str):
        store = get_store(room_id)
        data = application.state.ad_hoc.display() if room_id == "idoutai" else store.display()
        data["room_id"] = room_id
        return JSONResponse(data, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

    @application.get("/api/display")
    def display():
        return room_status("idoutai")

    def operate(action, request):
        # Custom header prevents cross-origin form submissions; no CORS is enabled.
        if request.headers.get("x-room-action") != "1":
            raise HTTPException(status_code=403, detail="操作ヘッダーが必要です")
        code = 200
        try:
            data = application.state.ad_hoc.operate(action)
        except AdHocError as exc:
            code = exc.status
            data = application.state.ad_hoc.display()
            data["operation_error"] = str(exc)
        data["room_id"] = "idoutai"
        return JSONResponse(data, status_code=code, headers={"Cache-Control": "no-store"})

    @application.post("/api/room/idoutai/ad-hoc/start")
    def ad_hoc_start(request: Request):
        return operate("start", request)

    @application.post("/api/room/idoutai/ad-hoc/stop")
    def ad_hoc_stop(request: Request):
        return operate("stop", request)

    return application


app = create_app(ad_hoc_path=DEFAULT_PATH)
