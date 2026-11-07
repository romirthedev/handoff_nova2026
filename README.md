# Handoff: AI expo for the pass

Live: https://romptl8--handoff.us-east.modal.run (demo kitchen at `/k/demo`)

- **Laptop** (`/k/<id>`): live ticket rail for DoorDash, Uber Eats, Grubhub, phone, web and walk-in orders.
  Voice control (Chrome/Edge, `Space` toggles the mic) or type commands:
  "start the timer for order 45 steak", "fire order 53", "how long on 45", "add 2 minutes to 45 lobster",
  "order 45 is ready", "order 45 picked up", "cancel order 46", "start John's steak", "what's next".
  Several orders in one breath work too: "start order 41 smashburger, order 43 margarita pizza, and cancel 46".
  Timers come from the dish and doneness (medium-rare ribeye = 15 min). The 10×/60× switch speeds them up for demos.
- **Phone** (scan the QR on the dashboard): tap *Ready to identify* with the packing spot empty. It watches for an
  order to slide in and hold still, photographs it, finds whose order it is, and shows green (good to go) or red
  (what's missing or extra). The laptop flashes the same result on the ticket.
- **Print kit** (`/k/<id>/print`): food cards for every open ticket, one-of-each menu cards, and bag labels.

## How plates are read
Running locally with `run_local.py`, plate checks go through the Claude Code CLI on this Mac (its own login, no
API key): one headless turn with the photo as an image and a JSON schema for the answer. If it says it's logged
out, run `claude` in Terminal and type `/login`; the next scan picks it up, no restart needed.
With an `ANTHROPIC_API_KEY` (in `handoff/.env` locally, or the Modal secret), Claude (`claude-opus-5-5`, low effort) looks at the photo and maps what it sees to the
menu, so real food and the printed cards both work. Set `HANDOFF_VISION_MODEL=claude-haiku-5-5` for the fastest checks.
Without a key, the server reads the small QR code printed on each card (and the bag-label QR), so the demo still
works offline. Matching to a ticket is done in code (`app/core.py: match_plate`).

## Run locally
    uv venv -p 3.12 .venv && uv pip install -p .venv "fastapi[standard]==0.162.0" anthropic==2.6.0 segno==1.6.6 zxing-cpp==3.1.1 pillow numpy pytest
    .venv/bin/python run_local.py        # laptop: http://localhost:8820, phones: https://<wi-fi ip>:8443
    .venv/bin/python -m pytest -q tests
Phones only open the camera over https, so `run_local.py` adds an https listener on the laptop's Wi-Fi address with
a self-signed certificate, and the dashboard QR points there. The phone warns once about the certificate
(Safari: Show Details > visit this website). The phone must be on the same Wi-Fi. On guest or venue Wi-Fi that
blocks device-to-device traffic, use the deployed site instead.

## Deploy
    read -s "k?Anthropic key: " && modal secret create handoff-anthropic ANTHROPIC_API_KEY="$k" --force && unset k
    modal deploy modal_app.py
