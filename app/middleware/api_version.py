from fastapi import Request, HTTPException


async def require_api_version(request: Request):
    version = request.headers.get("X-API-Version")
    if version != "1":
        raise HTTPException(
            status_code=400,
            detail="API version header required"
        )
