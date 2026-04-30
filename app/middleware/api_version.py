from fastapi import Request
from fastapi.responses import JSONResponse


async def require_api_version(request: Request):
    version = request.headers.get("X-API-Version")
    if version != "1":
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "API version header required"}
        )
