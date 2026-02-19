#!/usr/bin/env python3
"""
BEHA SmartHeater Cloud API — Full Control (Auto-Discovery)

Usage:
    python3 beha_auth.py login                  # Login (opens browser)
    python3 beha_auth.py login-headless         # Login without browser (needs email/password)
    python3 beha_auth.py refresh                # Refresh expired token
    python3 beha_auth.py token                  # Print access token
    python3 beha_auth.py status                 # Show all heaters (auto-discovered)
    python3 beha_auth.py set <room_name> <temp> # Set temperature by room name
    python3 beha_auth.py discover               # Print discovery JSON
"""

import sys, os, json, time, hashlib, base64, secrets, webbrowser, urllib.parse, urllib.request, re
import http.cookiejar
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Azure B2C Configuration ─────────────────────────────────────────────────
TENANT       = "targetb2corganisation"
POLICY       = "b2c_1_local_account_login"
TENANT_ID    = "0962aeff-8b11-4faa-ac95-d81de2004e5c"
CLIENT_ID    = "ded3ee19-b103-4c53-812e-5190e009d160"
SCOPE        = "https://targetb2corganisation.onmicrosoft.com/beha-backend-prod/admin-access openid profile offline_access"
REDIRECT_URI = "msauth.com.beha.wifi-smartheater://auth"

BASE_URL     = f"https://{TENANT}.b2clogin.com/{TENANT_ID}/{POLICY}"
AUTH_URL     = f"{BASE_URL}/oauth2/v2.0/authorize"
TOKEN_URL    = f"{BASE_URL}/oauth2/v2.0/token"

# ── BEHA Cloud API ──────────────────────────────────────────────────────────
API_BASE     = "https://behacloud.com/api"

TOKEN_FILE = os.path.expanduser("~/.beha_tokens.json")


# ── PKCE ─────────────────────────────────────────────────────────────────────
def generate_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ── Token Storage ────────────────────────────────────────────────────────────
def save_tokens(data):
    data["saved_at"] = int(time.time())
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)

def load_tokens():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f)


# ── Headless Login (no browser — pure HTTP) ──────────────────────────────────
def login_headless(email=None, password=None):
    if not email:
        email = input("Email: ")
    if not password:
        import getpass
        password = getpass.getpass("Password: ")

    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)

    cj = http.cookiejar.CookieJar()

    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def http_error_302(self, req, fp, code, msg, hdrs):
            loc = hdrs.get("Location", "")
            if loc.startswith("msauth"):
                raise urllib.error.HTTPError(req.full_url, code, msg, hdrs, fp)
            return urllib.request.HTTPRedirectHandler.http_error_302(self, req, fp, code, msg, hdrs)
        http_error_301 = http_error_303 = http_error_307 = http_error_302

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), SafeRedirect)

    # 1. GET login page
    params = {
        "client_id": CLIENT_ID, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "scope": SCOPE, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "prompt": "login",
    }
    print("🔐 Logging in...")
    resp = opener.open(urllib.request.Request(
        f"{AUTH_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0"}
    ))
    html = resp.read().decode()
    page_url = resp.url

    csrf = re.search(r'"csrf"\s*:\s*"([^"]+)"', html).group(1)
    trans = re.search(r'StateProperties=([^&"\']+)', html).group(1)

    # 2. POST credentials
    sa_url = f"{BASE_URL}/SelfAsserted?tx=StateProperties%3D{urllib.parse.quote(trans)}&p=B2C_1_LOCAL_ACCOUNT_LOGIN"
    form_data = urllib.parse.urlencode({
        "request_type": "RESPONSE",
        "email": email,
        "password": password,
    }).encode()

    resp2 = opener.open(urllib.request.Request(sa_url, data=form_data, headers={
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-CSRF-TOKEN": csrf,
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest",
    }))
    body = resp2.read().decode()
    if '"400"' in body or "error" in body.lower():
        raise Exception(f"Login failed: {body}")

    # 3. GET confirmed -> redirect with auth code
    confirmed = f"{BASE_URL}/api/CombinedSigninAndSignup/confirmed?rememberMe=false&csrf_token={csrf}&tx=StateProperties%3D{urllib.parse.quote(trans)}&p=B2C_1_LOCAL_ACCOUNT_LOGIN"

    class NoRed(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a): return None

    opener2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), NoRed())
    auth_code = None
    try:
        opener2.open(urllib.request.Request(confirmed, headers={"User-Agent": "Mozilla/5.0", "Referer": page_url}))
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if "code=" in loc:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
            auth_code = parsed["code"][0]
        else:
            raise Exception(f"No auth code in redirect: {loc}")

    if not auth_code:
        raise Exception("Failed to get auth code")

    # 4. Exchange code for tokens
    token_data = urllib.parse.urlencode({
        "grant_type": "authorization_code", "client_id": CLIENT_ID,
        "code": auth_code, "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier, "scope": SCOPE,
    }).encode()
    resp4 = urllib.request.urlopen(urllib.request.Request(
        TOKEN_URL, data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    ))
    tokens = json.loads(resp4.read())
    save_tokens(tokens)

    print(f"✅ Logged in! Token expires in {tokens.get('expires_in', '?')}s")
    if "refresh_token" in tokens:
        print("🔄 Refresh token saved")
    return tokens["access_token"]


