import asyncio
import os
import secrets
import sqlite3
import uuid
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn

app = FastAPI()
templates = Jinja2Templates(directory="templates")

conn = sqlite3.connect("shadow_vpn.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, uuid TEXT, active INTEGER, bw_used INTEGER)")
conn.commit()

class CustomProtocol:
    def __init__(self, key: str):
        self.key = key.encode()
        
    def encrypt(self, data: bytes) -> bytes:
        mask = self.key * (len(data) // len(self.key)) + self.key[:len(data) % len(self.key)]
        return bytes([b ^ m for b, m in zip(data, mask)])

    def decrypt(self, data: bytes) -> bytes:
        return self.encrypt(data)

protocol = CustomProtocol("IRAN_ANTI_FILTER_KEY_2026")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    cursor.execute("SELECT username, uuid, active, bw_used FROM users")
    users = [{"username": r[0], "uuid": r[1], "active": bool(r[2]), "bw": f"{r[3]/1024/1024:.2f} MB"} for r in cursor.fetchall()]
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
    target_writer = None
    try:
        raw_data = await websocket.receive_bytes()
        decrypted = protocol.decrypt(raw_data)
        
        # Parse Proxy Request
        headers = decrypted.split(b'\r\n')
        first_line = headers[0].decode('utf-8', errors='ignore')
        parts = first_line.split(' ')
        
        if len(parts) < 3:
            return
            
        method, url = parts[0], parts[1]
        host, port = "", 80
        
        if method == "CONNECT":
            host, port_str = url.split(':')
            port = int(port_str)
            await websocket.send_bytes(protocol.encrypt(b"HTTP/1.1 200 Connection Established\r\n\r\n"))
        else:
            if url.startswith("http://"):
                url_no_proto = url[7:]
                host_part = url_no_proto.split('/')[0]
                if ':' in host_part:
                    host, port_str = host_part.split(':')
                    port = int(port_str)
                else:
                    host = host_part
            else:
                host = url

        if not host:
            return

        # Real TCP Routing
        target_reader, target_writer = await asyncio.open_connection(host, port)
        
        if method != "CONNECT":
            target_writer.write(decrypted)
            await target_writer.drain()
            
        async def ws_to_tcp():
            try:
                while True:
                    data = await websocket.receive_bytes()
                    target_writer.write(protocol.decrypt(data))
                    await target_writer.drain()
            except Exception:
                pass
                
        async def tcp_to_ws():
            try:
                while True:
                    data = await target_reader.read(8192)
                    if not data:
                        break
                    
                    cursor.execute("UPDATE users SET bw_used = bw_used + ? WHERE uuid = ?", (len(data), client_uuid))
                    conn.commit()
                    
                    await websocket.send_bytes(protocol.encrypt(data))
            except Exception:
                pass
                
        await asyncio.gather(ws_to_tcp(), tcp_to_ws())
        
    except Exception:
        pass
    finally:
        if target_writer:
            target_writer.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
