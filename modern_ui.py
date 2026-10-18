import threading
import webbrowser
from http.server import ThreadingHTTPServer

from web_ui import Handler


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import webview
        webview.create_window("Aether", "http://127.0.0.1:8765", width=1440, height=900)
        webview.start()
    except ImportError:
        webbrowser.open("http://127.0.0.1:8765")
        print("Install pywebview for a desktop window; browser fallback opened.")
