import time
import random
from flask import Flask, jsonify, request
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

SOURCES = {
    "http": [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&country=all&ssl=all&anonymity=all",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ],
    "https": [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=https&timeout=3000&country=all&ssl=all&anonymity=all",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/https.txt",
    ],
    "socks4": [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=3000&country=all",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    ],
    "socks5": [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=3000&country=all",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    ],
}

TEST_URL = "http://httpbin.org/ip"

_cache = {}
CACHE_TTL = 60
MAX_TEST = 40
TIMEOUT = (0.8, 1.0)


def _fetch(url):
    try:
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            return [l.strip() for l in r.text.split("\n") if l.strip() and ":" in l]
    except:
        pass
    return []


def _get_proxies(ptype):
    now = time.time()
    c = _cache.get(ptype)
    if c and (now - c["ts"]) < CACHE_TTL:
        return c["list"]

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(_fetch, u) for u in SOURCES.get(ptype, [])]
        all_p = []
        for f in as_completed(futs):
            all_p.extend(f.result())

    unique = list(dict.fromkeys(all_p))
    random.shuffle(unique)
    _cache[ptype] = {"list": unique, "ts": now}
    return unique


def _test(proxy, ptype):
    schemes = {"socks5": "socks5", "socks4": "socks4"}
    scheme = schemes.get(ptype, "http")
    purl = f"{scheme}://{proxy}"
    try:
        s = time.perf_counter()
        r = requests.get(TEST_URL, proxies={"http": purl, "https": purl}, timeout=TIMEOUT, verify=False)
        ms = round((time.perf_counter() - s) * 1000, 2)
        if r.status_code == 200:
            return (proxy, ms)
    except:
        pass
    return None


def _bench(proxies, ptype, top_n):
    results = []
    with ThreadPoolExecutor(max_workers=40) as ex:
        futs = {ex.submit(_test, p, ptype): p for p in proxies[:MAX_TEST]}
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
        return jsonify({"status": "error", "message": f"No {ptype} proxies", "time_ms": elapsed(t0)}), 500

    fastest = _bench(proxies, ptype, top_n)
    return jsonify({
        "status": "success",
        "type": ptype,
        "total": len(proxies),
        "tested": min(MAX_TEST, len(proxies)),
        "count": len(fastest),
        "time_ms": elapsed(t0),
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

    top = _bench(proxies, ptype, 1)
    if top:
        return jsonify({"type": ptype, "proxy": top[0][0], "latency_ms": top[0][1], "time_ms": elapsed(t0)})
    return jsonify({"error": "no working proxy"}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


def elapsed(t0):
    return round((time.perf_counter() - t0) * 1000, 2)
