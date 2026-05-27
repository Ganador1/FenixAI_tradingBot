try:
    import websockets
except Exception:
    websockets = None


def ws_connect(url, **kwargs):
    if websockets is None:
        raise RuntimeError("websockets not available")
    return websockets.connect(url, **kwargs)
