"""Throwaway, fully isolated Ryot for tests and local poking.

No Docker needed: the Ryot backend binary is pulled straight out of the
ghcr.io/ignisda/ryot image layers and Postgres comes from Debian's packages
(`apt-get download`, unpacked without root). Both are cached in .cache/.

Each run gets a fresh temp dir (database included) bound to random loopback
ports, plus a small proxy that serves the backend under /backend like the
real container's Caddy does, so the integration uses the same URL shape.

    .venv/bin/python scripts/ryot_stack.py            # run until Ctrl-C
    .venv/bin/python scripts/ryot_stack.py --port 8055

Prints one JSON line {"url", "admin_token", ...} once Ryot answers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
IMAGE = "ignisda/ryot"
PG_MAJOR = "17"


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _log(msg: str) -> None:
    print(f"[ryot-stack] {msg}", file=sys.stderr, flush=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- binaries -------------------------------------------------------------

def _ghcr(path: str, token: str, accept: str | None = None) -> bytes:
    req = urllib.request.Request(f"https://ghcr.io/v2/{IMAGE}/{path}", headers={"Authorization": f"Bearer {token}"})
    if accept:
        req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def ensure_ryot(version: str) -> Path:
    dest = CACHE / f"ryot-{version}" / "backend"
    if dest.exists():
        return dest
    _log(f"fetching Ryot {version} backend from ghcr.io/{IMAGE}")
    with urllib.request.urlopen(f"https://ghcr.io/token?scope=repository:{IMAGE}:pull", timeout=30) as r:
        token = json.load(r)["token"]
    index = json.loads(_ghcr(f"manifests/{version}", token, "application/vnd.oci.image.index.v1+json"))
    digest = next(m["digest"] for m in index["manifests"] if m.get("platform", {}).get("architecture") == "amd64")
    manifest = json.loads(_ghcr(f"manifests/{digest}", token, "application/vnd.oci.image.manifest.v1+json"))
    # The backend is COPY'd near the end of the Dockerfile: walk layers newest first.
    for layer in reversed(manifest["layers"]):
        if layer["size"] < 1_000_000:
            continue
        blob = _ghcr(f"blobs/{layer['digest']}", token)
        if "sha256:" + hashlib.sha256(blob).hexdigest() != layer["digest"]:
            raise RuntimeError(f"digest mismatch for layer {layer['digest']}")
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            try:
                member = tar.getmember("usr/local/bin/backend")
            except KeyError:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            dest.chmod(0o755)
            return dest
    raise RuntimeError("backend binary not found in image layers")


def ensure_postgres() -> tuple[Path, Path]:
    root = CACHE / f"postgres-{PG_MAJOR}"
    bindir = root / f"usr/lib/postgresql/{PG_MAJOR}/bin"
    libdir = root / "usr/lib/x86_64-linux-gnu"
    if (bindir / "postgres").exists():
        return bindir, libdir
    _log(f"fetching postgresql-{PG_MAJOR} via apt-get download")
    with tempfile.TemporaryDirectory() as dl:
        subprocess.run(["apt-get", "download", f"postgresql-{PG_MAJOR}", "libpq5"], cwd=dl, check=True, capture_output=True)
        for deb in Path(dl).glob("*.deb"):
            subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
    return bindir, libdir


# --- stack ----------------------------------------------------------------

async def _wait_ready(url: str, proc: subprocess.Popen, log: Path, timeout: float = 180) -> None:
    async with ClientSession() as s:
        for _ in range(int(timeout * 2)):
            if proc.poll() is not None:
                raise RuntimeError(f"Ryot exited early:\n{log.read_text()[-3000:]}")
            try:
                async with s.post(url, json={"query": "{ coreDetails { version } }"}) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError(f"Ryot not ready after {timeout}s:\n{log.read_text()[-3000:]}")


def _proxy_app(backend: str) -> web.Application:
    session: ClientSession | None = None

    async def forward(request: web.Request) -> web.StreamResponse:
        nonlocal session
        session = session or ClientSession(auto_decompress=False)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        async with session.request(
            request.method, f"{backend}/{request.match_info['tail']}", params=request.query,
            headers=headers, data=await request.read(),
        ) as r:
            return web.Response(status=r.status, body=await r.read(), headers={
                k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-encoding")
            })

    async def close(_: web.Application) -> None:
        if session:
            await session.close()

    app = web.Application()
    app.router.add_route("*", "/backend/{tail:.*}", forward)
    app.on_cleanup.append(close)
    return app


async def run(port: int | None) -> None:
    _load_env()
    version = os.environ.get("RYOT_TEST_VERSION", "v10.5.6")
    backend_bin = ensure_ryot(version)
    pg_bin, pg_lib = ensure_postgres()

    work = Path(tempfile.mkdtemp(prefix="ryot-stack-"))
    pg_env = {"PATH": "/usr/bin:/bin", "LD_LIBRARY_PATH": str(pg_lib)}
    pg_port, be_port, px_port = _free_port(), _free_port(), port or _free_port()
    admin_token = secrets.token_hex(16)
    procs: list[subprocess.Popen] = []
    runner: web.AppRunner | None = None
    try:
        subprocess.run([pg_bin / "initdb", "-D", work / "pg", "-U", "postgres", "--auth=trust"],
                       env=pg_env, check=True, capture_output=True)
        subprocess.run([pg_bin / "pg_ctl", "-D", work / "pg", "-w", "-l", work / "pg.log",
                        "-o", f"-k {work} -p {pg_port} -c listen_addresses=127.0.0.1", "start"],
                       env=pg_env, check=True, capture_output=True)

        log = work / "ryot.log"
        procs.append(subprocess.Popen(
            [backend_bin],
            cwd=work,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "DATABASE_URL": f"postgres://postgres@127.0.0.1:{pg_port}/postgres",
                "SERVER_ADMIN_ACCESS_TOKEN": admin_token,
                "SERVER_BACKEND_HOST": "127.0.0.1",
                "SERVER_BACKEND_PORT": str(be_port),
                "FRONTEND_URL": f"http://127.0.0.1:{px_port}",
            },
            stdout=open(log, "wb"),
            stderr=subprocess.STDOUT,
        ))
        backend = f"http://127.0.0.1:{be_port}"
        await _wait_ready(f"{backend}/graphql", procs[0], log)

        runner = web.AppRunner(_proxy_app(backend))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", px_port).start()

        print(json.dumps({"url": f"http://127.0.0.1:{px_port}", "admin_token": admin_token,
                          "version": version, "workdir": str(work)}), flush=True)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    finally:
        if runner:
            await runner.cleanup()
        for p in procs:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        subprocess.run([pg_bin / "pg_ctl", "-D", work / "pg", "-m", "fast", "stop"], env=pg_env, capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
        _log("stopped and cleaned up")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, help="fixed loopback port for the /backend proxy")
    asyncio.run(run(ap.parse_args().port))


if __name__ == "__main__":
    main()
