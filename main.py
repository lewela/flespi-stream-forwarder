# -*- coding: utf-8 -*-
"""
Flespi → Uffizio Stream Forwarder (asyncio edition)

Listens for incoming TCP connections (expected from Flespi) and forwards the
byte stream to a target TCP server (Uffizio). Supports optional bidirectional
forwarding, back-pressure, timeouts, TLS-to-target, and graceful shutdown.

ENV VARS
--------
LISTEN_HOST        : Host/IP to bind (default: 0.0.0.0)
LISTEN_PORT        : Port to bind (default: 5000)
TARGET_HOST        : Target host/IP (default: 13.245.46.90)
TARGET_PORT        : Target port (default: 5722)
BUFFER_SIZE        : Read buffer size bytes (default: 65536)
BIDI               : "1" to enable target->client forwarding (default: "1")
CONNECT_TIMEOUT_S  : Seconds to wait when connecting to target (default: 10)
READ_TIMEOUT_S     : Seconds of inactivity before closing (default: 180)
WRITE_TIMEOUT_S    : Seconds for writer.drain() (default: 30)
IDLE_GRACE_S       : Extra seconds to allow on shutdown (default: 5)
TARGET_TLS         : "1" to enable TLS to target (default: "0")
TLS_VERIFY         : "1" to verify server cert if TLS enabled (default: "1")
SOURCE_WHITELIST   : Comma-separated client IPs allowed (optional)
LOG_LEVEL          : DEBUG | INFO | WARNING | ERROR (default: INFO)

NOTES
-----
- Flespi typically pushes one-way, but BIDI=1 is safe if target replies.
- Back-pressure: we await writer.drain() so we never overrun buffers.
- Timeouts protect against hung sockets; tune for your workload.
- If Uffizio requires TLS (often true), set TARGET_TLS=1 (and supply system CAs).
- Production tip: run under systemd or a container with proper restart policy.
"""

import asyncio
import os
import signal
import ssl
import sys
import logging
from typing import Optional

# -----------------------
# Configuration
# -----------------------
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5000"))

TARGET_HOST = os.environ.get("TARGET_HOST", "13.245.46.90")
TARGET_PORT = int(os.environ.get("TARGET_PORT", "5722"))

BUFFER_SIZE = int(os.environ.get("BUFFER_SIZE", "65536"))
BIDI = os.environ.get("BIDI", "1") == "1"

CONNECT_TIMEOUT_S = float(os.environ.get("CONNECT_TIMEOUT_S", "10"))
READ_TIMEOUT_S = float(os.environ.get("READ_TIMEOUT_S", "180"))
WRITE_TIMEOUT_S = float(os.environ.get("WRITE_TIMEOUT_S", "30"))
IDLE_GRACE_S = float(os.environ.get("IDLE_GRACE_S", "5"))

TARGET_TLS = os.environ.get("TARGET_TLS", "0") == "1"
TLS_VERIFY = os.environ.get("TLS_VERIFY", "1") == "1"

WHITELIST = {
    ip.strip() for ip in os.environ.get("SOURCE_WHITELIST", "").split(",") if ip.strip()
}

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("flespi→uffizio")

# -----------------------
# TLS to target (optional)
# -----------------------
def build_ssl_ctx() -> Optional[ssl.SSLContext]:
    if not TARGET_TLS:
        return None
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if not TLS_VERIFY:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx

SSL_CTX = build_ssl_ctx()

# -----------------------
# Utilities
# -----------------------
def socket_tune(stream_writer: asyncio.StreamWriter):
    """Set useful socket options (TCP keepalive) on the underlying socket."""
    sock = stream_writer.get_extra_info("socket")
    if not sock:
        return
    try:
        sock.setsockopt(asyncio.socket.SOL_SOCKET, asyncio.socket.SO_KEEPALIVE, 1)
    except Exception:
        pass
    # Platform-specific keepalive tuning can be added here if needed.

async def async_close(writer: asyncio.StreamWriter):
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass

async def pipe_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    direction: str,
    stop_event: asyncio.Event,
    read_timeout: float,
    write_timeout: float,
):
    """
    Forward bytes from reader -> writer with back-pressure and timeouts.
    Stops when EOF or on any error; signals stop_event to cancel the sibling task.
    """
    try:
        while not stop_event.is_set():
            try:
                data = await asyncio.wait_for(reader.read(BUFFER_SIZE), timeout=read_timeout)
            except asyncio.TimeoutError:
                log.warning("Timeout reading on %s; closing.", direction)
                break

            if not data:
                log.info("EOF on %s; closing.", direction)
                break

            try:
                writer.write(data)
                await asyncio.wait_for(writer.drain(), timeout=write_timeout)
            except asyncio.TimeoutError:
                log.warning("Timeout writing on %s; closing.", direction)
                break
            except (ConnectionResetError, BrokenPipeError) as e:
                log.warning("Write error on %s: %s; closing.", direction, e)
                break
    except Exception as e:
        log.error("Unexpected error in %s: %s", direction, e, exc_info=LOG_LEVEL == "DEBUG")
    finally:
        stop_event.set()

