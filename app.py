# -*- coding: utf-8 -*-
"""ALL IN ONE DEAL - Shop Website v1.5.0
Shared SQLite + shared products with the Discord bot.
"""
import importlib.util
import json
import os
import secrets
import sqlite3
import subprocess
import re
from pathlib import Path
from werkzeug.utils import secure_filename
import sys
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode

_REQUIRED = {
    "flask": "Flask>=3.1,<4",
    "requests": "requests>=2.32,<3",
    "dotenv": "python-dotenv>=1.1,<2",
}
missing = [spec for mod, spec in _REQUIRED.items() if importlib.util.find_spec(mod) is None]
if missing:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *missing])

import requests
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
DEFAULT_SHARED_DIR = os.path.abspath(os.path.join(ROOT_DIR, "data"))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SHOP_NAME = os.getenv("SHOP_NAME", "ALL IN ONE DEAL")
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8090"))
WEBSITE_URL = os.getenv("WEBSITE_URL", "").rstrip("/")
SHARED_DATA_DIR = os.path.abspath(os.getenv("SHARED_DATA_DIR", DEFAULT_SHARED_DIR))
PRODUCTS_FILE = os.path.abspath(os.getenv("SHARED_PRODUCTS_FILE", os.path.join(SHARED_DATA_DIR, "products.json")))
DB_FILE = os.path.abspath(os.getenv("SHARED_DB_FILE", os.path.join(SHARED_DATA_DIR, "shop.sqlite3")))
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
OWNER_DISCORD_ID = str(os.getenv("OWNER_DISCORD_ID", "0"))
OAUTH_CLIENT_ID = (os.getenv("DISCORD_OAUTH_CLIENT_ID") or os.getenv("DISCORD_CLIENT_ID") or os.getenv("OAUTH_CLIENT_ID") or os.getenv("CLIENT_ID") or "").strip()
OAUTH_CLIENT_SECRET = (os.getenv("DISCORD_OAUTH_CLIENT_SECRET") or os.getenv("DISCORD_CLIENT_SECRET") or os.getenv("OAUTH_CLIENT_SECRET") or os.getenv("CLIENT_SECRET") or "").strip()
OAUTH_REDIRECT_URI = (os.getenv("DISCORD_OAUTH_REDIRECT_URI") or os.getenv("DISCORD_REDIRECT_URI") or os.getenv("OAUTH_REDIRECT_URI") or "").strip()
SECURITY_WEBHOOK_URL = (os.getenv("SECURITY_WEBHOOK_URL") or os.getenv("LOGIN_WEBHOOK_URL") or os.getenv("DISCORD_LOGIN_WEBHOOK") or os.getenv("WEBHOOK_URL") or "").strip()
LOG_SECURITY_METADATA = os.getenv("LOG_SECURITY_METADATA", "true").lower() in {"1", "true", "yes", "on"}
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() in {"1", "true", "yes", "on"}

