# Insighta Labs+ — Backend API

A secure, multi-interface demographic intelligence platform built with FastAPI and PostgreSQL.

## Live URLs
- **Backend API**: https://hng-task-3-05rc.onrender.com
- **Web Portal**: https://insighta-web-chi.vercel.app
- **API Docs**: https://hng-task-3-05rc.onrender.com/docs

## System Architecture

## Authentication Flow

1. User initiates login via CLI or Web Portal
2. Backend redirects to GitHub OAuth
3. GitHub redirects back with authorization code
4. Backend exchanges code for GitHub access token
5. Backend fetches user info from GitHub
6. Backend creates/updates user in database
7. Backend issues JWT access token (3 min) + refresh token (5 min)
8. Tokens stored in localStorage (web) or ~/.insighta/credentials.json (CLI)

## Token Handling

- **Access Token**: JWT, expires in 3 minutes
- **Refresh Token**: Opaque random string, stored hashed in DB, expires in 5 minutes
- **Rotation**: Each refresh invalidates old token and issues new pair
- **Web**: Tokens passed via URL params after OAuth, stored in localStorage
- **CLI**: Tokens stored in ~/.insighta/credentials.json

## Role Enforcement

| Role | Permissions |
|------|-------------|
| admin | Full access: create, delete, read, search profiles |
| analyst | Read-only: list, search, export profiles |

All `/api/*` endpoints require authentication and enforce role permissions via FastAPI `Depends`.

## API Versioning

All profile endpoints require the header:






Requests without this header return `400 Bad Request`.

## Natural Language Parsing

The search endpoint parses plain English queries into structured filters:
- Gender: "males", "females", "women", "men"
- Age groups: "young", "adult", "senior", "teenager", "child"
- Age ranges: "above 30", "between 20 and 40", "under 25"
- Countries: "from Nigeria", "in Ghana"

Example: `"young males from Nigeria"` → `{gender: male, min_age: 16, max_age: 24, country_id: NG}`

## CLI Usage

```bash
# Install
npm install -g .

# Auth
insighta login
insighta logout
insighta whoami

# Profiles
insighta profiles list
insighta profiles list --gender male --country NG
insighta profiles list --age-group adult --sort-by age --order desc
insighta profiles get <id>
insighta profiles search "young females from Ghana"
insighta profiles create --name "John Doe"
insighta profiles export --format csv
```

## Rate Limiting

| Scope | Limit |
|-------|-------|
| Auth endpoints (/auth/*) | 10 requests/minute |
| All other endpoints | 60 requests/minute per user |

## Tech Stack

- **Backend**: Python 3.11, FastAPI, SQLAlchemy
- **Database**: PostgreSQL (Supabase)
- **Auth**: GitHub OAuth 2.0 with PKCE
- **CLI**: Node.js, Commander.js
- **Web Portal**: Next.js, deployed on Vercel
- **Deployment**: Render (backend), Vercel (web)

## Environment Variables

```env
DATABASE_URL=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_REDIRECT_URI=
GITHUB_CLI_CLIENT_ID=
GITHUB_CLI_CLIENT_SECRET=
FRONTEND_URL=
JWT_SECRET=
ACCESS_TOKEN_EXPIRE_SECONDS=180
REFRESH_TOKEN_EXPIRE_SECONDS=300
CLI_REDIRECT_BASE=http://localhost
```

## CI/CD

GitHub Actions runs on every PR to main:
- Linting
- Build checks
