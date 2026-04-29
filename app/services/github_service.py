import httpx
import os


async def exchange_code_for_token(code: str, redirect_uri: str, code_verifier: str = None, is_cli: bool = False) -> str:
    import os
    
    if is_cli:
        client_id = os.getenv("GITHUB_CLI_CLIENT_ID", os.getenv("GITHUB_CLIENT_ID"))
        client_secret = os.getenv("GITHUB_CLI_CLIENT_SECRET", os.getenv("GITHUB_CLIENT_SECRET"))
    else:
        client_id = os.getenv("GITHUB_CLIENT_ID")
        client_secret = os.getenv("GITHUB_CLIENT_SECRET")

    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }

    if code_verifier:
        params["code_verifier"] = code_verifier

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            params=params,
            headers={"Accept": "application/json"},
            timeout=10.0,
        )

    data = response.json()

    if "error" in data:
        raise ValueError(data.get("error_description", "GitHub token exchange failed"))

    return data["access_token"]    


async def get_github_user(github_access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {github_access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
        )

    if response.status_code != 200:
        raise ValueError("Failed to fetch GitHub user info")

    return response.json()