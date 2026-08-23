from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routes.market import router as market_router
from app.routes.portfolio import router as portfolio_router
from app.routes.history import router as history_router

app = FastAPI(
    title="Coinbase Portfolio API",
    version="0.1.0",
    description="Read-only live Coinbase portfolio and USD market data.",
    openapi_tags=[{"name": "portfolio"}, {"name": "market"}],
)


def _to_openapi_3_0(node: object) -> object:
    """Rewrite Pydantic v2's OpenAPI 3.1 `anyOf: [T, {type: null}]` nullability into
    3.0's `nullable: true`, since Foundry's portal tool-import only accepts 3.0."""
    if isinstance(node, dict):
        converted = {key: _to_openapi_3_0(value) for key, value in node.items()}
        any_of = converted.get("anyOf")
        if isinstance(any_of, list) and {"type": "null"} in any_of:
            remaining = [branch for branch in any_of if branch != {"type": "null"}]
            del converted["anyOf"]
            converted = {**converted, **remaining[0], "nullable": True} if len(remaining) == 1 else {**converted, "anyOf": remaining, "nullable": True}
        return converted
    if isinstance(node, list):
        return [_to_openapi_3_0(item) for item in node]
    return node


def custom_openapi() -> dict:
    if app.openapi_schema is None:
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes, tags=app.openapi_tags)
        schema = _to_openapi_3_0(schema)
        schema["openapi"] = "3.0.3"
        base_url = get_settings().public_base_url
        if base_url:
            schema["servers"] = [{"url": base_url}]
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if exc.status_code == 401:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return JSONResponse(status_code=exc.status_code, content=exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail})


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok"}


app.include_router(portfolio_router, tags=["portfolio"])
app.include_router(market_router, tags=["market"])
app.include_router(history_router, tags=["portfolio"])

