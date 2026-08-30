/**
 * WhatsApp sidecar (Baileys) for MVP-CRM.
 *
 * This is NOT the official WhatsApp Cloud API. It connects like WhatsApp
 * Web/Desktop does -- QR code scan from a real phone, multi-device protocol
 * -- so it needs no Meta Developers app, no business verification, no
 * template restriction. That's exactly why it's useful for a fast demo, and
 * exactly why it's not meant for production: it is unofficial, against
 * WhatsApp's Terms of Service, and the number can be banned without notice.
 *
 * Responsibilities:
 *  - Hold the WhatsApp connection (auth persisted to ./auth so a restart
 *    doesn't force a re-scan).
 *  - POST /send  { to, body }  -> sends a text message, returns { id }.
 *  - On inbound messages and outbound delivery-status updates, POST
 *    normalized events to Django's webhook, shaped exactly like the "fake"
 *    provider's payload: { "events": [ {...InboundEvent fields...} ] }.
 *
 * Auth between this process and Django is a shared secret
 * (X-Sidecar-Secret), checked both ways: Django's baileys provider checks it
 * on webhook POSTs from here, and /send checks it on calls from Django.
 */

require('dotenv/config');

const express = require('express');
const qrcodeTerminal = require('qrcode-terminal');
const pino = require('pino');
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require('@whiskeysockets/baileys');

const PORT = process.env.PORT || 4000;
const SIDECAR_SECRET = process.env.SIDECAR_SECRET || '';
const DJANGO_WEBHOOK_URL = process.env.DJANGO_WEBHOOK_URL || '';
const AUTH_DIR = process.env.AUTH_DIR || './auth';

if (!SIDECAR_SECRET) {
  console.error('SIDECAR_SECRET is not set (see .env.example) -- refusing to start.');
  process.exit(1);
}
if (!DJANGO_WEBHOOK_URL) {
  console.error('DJANGO_WEBHOOK_URL is not set (see .env.example) -- refusing to start.');
  process.exit(1);
}

const logger = pino({ level: process.env.LOG_LEVEL || 'info' });

// Baileys ack levels: 0 error, 1 pending, 2 server ack ("sent"), 3 delivery
// ack ("delivered"), 4 read, 5 played (voice notes -- treat as read).
const ACK_STATUS = { 2: 'sent', 3: 'delivered', 4: 'read', 5: 'read' };

let sock; // current socket, used by the /send route

/** Pull plain text out of whichever message-type wrapper Baileys hands us. */
function extractText(message) {
  if (!message) return '';
  return (
    message.conversation ||
    message.extendedTextMessage?.text ||
    message.imageMessage?.caption ||
    message.videoMessage?.caption ||
    ''
  );
}

/** E.164-ish digits-with-plus from a Baileys JID ("573001234567@s.whatsapp.net"). */
function jidToE164(jid) {
  const digits = (jid || '').split('@')[0].split(':')[0];
  return digits ? `+${digits}` : '';
}

function e164ToJid(to) {
  const digits = String(to).replace(/[^\d]/g, '');
  return `${digits}@s.whatsapp.net`;
}

async function postEvents(events) {
  if (events.length === 0) return;
  try {
    const res = await fetch(DJANGO_WEBHOOK_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Sidecar-Secret': SIDECAR_SECRET,
      },
      body: JSON.stringify({ events }),
    });
    if (!res.ok) {
      logger.error({ status: res.status }, 'Django webhook rejected event batch');
    }
  } catch (err) {
    logger.error({ err }, 'failed to reach Django webhook');
  }
}

async function startSock() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false, // we render it ourselves for a bigger/clearer code
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log('\nScan this QR code from the phone that should be the CRM\'s WhatsApp number:');
      console.log('WhatsApp app > Linked devices > Link a device\n');
      qrcodeTerminal.generate(qr, { small: true });
    }

    if (connection === 'open') {
      console.log(`\nConnected as ${sock.user?.id?.split(':')[0] || 'unknown'}. Sidecar is ready.\n`);
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      console.log(`Connection closed (code=${statusCode}). ${loggedOut ? 'Logged out -- delete ./auth and restart to re-pair.' : 'Reconnecting...'}`);
      if (!loggedOut) {
        startSock();
      }
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    const events = [];
    for (const m of messages) {
      if (m.key.fromMe) continue; // our own sends, not inbound
      if (m.key.remoteJid?.endsWith('@g.us')) continue; // skip groups for now
      if (m.key.remoteJid === 'status@broadcast') continue;

      const body = extractText(m.message);
      if (!body) continue; // media-only / reaction / system messages: skip for now

      events.push({
        event_type: 'message',
        provider_message_id: m.key.id,
        from_number: jidToE164(m.key.remoteJid),
        body,
        channel: 'whatsapp',
        contact_name: m.pushName || '',
        timestamp: m.messageTimestamp
          ? new Date(Number(m.messageTimestamp) * 1000).toISOString()
          : new Date().toISOString(),
      });
    }
    await postEvents(events);
  });

  sock.ev.on('messages.update', async (updates) => {
    const events = [];
    for (const { key, update } of updates) {
      const mapped = ACK_STATUS[update.status];
      if (!mapped) continue;
      events.push({
        event_type: 'status',
        provider_message_id: key.id,
        status: mapped,
        timestamp: new Date().toISOString(),
      });
    }
    await postEvents(events);
  });

  return sock;
}

const app = express();
app.use(express.json());

app.post('/send', async (req, res) => {
  if (req.headers['x-sidecar-secret'] !== SIDECAR_SECRET) {
    return res.status(401).json({ error: 'invalid secret' });
  }
  const { to, body } = req.body || {};
  if (!to || !body) {
    return res.status(400).json({ error: 'to and body are required' });
  }
  if (!sock) {
    return res.status(503).json({ error: 'WhatsApp connection not ready' });
  }
  try {
    const result = await sock.sendMessage(e164ToJid(to), { text: body });
    return res.json({ id: result.key.id });
  } catch (err) {
    logger.error({ err }, 'send failed');
    return res.status(502).json({ error: 'send failed' });
  }
});

app.get('/health', (req, res) => {
  res.json({ connected: Boolean(sock?.user) });
});

app.listen(PORT, () => {
  console.log(`Sidecar HTTP listening on :${PORT} (POST /send, GET /health)`);
});

startSock().catch((err) => {
  console.error('failed to start WhatsApp connection', err);
  process.exit(1);
});
