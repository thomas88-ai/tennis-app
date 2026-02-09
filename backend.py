#!/usr/bin/env python3
import json
import mimetypes
import os
import random
import re
import threading
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DATA_FILE = ROOT_DIR / "data" / "store.json"
LOCK = threading.Lock()


def load_env_file():
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return
    with env_file.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin123")
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "2026-S1")


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digits(value):
    return re.sub(r"\D", "", value or "")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def load_store_unlocked():
    if not DATA_FILE.exists():
        return {
            "players": [],
            "matches": [],
            "tournament_matches": [],
            "news": [],
            "community_posts": [],
            "accounts": [],
            "tac_requests": [],
        }
    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_store_unlocked(store):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def load_store():
    with LOCK:
        return load_store_unlocked()


def update_store(mutator):
    with LOCK:
        store = load_store_unlocked()
        result = mutator(store)
        save_store_unlocked(store)
    return result


def ntrp_to_group(ntrp):
    try:
        score = float(ntrp)
    except (TypeError, ValueError):
        return "D5"
    if score >= 4.5:
        return "D1"
    if score >= 4.0:
        return "D2"
    if score >= 3.5:
        return "D3"
    if score >= 3.0:
        return "D4"
    return "D5"


def parse_score_sets(score):
    sets = []
    for part in (score or "").split(","):
        match = re.match(r"\s*(\d+)\s*[:\-]\s*(\d+)\s*$", part)
        if not match:
            continue
        sets.append((int(match.group(1)), int(match.group(2))))
    return sets


def determine_winner_from_score(player_a_id, player_b_id, score):
    wins_a = 0
    wins_b = 0
    for a, b in parse_score_sets(score):
        if a > b:
            wins_a += 1
        elif b > a:
            wins_b += 1
    if wins_a == wins_b:
        return None
    return player_a_id if wins_a > wins_b else player_b_id


def player_index(players):
    return {player["id"]: player for player in players}


def enrich_match(match, players_by_id):
    player_a = players_by_id.get(match.get("player_a_id"), {})
    player_b = players_by_id.get(match.get("player_b_id"), {})
    winner = players_by_id.get(match.get("winner_id"), {})
    loser_id = None
    if match.get("winner_id") == match.get("player_a_id"):
        loser_id = match.get("player_b_id")
    elif match.get("winner_id") == match.get("player_b_id"):
        loser_id = match.get("player_a_id")
    loser = players_by_id.get(loser_id, {}) if loser_id else {}
    hydrated = dict(match)
    hydrated["player_a_name"] = player_a.get("display_name", "Unknown")
    hydrated["player_b_name"] = player_b.get("display_name", "Unknown")
    hydrated["winner_name"] = winner.get("display_name", "TBD")
    hydrated["loser_name"] = loser.get("display_name", "TBD")
    return hydrated


def compute_player_stats(store, player_id):
    matches = [
        m
        for m in store.get("matches", [])
        if m.get("status") == "completed"
        and player_id in (m.get("player_a_id"), m.get("player_b_id"))
    ]
    won = sum(1 for m in matches if m.get("winner_id") == player_id)
    lost = len(matches) - won
    return {
        "played": len(matches),
        "won": won,
        "lost": lost,
    }


