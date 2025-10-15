# -*- coding: utf-8 -*-
"""Flespi Stream Forwarder

This application listens for incoming TCP connections (expected from Flespi)
and forwards the received data stream to a predefined target TCP server.

Configuration is done via environment variables:
- LISTEN_PORT: The port the application will listen on (default: 8080)
- TARGET_HOST: The hostname or IP address of the target server (default: 185.213.2.30)
- TARGET_PORT: The port of the target server (default: 20493)
"""

import socket
import threading
import os
import sys
import signal
import time

# Configuration from environment variables
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", 4000))
TARGET_HOST = os.environ.get("TARGET_HOST", "13.245.46.90")
TARGET_PORT = int(os.environ.get("TARGET_PORT", 5722))
BUFFER_SIZE = 4096  # 4KB buffer

def handle_client(client_socket, client_address):
    """Handles a single client connection: connects to target and forwards data."""
    print(f"[INFO] Accepted connection from {client_address}")

    target_socket = None
    try:
        # Connect to the target server
        target_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target_socket.connect((TARGET_HOST, TARGET_PORT))
        print(f"[INFO] Connected to target {TARGET_HOST}:{TARGET_PORT} for {client_address}")

        # Start forwarding threads
        stop_event = threading.Event()

        def forward(src, dst, direction):
            """Forwards data from src socket to dst socket."""
            try:
                while not stop_event.is_set():
                    data = src.recv(BUFFER_SIZE)
                    if not data:
                        print(f"[INFO] Connection closed by {src.getpeername()} ({direction}) for {client_address}")
                        break
                    dst.sendall(data)
                    # print(f"[DEBUG] Forwarded {len(data)} bytes {direction} for {client_address}") # Uncomment for debug
            except socket.error as e:
                if not stop_event.is_set(): # Avoid logging errors during shutdown
                    print(f"[ERROR] Socket error during forwarding {direction} for {client_address}: {e}")
            finally:
                stop_event.set() # Signal the other thread to stop

        # Thread to forward data from client (Flespi) to target
        forward_to_target_thread = threading.Thread(
            target=forward,
            args=(client_socket, target_socket, "client->target"),
            daemon=True
        )
        # Thread to forward data from target back to client (Flespi) - if needed
        # Flespi streams are typically one-way, but bidirectional forwarding might be useful in some cases.
        # If Flespi *only* sends, this second thread isn't strictly necessary but doesn't hurt.
        forward_to_client_thread = threading.Thread(
            target=forward,
            args=(target_socket, client_socket, "target->client"),
            daemon=True
        )

        forward_to_target_thread.start()
        forward_to_client_thread.start()

        # Wait for either thread to finish (which means a connection closed or error)
        while not stop_event.is_set():
            time.sleep(0.1) # Prevent busy-waiting
            if not forward_to_target_thread.is_alive() or not forward_to_client_thread.is_alive():
                stop_event.set()

        print(f"[INFO] Forwarding stopped for {client_address}")

    except socket.error as e:
        print(f"[ERROR] Could not connect to target {TARGET_HOST}:{TARGET_PORT} for {client_address}: {e}")
    except Exception as e:
        print(f"[ERROR] Unexpected error handling client {client_address}: {e}")
    finally:
        if target_socket:
            try:
                target_socket.shutdown(socket.SHUT_RDWR)
            except socket.error:
                pass # Ignore errors on shutdown
            target_socket.close()
            print(f"[INFO] Closed target connection for {client_address}")
        try:
            client_socket.shutdown(socket.SHUT_RDWR)
        except socket.error:
            pass # Ignore errors on shutdown
        client_socket.close()
        print(f"[INFO] Closed client connection from {client_address}")

def start_server():
    """Starts the main TCP server to listen for incoming connections."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Allow reusing address quickly after server restart
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_socket.bind(("0.0.0.0", LISTEN_PORT))
        server_socket.listen(5) # Listen for up to 5 queued connections
        print(f"[INFO] Server listening on 0.0.0.0:{LISTEN_PORT}")
        print(f"[INFO] Forwarding incoming connections to {TARGET_HOST}:{TARGET_PORT}")

        while True:
            try:
                client_socket, client_address = server_socket.accept()
                # Create a new thread for each client
                client_handler = threading.Thread(
                    target=handle_client,
                    args=(client_socket, client_address),
                    daemon=True # Allows main thread to exit even if client threads are running
                )
                client_handler.start()
            except socket.error as e:
                print(f"[ERROR] Error accepting connection: {e}")
                # Decide if the error is fatal or recoverable
                if e.errno not in [socket.errno.ECONNABORTED, socket.errno.EAGAIN]:
                    break # Break on more serious errors
            except KeyboardInterrupt:
                 print("\n[INFO] KeyboardInterrupt received, shutting down server.")
                 break

    except socket.error as e:
        print(f"[FATAL] Could not bind to port {LISTEN_PORT}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[FATAL] Unexpected error starting server: {e}")
        sys.exit(1)
    finally:
        print("[INFO] Closing server socket.")
        server_socket.close()

def signal_handler(sig, frame):
    print(f"\n[INFO] Signal {sig} received, shutting down gracefully.")
    # Perform any cleanup here if needed before exiting
    sys.exit(0)

if __name__ == "__main__":
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)  # Handle Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # Handle termination signals (e.g., from Render)

    start_server()

