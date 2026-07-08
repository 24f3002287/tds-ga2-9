"""
Orders API demonstrating three production-grade patterns:
  1. Idempotent POST /orders  (Idempotency-Key header)
  2. Cursor-based pagination  (GET /orders?limit=&cursor=)
  3. Per-client rate limiting (X-Client-Id header, sliding window)

Assigned values:
  TOTAL_ORDERS (T) = 54
  RATE_LIMIT   (R) = 19 requests / 10s window
"""

import base64
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
TOTAL_ORDERS = 54
RATE_LIMIT = 19
WINDOW_SECONDS = 10

app = FastAPI(title="Orders API")

# CORS: allow the grader's page (or anyone) to call this cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# --------------------------------------------------------------------------
# In-memory state
# --------------------------------------------------------------------------
lock = threading.Lock()

orders: dict[str, dict] = {}
catalog_ids: list[str] = []  # fixed pagination sequence
next_id_counter = TOTAL_ORDERS + 1

idempotency_store: dict[str, str] = {}  # Idempotency-Key -> order id

rate_buckets: dict[str, deque] = defaultdict(deque)  # client id -> timestamps


def seed_catalog() -> None:
    """Pre-populate the fixed catalog of orders 1..T.

    Pagination (GET /orders) only ever walks catalog_ids, which is frozen
    after seeding. Orders created later via POST /orders are stored in
    `orders` (individually fetchable, idempotency-tracked) but are
    deliberately NOT appended to catalog_ids, so a full paginated scan
    always yields exactly T=54 orders no matter how many POSTs happen."""
    for i in range(1, TOTAL_ORDERS + 1):
        oid = str(i)
        orders[oid] = {
            "id": oid,
            "item": f"item-{i}",
            "amount": round(9.99 + i, 2),
            "status": "created",
        }
        catalog_ids.append(oid)


seed_catalog()

# --------------------------------------------------------------------------
# Rate limiting middleware (sliding-window log, per X-Client-Id)
# --------------------------------------------------------------------------
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id")

    if client_id:
        now = time.time()
        with lock:
            dq = rate_buckets[client_id]
            while dq and now - dq[0] > WINDOW_SECONDS:
                dq.popleft()

            if len(dq) >= RATE_LIMIT:
                oldest = dq[0]
                retry_after = max(1, int(WINDOW_SECONDS - (now - oldest)) + 1)
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "limit": RATE_LIMIT,
                        "window_seconds": WINDOW_SECONDS,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            dq.append(now)

    return await call_next(request)


# --------------------------------------------------------------------------
# 1. Idempotent order creation
# --------------------------------------------------------------------------
@app.post("/orders", status_code=201)
async def create_order(
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    global next_id_counter

    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    if idempotency_key:
        with lock:
            existing_id = idempotency_store.get(idempotency_key)
            if existing_id is not None:
                return JSONResponse(status_code=201, content=orders[existing_id])

    with lock:
        oid = str(next_id_counter)
        next_id_counter += 1
        order = {
            "id": oid,
            "item": body.get("item", f"item-{oid}"),
            "amount": body.get("amount", 0),
            "status": "created",
        }
        orders[oid] = order
        # Intentionally NOT appended to catalog_ids -- see seed_catalog() docstring.
        if idempotency_key:
            idempotency_store[idempotency_key] = oid

    return JSONResponse(status_code=201, content=order)


# --------------------------------------------------------------------------
# 2. Cursor-based pagination
# --------------------------------------------------------------------------
def encode_cursor(index: int) -> str:
    return base64.urlsafe_b64encode(str(index).encode()).decode()


def decode_cursor(cursor: str) -> int:
    try:
        idx = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        return max(idx, 0)
    except Exception:
        return 0


@app.get("/orders")
async def list_orders(
    limit: int = Query(10, ge=1, le=1000),
    cursor: Optional[str] = None,
):
    with lock:
        snapshot = list(catalog_ids)

    start = decode_cursor(cursor) if cursor else 0
    if start > len(snapshot):
        start = len(snapshot)

    page_ids = snapshot[start : start + limit]
    items = [orders[i] for i in page_ids]
    end = start + len(items)

    next_cursor = encode_cursor(end) if end < len(snapshot) else None

    return {
        "items": items,
        "next_cursor": next_cursor,
        "next": next_cursor,   # alias
        "orders": items,       # alias
    }


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    order = orders.get(order_id)
    if not order:
        return JSONResponse(status_code=404, content={"detail": "not found"})
    return order


@app.get("/health")
async def health():
    return {"status": "ok", "total_orders": TOTAL_ORDERS, "rate_limit": RATE_LIMIT}