os.makedirs(SHARED_DATA_DIR, exist_ok=True)

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"), static_folder=os.path.join(BASE_DIR, "static"))
app.secret_key = SESSION_SECRET
if TRUST_PROXY:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def db():
    c = sqlite3.connect(DB_FILE, timeout=20)
    c.row_factory = sqlite3.Row
    return c


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              discord_id TEXT PRIMARY KEY,
              balance INTEGER NOT NULL DEFAULT 0,
              verified INTEGER NOT NULL DEFAULT 0,
              total_spent INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS charge_requests (
              id TEXT PRIMARY KEY,
              discord_id TEXT NOT NULL,
              depositor TEXT NOT NULL,
              amount INTEGER NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              ephemeral_token TEXT,
              application_id TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
              id TEXT PRIMARY KEY,
              discord_id TEXT NOT NULL,
              product_id TEXT NOT NULL,
              product_name TEXT NOT NULL,
              price INTEGER NOT NULL,
              quantity INTEGER NOT NULL DEFAULT 1,
              claim_code_hash TEXT,
              claim_code_hint TEXT,
              created_at TEXT NOT NULL,
              redeemed_at TEXT,
              status TEXT NOT NULL DEFAULT 'paid'
            );
            CREATE TABLE IF NOT EXISTS inventory (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              discord_id TEXT NOT NULL,
              item_name TEXT NOT NULL,
              source_order_id TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id TEXT NOT NULL UNIQUE,
              discord_id TEXT NOT NULL,
              rating INTEGER NOT NULL,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
              state TEXT PRIMARY KEY,
              next_path TEXT NOT NULL,
              redirect_uri TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stock_codes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              product_id TEXT NOT NULL,
              pool TEXT NOT NULL,
              code TEXT NOT NULL,
              used INTEGER NOT NULL DEFAULT 0,
              used_at TEXT,
              order_id TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              action TEXT NOT NULL,
              product_id TEXT,
              detail TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_stock_codes_pool ON stock_codes(product_id,pool,used,id);
            """
        )


def _default_products():
    return {
        "products": [
            {"id":"netflix","name":"넷플릭스","category":"OTT","subtitle":"넷플릭스 디지털 상품","description":"가격과 실제 재고는 오너 대시보드에서 설정할 수 있습니다.","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"code","image":DEFAULT_PRODUCT_IMAGES["netflix"],"enabled":True,"min_qty":1,"step":1,"max_qty":10},
            {"id":"disneyplus","name":"디즈니+","category":"OTT","subtitle":"디즈니+ 디지털 상품","description":"가격과 실제 재고는 오너 대시보드에서 설정할 수 있습니다.","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"code","image":DEFAULT_PRODUCT_IMAGES["disneyplus"],"enabled":True,"min_qty":1,"step":1,"max_qty":10},
            {"id":"spotify","name":"스포티파이 프리미엄","category":"OTT","subtitle":"스포티파이 프리미엄","description":"스포티파이 프리미엄 디지털 상품","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"code","image":DEFAULT_PRODUCT_IMAGES["spotify"],"enabled":True,"min_qty":1,"step":1,"max_qty":10},
            {"id":"discord-random-decoration","name":"디스코드 랜덤 장식","category":"Discord","subtitle":"랜덤 장식 / 아이템","description":"구매 후 1회용 코드 사용 페이지를 통해 랜덤 아이템을 지급하는 구조입니다.","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"inventory","image":DEFAULT_PRODUCT_IMAGES["discord-random-decoration"],"enabled":True,"min_qty":1,"step":1,"max_qty":20,"item_pool":["랜덤 장식 A","랜덤 장식 B","랜덤 장식 C","랜덤 장식 D"]},
            {"id":"discord-nitro-prime","name":"디스코드 니트로 프라임","category":"Discord","subtitle":"니트로 프라임 코드","description":"실제 코드 재고는 대시보드에서 순서대로 추가할 수 있습니다.","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"code","image":DEFAULT_PRODUCT_IMAGES["discord-nitro-prime"],"enabled":True,"min_qty":1,"step":1,"max_qty":20},
            {"id":"discord-nitro-basic","name":"디스코드 니트로 베이직","category":"Discord","subtitle":"니트로 베이직 코드","description":"실제 코드 재고는 대시보드에서 순서대로 추가할 수 있습니다.","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"code","image":DEFAULT_PRODUCT_IMAGES["discord-nitro-basic"],"enabled":True,"min_qty":1,"step":1,"max_qty":20},
            {"id":"server-boost","name":"디스코드 서버 부스트","category":"Discord","subtitle":"서버 부스트 · 2개 단위","description":"수량은 2, 4, 6, 8...처럼 짝수 단위로만 선택할 수 있습니다.","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"boost_order","image":DEFAULT_PRODUCT_IMAGES["server-boost"],"enabled":True,"min_qty":2,"step":2,"max_qty":20},
            {"id":"robux","name":"로벅스","category":"Roblox","subtitle":"로블록스 상품","description":"Robux 상품","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"manual","image":DEFAULT_PRODUCT_IMAGES["robux"],"enabled":True,"min_qty":1,"step":1,"max_qty":10},
            {"id":"valorant-vp","name":"발로란트 VP","category":"VALORANT","subtitle":"발로란트 VP 상품","description":"VALORANT VP 상품","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"manual","image":DEFAULT_PRODUCT_IMAGES["valorant-vp"],"enabled":True,"min_qty":1,"step":1,"max_qty":10},
            {"id":"valorant-random-account","name":"발로란트 랜덤 계정","category":"VALORANT","subtitle":"수동 지급 상품 · 준비중","description":"계정형 상품은 수동 검토/지급을 전제로 한 준비용 상품입니다.","price":None,"website_stock":0,"bot_stock":0,"delivery_type":"manual","image":DEFAULT_PRODUCT_IMAGES["valorant-vp"],"enabled":True,"min_qty":1,"step":1,"max_qty":1}
        ]
    }

def _merge_catalog(data):
    """Ensure the standard catalog is never lost when an older/custom JSON is present."""
    defaults = _default_products()["products"]
    current = {str(p.get("id")): p for p in (data.get("products") or []) if isinstance(p, dict) and p.get("id")}
    changed = False
    for item in defaults:
        pid = item["id"]
        if pid not in current:
            current[pid] = dict(item)
            changed = True
        else:
            base = current[pid]
            for key in ("name","category","subtitle","description","delivery_type","image","min_qty","step","max_qty"):
                if not base.get(key) and item.get(key):
                    base[key] = item[key]; changed = True
            if pid == "valorant-vp" and not base.get("enabled"):
                base["enabled"] = True; changed = True
            if pid == "valorant-random-account" and "enabled" not in base:
                base["enabled"] = False; changed = True
    legacy_image_tokens = (
        "cdn.jsdelivr.net", "upload.wikimedia.org", "storage.googleapis.com",
        "discord.com/assets", "clearvision.gitlab.io", "media/products/",
        "image.pngaaa.com/680/4213680-middle.png", "cdn3.emoji.gg/emojis/853887-serverboost.png", "cdn.prod.website-files.com",
    )
    legacy_names = {
        "Netflix": "넷플릭스", "Disney+": "디즈니+", "Spotify Premium": "스포티파이 프리미엄",
        "Discord 랜덤 장식": "디스코드 랜덤 장식", "Discord Nitro Prime": "디스코드 니트로 프라임",
        "Discord Nitro Basic": "디스코드 니트로 베이직", "Discord Server Boost": "디스코드 서버 부스트",
        "Robux": "로벅스", "VALORANT VP": "발로란트 VP", "VALORANT 랜덤 계정": "발로란트 랜덤 계정",
    }
    for pid, item in current.items():
        if item.get("name") in legacy_names:
            item["name"] = legacy_names[item["name"]]; changed = True
        img = str(item.get("image") or "")
        # Migrate old/generated/invalid image references to the canonical web asset.
        # Keep dashboard-uploaded local files intact once they are explicitly stored under uploads/.
        if (any(tok in img for tok in legacy_image_tokens) or img.endswith(f"media/products/{pid}.svg")) and not img.startswith("uploads/products/"):
            canonical = DEFAULT_PRODUCT_IMAGES.get(pid, DEFAULT_PRODUCT_IMAGES["default"])
            if img != canonical:
                item["image"] = canonical
                changed = True
    merged = {"products": list(current.values())}
    return merged, changed

def load_products():
    candidates = [PRODUCTS_FILE, os.path.join(BASE_DIR, "products.json")]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("products"), list):
                merged, changed = _merge_catalog(data)
                if changed and os.path.abspath(path) == os.path.abspath(PRODUCTS_FILE):
                    try:
                        save_products(merged)
                    except OSError:
                        pass
                return merged
        except (OSError, json.JSONDecodeError):
            continue
    defaults = _default_products()
    try:
        os.makedirs(os.path.dirname(PRODUCTS_FILE), exist_ok=True)
        with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
            json.dump(defaults, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return defaults

def save_products(data):
    os.makedirs(os.path.dirname(PRODUCTS_FILE), exist_ok=True)
    tmp = PRODUCTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PRODUCTS_FILE)


def get_product(product_id):
    return next((p for p in load_products().get("products", []) if p.get("id") == product_id), None)

# Canonical product artwork is loaded from public web assets. No generated artwork is used.
# The URLs below are brand/product images found on the web; Discord product assets
# use Discord-hosted/CDN images where available. Custom dashboard uploads still
# take precedence because product_image() preserves local upload paths.
DEFAULT_PRODUCT_IMAGES = {
    "netflix": "https://upload.wikimedia.org/wikipedia/commons/e/ea/Netflix_Logomark.png",
    "disneyplus": "https://upload.wikimedia.org/wikipedia/commons/2/27/Disney%2B_Original_%282024%29.svg",
    "spotify": "https://storage.googleapis.com/pr-newsroom-wp/1/2023/05/Spotify_Full_Logo_RGB_Green.png",
    # Official Discord brand symbol from Discord's current brand-kit CDN.
    "discord-random-decoration": "https://cdn.discordapp.com/avatar-decoration-presets/a_306a56249fe3c3d2bc7a30041cb63e0e.png?size=240&passthrough=true",
    # Current Discord Nitro product artwork from Discord's official Nitro page CDN.
    "discord-nitro-prime": "https://cdn.prod.website-files.com/6257adef93867e50d84d30e2/688bbecf5acbcb52df0a7455_Nitro-icon3.webp",
    "discord-nitro-basic": "https://cdn.prod.website-files.com/6257adef93867e50d84d30e2/688bbe674c152c2de12791f4_Nitro-icon-2.webp",
    # Transparent Server Boost icon sourced from the web.
    "server-boost": "https://cdn3.emoji.gg/emojis/853887-serverboost.png",
    "robux": "https://upload.wikimedia.org/wikipedia/commons/6/6c/Roblox_Logo.svg",
    "valorant-vp": "https://upload.wikimedia.org/wikipedia/commons/4/44/Valorant_logo.svg",
    "valorant-random-account": "https://upload.wikimedia.org/wikipedia/commons/4/44/Valorant_logo.svg",
    # Default for newly-added products when no upload was provided.
    "default": "https://cdn.prod.website-files.com/6257adef93867e50d84d30e2/66e3d80db9971f10a9757c99_Symbol.svg",
}


DISPLAY_PRODUCT_NAMES = {
    "netflix": "넷플릭스",
    "disneyplus": "디즈니+",
    "spotify": "스포티파이 프리미엄",
    "discord-random-decoration": "디스코드 랜덤 장식",
    "discord-nitro-prime": "디스코드 니트로 프라임",
    "discord-nitro-basic": "디스코드 니트로 베이직",
    "server-boost": "디스코드 서버 부스트",
    "robux": "로벅스",
    "valorant-vp": "발로란트 VP",
    "valorant-random-account": "발로란트 랜덤 계정",
}

DISPLAY_CATEGORIES = {
    "OTT": "OTT", "Discord": "디스코드", "Roblox": "로블록스", "VALORANT": "발로란트"
}

@app.template_global()
def product_name(product):
    pid = str((product or {}).get("id", ""))
    return DISPLAY_PRODUCT_NAMES.get(pid) or (product or {}).get("name", "상품")

@app.template_global()
def category_name(category):
    return DISPLAY_CATEGORIES.get(str(category), str(category))

@app.template_global()
def product_image(product):
    product = product or {}
    image = str(product.get("image") or "").strip()
    pid = str(product.get("id", ""))
    if image.startswith(("http://", "https://", "data:")):
        return image
    if image.startswith("/static/"):
        return image
    # Custom dashboard uploads stay local. Old/default relative paths are treated as invalid
    # and use the canonical web asset instead, preventing broken-image icons.
    if image.startswith("uploads/products/"):
        return url_for("static", filename=image.lstrip("/"))
    if image:
        return DEFAULT_PRODUCT_IMAGES.get(pid, DEFAULT_PRODUCT_IMAGES["default"])
    return DEFAULT_PRODUCT_IMAGES.get(pid, DEFAULT_PRODUCT_IMAGES["default"])

@app.template_global()
def product_fallback_image(product):
    pid = str((product or {}).get("id", ""))
    return DEFAULT_PRODUCT_IMAGES.get(pid, DEFAULT_PRODUCT_IMAGES["default"])



def get_avatar_url(user, size=128):
    if not user:
        return ""
    avatar = user.get("avatar")
    uid = user.get("id")
    if avatar and uid:
        return f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png?size={size}"
    # Discord default avatar index is derived from id in normal cases.
    try:
        idx = (int(uid) >> 22) % 6
        return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"
    except Exception:
        return "https://cdn.discordapp.com/embed/avatars/0.png"


def client_ip():
    # Prefer trusted proxy headers only when TRUST_PROXY=true.
    if TRUST_PROXY:
        for header in ("CF-Connecting-IP", "True-Client-IP", "X-Real-IP", "X-Forwarded-For"):
            value = request.headers.get(header, "").strip()
            if value:
                return value.split(",")[0].strip()
    return request.remote_addr or "unknown"


def browser_name(user_agent):
    ua = (user_agent or "").lower()
    if "edg/" in ua or "edge/" in ua: return "Microsoft Edge"
    if "opr/" in ua or "opera" in ua: return "Opera"
    if "whale" in ua: return "Naver Whale"
    if "firefox/" in ua: return "Firefox"
    if "samsungbrowser/" in ua: return "Samsung Internet"
    if "chrome/" in ua and "chromium" not in ua: return "Chrome"
    if "safari/" in ua and "chrome" not in ua and "android" not in ua: return "Safari"
    return "Other browser"


def send_security_webhook(user):
    if not SECURITY_WEBHOOK_URL:
        return False
    ua = request.headers.get("User-Agent", "unknown")
    embed = {
        "title": "🔐 ALL IN ONE DEAL · Discord 로그인",
        "description": "Discord OAuth 로그인이 완료되었습니다.",
        "color": 0x8B5CF6,
        "fields": [
            {"name": "Discord", "value": f"{user.get('global_name') or user.get('username')} (`{user.get('id')}`)", "inline": False},
            {"name": "IP", "value": f"`{client_ip()}`", "inline": True},
            {"name": "브라우저", "value": browser_name(ua), "inline": True},
            {"name": "User-Agent", "value": f"```{ua[:1000]}```", "inline": False},
        ],
        "timestamp": now_iso(),
        "footer": {"text": SHOP_NAME},
    }
    try:
        resp = requests.post(SECURITY_WEBHOOK_URL, params={"wait": "true"}, json={"username": SHOP_NAME, "embeds": [embed]}, timeout=10)
        if resp.status_code not in (200, 204):
            print(f"[SECURITY WEBHOOK] HTTP {resp.status_code}: {resp.text[:1000]}")
            return False
        print(f"[SECURITY WEBHOOK] sent for Discord user {user.get('id')} from {client_ip()}")
        return True
    except requests.RequestException as exc:
        print(f"[SECURITY WEBHOOK] 전송 실패: {exc}")
        return False

def owner_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("discord_user"):
            session["after_login"] = request.full_path.rstrip("?")
            return redirect(url_for("verify"))
        if str(session["discord_user"].get("id")) != OWNER_DISCORD_ID:
            if OWNER_DISCORD_ID and OWNER_DISCORD_ID == str(os.getenv("DISCORD_GUILD_ID", "0")):
                return render_template("403.html", owner_misconfigured=True), 403
            return render_template("403.html", owner_misconfigured=False), 403
        return fn(*args, **kwargs)
    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("discord_user"):
            session["after_login"] = request.full_path.rstrip("?")
            return redirect(url_for("verify"))
        return fn(*args, **kwargs)
    return wrapper


def stock_counts(product_id):
    with db() as conn:
        rows = conn.execute("SELECT pool, COUNT(*) c FROM stock_codes WHERE product_id=? AND used=0 GROUP BY pool", (product_id,)).fetchall()
    out = {"website": 0, "bot": 0}
    for row in rows:
        out[row["pool"]] = row["c"]
    return out


def audit(action, product_id=None, detail=None):
    with db() as conn:
        conn.execute("INSERT INTO admin_audit(action,product_id,detail,created_at) VALUES(?,?,?,?)", (action, product_id, detail, now_iso()))


@app.context_processor
def globals_ctx():
    user = session.get("discord_user")
    return {
        "shop_name": SHOP_NAME,
        "discord_user": user,
        "is_owner": bool(user and str(user.get("id")) == OWNER_DISCORD_ID),
        "owner_config_valid": bool(OWNER_DISCORD_ID and OWNER_DISCORD_ID != str(os.getenv("DISCORD_GUILD_ID", "0"))),
        "avatar_url": get_avatar_url(user),
        "balance": get_user_balance(user.get("id")) if user else 0,
        "search_query": " ".join((request.args.get("q") or "").split()),
    }


def get_user_balance(discord_id):
    if not discord_id:
        return 0
    with db() as conn:
        row = conn.execute("SELECT balance FROM users WHERE discord_id=?", (str(discord_id),)).fetchone()
    return int(row["balance"]) if row else 0


@app.get("/")
def home():
    products = [p for p in load_products().get("products", []) if p.get("enabled", True)]
    order = ["OTT", "Discord", "Roblox", "VALORANT"]
    grouped = {cat: [p for p in products if p.get("category") == cat] for cat in order}
    return render_template("home.html", products=products, grouped=grouped, category_order=order)


@app.get("/shop")
def shop():
    products = [p for p in load_products().get("products", []) if p.get("enabled", True)]
    category = (request.args.get("category") or "all").strip()
    q = " ".join((request.args.get("q") or "").split())
    if category != "all":
        products = [p for p in products if p.get("category") == category]
    if q:
        needle = q.casefold()
        products = [
            p for p in products
            if needle in " ".join([
                str(p.get("name") or ""), str(p.get("category") or ""), str(p.get("subtitle") or ""), str(p.get("description") or ""),
                product_name(p), category_name(p.get("category")),
            ]).casefold()
        ]
    return render_template("shop.html", products=products, category=category, q=q)


@app.get("/shop/<product_id>")
def product_detail(product_id):
    product = get_product(product_id)
    if not product:
        return render_template("404.html"), 404
    return render_template("product.html", product=product)


@app.get("/login")
def login():
    return redirect(url_for("oauth_start"))


@app.get("/verify")
def verify():
    return render_template("verify.html", next_path=request.args.get("next", "/shop"))


def current_redirect_uri():
    if OAUTH_REDIRECT_URI:
        return OAUTH_REDIRECT_URI
    base = WEBSITE_URL or request.url_root.rstrip("/")
    return f"{base}/callback"


def safe_next_path(value):
    value = (value or "/shop").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/shop"
    if value.startswith("/shop/"):
        product_id = value[len("/shop/"):].split("?", 1)[0].strip("/")
        if not product_id or not get_product(product_id):
            return "/shop"
    allowed = {"/", "/shop", "/account", "/redeem", "/dashboard"}
    return value if value in allowed or value.startswith("/shop/") else "/shop"

@app.route("/oauth/start", methods=["GET", "POST"])
def oauth_start():
    placeholder_values = {"YOUR_DISCORD_APPLICATION_ID", "YOUR_DISCORD_OAUTH_CLIENT_SECRET", "YOUR_CLIENT_ID", "YOUR_CLIENT_SECRET"}
    if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET or OAUTH_CLIENT_ID in placeholder_values or OAUTH_CLIENT_SECRET in placeholder_values:
        print("[OAUTH] Missing/placeholder Discord OAuth credentials. Check .env: DISCORD_OAUTH_CLIENT_ID and DISCORD_OAUTH_CLIENT_SECRET")
        flash("Discord OAuth 설정이 아직 완료되지 않았습니다. .env의 Client ID / Client Secret을 실제 값으로 입력해 주세요.", "error")
        return redirect(url_for("verify"))
    next_path = safe_next_path(request.values.get("next", "/shop"))
    redirect_uri = current_redirect_uri()
    state = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO oauth_states(state,next_path,redirect_uri,created_at) VALUES(?,?,?,?)", (state, next_path, redirect_uri, now_iso()))
    # Keep a short-lived browser-session copy as a second fallback for hosted environments.
    session["oauth_state"] = state
    session["oauth_redirect_uri"] = redirect_uri
    session["oauth_next_path"] = next_path
    session["oauth_created_at"] = now_iso()
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "identify",
        "state": state,
        "prompt": "consent",
    }
    return redirect("https://discord.com/oauth2/authorize?" + urlencode(params))


@app.get("/shop/callback")
@app.get("/oauth/callback")
@app.get("/callback")
@app.get("/oauth/discord/callback")
@app.get("/discord/callback")
@app.get("/oauth2/callback")
@app.get("/auth/callback")
@app.get("/callback/")
def oauth_callback():
    if request.args.get("error"):
        flash("Discord 로그인이 취소되었습니다.", "error")
        return redirect(url_for("verify"))
    code = request.args.get("code")
    state = request.args.get("state")
    state_row = None
    if state:
        with db() as conn:
            state_row = conn.execute("SELECT * FROM oauth_states WHERE state=?", (state,)).fetchone()
            if state_row:
                conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
    # Browser-session fallback for environments where the SQLite file is not persistent.
    if not state_row and state and session.get("oauth_state") == state:
        state_row = {
            "state": state,
            "next_path": session.get("oauth_next_path", "/shop"),
            "redirect_uri": session.get("oauth_redirect_uri") or current_redirect_uri(),
            "created_at": session.get("oauth_created_at") or now_iso(),
        }
    if not code or not state_row:
        flash("로그인 세션이 만료되었거나 올바르지 않습니다. 사이트 주소와 OAuth Redirect URI가 같은지 확인해 주세요.", "error")
        return redirect(url_for("verify"))
    session.pop("oauth_state", None)
    session.pop("oauth_redirect_uri", None)
    session.pop("oauth_created_at", None)
    session.pop("oauth_next_path", None)
    try:
        created = datetime.fromisoformat(state_row["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created).total_seconds() > 600:
            flash("로그인 요청이 만료되었습니다. 다시 로그인해 주세요.", "error")
            return redirect(url_for("verify"))
    except Exception:
        pass
    redirect_uri = state_row["redirect_uri"]
    # Discord's OAuth2 token endpoint requires x-www-form-urlencoded data.
    # Use HTTP Basic authentication for the client credentials, matching Discord's
    # current Authorization Code Grant example.
    try:
        token = requests.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=(OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET),
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"[OAUTH] token exchange request failed: {exc}")
        flash(f"Discord 연결에 실패했습니다: {exc}", "error")
        return redirect(url_for("verify"))
    if not token.ok:
        print(f"[OAUTH] token exchange HTTP {token.status_code}: {token.text[:1500]}")
        # Keep the raw Discord error out of the page; it may expose implementation details.
        flash("Discord 로그인 처리에 실패했습니다. Discord Developer Portal의 Redirect URL과 사이트의 Redirect URL이 완전히 같은지 확인해 주세요.", "error")
        return redirect(url_for("verify"))
    access_token = token.json().get("access_token")
    if not access_token:
        flash("Discord에서 로그인 토큰을 받지 못했습니다.", "error")
        return redirect(url_for("verify"))
    try:
        user_resp = requests.get("https://discord.com/api/v10/users/@me", headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    except requests.RequestException as exc:
        flash(f"Discord 사용자 정보를 가져오지 못했습니다: {exc}", "error")
        return redirect(url_for("verify"))
    if not user_resp.ok:
        print(f"[OAUTH] user fetch HTTP {user_resp.status_code}: {user_resp.text[:500]}")
        flash("Discord 사용자 정보를 가져오지 못했습니다.", "error")
        return redirect(url_for("verify"))
    user = user_resp.json()
    session["discord_user"] = user
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(discord_id,balance,verified,total_spent,created_at) VALUES(?,?,?,?,?)",
            (str(user["id"]), 0, 0, 0, now_iso()),
        )
    # IMPORTANT: website OAuth is login-only. Discord verification/role assignment
    # is handled by the separate verification panel in the Discord server bot.
    flash("Discord 웹 로그인이 완료되었습니다! 서버 인증 역할은 디스코드 인증패널에서만 지급됩니다.", "success")
    try:
        send_security_webhook(user)
    except Exception as exc:
        print(f"[SECURITY WEBHOOK] unexpected error: {exc}")
    next_path = safe_next_path(state_row["next_path"] or "/shop")
    print(f"[OAUTH] success user={user.get('id')} redirecting=/auth/success next={next_path} host={request.host}")
    return redirect(url_for("auth_success", next=next_path))


@app.get("/auth/success")
@login_required
def auth_success():
    next_path = safe_next_path(request.args.get("next", "/shop"))
    uid = str(session["discord_user"]["id"])
    with db() as conn:
        row = conn.execute("SELECT verified FROM users WHERE discord_id=?", (uid,)).fetchone()
    server_verified = bool(row and row["verified"])
    return render_template("auth_success.html", next_path=next_path, server_verified=server_verified)


@app.get("/account")
@login_required
def account():
    uid = str(session["discord_user"]["id"])
    with db() as conn:
        inventory = [dict(r) for r in conn.execute("SELECT * FROM inventory WHERE discord_id=? ORDER BY id DESC", (uid,)).fetchall()]
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders WHERE discord_id=? ORDER BY created_at DESC LIMIT 20", (uid,)).fetchall()]
        balance = get_user_balance(uid)
    return render_template("account.html", inventory=inventory, orders=orders, balance=balance)


@app.route("/redeem", methods=["GET", "POST"])
@login_required
def redeem():
    code = (request.form.get("code") or request.args.get("code") or "").strip().upper()
    message = None
    success = False
    uid = str(session["discord_user"]["id"])
    if request.method == "POST" and code:
        import hashlib
        h = hashlib.sha256(code.encode("utf-8")).hexdigest()
        with db() as conn:
            order = conn.execute("SELECT * FROM orders WHERE claim_code_hash=?", (h,)).fetchone()
            if not order:
                message = ("존재하지 않거나 사용할 수 없는 코드입니다.", "error")
            elif order["discord_id"] != uid:
                message = ("이 코드는 구매한 Discord 계정에서만 사용할 수 있습니다.", "error")
            elif order["redeemed_at"]:
                message = ("이미 사용된 코드입니다.", "error")
            else:
                product = get_product(order["product_id"])
                if not product:
                    message = ("상품 정보를 찾을 수 없습니다.", "error")
                elif product.get("delivery_type") == "inventory":
                    item_pool = product.get("item_pool") or [product.get("name", "Digital Item")]
                    for _ in range(max(1, int(order["quantity"] or 1))):
                        item_name = secrets.choice(item_pool)
                        conn.execute("INSERT INTO inventory(discord_id,item_name,source_order_id,created_at) VALUES(?,?,?,?)", (uid, item_name, order["id"], now_iso()))
                    conn.execute("UPDATE orders SET redeemed_at=?, status='redeemed' WHERE id=?", (now_iso(), order["id"]))
                    success = True
                    message = ("✅ 상품 지급이 완료되었습니다! 인벤토리를 확인해 주세요.", "success")
                elif product.get("delivery_type") == "code":
                    # Claim codes are intentionally separate from stock codes. Consume FIFO website stock on redemption.
                    claimed = []
                    for _ in range(max(1, int(order["quantity"] or 1))):
                        row = conn.execute("SELECT id,code FROM stock_codes WHERE product_id=? AND pool='website' AND used=0 ORDER BY id ASC LIMIT 1", (product["id"],)).fetchone()
                        if not row:
                            break
                        claimed.append(row["code"])
                        conn.execute("UPDATE stock_codes SET used=1,used_at=?,order_id=? WHERE id=?", (now_iso(), order["id"], row["id"]))
                    if len(claimed) < max(1, int(order["quantity"] or 1)):
                        message = ("현재 상품 실물 코드 재고가 부족합니다. 관리자에게 문의해 주세요.", "error")
                    else:
                        for c in claimed:
                            conn.execute("INSERT INTO inventory(discord_id,item_name,source_order_id,created_at) VALUES(?,?,?,?)", (uid, f"{product['name']} · {c}", order["id"], now_iso()))
                        conn.execute("UPDATE orders SET redeemed_at=?, status='redeemed' WHERE id=?", (now_iso(), order["id"]))
                        success = True
                        message = ("✅ 상품 코드 지급이 완료되었습니다! 계정 페이지에서 확인할 수 있습니다.", "success")
                else:
                    message = ("이 상품은 관리자 수동 지급 상품입니다.", "error")
    elif request.method == "POST":
        message = ("코드를 입력해 주세요.", "error")
    return render_template("redeem.html", user=session["discord_user"], code=code, message=message, success=success)


@app.get("/privacy")
def privacy():
    return render_template("privacy.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.get("/dashboard")
@owner_required
def owner_dashboard():
    data = load_products().get("products", [])
    products = []
    for p in data:
        counts = stock_counts(p["id"])
        item = dict(p)
        item["live_website_stock"] = counts["website"]
        item["live_bot_stock"] = counts["bot"]
        item["display_name"] = product_name(item)
        item["display_category"] = category_name(item.get("category"))
        products.append(item)
    with db() as conn:
        audits = [dict(r) for r in conn.execute("SELECT * FROM admin_audit ORDER BY id DESC LIMIT 40").fetchall()]
        user_count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        total_balance = conn.execute("SELECT COALESCE(SUM(balance),0) s FROM users").fetchone()["s"]
        total_stock = conn.execute("SELECT COUNT(*) c FROM stock_codes WHERE used=0").fetchone()["c"]
    return render_template("owner_dashboard.html", products=products, audits=audits, user_count=user_count, total_balance=total_balance, total_stock=total_stock)


def save_uploaded_product_image(upload, product_id):
    if not upload or not upload.filename:
        return None
    original = secure_filename(upload.filename)
    ext = Path(original).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ValueError("PNG, JPG, JPEG, WEBP, GIF만 업로드할 수 있습니다.")
    max_mb = max(1, int(os.getenv("MAX_PRODUCT_IMAGE_MB", "8")))
    upload.stream.seek(0, 2)
    size = upload.stream.tell()
    upload.stream.seek(0)
    if size > max_mb * 1024 * 1024:
        raise ValueError(f"이미지 용량은 {max_mb}MB 이하로 업로드해 주세요.")
    upload_dir = Path(BASE_DIR) / "static" / "uploads" / "products"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{secure_filename(product_id) or 'product'}-{secrets.token_hex(5)}{ext}"
    upload.save(upload_dir / filename)
    return f"uploads/products/{filename}"


@app.post("/dashboard/product/add")
@owner_required
def dashboard_product_add():
    name = (request.form.get("name") or "").strip()
    pid = re.sub(r"[^a-z0-9-]+", "-", (request.form.get("id") or name.lower()).strip().lower()).strip("-")
    category = (request.form.get("category") or "Discord").strip()
    subtitle = (request.form.get("subtitle") or "디지털 상품").strip()
    description = (request.form.get("description") or "").strip()
    if not name or not pid:
        flash("상품명과 상품 ID를 입력해 주세요.", "error")
        return redirect(url_for("owner_dashboard"))
    data = load_products()
    if any(str(p.get("id")) == pid for p in data.get("products", [])):
        flash("이미 존재하는 상품 ID입니다.", "error")
        return redirect(url_for("owner_dashboard"))
    try:
        image = save_uploaded_product_image(request.files.get("image"), pid) or DEFAULT_PRODUCT_IMAGES["default"]
        price_raw = (request.form.get("price") or "").replace(",", "").strip()
        item = {
            "id": pid, "name": name, "category": category, "subtitle": subtitle, "description": description,
            "price": int(price_raw) if price_raw.isdigit() else None, "website_stock": 0, "bot_stock": 0,
            "delivery_type": "code", "image": image, "enabled": request.form.get("enabled") == "on",
            "min_qty": max(1, int(request.form.get("min_qty") or 1)),
            "step": max(1, int(request.form.get("step") or 1)),
            "max_qty": max(1, int(request.form.get("max_qty") or 10)),
        }
        data.setdefault("products", []).append(item)
        save_products(data)
        audit("product_add", pid, json.dumps({"name":name,"category":category}, ensure_ascii=False))
        flash(f"{name} 상품을 추가했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("owner_dashboard"))


@app.post("/dashboard/product/image")
@owner_required
def dashboard_product_image():
    pid = (request.form.get("id") or "").strip()
    product = get_product(pid)
    if not product:
        flash("상품을 찾을 수 없습니다.", "error")
        return redirect(url_for("owner_dashboard"))
    try:
        saved = save_uploaded_product_image(request.files.get("image"), pid)
        if not saved:
            raise ValueError("이미지 파일을 선택해 주세요.")
        data = load_products()
        for p in data.get("products", []):
            if p.get("id") == pid:
                p["image"] = saved
                break
        save_products(data)
        audit("product_image_update", pid, saved)
        flash(f"{product_name(product)} 이미지를 변경했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("owner_dashboard"))


@app.post("/dashboard/product/save")
@owner_required
def dashboard_product_save():
    pid = (request.form.get("id") or "").strip()
    data = load_products()
    for p in data.get("products", []):
        if p.get("id") != pid:
            continue
        price_raw = (request.form.get("price") or "").replace(",", "").strip()
        p["price"] = int(price_raw) if price_raw.isdigit() else None
        p["enabled"] = request.form.get("enabled") == "on"
        p["name"] = (request.form.get("name") or p.get("name") or "상품").strip()
        p["category"] = (request.form.get("category") or p.get("category") or "Discord").strip()
        p["subtitle"] = (request.form.get("subtitle") or p.get("subtitle") or "디지털 상품").strip()
        p["min_qty"] = max(1, int(request.form.get("min_qty") or p.get("min_qty", 1)))
        p["step"] = max(1, int(request.form.get("step") or p.get("step", 1)))
        p["max_qty"] = max(p["min_qty"], int(request.form.get("max_qty") or p.get("max_qty", 1)))
        p["website_stock"] = int(request.form.get("website_stock") or 0)
        p["bot_stock"] = int(request.form.get("bot_stock") or 0)
        save_products(data)
        audit("product_update", pid, json.dumps({"price":p["price"],"enabled":p["enabled"],"website_stock":p["website_stock"],"bot_stock":p["bot_stock"]},ensure_ascii=False))
        flash(f"{product_name(p)} 설정을 저장했습니다.", "success")
        break
    return redirect(url_for("owner_dashboard"))


@app.post("/dashboard/stock/add")
@owner_required
def dashboard_stock_add():
    pid = (request.form.get("product_id") or "").strip()
    pool = request.form.get("pool") if request.form.get("pool") in {"website","bot"} else "website"
    codes = [line.strip() for line in (request.form.get("codes") or "").splitlines() if line.strip()]
    if not codes or not get_product(pid):
        flash("상품과 재고 코드를 확인해 주세요.", "error")
        return redirect(url_for("owner_dashboard"))
    with db() as conn:
        conn.executemany("INSERT INTO stock_codes(product_id,pool,code) VALUES(?,?,?)", [(pid,pool,c) for c in codes])
    audit("stock_add", pid, f"pool={pool}, count={len(codes)}")
    flash(f"{len(codes)}개 코드를 {('웹사이트' if pool=='website' else 'Discord 봇')} 재고에 추가했습니다.", "success")
    return redirect(url_for("owner_dashboard"))


@app.post("/dashboard/code/generate")
@owner_required
def dashboard_code_generate():
    pid = (request.form.get("product_id") or "").strip()
    pool = request.form.get("pool") if request.form.get("pool") in {"website","bot"} else "website"
    prefix = re.sub(r"[^A-Za-z0-9_-]", "", request.form.get("prefix") or "VEXO")[:10]
    try:
        count = max(1, min(500, int(request.form.get("count") or 10)))
        length = max(4, min(32, int(request.form.get("length") or 12)))
    except ValueError:
        flash("생성 수량과 코드 길이를 확인해 주세요.", "error")
        return redirect(url_for("owner_dashboard"))
    if not get_product(pid):
        flash("상품을 선택해 주세요.", "error")
        return redirect(url_for("owner_dashboard"))
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    generated=[]; seen=set()
    while len(generated) < count:
        raw = "".join(secrets.choice(alphabet) for _ in range(length))
        code = f"{prefix}-{raw}" if prefix else raw
        if code in seen:
            continue
        seen.add(code); generated.append(code)
    with db() as conn:
        conn.executemany("INSERT INTO stock_codes(product_id,pool,code) VALUES(?,?,?)", [(pid,pool,c) for c in generated])
    audit("code_generate", pid, f"pool={pool}, count={len(generated)}, length={length}")
    flash(f"{len(generated)}개 코드를 생성해 {('웹사이트' if pool=='website' else 'Discord 봇')} 재고에 넣었습니다.", "success")
    return redirect(url_for("owner_dashboard"))


@app.post("/dashboard/stock/clear")
@owner_required
def dashboard_stock_clear():
    pid = (request.form.get("product_id") or "").strip()
    pool = request.form.get("pool") if request.form.get("pool") in {"website","bot"} else "website"
    with db() as conn:
        conn.execute("DELETE FROM stock_codes WHERE product_id=? AND pool=? AND used=0", (pid,pool))
    audit("stock_clear", pid, f"pool={pool}")
    flash("사용하지 않은 해당 재고를 비웠습니다.", "success")
    return redirect(url_for("owner_dashboard"))


@app.get("/api/shop/products")
def api_shop_products():
    products=[]
    for p in [p for p in load_products().get("products",[]) if p.get("enabled",True)]:
        item=dict(p); counts=stock_counts(p["id"]); item["website_stock_live"]=counts["website"]; item["bot_stock_live"]=counts["bot"]; item["image"]=product_image(item); products.append(item)
    return jsonify({"shop":SHOP_NAME,"products":products})


@app.get("/health")
def health():
    return jsonify({"ok":True,"shop":SHOP_NAME,"db":DB_FILE,"products":len(load_products().get("products",[]))})


@app.errorhandler(404)
def page_not_found(_error):
    path = request.path.lower()
    if request.args.get("code") and request.args.get("state") and "callback" in path:
        return oauth_callback()
    print(f"[404] {request.method} {request.url}")
    return render_template("404.html"), 404


# Initialize storage for both local runs and WSGI servers (gunicorn/Render/etc.).
init_db()

if __name__ == "__main__":
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)