def compute_standings(store, season, group=None):
    players = [p for p in store.get("players", []) if p.get("active", True)]
    if group and group != "ALL":
        players = [p for p in players if p.get("group") == group]

    table = {}
    for p in players:
        table[p["id"]] = {
            "player_id": p["id"],
            "name": p.get("display_name", "Unknown"),
            "group": p.get("group", "D5"),
            "ntrp": p.get("ntrp", "-"),
            "played": 0,
            "won": 0,
            "lost": 0,
            "sets_won": 0,
            "sets_lost": 0,
            "points": 0,
            "paid": bool(p.get("tac_verified", False)),
        }

    for m in store.get("matches", []):
        if m.get("stage") != "REGULAR":
            continue
        if m.get("status") != "completed":
            continue
        if season and m.get("season") != season:
            continue

        a_id = m.get("player_a_id")
        b_id = m.get("player_b_id")
        if a_id not in table or b_id not in table:
            continue

        table[a_id]["played"] += 1
        table[b_id]["played"] += 1

        winner_id = m.get("winner_id")
        if winner_id == a_id:
            table[a_id]["won"] += 1
            table[b_id]["lost"] += 1
            table[a_id]["points"] += 3
            table[b_id]["points"] += 1
        elif winner_id == b_id:
            table[b_id]["won"] += 1
            table[a_id]["lost"] += 1
            table[b_id]["points"] += 3
            table[a_id]["points"] += 1

        for s1, s2 in parse_score_sets(m.get("score", "")):
            table[a_id]["sets_won"] += s1
            table[a_id]["sets_lost"] += s2
            table[b_id]["sets_won"] += s2
            table[b_id]["sets_lost"] += s1

    rows = list(table.values())
    rows.sort(
        key=lambda r: (
            r["points"],
            r["sets_won"] - r["sets_lost"],
            r["won"],
            r["name"].lower(),
        ),
        reverse=True,
    )

    for i, row in enumerate(rows):
        row["rank"] = i + 1

    return rows