# ── Browser Login (fallback) ────────────────────────────────────────────────
def login_browser():
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": CLIENT_ID, "response_type": "code",
        "redirect_uri": "http://localhost:9876/callback",
        "scope": SCOPE, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "login",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    auth_code = [None]

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                auth_code[0] = qs["code"][0]
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(b"<h1>&#10004; Login successful!</h1>")
            else:
                self.send_response(400); self.end_headers()
        def log_message(self, *a): pass

    server = HTTPServer(("127.0.0.1", 9876), H)
    webbrowser.open(url)
    print("⏳ Waiting for browser login...")
    while auth_code[0] is None:
        server.handle_request()
    server.server_close()

    data = urllib.parse.urlencode({
        "grant_type": "authorization_code", "client_id": CLIENT_ID,
        "code": auth_code[0], "redirect_uri": "http://localhost:9876/callback",
        "code_verifier": verifier, "scope": SCOPE,
    }).encode()
    resp = urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}))
    tokens = json.loads(resp.read())
    save_tokens(tokens)
    print(f"✅ Logged in! Token expires in {tokens.get('expires_in', '?')}s")
    return tokens["access_token"]


# ── Refresh ──────────────────────────────────────────────────────────────────
def refresh():
    tokens = load_tokens()
    if not tokens or "refresh_token" not in tokens:
        raise Exception("No refresh token found. Run login first.")
    
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "refresh_token": tokens["refresh_token"], "scope": SCOPE,
    }).encode()
    try:
        resp = urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}))
        new_tokens = json.loads(resp.read())
        save_tokens(new_tokens)
        print(f"✅ Refreshed! Expires in {new_tokens.get('expires_in', '?')}s")
        return new_tokens["access_token"]
    except urllib.error.HTTPError as e:
        raise Exception(f"Refresh failed: {e.read().decode()}")


# ── Get Valid Token ──────────────────────────────────────────────────────────
def get_token():
    tokens = load_tokens()
    if not tokens:
        raise Exception("No tokens found. Run login first.")
    
    expires_at = tokens.get("saved_at", 0) + tokens.get("expires_in", 0)
    if time.time() > expires_at - 60:
        if "refresh_token" in tokens:
            return refresh()
        raise Exception("Token expired and no refresh token")
    return tokens["access_token"]


# ── API ──────────────────────────────────────────────────────────────────────
def api(method, path, body=None):
    token = get_token()
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{API_BASE}/{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()) if resp.status != 204 else None
    except urllib.error.HTTPError as e:
        raise Exception(f"API Error {e.code}: {e.read().decode()}")


def show_status():
    print("\n🏠 BEHA Heater Status\n")
    discovery = get_discovery_info()
    for room in discovery:
        try:
            off = any(h.get("is_offline", False) for h in room["heaters"])
            name = room["room_name"]
            temp = room.get("latest_temperature", "?")
            target = room.get("target_temperature", "?")
            temp_str = f"{temp:5.1f}" if isinstance(temp, (int, float)) else str(temp)
            print(f"  {'🔴' if off else '🟢'} {name:25s} 🌡 {temp_str}°C → 🎯 {target}°C")
            for h in room["heaters"]:
                print(f"     └─ {h['name']} ({h['device_unique_id']}) [{'OFFLINE' if h['is_offline'] else 'online'}]")
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
    print()


def set_temp(room_name, temp):
    """Set target temperature for a room by name (case-insensitive partial match)."""
    discovery = get_discovery_info()
    room_name_lower = room_name.lower()
    matched = None
    for room in discovery:
        if room["room_name"].lower() == room_name_lower or room_name_lower in room["room_name"].lower():
            matched = room
            break
    if not matched:
        available = [r["room_name"] for r in discovery]
        raise Exception(f"Unknown room '{room_name}'. Available: {', '.join(available)}")
    temp = float(temp)
    place_id = matched["place_id"]
    room_id = matched["room_id"]
    api("PATCH", f"places/{place_id}/rooms/{room_id}/set_target_temperature",
        {"target_temperature": temp})
    print(f"✅ {matched['room_name']} → {temp}°C")



# ── Discovery ──────────────────────────────────────────────────────────────────
def get_discovery_info():
    """Fetches all places, rooms, and heaters via users/configuration — single API call."""
    discovery_data = []
    try:
        config = api("GET", "users/configuration")
        if not config:
            return []

        places = config.get("places", [])
        for place in places:
            place_id = place.get("id")
            if not place_id:
                continue

            for room in place.get("rooms", []):
                room_info = {
                    "place_id": place_id,
                    "room_id": room["id"],
                    "room_name": room["name"],
                    "target_temperature": room.get("target_temperature"),
                    "latest_temperature": room.get("latest_temperature"),
                    "heaters": []
                }
                for heater in room.get("heaters", []):
                    room_info["heaters"].append({
                        "id": heater["id"],
                        "device_unique_id": heater["device_unique_id"],
                        "name": heater["name"],
                        "is_offline": heater.get("is_offline", False),
                        "is_enabled": heater.get("is_enabled", True)
                    })
                discovery_data.append(room_info)
    except Exception as e:
        sys.stderr.write(f"❌ Error fetching discovery info: {e}\n")
    return discovery_data

def print_discovery():
    try:
        data = get_discovery_info()
        print(json.dumps(data, indent=2))
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
        if cmd == "login":
            login_browser()
        elif cmd == "login-headless":
            email = sys.argv[2] if len(sys.argv) > 2 else None
            password = sys.argv[3] if len(sys.argv) > 3 else None
            login_headless(email, password)
        elif cmd == "refresh":
            refresh()
        elif cmd == "token":
            print(get_token())
        elif cmd == "status":
            show_status()
        elif cmd == "discover":
            print_discovery()
        elif cmd == "set":
            if len(sys.argv) != 4:
                print("Usage: python3 beha_auth.py set <room> <temp>")
                sys.exit(1)
            set_temp(sys.argv[2], sys.argv[3])
        else:
            print(__doc__)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
