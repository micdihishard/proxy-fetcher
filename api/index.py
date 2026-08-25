import time
import random
from flask import Flask, jsonify, request
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import urllib3

urllib3.disable_warnings()

app = Flask(__name__)

V4 = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=get_proxies&proxy_format=protocolipport&format=json&timeout=5000"

SOURCES = {
    "http": f"{V4}&protocol=http",
    "https": f"{V4}&protocol=https",
    "socks4": f"{V4}&protocol=socks4",
    "socks5": f"{V4}&protocol=socks5",
}

TEST_URL = "http://httpbin.org/ip"

_cache = {}
CACHE_TTL = 60
MAX_TEST = {"http": 50, "https": 50, "socks4": 25, "socks5": 25}
WORKERS = 80
TIMEOUT_SOCKS = (1.2, 1.8)
TIMEOUT_HTTP = (0.8, 1.2)


def _fetch_v4(ptype):
    url = SOURCES.get(ptype)
    if not url:
        return []
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            proxies = []
            for p in data.get("proxies", []):
                if p.get("alive"):
                    proto = p.get("protocol", ptype)
                    proxy_url = f"{proto}://{p['ip']}:{p['port']}"
                    avg = p.get("average_timeout", 9999)
                    proxies.append({"url": proxy_url, "avg": avg})
            proxies.sort(key=lambda x: x["avg"])
            return proxies
    except:
        pass
    return []


def _get_proxies(ptype):
    now = time.time()
    c = _cache.get(ptype)
    if c and (now - c["ts"]) < CACHE_TTL:
        return c["list"]

    raw = _fetch_v4(ptype)
    random.shuffle(raw)
    _cache[ptype] = {"list": raw, "ts": now}
    return raw


def _test(proxy_url):
    proto = proxy_url.split("://")[0]
    to = TIMEOUT_SOCKS if proto.startswith("socks") else TIMEOUT_HTTP
    try:
        s = time.perf_counter()
        r = requests.get(
            TEST_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=to,
            verify=False,
        )
        ms = round((time.perf_counter() - s) * 1000, 2)
        if r.status_code == 200:
            return (proxy_url, ms)
    except:
        pass
    return None


def _bench(proxies, top_n, ptype="http"):
    limit = MAX_TEST.get(ptype, 50)
    urls = [p["url"] for p in proxies[:limit]]
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_test, u): u for u in urls}
        for f in as_completed(futs):
            r = f.result()
            if r:
                results.append(r)
                if len(results) >= top_n * 2:
                    break
    results.sort(key=lambda x: x[1])
    return results[:top_n]


@app.route("/fetch", methods=["GET"])
def fetch():
    t0 = time.perf_counter()
    ptype = request.args.get("type", "http").lower()
    top_n = request.args.get("top", 10, type=int)

    if ptype not in SOURCES:
        return jsonify({"status": "error", "message": f"Invalid type. Use: {', '.join(SOURCES)}"}), 400

    proxies = _get_proxies(ptype)
    if not proxies:
        return jsonify({"status": "error", "message": f"No {ptype} proxies", "time_ms": ms(t0)}), 500

    fastest = _bench(proxies, top_n, ptype)
    return jsonify({
        "status": "success",
        "type": ptype,
        "total": len(proxies),
        "tested": min(MAX_TEST, len(proxies)),
        "count": len(fastest),
        "time_ms": ms(t0),
        "fastest": [{"proxy": p, "latency_ms": l} for p, l in fastest],
    })


@app.route("/fetch/fastest", methods=["GET"])
def fastest():
    t0 = time.perf_counter()
    ptype = request.args.get("type", "http").lower()

    if ptype not in SOURCES:
        return jsonify({"error": f"Invalid type. Use: {', '.join(SOURCES)}"}), 400

    proxies = _get_proxies(ptype)
    if not proxies:
        return jsonify({"error": f"No {ptype} proxies"}), 500

    top = _bench(proxies, 1, ptype)
    if top:
        return jsonify({"type": ptype, "proxy": top[0][0], "latency_ms": top[0][1], "time_ms": ms(t0)})
    return jsonify({"error": "no working proxy"}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


def ms(t0):
    return round((time.perf_counter() - t0) * 1000, 2)