def send_whatsapp_tac(whatsapp_number, code):
    phone_id = os.getenv("WHATSAPP_PHONE_ID")
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    template_name = os.getenv("WHATSAPP_TEMPLATE_NAME", "verification_code")

    if not phone_id or not access_token:
        print(f"[WhatsApp Mock] Send TAC {code} to +{whatsapp_number}")
        return {
            "provider": "mock",
            "delivered": True,
            "detail": "WhatsApp API not configured. TAC printed in server logs.",
        }

    payload = {
        "messaging_product": "whatsapp",
        "to": whatsapp_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en_US"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": code}],
                }
            ],
        },
    }

    req = urllib.request.Request(
        url=f"https://graph.facebook.com/v21.0/{phone_id}/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return {"provider": "whatsapp-cloud", "delivered": True, "detail": body}
    except Exception as exc:
        print(f"[WhatsApp Error] {exc}")
        return {
            "provider": "whatsapp-cloud",
            "delivered": False,
            "detail": str(exc),
        }


class TennisHandler(BaseHTTPRequestHandler):
    server_version = "TennisServer/1.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}")

    def send_json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-admin-token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, status, payload, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def parse_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON payload")

    def is_admin(self):
        token = self.headers.get("x-admin-token", "")
        return token == ADMIN_TOKEN

    def require_admin(self):
        if not self.is_admin():
            self.send_json(401, {"error": "Admin token is invalid."})
            return False
        return True

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-admin-token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            self.handle_api(method, path, query)
            return

        if method != "GET":
            self.send_json(405, {"error": "Method not allowed."})
            return

        self.handle_static(path)

    def handle_static(self, path):
        safe = path or "/"
        if safe == "/":
            safe = "/index.html"

        target = (ROOT_DIR / safe.lstrip("/")).resolve()
        if not str(target).startswith(str(ROOT_DIR.resolve())):
            self.send_json(403, {"error": "Forbidden"})
            return

        if not target.exists() or target.is_dir():
            self.send_json(404, {"error": "Not found"})
            return

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        with target.open("rb") as f:
            payload = f.read()
        self.send_file(200, payload, content_type)

    def handle_api(self, method, path, query):
        try:
            if path == "/api/health" and method == "GET":
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "service": "tennis-backend",
                        "now": utc_now(),
                        "default_season": DEFAULT_SEASON,
                    },
                )
                return

            if path == "/api/auth/request-tac" and method == "POST":
                self.api_request_tac()
                return

            if path == "/api/auth/verify-tac" and method == "POST":
                self.api_verify_tac()
                return

            if path == "/api/auth/login-by-phone" and method == "POST":
                self.api_login_by_phone()
                return

            if path == "/api/players" and method == "GET":
                self.api_get_players(query)
                return

            match = re.fullmatch(r"/api/players/([A-Za-z0-9_\-]+)", path)
            if match and method == "GET":
                self.api_get_player(match.group(1))
                return

            if path == "/api/players/by-phone" and method == "GET":
                self.api_get_player_by_phone(query)
                return

            if path == "/api/matches" and method == "GET":
                self.api_get_matches(query)
                return

            if path == "/api/matches" and method == "POST":
                self.api_create_match_public()
                return

            if path == "/api/standings" and method == "GET":
                self.api_get_standings(query)
                return

            if path == "/api/news" and method == "GET":
                self.api_get_news(query)
                return

            if path == "/api/community" and method == "GET":
                self.api_get_community(query)
                return

            if path == "/api/community" and method == "POST":
                self.api_create_community_post()
                return

            if path == "/api/tournament" and method == "GET":
                self.api_get_tournament(query)
                return

            if path == "/api/profile" and method == "GET":
                self.api_get_profile(query)
                return

            match = re.fullmatch(r"/api/profile/([A-Za-z0-9_\-]+)", path)
            if match and method == "PUT":
                self.api_update_profile(match.group(1))
                return

            if path == "/api/admin/dashboard" and method == "GET":
                if not self.require_admin():
                    return
                self.api_admin_dashboard()
                return

            if path == "/api/admin/players" and method == "POST":
                if not self.require_admin():
                    return
                self.api_admin_create_player()
                return

            match = re.fullmatch(r"/api/admin/players/([A-Za-z0-9_\-]+)", path)
            if match and method == "PUT":
                if not self.require_admin():
                    return
                self.api_admin_update_player(match.group(1))
                return

            if match and method == "DELETE":
                if not self.require_admin():
                    return
                self.api_admin_delete_player(match.group(1))
                return

            if path == "/api/admin/matches" and method == "POST":
                if not self.require_admin():
                    return
                self.api_admin_create_match()
                return

            match = re.fullmatch(r"/api/admin/matches/([A-Za-z0-9_\-]+)", path)
            if match and method == "PUT":
                if not self.require_admin():
                    return
                self.api_admin_update_match(match.group(1))
                return

            if match and method == "DELETE":
                if not self.require_admin():
                    return
                self.api_admin_delete_match(match.group(1))
                return

            if path == "/api/admin/news" and method == "POST":
                if not self.require_admin():
                    return
                self.api_admin_create_news()
                return

            match = re.fullmatch(r"/api/admin/news/([A-Za-z0-9_\-]+)", path)
            if match and method == "DELETE":
                if not self.require_admin():
                    return
                self.api_admin_delete_news(match.group(1))
                return

            if path == "/api/admin/tournament/matches" and method == "POST":
                if not self.require_admin():
                    return
                self.api_admin_upsert_tournament_match()
                return

            match = re.fullmatch(r"/api/admin/tournament/matches/([A-Za-z0-9_\-]+)", path)
            if match and method == "DELETE":
                if not self.require_admin():
                    return
                self.api_admin_delete_tournament_match(match.group(1))
                return

            match = re.fullmatch(r"/api/admin/community/([A-Za-z0-9_\-]+)", path)
            if match and method == "DELETE":
                if not self.require_admin():
                    return
                self.api_admin_delete_community_post(match.group(1))
                return

            self.send_json(404, {"error": "Route not found."})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": "Internal server error", "detail": str(exc)})

    def api_request_tac(self):
        payload = self.parse_json_body()
        display_name = (payload.get("display_name") or "").strip()
        ntrp = (payload.get("ntrp") or "").strip()
        country_code = (payload.get("country_code") or "+60").strip()
        phone = digits(payload.get("phone"))
        accept_tac = bool(payload.get("accept_tac"))

        if not display_name:
            raise ValueError("Display name is required.")
        if not ntrp:
            raise ValueError("NTRP level is required.")
        if len(phone) < 7:
            raise ValueError("Valid phone number is required.")
        if not accept_tac:
            raise ValueError("You must accept Terms and Conditions.")

        whatsapp_number = digits(country_code + phone)
        code = f"{random.randint(0, 999999):06d}"
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0)

        def mutate(store):
            request = {
                "id": new_id("tac"),
                "display_name": display_name,
                "ntrp": ntrp,
                "country_code": country_code,
                "phone": phone,
                "whatsapp_number": whatsapp_number,
                "code": code,
                "accept_tac": True,
                "verified": False,
                "created_at": utc_now(),
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            }
            store.setdefault("tac_requests", []).append(request)
            return request

        saved = update_store(mutate)
        provider_response = send_whatsapp_tac(whatsapp_number, code)

        body = {
            "ok": True,
            "message": "TAC sent to WhatsApp.",
            "expires_in_seconds": 300,
            "provider": provider_response,
            "request_id": saved["id"],
        }
        if os.getenv("EXPOSE_TAC_CODE", "true").lower() == "true":
            body["dev_tac_code"] = code

        self.send_json(200, body)

    def api_verify_tac(self):
        payload = self.parse_json_body()
        country_code = (payload.get("country_code") or "+60").strip()
        phone = digits(payload.get("phone"))
        code = digits(payload.get("code"))

        if len(phone) < 7:
            raise ValueError("Phone is required.")
        if len(code) < 4:
            raise ValueError("TAC code is required.")

        now_dt = datetime.now(timezone.utc)

        def mutate(store):
            requests = store.setdefault("tac_requests", [])
            target = None
            for req in reversed(requests):
                expires = datetime.fromisoformat(req["expires_at"].replace("Z", "+00:00"))
                if (
                    req.get("country_code") == country_code
                    and req.get("phone") == phone
                    and not req.get("verified")
                    and expires > now_dt
                ):
                    target = req
                    break

            if not target:
                raise ValueError("No valid TAC request found. Please request a new TAC.")
            if target.get("code") != code:
                raise ValueError("Invalid TAC code.")

            target["verified"] = True
            target["verified_at"] = utc_now()

            whatsapp_number = target.get("whatsapp_number")
            existing = None
            for p in store.setdefault("players", []):
                if p.get("whatsapp_number") == whatsapp_number:
                    existing = p
                    break

            if existing is None:
                player = {
                    "id": new_id("p"),
                    "display_name": target.get("display_name"),
                    "ntrp": target.get("ntrp"),
                    "group": ntrp_to_group(target.get("ntrp")),
                    "phone": target.get("phone"),
                    "country_code": target.get("country_code"),
                    "whatsapp_number": whatsapp_number,
                    "email": "",
                    "bio": "",
                    "registered_via_whatsapp": True,
                    "tac_verified": True,
                    "active": True,
                    "created_at": utc_now(),
                }
                store["players"].append(player)
            else:
                existing["tac_verified"] = True
                existing["active"] = True
                existing["display_name"] = target.get("display_name") or existing.get("display_name")
                existing["ntrp"] = target.get("ntrp") or existing.get("ntrp")
                existing["group"] = ntrp_to_group(existing.get("ntrp"))
                player = existing

            account = None
            for acc in store.setdefault("accounts", []):
                if acc.get("player_id") == player["id"]:
                    account = acc
                    break

            if account is None:
                account = {
                    "id": new_id("a"),
                    "player_id": player["id"],
                    "display_name": player.get("display_name"),
                    "email": player.get("email", ""),
                    "bio": player.get("bio", ""),
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
                store["accounts"].append(account)
            else:
                account["display_name"] = player.get("display_name")
                account["updated_at"] = utc_now()

            return {"player": player, "account": account}

        result = update_store(mutate)
        session_token = new_id("sess")
        self.send_json(
            200,
            {
                "ok": True,
                "message": "Registration complete.",
                "session": {
                    "token": session_token,
                    "player_id": result["player"]["id"],
                    "player_name": result["player"].get("display_name"),
                    "whatsapp_number": result["player"].get("whatsapp_number"),
                },
                "player": result["player"],
                "account": result["account"],
            },
        )

    def api_login_by_phone(self):
        payload = self.parse_json_body()
        country_code = (payload.get("country_code") or "+60").strip()
        phone = digits(payload.get("phone"))
        if len(phone) < 7:
            raise ValueError("Valid phone number is required.")

        store = load_store()
        target = None
        for p in store.get("players", []):
            if p.get("country_code") == country_code and p.get("phone") == phone:
                target = p
                break
        if not target:
            self.send_json(404, {"error": "Player not found. Please register first."})
            return

        self.send_json(
            200,
            {
                "ok": True,
                "session": {
                    "token": new_id("sess"),
                    "player_id": target["id"],
                    "player_name": target.get("display_name"),
                    "whatsapp_number": target.get("whatsapp_number"),
                },
                "player": target,
            },
        )

    def api_get_players(self, query):
        group = (query.get("group", ["ALL"])[0] or "ALL").upper()
        search = (query.get("search", [""])[0] or "").strip().lower()

        store = load_store()
        players = [p for p in store.get("players", []) if p.get("active", True)]

        if group != "ALL":
            players = [p for p in players if p.get("group") == group]
        if search:
            players = [p for p in players if search in p.get("display_name", "").lower()]

        players.sort(key=lambda p: p.get("display_name", "").lower())
        self.send_json(200, {"players": players})

    def api_get_player(self, player_id):
        store = load_store()
        for p in store.get("players", []):
            if p.get("id") == player_id:
                stats = compute_player_stats(store, player_id)
                self.send_json(200, {"player": p, "stats": stats})
                return
        self.send_json(404, {"error": "Player not found."})

    def api_get_player_by_phone(self, query):
        country_code = (query.get("country_code", ["+60"])[0] or "+60").strip()
        phone = digits(query.get("phone", [""])[0])
        if not phone:
            raise ValueError("phone query is required.")

        store = load_store()
        for p in store.get("players", []):
            if p.get("country_code") == country_code and p.get("phone") == phone:
                self.send_json(200, {"player": p})
                return
        self.send_json(404, {"error": "Player not found."})

    def api_get_matches(self, query):
        season = query.get("season", [DEFAULT_SEASON])[0] or DEFAULT_SEASON
        stage = (query.get("stage", [""])[0] or "").upper().strip()

        store = load_store()
        players_by_id = player_index(store.get("players", []))
        matches = [m for m in store.get("matches", []) if m.get("season") == season]
        if stage:
            matches = [m for m in matches if m.get("stage") == stage]

        matches.sort(key=lambda m: (m.get("date", ""), m.get("created_at", "")), reverse=True)
        self.send_json(200, {"matches": [enrich_match(m, players_by_id) for m in matches]})

    def validate_match_payload(self, payload, store, admin=False):
        season = (payload.get("season") or DEFAULT_SEASON).strip()
        stage = (payload.get("stage") or "REGULAR").strip().upper()
        date = (payload.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
        player_a_id = (payload.get("player_a_id") or "").strip()
        player_b_id = (payload.get("player_b_id") or "").strip()
        winner_id = (payload.get("winner_id") or "").strip()
        score = (payload.get("score") or "").strip()

        if not player_a_id or not player_b_id:
            raise ValueError("Both players are required.")
        if player_a_id == player_b_id:
            raise ValueError("Players must be different.")

        players_by_id = player_index(store.get("players", []))
        if player_a_id not in players_by_id or player_b_id not in players_by_id:
            raise ValueError("Invalid player ID.")

        if not winner_id:
            winner_id = determine_winner_from_score(player_a_id, player_b_id, score)
        if winner_id not in (player_a_id, player_b_id):
            raise ValueError("Winner must be one of the selected players.")

        if not score:
            raise ValueError("Score is required.")

        return {
            "season": season,
            "stage": stage,
            "date": date,
            "player_a_id": player_a_id,
            "player_b_id": player_b_id,
            "winner_id": winner_id,
            "score": score,
            "status": "completed",
            "created_by": payload.get("created_by", "admin" if admin else "player"),
        }

    def api_create_match_public(self):
        payload = self.parse_json_body()

        def mutate(store):
            match_data = self.validate_match_payload(payload, store, admin=False)
            match_data["id"] = new_id("m")
            match_data["created_at"] = utc_now()
            store.setdefault("matches", []).append(match_data)
            return match_data

        created = update_store(mutate)
        store = load_store()
        players_by_id = player_index(store.get("players", []))
        self.send_json(201, {"ok": True, "match": enrich_match(created, players_by_id)})

    def api_get_standings(self, query):
        season = query.get("season", [DEFAULT_SEASON])[0] or DEFAULT_SEASON
        group = (query.get("group", ["ALL"])[0] or "ALL").upper()
        rows = compute_standings(load_store(), season, group)
        self.send_json(200, {"season": season, "group": group, "rows": rows})

    def api_get_news(self, query):
        category = (query.get("category", ["All"])[0] or "All").strip()
        search = (query.get("search", [""])[0] or "").lower().strip()

        items = list(load_store().get("news", []))
        if category != "All":
            items = [n for n in items if n.get("category") == category]
        if search:
            items = [
                n
                for n in items
                if search in n.get("title", "").lower() or search in n.get("content", "").lower()
            ]
        items.sort(key=lambda n: n.get("created_at", ""), reverse=True)
        self.send_json(200, {"news": items})

    def api_get_community(self, query):
        search = (query.get("search", [""])[0] or "").lower().strip()
        items = list(load_store().get("community_posts", []))
        if search:
            items = [
                p
                for p in items
                if search in p.get("author", "").lower() or search in p.get("content", "").lower()
            ]
        items.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        self.send_json(200, {"posts": items})

    def api_create_community_post(self):
        payload = self.parse_json_body()
        author = (payload.get("author") or "").strip()
        content = (payload.get("content") or "").strip()
        player_id = (payload.get("player_id") or "").strip()

        if not author:
            raise ValueError("Author is required.")
        if len(content) < 3:
            raise ValueError("Community post is too short.")

        def mutate(store):
            post = {
                "id": new_id("c"),
                "author": author,
                "player_id": player_id,
                "content": content,
                "created_at": utc_now(),
            }
            store.setdefault("community_posts", []).append(post)
            return post

        post = update_store(mutate)
        self.send_json(201, {"ok": True, "post": post})

    def api_get_tournament(self, query):
        season = query.get("season", [DEFAULT_SEASON])[0] or DEFAULT_SEASON
        store = load_store()
        players_by_id = player_index(store.get("players", []))

        matches = [m for m in store.get("tournament_matches", []) if m.get("season") == season]
        round_order = {"R32": 1, "R16": 2, "QF": 3, "SF": 4, "F": 5}
        matches.sort(key=lambda m: (round_order.get(m.get("round"), 99), m.get("slot", 999)))

        rows = []
        for m in matches:
            row = dict(m)
            row["player1_name"] = players_by_id.get(m.get("player1_id"), {}).get("display_name", "---")
            row["player2_name"] = players_by_id.get(m.get("player2_id"), {}).get("display_name", "---")
            row["winner_name"] = players_by_id.get(m.get("winner_id"), {}).get("display_name", "TBD")
            rows.append(row)

        self.send_json(200, {"season": season, "matches": rows})

    def api_get_profile(self, query):
        player_id = (query.get("player_id", [""])[0] or "").strip()
        if not player_id:
            raise ValueError("player_id is required.")

        store = load_store()
        player = next((p for p in store.get("players", []) if p.get("id") == player_id), None)
        if not player:
            self.send_json(404, {"error": "Player not found."})
            return

        account = next((a for a in store.get("accounts", []) if a.get("player_id") == player_id), None)
        players_by_id = player_index(store.get("players", []))
        recent_matches = [
            enrich_match(m, players_by_id)
            for m in store.get("matches", [])
            if player_id in (m.get("player_a_id"), m.get("player_b_id"))
        ]
        recent_matches.sort(key=lambda m: (m.get("date", ""), m.get("created_at", "")), reverse=True)

        self.send_json(
            200,
            {
                "player": player,
                "account": account,
                "stats": compute_player_stats(store, player_id),
                "recent_matches": recent_matches[:10],
            },
        )

    def api_update_profile(self, player_id):
        payload = self.parse_json_body()

        def mutate(store):
            player = next((p for p in store.get("players", []) if p.get("id") == player_id), None)
            if not player:
                raise ValueError("Player not found.")

            player["display_name"] = (payload.get("display_name") or player.get("display_name") or "").strip()
            player["ntrp"] = (payload.get("ntrp") or player.get("ntrp") or "").strip()
            player["group"] = (payload.get("group") or ntrp_to_group(player.get("ntrp"))).strip()
            player["email"] = (payload.get("email") or player.get("email") or "").strip()
            player["bio"] = (payload.get("bio") or player.get("bio") or "").strip()
            player["updated_at"] = utc_now()

            account = next((a for a in store.setdefault("accounts", []) if a.get("player_id") == player_id), None)
            if account is None:
                account = {
                    "id": new_id("a"),
                    "player_id": player_id,
                    "display_name": player.get("display_name"),
                    "email": player.get("email", ""),
                    "bio": player.get("bio", ""),
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
                store["accounts"].append(account)
            else:
                account["display_name"] = player.get("display_name")
                account["email"] = player.get("email", "")
                account["bio"] = player.get("bio", "")
                account["updated_at"] = utc_now()

            return {"player": player, "account": account}

        updated = update_store(mutate)
        self.send_json(200, {"ok": True, **updated})

    def api_admin_dashboard(self):
        store = load_store()
        self.send_json(
            200,
            {
                "counts": {
                    "players": len(store.get("players", [])),
                    "matches": len(store.get("matches", [])),
                    "news": len(store.get("news", [])),
                    "community_posts": len(store.get("community_posts", [])),
                },
                "admin_token_hint": "Set ADMIN_TOKEN env in production.",
            },
        )

    def api_admin_create_player(self):
        payload = self.parse_json_body()
        display_name = (payload.get("display_name") or "").strip()
        ntrp = (payload.get("ntrp") or "3.0").strip()
        group = (payload.get("group") or ntrp_to_group(ntrp)).strip().upper()
        country_code = (payload.get("country_code") or "+60").strip()
        phone = digits(payload.get("phone"))
        email = (payload.get("email") or "").strip()
        bio = (payload.get("bio") or "").strip()

        if not display_name:
            raise ValueError("Display name is required.")

        def mutate(store):
            player = {
                "id": new_id("p"),
                "display_name": display_name,
                "ntrp": ntrp,
                "group": group,
                "phone": phone,
                "country_code": country_code,
                "whatsapp_number": digits(country_code + phone),
                "email": email,
                "bio": bio,
                "registered_via_whatsapp": bool(phone),
                "tac_verified": True,
                "active": True,
                "created_at": utc_now(),
            }
            store.setdefault("players", []).append(player)
            return player

        player = update_store(mutate)
        self.send_json(201, {"ok": True, "player": player})

    def api_admin_update_player(self, player_id):
        payload = self.parse_json_body()

        def mutate(store):
            player = next((p for p in store.get("players", []) if p.get("id") == player_id), None)
            if not player:
                raise ValueError("Player not found.")

            for field in ["display_name", "ntrp", "group", "email", "bio", "country_code"]:
                if field in payload and payload[field] is not None:
                    player[field] = str(payload[field]).strip()

            if "phone" in payload:
                player["phone"] = digits(payload.get("phone"))
            if "group" not in payload and "ntrp" in payload:
                player["group"] = ntrp_to_group(player.get("ntrp"))

            player["whatsapp_number"] = digits(player.get("country_code", "+60") + player.get("phone", ""))
            player["updated_at"] = utc_now()
            return player

        player = update_store(mutate)
        self.send_json(200, {"ok": True, "player": player})

    def api_admin_delete_player(self, player_id):
        def mutate(store):
            players = store.get("players", [])
            before = len(players)
            store["players"] = [p for p in players if p.get("id") != player_id]
            if len(store["players"]) == before:
                raise ValueError("Player not found.")

            store["matches"] = [
                m
                for m in store.get("matches", [])
                if m.get("player_a_id") != player_id and m.get("player_b_id") != player_id
            ]
            store["tournament_matches"] = [
                m
                for m in store.get("tournament_matches", [])
                if m.get("player1_id") != player_id and m.get("player2_id") != player_id
            ]
            store["accounts"] = [a for a in store.get("accounts", []) if a.get("player_id") != player_id]

        update_store(mutate)
        self.send_json(200, {"ok": True})

    def api_admin_create_match(self):
        payload = self.parse_json_body()

        def mutate(store):
            match_data = self.validate_match_payload(payload, store, admin=True)
            match_data["id"] = new_id("m")
            match_data["created_at"] = utc_now()
            store.setdefault("matches", []).append(match_data)
            return match_data

        created = update_store(mutate)
        players_by_id = player_index(load_store().get("players", []))
        self.send_json(201, {"ok": True, "match": enrich_match(created, players_by_id)})

    def api_admin_update_match(self, match_id):
        payload = self.parse_json_body()

        def mutate(store):
            target = next((m for m in store.get("matches", []) if m.get("id") == match_id), None)
            if not target:
                raise ValueError("Match not found.")

            current = dict(target)
            current.update(payload)
            validated = self.validate_match_payload(current, store, admin=True)
            for key, value in validated.items():
                target[key] = value
            target["updated_at"] = utc_now()
            return target

        updated = update_store(mutate)
        players_by_id = player_index(load_store().get("players", []))
        self.send_json(200, {"ok": True, "match": enrich_match(updated, players_by_id)})

    def api_admin_delete_match(self, match_id):
        def mutate(store):
            matches = store.get("matches", [])
            before = len(matches)
            store["matches"] = [m for m in matches if m.get("id") != match_id]
            if len(store["matches"]) == before:
                raise ValueError("Match not found.")

        update_store(mutate)
        self.send_json(200, {"ok": True})

    def api_admin_create_news(self):
        payload = self.parse_json_body()
        title = (payload.get("title") or "").strip()
        category = (payload.get("category") or "Official").strip()
        content = (payload.get("content") or "").strip()
        image = (payload.get("image") or "").strip()
        if not title or not content:
            raise ValueError("Title and content are required.")

        def mutate(store):
            item = {
                "id": new_id("n"),
                "title": title,
                "category": category,
                "content": content,
                "image": image,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "created_at": utc_now(),
            }
            store.setdefault("news", []).append(item)
            return item

        item = update_store(mutate)
        self.send_json(201, {"ok": True, "news": item})

    def api_admin_delete_news(self, news_id):
        def mutate(store):
            news = store.get("news", [])
            before = len(news)
            store["news"] = [n for n in news if n.get("id") != news_id]
            if len(store["news"]) == before:
                raise ValueError("News post not found.")

        update_store(mutate)
        self.send_json(200, {"ok": True})

    def api_admin_upsert_tournament_match(self):
        payload = self.parse_json_body()

        required = ["season", "round", "slot", "player1_id", "player2_id", "winner_id", "score", "date"]
        for field in required:
            if field not in payload:
                raise ValueError(f"{field} is required.")

        def mutate(store):
            existing = None
            for m in store.setdefault("tournament_matches", []):
                if m.get("season") == payload["season"] and m.get("round") == payload["round"] and int(m.get("slot", -1)) == int(payload["slot"]):
                    existing = m
                    break

            if existing is None:
                existing = {"id": new_id("t")}
                store["tournament_matches"].append(existing)

            existing["season"] = str(payload["season"]).strip()
            existing["round"] = str(payload["round"]).strip().upper()
            existing["slot"] = int(payload["slot"])
            existing["player1_id"] = str(payload["player1_id"]).strip()
            existing["player2_id"] = str(payload["player2_id"]).strip()
            existing["winner_id"] = str(payload["winner_id"]).strip()
            existing["score"] = str(payload["score"]).strip()
            existing["date"] = str(payload["date"]).strip()
            existing["updated_at"] = utc_now()
            return existing

        row = update_store(mutate)
        self.send_json(200, {"ok": True, "match": row})

    def api_admin_delete_tournament_match(self, match_id):
        def mutate(store):
            items = store.get("tournament_matches", [])
            before = len(items)
            store["tournament_matches"] = [m for m in items if m.get("id") != match_id]
            if len(store["tournament_matches"]) == before:
                raise ValueError("Tournament match not found.")

        update_store(mutate)
        self.send_json(200, {"ok": True})

    def api_admin_delete_community_post(self, post_id):
        def mutate(store):
            items = store.get("community_posts", [])
            before = len(items)
            store["community_posts"] = [p for p in items if p.get("id") != post_id]
            if len(store["community_posts"]) == before:
                raise ValueError("Community post not found.")

        update_store(mutate)
        self.send_json(200, {"ok": True})


def run():
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        save_store_unlocked(load_store_unlocked())

    server = ThreadingHTTPServer((host, port), TennisHandler)
    print(f"Tennis app server running at http://{host}:{port}")
    print(f"Admin token: {ADMIN_TOKEN}")
    server.serve_forever()


if __name__ == "__main__":
    run()
