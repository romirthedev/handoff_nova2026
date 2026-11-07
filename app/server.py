"""Handoff web service: dashboard, phone pass camera, print kit, and a websocket per kitchen."""

import asyncio
import io
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, vision
from .menu import BY_ID, MENU, SOURCES

STATIC = Path(__file__).parent / "static"


class FreshStatic(StaticFiles):
    """Browsers revalidate scripts and styles on every load (a cheap 304), so a redeploy is never hidden by cache."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp
DEMO_ID = "demo"


# ----------------------------------------------------------------------------- persistence

class FileStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self, kitchens: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(kitchens))
        tmp.replace(self.path)


class ModalStore:
    def __init__(self):
        import modal
        self.d = modal.Dict.from_name("handoff-kitchens", create_if_missing=True)

    def load(self) -> dict:
        return self.d.get("kitchens", {})

    def save(self, kitchens: dict):
        self.d["kitchens"] = kitchens


class Hub:
    def __init__(self, store):
        self.store = store
        self.kitchens: dict = {}
        self.sockets: dict[str, dict[WebSocket, str]] = {}
        self.dirty = False
        self.scan_locks: dict[str, asyncio.Lock] = {}
        self.scan_times: dict[str, list[float]] = {}

    def load(self):
        try:
            self.kitchens = self.store.load()
        except Exception as e:  # a bad save shouldn't keep the pass from opening
            print("could not load saved kitchens:", e)
        if DEMO_ID not in self.kitchens:
            k = core.new_kitchen("Harbor & Hearth")
            k["id"] = DEMO_ID
            self.kitchens[DEMO_ID] = k
        for k in self.kitchens.values():
            core.upgrade(k)

    def get(self, kid: str) -> dict:
        if kid not in self.kitchens:
            raise HTTPException(404, "No kitchen with that link. Start one from the home page.")
        return self.kitchens[kid]

    def cameras(self, kid):
        return sum(1 for role in self.sockets.get(kid, {}).values() if role == "camera")

    def public(self, k):
        return {"type": "state", "now": core.now_ms(), "kitchen": {**k, "log": k["log"][-25:]},
                "cameras": self.cameras(k["id"]), "vision": vision.vision_ready(), "model": vision.engine_label()}

    async def send_all(self, kid, msg):
        dead = []
        for ws in list(self.sockets.get(kid, {})):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.sockets.get(kid, {}).pop(ws, None)

    async def changed(self, k, event=None):
        self.dirty = True
        if event:
            await self.send_all(k["id"], event)
        await self.send_all(k["id"], self.public(k))

    async def loop(self):
        last_save = 0.0
        while True:
            await asyncio.sleep(0.5)
            for k in list(self.kitchens.values()):
                for o, it in core.tick(k):
                    name = BY_ID[it["menu"]]["name"]
                    core.log(k, f"⏲ #{o['num']} {name} is up")
                    await self.changed(k, {"type": "timer_done", "order": o["num"], "item": it["uid"],
                                           "say": f"Order {o['num']}, {name.lower()} is up."})
                for o in core.due_to_start(k):
                    core.log(k, f"▶ #{o['num']} time to start")
                    await self.changed(k, {"type": "start_now", "order": o["num"],
                                           "say": f"Start order {o['num']} for {o['guest'].split()[0]} now. {core.pickup_say(o)}"})
            if self.dirty and time.time() - last_save > 2:
                self.dirty, last_save = False, time.time()
                try:
                    await asyncio.to_thread(self.store.save, self.kitchens)
                except Exception as e:
                    print("save failed:", e)


def build_app() -> FastAPI:
    store = ModalStore() if os.environ.get("HANDOFF_STORE") == "modal" else \
        FileStore(Path(os.environ.get("HANDOFF_DATA", Path(__file__).parent.parent / "data")) / "kitchens.json")
    hub = Hub(store)

    @asynccontextmanager
    async def lifespan(app):
        hub.load()
        task = asyncio.create_task(hub.loop())
        yield
        task.cancel()
        hub.store.save(hub.kitchens)

    app = FastAPI(lifespan=lifespan, title="Handoff")
    app.mount("/static", FreshStatic(directory=STATIC), name="static")
    page = lambda name: FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})  # noqa: E731

    @app.get("/")
    def landing():
        return page("landing.html")

    @app.get("/k/{kid}")
    def dashboard(kid: str):
        if kid not in hub.kitchens:
            return RedirectResponse("/")
        return page("dashboard.html")

    @app.get("/k/{kid}/scan")
    def scanner(kid: str):
        hub.get(kid)
        return page("scan.html")

    @app.get("/k/{kid}/print")
    def print_kit(kid: str):
        hub.get(kid)
        return page("print.html")

    @app.get("/qr.svg")
    def qr(d: str, dark: str = "#141414", light: str = "#ffffff"):
        buf = io.BytesIO()
        segno.make(d[:600], error="m").save(buf, kind="svg", scale=8, border=2, dark=dark, light=light, xmldecl=False)
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})

    @app.get("/api/config")
    def config(request: Request):
        """Where a phone should open the pass camera. A phone can't reach localhost, so local runs hand out the
        laptop's Wi-Fi https address (see run_local.py); HANDOFF_PUBLIC_URL overrides both."""
        host = request.url.hostname or ""
        local = host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")
        public, lan = os.environ.get("HANDOFF_PUBLIC_URL"), os.environ.get("HANDOFF_LAN_URL")
        base = public or (lan if local else None)
        return {"phone_base": base, "lan": bool(base and base == lan), "unreachable": local and not base}

    @app.get("/api/menu")
    def menu():
        return {"menu": MENU, "sources": SOURCES}

    class NewKitchen(BaseModel):
        name: str
        demo: bool = True

    @app.post("/api/kitchens")
    async def create(body: NewKitchen):
        if len(hub.kitchens) > 500:
            raise HTTPException(429, "Too many kitchens on this server.")
        k = core.new_kitchen(body.name[:60], body.demo)
        hub.kitchens[k["id"]] = k
        hub.dirty = True
        return {"id": k["id"]}

    @app.get("/api/k/{kid}")
    def state(kid: str):
        return hub.public(hub.get(kid))

    class Command(BaseModel):
        text: str

    @app.post("/api/k/{kid}/command")
    async def command(kid: str, body: Command):
        k = hub.get(kid)
        cmds = core.parse_commands(body.text[:300], k)
        res = core.run_commands(k, cmds)
        res["heard"] = body.text
        await hub.changed(k, {"type": "voice", **res})
        return {**res, "parsed": cmds[0], "commands": cmds}

    class Action(BaseModel):
        intent: str
        order: str
        item: str | None = None
        minutes: float | None = None
        shift: int | None = None

    @app.post("/api/k/{kid}/action")
    async def action(kid: str, body: Action):
        k = hub.get(kid)
        res = core.run_command(k, body.model_dump())
        await hub.changed(k)
        return res

    class OrderLine(BaseModel):
        menu: str
        qty: int = 1
        mod: str | None = None

    class NewOrder(BaseModel):
        guest: str
        source: str = "Walk-in"
        courier: str | None = None
        pickup_in: int | None = None
        items: list[OrderLine]

    @app.post("/api/k/{kid}/orders")
    async def add_order(kid: str, body: NewOrder):
        k = hub.get(kid)
        lines = [(x.menu, max(1, min(x.qty, 20)), (x.mod or None)) for x in body.items if x.menu in BY_ID]
        if not lines or not body.guest.strip():
            raise HTTPException(400, "An order needs a guest name and at least one item.")
        num = max([int(n) for n in k["orders"]] + [40]) + 1
        o = core.new_order(num, body.guest.strip()[:40], body.source[:20], lines, body.courier)
        if body.pickup_in:
            core.set_pickup(o, core.now_ms() + max(1, min(body.pickup_in, 240)) * 60_000)
        k["orders"][str(num)] = o
        core.log(k, f"+ #{num} {body.guest.strip()} ({body.source})")
        await hub.changed(k, {"type": "new_order", "order": num, "say": f"New {body.source} order, number {num}. {core.timing_say(o)}"})
        return {"num": num}

    @app.post("/api/k/{kid}/reset")
    async def reset(kid: str):
        k = hub.get(kid)
        k["orders"], k["scans"], k["log"] = core.demo_orders(), [], []
        core.log(k, "Demo tickets reset")
        await hub.changed(k)
        return {"ok": True}

    class Settings(BaseModel):
        speed: int | None = None
        name: str | None = None

    @app.post("/api/k/{kid}/settings")
    async def settings(kid: str, body: Settings):
        k = hub.get(kid)
        if body.speed in (1, 10, 60):
            k["speed"] = body.speed
        if body.name and body.name.strip():
            k["name"] = body.name.strip()[:60]
        await hub.changed(k)
        return {"ok": True}

    @app.post("/api/k/{kid}/scan")
    async def scan(kid: str, request: Request):
        k = hub.get(kid)
        jpeg = await request.body()
        if not 1000 < len(jpeg) < 12_000_000:
            raise HTTPException(400, "Send a JPEG photo.")
        recent = [t for t in hub.scan_times.get(kid, []) if time.time() - t < 60]
        if len(recent) >= 40:
            raise HTTPException(429, "Too many scans this minute. Give it a moment.")
        hub.scan_times[kid] = recent + [time.time()]
        lock = hub.scan_locks.setdefault(kid, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "Still checking the last plate.")
        async with lock:
            await hub.send_all(kid, {"type": "scanning"})
            t0 = time.perf_counter()
            try:
                seen = await vision.identify(jpeg)
            except Exception as e:
                await hub.send_all(kid, {"type": "scan_failed"})
                raise HTTPException(502, f"Couldn't read the photo: {e}") from e
            result = core.match_plate(k, seen["items"], seen["order_tag"])
            result.update(engine=seen["engine"], unrecognized=seen.get("unrecognized", []),
                          seconds=round(time.perf_counter() - t0, 2), warning=seen.get("warning"))
            if not seen["items"] and not seen["order_tag"]:
                # nothing recognisable: tell the phone, but don't log it or flash the laptop
                reason = "No menu items in view." if seen["engine"] != "card codes" else \
                    f"{seen.get('warning') or 'Vision is off.'} Only the codes printed on the cards could be read, and none were."
                result.update(empty=True, at=core.now_ms(), reason=reason)
                await hub.send_all(kid, {"type": "scan_failed", "quiet": True})
                return result
            result = core.record_check(k, result)
            await hub.changed(k, {"type": "scan", "result": result})
            return result

    @app.websocket("/ws/k/{kid}")
    async def ws(websocket: WebSocket, kid: str, role: str = "dashboard"):
        await websocket.accept()
        if kid not in hub.kitchens:
            await websocket.close(code=4404)
            return
        hub.sockets.setdefault(kid, {})[websocket] = "camera" if role == "camera" else "dashboard"
        k = hub.kitchens[kid]
        await hub.send_all(kid, hub.public(k))
        try:
            while True:
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            hub.sockets.get(kid, {}).pop(websocket, None)
            await hub.send_all(kid, hub.public(k))

    return app


app = build_app()