# -----------------------
# Main per-connection handler
# -----------------------
async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer = writer.get_extra_info("peername")
    client_ip = (peer[0] if isinstance(peer, tuple) else str(peer)) if peer else "unknown"
    log.info("Accepted connection from %s", peer)

    # Optional whitelist
    if WHITELIST and client_ip not in WHITELIST:
        log.warning("Client %s not in whitelist, closing.", client_ip)
        await async_close(writer)
        return

    # Set keepalive and other options
    socket_tune(writer)

    # Connect to target (Uffizio)
    try:
        target_reader, target_writer = await asyncio.wait_for(
            asyncio.open_connection(TARGET_HOST, TARGET_PORT, ssl=SSL_CTX),
            timeout=CONNECT_TIMEOUT_S,
        )
        socket_tune(target_writer)
        log.info("Connected to target %s:%s for %s", TARGET_HOST, TARGET_PORT, peer)
    except asyncio.TimeoutError:
        log.error("Timeout connecting to target %s:%s for %s", TARGET_HOST, TARGET_PORT, peer)
        await async_close(writer)
        return
    except Exception as e:
        log.error("Failed to connect to target %s:%s for %s: %s",
                  TARGET_HOST, TARGET_PORT, peer, e)
        await async_close(writer)
        return

    # Forwarding tasks
    stop_event = asyncio.Event()
    tasks = []

    # Client -> Target (primary direction)
    tasks.append(asyncio.create_task(
        pipe_stream(reader, target_writer, "client→target", stop_event, READ_TIMEOUT_S, WRITE_TIMEOUT_S)
    ))

    # Target -> Client (optional but usually harmless)
    if BIDI:
        tasks.append(asyncio.create_task(
            pipe_stream(target_reader, writer, "target→client", stop_event, READ_TIMEOUT_S, WRITE_TIMEOUT_S)
        ))

    # Wait until one direction finishes, then give the other a moment to flush
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        await asyncio.sleep(min(1.0, IDLE_GRACE_S))  # tiny grace period
    finally:
        stop_event.set()
        # Cancel any still-pending tasks
        for t in tasks:
            if not t.done():
                t.cancel()
        # Close both sides
        await async_close(target_writer)
        await async_close(writer)
        log.info("Closed connection for %s", peer)

# -----------------------
# Graceful server
# -----------------------
class GracefulServer:
    def __init__(self):
        self.server: Optional[asyncio.AbstractServer] = None
        self.shutdown_event = asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_server(
            handle_client,
            host=LISTEN_HOST,
            port=LISTEN_PORT,
            reuse_address=True,
            reuse_port=False,  # set True only if you really need multi-process sharing
            start_serving=True
        )
        sockets = self.server.sockets or []
        for s in sockets:
            addr = s.getsockname()
            log.info("Listening on %s:%s", addr[0], addr[1])
        log.info(
            "Forwarding to %s:%s (TLS=%s, verify=%s, BIDI=%s)",
            TARGET_HOST, TARGET_PORT, TARGET_TLS, TLS_VERIFY, BIDI
        )

    async def run_forever(self):
        assert self.server is not None
        async with self.server:
            await self.shutdown_event.wait()

    async def shutdown(self):
        log.info("Shutting down server gracefully…")
        self.shutdown_event.set()
        if self.server:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), timeout=IDLE_GRACE_S)
            except asyncio.TimeoutError:
                log.warning("Timed out waiting for server socket to close.")

# -----------------------
# Entry point
# -----------------------
def main():
    srv = GracefulServer()

    async def runner():
        await srv.start()
        await srv.run_forever()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Signal handlers (POSIX only)
    def _sig_handler(sig):
        log.info("Signal %s received.", sig)
        loop.create_task(srv.shutdown())

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _sig_handler, sig)
            except NotImplementedError:
                # e.g., on Windows
                pass

        loop.run_until_complete(runner())
    finally:
        pending = asyncio.all_tasks(loop=loop)
        for task in pending:
            task.cancel()
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()
        log.info("Exited.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Fallback for platforms without signal handlers
        pass
