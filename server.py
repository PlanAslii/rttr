import asyncio
import os
import secrets
import sqlite3
import base64
import json
import uuid
import struct
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialize SQLite
conn = sqlite3.connect("shadow_vpn.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, uuid TEXT, active INTEGER, bw_used INTEGER)")
conn.commit()

# Custom Obfuscation Protocol for Iran Internet (Fake HTTP / MUX over WS)
class CustomProtocol:
    def __init__(self, key: str):
        self.key = key.encode()
        
    def encrypt(self, data: bytes) -> bytes:
        # Simple XOR masking (mock advanced obfuscation)
        mask = self.key * (len(data) // len(self.key)) + self.key[:len(data) % len(self.key)]
        return bytes([b ^ m for b, m in zip(data, mask)])

    def decrypt(self, data: bytes) -> bytes:
        return self.encrypt(data)

protocol = CustomProtocol("IRAN_ANTI_FILTER_KEY_2026")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    cursor.execute("SELECT username, uuid, active, bw_used FROM users")
    users = [{"username": r[0], "uuid": r[1], "active": bool(r[2]), "bw": f"{r[3]/1024/1024:.2f} MB"} for r in cursor.fetchall()]
    
    # کد اصلاح شده برای سازگاری با نسخه‌های جدید FastAPI و Starlette
    return templates.TemplateResponse(
        request=request, 
        name="dashboard.html", 
        context={"request": request, "users": users}
    )

@app.post("/api/create_user")
async def create_user(request: Request):
    data = await request.json()
    username = data.get("username") or f"user_{secrets.token_hex(4)}"
    user_uuid = str(uuid.uuid4())
    cursor.execute("INSERT INTO users (username, uuid, active, bw_used) VALUES (?, ?, 1, 0)", (username, user_uuid))
    conn.commit()
    return {"status": "success", "username": username, "uuid": user_uuid}

@app.websocket("/ws/{client_uuid}")
async def vpn_tunnel(websocket: WebSocket, client_uuid: str):
    cursor.execute("SELECT active FROM users WHERE uuid = ?", (client_uuid,))
    user = cursor.fetchone()
    if not user or not user[0]:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        while True:
            raw_data = await websocket.receive_bytes()
            decrypted = protocol.decrypt(raw_data)
            
            # Simulated TCP routing
            cursor.execute("UPDATE users SET bw_used = bw_used + ? WHERE uuid = ?", (len(raw_data), client_uuid))
            conn.commit()
            
            # Send fake response
            mock_response = b"HTTP/1.1 200 Connection Established\r\n\r\n" + decrypted
            await websocket.send_bytes(protocol.encrypt(mock_response))
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
