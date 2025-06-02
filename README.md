# Flespi Stream Forwarder

This application listens for incoming TCP connections (expected from Flespi) on a specified port and forwards the received data stream to a predefined target TCP server.

## Functionality

-   Listens on `0.0.0.0` at a configurable port for incoming TCP connections.
-   For each incoming connection, it establishes a new TCP connection to a configurable target host and port.
-   It forwards data bidirectionally between the client (e.g., Flespi) and the target server.
-   Handles multiple client connections concurrently using threading.
-   Includes basic logging for connection events and errors.
-   Designed for deployment on platforms like Render.com using a `Procfile`.

## Configuration

The application is configured using environment variables:

-   `LISTEN_PORT`: The port the application will listen on. Defaults to `8080`.
-   `TARGET_HOST`: The hostname or IP address of the target server to forward data to. Defaults to `185.213.2.30`.
-   `TARGET_PORT`: The port of the target server. Defaults to `20493`.

## Deployment on Render.com

1.  **Fork/Clone this Repository:** Get a copy of this code in your own GitHub account.
2.  **Create a New Web Service on Render:**
    *   Connect your GitHub account to Render.
    *   Select this repository.
    *   Render should automatically detect the Python environment.
    *   **Service Type:** Choose `Worker` (since this is a background service, not a web server serving HTTP requests).
    *   **Start Command:** Render should pick up `python main.py` from the `Procfile`. If not, set it manually.
    *   **Environment Variables:** Add the environment variables (`LISTEN_PORT`, `TARGET_HOST`, `TARGET_PORT`) under the 'Environment' section if you need to override the defaults.
        *   **Important:** Render assigns a port dynamically via the `PORT` environment variable for *Web Services*. Since we are using a *Worker*, Render doesn't automatically assign a `PORT`. Flespi needs a stable public IP and port to connect to. Render's *Private Services* with a static IP might be needed, or you might need to configure Flespi to connect to the specific Render worker URL/IP if available and stable (consult Render documentation for worker networking).
        *   For a simple setup, you might deploy this as a *Web Service* instead of a *Worker*, let Render assign the `PORT` (which becomes `LISTEN_PORT`), and then configure Flespi to send data to the `your-service-name.onrender.com:PORT` endpoint. However, ensure Flespi can handle dynamic ports or use Render's features for stable ingress if required.
3.  **Deploy:** Click 'Create Web Service' (or 'Create Worker Service').

## Running Locally

1.  Clone the repository: `git clone <repository-url>`
2.  Navigate to the directory: `cd flespi-stream-forwarder`
3.  (Optional) Set environment variables:
    ```bash
    export LISTEN_PORT=9000
    export TARGET_HOST=localhost
    export TARGET_PORT=12345
    ```
4.  Run the application: `python main.py`

## Flespi Configuration

In your Flespi account, configure a stream to output data via TCP to the address and port where this forwarder application is running (e.g., `your-service-name.onrender.com:PORT` or the static IP/port if configured).

