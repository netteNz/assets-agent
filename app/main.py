from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from app.routes.market import router as market_router
from app.routes.portfolio import router as portfolio_router

app = FastAPI(
    title="Coinbase Portfolio API",
    version="0.1.0",
    description="Read-only live Coinbase portfolio and USD market data.",
    openapi_tags=[{"name": "portfolio"}, {"name": "market"}],
)


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

