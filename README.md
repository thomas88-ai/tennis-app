# Tennis Tournament App (Web + Admin Backend)

This project now includes:
- Regular season match posting (`/match.html`)
- Tournament bracket (`/tournament.html`)
- Scoring table/standings (`/table.html`)
- Player directory with WhatsApp chat links (`/player.html`)
- Community posts (`/community.html`)
- Official news feed (`/news.html`)
- Profile and account management (`/profile.html`, `/account.html`)
- WhatsApp + TAC registration flow (`/register.html`)
- Admin panel for players/matches/news (`/admin.html`)
- Backend API + static hosting (`backend.py`)

## 1) Run Locally

Prerequisite: Python 3.9+

```bash
cd /Users/thomas/Downloads/Tennis
cp .env.example .env  # optional reference only
python3 backend.py
```

Open:
- `http://localhost:8080`

Default admin token:
- `admin123`

## 2) Core Backend Files

- `backend.py`: API + static server
- `data/store.json`: persistent app data
- `app-api.js`: shared frontend API client

## 3) Important API Endpoints

Public:
- `POST /api/auth/request-tac`
- `POST /api/auth/verify-tac`
- `POST /api/auth/login-by-phone`
- `GET /api/players`
- `GET /api/matches`
- `POST /api/matches`
- `GET /api/standings`
- `GET /api/tournament`
- `GET /api/news`
- `GET /api/community`
- `POST /api/community`
- `GET /api/profile?player_id=...`
- `PUT /api/profile/{player_id}`

Admin (require header `x-admin-token`):
- `GET /api/admin/dashboard`
- `POST /api/admin/players`
- `PUT /api/admin/players/{id}`
- `DELETE /api/admin/players/{id}`
- `POST /api/admin/matches`
- `PUT /api/admin/matches/{id}`
- `DELETE /api/admin/matches/{id}`
- `POST /api/admin/news`
- `DELETE /api/admin/news/{id}`
- `POST /api/admin/tournament/matches`

## 4) WhatsApp TAC Setup (Production)

1. Create Meta Developer account.
2. Create Meta App and enable WhatsApp Cloud API.
3. Get:
   - `WHATSAPP_PHONE_ID`
   - `WHATSAPP_ACCESS_TOKEN`
   - approved message template (default name in code: `verification_code`)
4. Set env vars in your hosting environment.
5. Set `EXPOSE_TAC_CODE=false` in production.

## 5) Hosting + Domain Accounts You Need

Required accounts:
1. Hosting: Render / Railway / Fly.io / VPS provider.
2. Domain registrar: Namecheap / GoDaddy / Cloudflare Domains.
3. DNS manager: usually registrar or Cloudflare.
4. (Optional but recommended) Cloudflare for SSL + WAF.

Deployment checklist:
1. Push this project to GitHub.
2. Create a new web service on your host.
3. Set start command to `python3 backend.py`.
4. Configure env vars from `.env.example`.
5. Point domain DNS `A`/`CNAME` to host.
6. Enable HTTPS certificate.
7. Test TAC flow and admin token before launch.

## 6) Publish to Google Play and Apple App Store

You cannot publish directly from static HTML only. Wrap this web app as a mobile app first.

Recommended: Capacitor wrapper.

### Google Play (Android)

Accounts required:
1. Google Play Developer account (one-time USD 25).
2. Google Payments profile.

Steps:
1. Install Node.js and Capacitor locally.
2. Build mobile wrapper around this web app URL/domain.
3. Generate Android project (`npx cap add android`).
4. Open in Android Studio, set package id, app name, icons, splash.
5. Build signed AAB.
6. In Play Console: create app listing, privacy policy URL, content rating, data safety.
7. Upload AAB and submit for review.

### Apple App Store (iOS)

Accounts required:
1. Apple Developer Program (USD 99/year).
2. App Store Connect access.
3. macOS + Xcode.

Steps:
1. Generate iOS project (`npx cap add ios`).
2. Open in Xcode and set Bundle ID + signing team.
3. Configure app icons, launch screen, permissions.
4. Archive and upload through Xcode.
5. In App Store Connect: app metadata, screenshots, privacy nutrition labels.
6. Submit for TestFlight and App Review.

## 7) Production Hardening (Do Next)

1. Replace JSON file with PostgreSQL/MySQL.
2. Add real auth (JWT/session) and role-based admin access.
3. Add rate-limits on TAC requests and brute-force protection.
4. Add server logs + monitoring (Sentry, OpenTelemetry).
5. Add unit/integration tests and CI pipeline.

