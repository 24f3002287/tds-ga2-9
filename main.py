from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import time
import uuid

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

T = 54
R = 19

orders = [{"id": i, "item": f"order-{i}"} for i in range(1, T + 1)]
idempotency_store = {}
client_buckets = {}

def check_rate_limit(client_id: str):
    now = time.time()
    window_start = now - 10

    if client_id not in client_buckets:
        client_buckets[client_id] = []

    client_buckets[client_id] = [
        ts for ts in client_buckets[client_id] if ts >= window_start
    ]

    if len(client_buckets[client_id]) >= R:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "10"},
        )

    client_buckets[client_id].append(now)

class OrderIn(BaseModel):
    item: str

@app.post("/orders", status_code=201)
async def create_order(
    order: OrderIn,
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
    x_client_id: str = Header(None, alias="X-Client-Id"),
):
    if x_client_id:
        check_rate_limit(x_client_id)

    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key required")

    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    new_order = {"id": str(uuid.uuid4()), "item": order.item}
    idempotency_store[idempotency_key] = new_order
    return JSONResponse(status_code=201, content=new_order)

@app.get("/orders")
async def list_orders(
    limit: int = 10,
    cursor: str = None,
    x_client_id: str = Header(None, alias="X-Client-Id"),
):
    if x_client_id:
        check_rate_limit(x_client_id)

    limit = max(1, min(limit, T))

    start_index = int(cursor) if cursor is not None else 0
    page = orders[start_index:start_index + limit]

    next_cursor = None
    if start_index + limit < len(orders):
        next_cursor = str(start_index + limit)

    return {"items": page, "next_cursor": next_cursor}
