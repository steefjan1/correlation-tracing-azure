#!/usr/bin/env python3
"""Drives the deployed sample through the gateway and reports both identities per order.

Standard library only. Reads measure/.env (written by scripts/postprovision.ps1).

  python measure/drive.py happy        one order, wait for payment + notification, compare trace ids
  python measure/drive.py mine         same, but the client supplies X-Correlation-ID (ticket-<n>)
  python measure/drive.py slow         SLOW-PAY: the provider takes 5 s, the waterfall should show it
  python measure/drive.py failonce     FAIL-ONCE: payment worker throws on delivery 1, redelivery in the same trace
  python measure/drive.py decline      DECLINE: provider answers 402, order ends Declined
  python measure/drive.py burst 100    100 orders; with TRACING_SAMPLING_RATIO=0.1 use query 6 to count whole traces
  python measure/drive.py all          everything except burst
"""
import concurrent.futures as cf
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env():
    env = {}
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="ascii"):
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                env[k] = v
    if "GATEWAY_URL" in os.environ:
        env["GATEWAY_URL"] = os.environ["GATEWAY_URL"]
    return env


ENV = load_env()
BASE = ENV.get("GATEWAY_URL", "").rstrip("/")

# Build the opener (and its SSL context) once, up front. Letting ten threads create it
# concurrently on first use trips "ssl.SSLError: [SSL] malloc failure" on Windows arm64 Python.
urllib.request.install_opener(urllib.request.build_opener())


def call(method, path, body=None, headers=None, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Connection", "close")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw), dict(e.headers)
            except Exception:
                return e.code, raw.decode(errors="replace"), dict(e.headers)
        except (http.client.RemoteDisconnected, urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise
            print(f"    transient {type(e).__name__}, retry {attempt}")
            time.sleep(2 * attempt)


def place(sku, correlation=None, amount=42):
    headers = {"X-Correlation-ID": correlation} if correlation else {}
    t0 = time.perf_counter()
    status, body, resp_headers = call("POST", "/api/orders", {"customerId": "drive", "sku": sku, "amount": amount}, headers)
    ms = (time.perf_counter() - t0) * 1000
    echoed = resp_headers.get("X-Correlation-ID") or resp_headers.get("x-correlation-id")
    return status, body, ms, echoed


def wait_terminal(order_id, timeout=120):
    t0 = time.perf_counter()
    last = None
    while time.perf_counter() - t0 < timeout:
        s, j, _ = call("GET", f"/api/orders/{order_id}")
        if s == 200:
            state = (j["status"], j["paymentStatus"], j["notificationStatus"])
            if state != last:
                print(f"    {time.perf_counter() - t0:6.1f}s  order={state[0]} payment={state[1]} notification={state[2]}")
                last = state
            done = j["status"] == "Declined" or (j["paymentStatus"] == "Captured" and j["notificationStatus"] == "Sent")
            if done:
                return j, time.perf_counter() - t0
        time.sleep(2)
    raise TimeoutError(order_id)


def report(j, secs, ms, echoed):
    same_p = j.get("paymentTraceId") == j.get("traceId") if j.get("paymentTraceId") else None
    same_n = j.get("notificationTraceId") == j.get("traceId") if j.get("notificationTraceId") else None
    print(f"  POST {ms:.0f} ms, terminal after {secs:.1f}s")
    print(f"  correlation id : {j['correlationId']}   (echoed by gateway: {echoed})")
    print(f"  trace id       : {j['traceId']}")
    print(f"  payment worker in same trace: {same_p}   notification worker in same trace: {same_n}")


def scenario(title, sku, correlation=None):
    print(f"== {title}")
    status, body, ms, echoed = place(sku, correlation)
    print(f"  HTTP {status}, order {body.get('orderId')}")
    if status not in (200, 202, 402):
        print(f"  unexpected: {body}")
        return
    j, secs = wait_terminal(body["orderId"])
    report(j, secs, ms, echoed)
    print()


def burst(n):
    print(f"== burst of {n} orders (use query 6 afterwards to count complete traces)")
    call("GET", "/health")   # warm the connection and the gateway before the parallel part
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda _: place("BOOK-1"), range(n)))
    codes = {}
    for s, *_ in results:
        codes[s] = codes.get(s, 0) + 1
    lat = sorted(r[2] for r in results)
    print(f"  submitted in {time.perf_counter() - t0:.1f}s, HTTP {codes}, POST p50 {lat[len(lat)//2]:.0f} ms, p95 {lat[int(len(lat)*0.95)-1]:.0f} ms")
    ids = [r[1]["orderId"] for r in results if r[0] in (200, 202)]
    t1 = time.perf_counter()
    while time.perf_counter() - t1 < 300:
        done = 0
        for oid in ids:
            s, j, _ = call("GET", f"/api/orders/{oid}")
            if s == 200 and j["paymentStatus"] == "Captured" and j["notificationStatus"] == "Sent":
                done += 1
        print(f"    {time.perf_counter() - t1:6.1f}s  {done}/{len(ids)} complete")
        if done == len(ids):
            break
        time.sleep(5)
    trace_ids = [r[1]["traceId"] for r in results if r[0] in (200, 202)]
    path = os.path.join(HERE, "burst-trace-ids.txt")
    with open(path, "w") as f:
        f.write("\n".join(trace_ids))
    print(f"  {len(trace_ids)} trace ids written to {path} (paste into query 6)")


def main():
    if not BASE:
        sys.exit("GATEWAY_URL missing: run azd up (postprovision writes measure/.env)")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    steps = {
        "happy": lambda: scenario("happy path, gateway mints the correlation id", "BOOK-1"),
        "mine": lambda: scenario("client-supplied correlation id", "BOOK-1", f"ticket-{uuid.uuid4().hex[:6]}"),
        "slow": lambda: scenario("slow payment provider (5 s)", "SLOW-PAY"),
        "failonce": lambda: scenario("payment worker fails once, redelivery", "FAIL-ONCE"),
        "decline": lambda: scenario("declined by provider", "DECLINE"),
        "burst": lambda: burst(n),
    }
    if cmd == "all":
        for k, f in steps.items():
            if k != "burst":
                f()
    elif cmd in steps:
        steps[cmd]()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
