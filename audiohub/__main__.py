"""Start the hub in a worker thread and show the mixer in a small window."""
import argparse
import asyncio
import threading
import webbrowser


def serve(args, ready, failed):
    # Imported here so COM is set up in this thread, not the UI thread.
    try:
        from .hub import Hub
        hub = Hub(recent_timeout=args.recent_minutes * 60)
        asyncio.run(hub.run(args.host, args.port, ready=ready.set))
    except Exception as e:
        failed.append(e)
        ready.set()
        raise


def main():
    ap = argparse.ArgumentParser(prog="audiohub")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--recent-minutes", type=float, default=10,
                    help="how long a stopped app with no player stays under Recent")
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of a window")
    ap.add_argument("--headless", action="store_true", help="only run the server")
    args = ap.parse_args()

    ready, failed = threading.Event(), []
    worker = threading.Thread(target=serve, args=(args, ready, failed), daemon=True)
    worker.start()
    ready.wait()
    if failed:
        raise SystemExit(f"Audio Hub failed to start: {failed[0]!r}")

    url = f"http://{args.host}:{args.port}/"
    if not (args.browser or args.headless):
        try:
            import webview
            webview.create_window("Audio Hub", url, width=440, height=680, min_size=(340, 400))
            webview.start()
            return
        except Exception as e:
            print("No app window, using the browser:", repr(e))
    if not args.headless:
        webbrowser.open(url)
    try:
        worker.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
