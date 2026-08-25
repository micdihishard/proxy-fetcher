import time
import random
from flask import Flask, jsonify, request
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
import threading
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/R0x1t/Germey-Proxies/main/http_proxies.txt",
    "https://raw.githubusercontent.com/saisuiu/Lionkings-Http-Proxys/main/cnfree.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    "https://raw.githubusercontent.com/mmpx12/proxy-list/master/https.txt",
    "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
]

TEST_URL = "http://httpbin.org/ip"

_lock = threading.Lock()
_proxy_cache = {"list": [], "ts": 0}
CACHE_TTL = 60
MAX_TO_BENCHMARK = 150
CONNECT_TIMEOUT = 0.8
READ_TIMEOUT = 1.2
WORKERS_FETCH = 25
WORKERS_TEST = 150


def _fetch_proxy_list(url):
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return [
                line.strip()
                for line in r.text.strip().split("\n")
                if line.strip() and ":" in line
            ]
    except Exception:
        pass
    return []


def _fetch_all_proxies():
    now = time.time()
    with _lock:
        if _proxy_cache["list"] and (now - _proxy_cache["ts"]) < CACHE_TTL:
            return _proxy_cache["list"]

    with ThreadPoolExecutor(max_workers=WORKERS_FETCH) as ex:
        futs = [ex.submit(_fetch_proxy_list, u) for u in PROXY_SOURCES]
        all_proxies = []
        for f in as_completed(futs):
            all_proxies.extend(f.result())

    unique = list(dict.fromkeys(all_proxies))
    random.shuffle(unique)

    with _lock:
        _proxy_cache["list"] = unique
        _proxy_cache["ts"] = now
    return unique


def _test_proxy(proxy):
    proxy_url = proxy if proxy.startswith("http") else f"http://{proxy}"
    try:
        start = time.perf_counter()
        r = requests.get(
            TEST_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            verify=False,
        )
        latency = (time.perf_counter() - start) * 1000
        if r.status_code == 200:
            return (proxy, round(latency, 2))
    except Exception:
        pass
    return None


def _benchmark(proxies, top_n):
    batch = proxies[:MAX_TO_BENCHMARK]
    results = []

    with ThreadPoolExecutor(max_workers=WORKERS_TEST) as ex:
        futs = {ex.submit(_test_proxy, p): p for p in batch}
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
    start = time.perf_counter()
    top_n = request.args.get("top", 10, type=int)

    proxies = _fetch_all_proxies()
    if not proxies:
        return jsonify({
            "status": "error",
            "message": "No proxies fetched",
            "time_ms": round((time.perf_counter() - start) * 1000, 2),
        }), 500

    fastest = _benchmark(proxies, top_n)
    elapsed = round((time.perf_counter() - start) * 1000, 2)

    return jsonify({
        "status": "success",
        "total_proxies_fetched": len(proxies),
        "tested": min(MAX_TO_BENCHMARK, len(proxies)),
        "fastest_count": len(fastest),
        "time_ms": elapsed,
        "fastest": [{"proxy": p, "latency_ms": l} for p, l in fastest],
    })


@app.route("/fetch/fastest", methods=["GET"])
def fetch_fastest():
    start = time.perf_counter()
    proxies = _fetch_all_proxies()
    if not proxies:
        return jsonify({"error": "no proxies"}), 500

    fastest = _benchmark(proxies, top_n=1)
    elapsed = round((time.perf_counter() - start) * 1000, 2)

    if fastest:
        return jsonify({
            "proxy": fastest[0][0],
            "latency_ms": fastest[0][1],
            "time_ms": elapsed,
        })
    return jsonify({"error": "no working proxy found"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("Pre-fetching proxy lists...")
    _fetch_all_proxies()
    print(f"Ready: {len(_proxy_cache['list'])} proxies cached")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
