# whatsapp-sidecar

A small Node process that connects to WhatsApp the way WhatsApp Web/Desktop
does -- QR code scan, [Baileys](https://github.com/WhiskeySockets/Baileys)'
multi-device protocol -- and bridges messages to the Django CRM.

**This is not the official WhatsApp Cloud API.** No Meta Developers app, no
business verification, no app review, no template restriction -- which is
exactly why it exists: to get real inbound/outbound WhatsApp messages into
the CRM fast (demo, MVP, internal testing).

It is also **not something to run in production**: this connection method
is against WhatsApp's Terms of Service, and Meta can suspend a number using
it without warning. Once real Meta Developers/Cloud API access is unblocked,
switch `MESSAGING_PROVIDER` in the Django app to `meta` (or `twilio`) and
retire this sidecar -- no other code changes needed, see the main
[README.md](../README.md).

## Setup

```bash
cd whatsapp-sidecar
npm install
cp .env.example .env      # edit if you're not running Django on localhost:8000
npm start
```

A QR code prints in the terminal. On your phone: **WhatsApp > Settings >
Linked devices > Link a device**, and scan it. Use the phone number you want
the CRM to send/receive as.

Once connected you'll see `Connected as <number>. Sidecar is ready.` in the
terminal. The session persists in `./auth/` -- restarting `npm start` will
not ask you to re-scan unless you delete that folder or WhatsApp logs the
device out remotely.

## Then, on the Django side

Set in your `.env` (see the project root's `.env.example`):

```
MESSAGING_PROVIDER=baileys
BAILEYS_SIDECAR_URL=http://localhost:4000
BAILEYS_SIDECAR_SECRET=dev-sidecar-secret   # must match this folder's SIDECAR_SECRET
```

Run Django as usual. Messages sent from real phones to the linked WhatsApp
number will now appear in the Inbox; replies sent from the Inbox go out
through this sidecar to the real phone.

## Endpoints

- `POST /send` `{ "to": "+573...", "body": "..." }` -> `{ "id": "<message id>" }`
  Requires header `X-Sidecar-Secret: <SIDECAR_SECRET>`.
- `GET /health` -> `{ "connected": true|false }`
