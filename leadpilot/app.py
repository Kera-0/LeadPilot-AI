import hmac
import asyncio
from contextlib import asynccontextmanager, suppress
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .config import env, ROOT
from .service import Service
from .store import Store


class ChatInput(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    request_id: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


def create_app(store=None, model=None):
    service = Service(store or Store(env('DATABASE_PATH', str(ROOT / 'leads.db'))), model)

    @asynccontextmanager
    async def lifespan(app):
        async def cleanup():
            while True:
                await asyncio.to_thread(service.cleanup)
                await asyncio.sleep(3600)
        task = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title='LeadPilot AI', version='0.2.0', lifespan=lifespan)
    app.state.service = service
    rates = defaultdict(deque)

    @app.middleware('http')
    async def protections(request, call_next):
        origin = request.headers.get('origin')
        if request.method in ('POST', 'DELETE') and origin and urlsplit(origin).netloc != request.headers.get('host'):
            return Response('Cross-origin request rejected', status_code=403)
        try:
            size = int(request.headers.get('content-length', '0') or 0)
        except ValueError:
            return Response('Invalid Content-Length', status_code=400)
        if size > 16000:
            return Response('Request too large', status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    def session(request: Request):
        token = request.cookies.get('leadpilot_session', '')
        if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
            raise HTTPException(401, 'Open a session first')
        return 'web:' + token

    def admin(request: Request):
        secret = env('ADMIN_TOKEN')
        if not secret or not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + secret):
            raise HTTPException(401, 'Admin token required')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'static' / 'index.html')

    @app.get('/app.js')
    def javascript():
        return FileResponse(ROOT / 'static' / 'app.js', media_type='application/javascript')

    @app.get('/style.css')
    def style():
        return FileResponse(ROOT / 'static' / 'style.css', media_type='text/css')

    @app.get('/health')
    def health():
        return {'status': 'ok', 'llm_configured': service.model.configured, 'demo_catalog': service.kb['demo_data']}

    @app.post('/api/session')
    def open_session(request: Request, response: Response):
        token = request.cookies.get('leadpilot_session', '')
        if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
            token = secrets.token_hex(32)
        response.set_cookie('leadpilot_session', token, httponly=True, samesite='strict',
                            secure=env('COOKIE_SECURE', 'false') == 'true', max_age=86400 * 30)
        return {'ok': True}

    @app.get('/api/history')
    def history(cid=Depends(session)):
        with service.store.lock:
            client = service.store.client(cid)
            return {'messages': service.store.history(cid), 'state': client['state'] if client else None}

    @app.post('/api/chat')
    def chat(body: ChatInput, request: Request, cid=Depends(session)):
        ip = request.client.host if request.client else 'local'
        with service.store.lock:
            recent = rates[ip]
            now = time.monotonic()
            while recent and recent[0] < now - 60:
                recent.popleft()
            if len(recent) >= 30:
                raise HTTPException(429, 'Too many messages. Retry in a minute.')
            recent.append(now)
        return service.handle(cid, body.text, body.request_id)

    @app.get('/api/admin/leads', dependencies=[Depends(admin)])
    def leads():
        with service.store.lock:
            return [{'id': row['id'], 'channel': row['channel'], 'external_id': row['external_id'],
                     'state': service.store.client(row['id'])['state'], 'updated': row['updated']}
                    for row in service.store.db.execute('SELECT * FROM clients ORDER BY updated DESC LIMIT 100')]

    @app.get('/api/admin/metrics', dependencies=[Depends(admin)])
    def metrics():
        with service.store.lock:
            return service.store.metrics()

    @app.get('/api/admin/history/{client_id:path}', dependencies=[Depends(admin)])
    def admin_history(client_id: str):
        with service.store.lock:
            return service.store.history(client_id)

    return app


app = create_app()
