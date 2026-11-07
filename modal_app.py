"""Deploy Handoff to Modal (personal workspace). Phones need https for the camera, which Modal gives us.

Optional, for real-food recognition with Claude (you run this; the key never appears in code):
    read -s "k?Anthropic key: " && modal secret create handoff-anthropic ANTHROPIC_API_KEY="$k" --force && unset k
Deploy:  modal deploy modal_app.py   ->  https://romptl8--handoff.modal.run
Without the secret it still runs, reading the printed card markers instead.
"""

import modal

app = modal.App("handoff")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]==0.162.0", "anthropic==2.6.0", "segno==1.6.6", "zxing-cpp==3.1.1", "pillow", "numpy")
    .env({"HANDOFF_STORE": "modal"})
    .add_local_dir("app", "/root/app", ignore=["**/__pycache__"])
)


def _secrets():
    try:
        s = modal.Secret.from_name("handoff-anthropic")
        s.hydrate()
        return [s]
    except Exception:
        return []


# One container: tickets and websockets live in its memory (saved to a modal.Dict so restarts keep them).
@app.function(image=image, secrets=_secrets(), scaledown_window=1800, max_containers=1, timeout=3600)
@modal.concurrent(max_inputs=200)
@modal.asgi_app(label="handoff")
def web():
    from app.server import app as fastapi_app
    return fastapi_app
