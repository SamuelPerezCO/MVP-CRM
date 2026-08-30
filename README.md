# MVP-CRM

MVP de un CRM omnicanal para comercios, inspirado en plataformas tipo Treble/Leadsales: una bandeja de entrada unificada para los canales de mensajería (WhatsApp, Messenger, Instagram, Facebook, TikTok), gestión de clientes y embudos de venta, todo dentro de un shell de una sola página con barra lateral de iconos.

## Funcionalidades

- **Inbox** — conversaciones reales filtradas por canal y asignación, con lista, chat en vivo (polling htmx), compositor con la regla de 24 horas de WhatsApp y panel de detalles del cliente.
- **CRM** — tabla de clientes (nombre, teléfono con bandera de país, mail, canal) y listas de clientes.
- **Embudos** — panel de embudos de venta con creación de nuevos embudos.
- **Automatizaciones** — flujos de chatbots y banner de Academy.
- **Mi comercio** — catálogo de productos con creación e importación.
- **Campañas, Estadísticas y Mensajería** — métricas de mensajería y plantillas de WhatsApp.

Las secciones sin pantalla propia todavía (Performance HUB, Integraciones, etc.) muestran un placeholder automáticamente; agregar una sección nueva es una línea en [core/nav.py](core/nav.py).

## Stack

- [Django 6.1](https://www.djangoproject.com/) (Python) con SQLite.
- [htmx](https://htmx.org/) para los paneles dinámicos — sin build de frontend.
- CSS y SVG propios en [static/](static/) y [templates/icons/](templates/icons/).

## Puesta en marcha

```powershell
python -m venv venv
venv\Scripts\activate        # en Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Abre http://127.0.0.1:8000/ — la pantalla de bienvenida enlaza a Inbox, CRM y Embudos.

Para ver el Inbox con datos:

```powershell
python manage.py seed_conversations       # contactos y conversaciones de demo (--fresh para regenerar)
python manage.py simulate_inbound "+573000000001" "Hola, ¿sigue disponible?"
```

`seed_conversations` crea un usuario `asesor` / `asesor123` — inicia sesión en `/admin` con él para que el filtro "Tu inbox" tenga conversaciones. `simulate_inbound` empuja un mensaje entrante por el **mismo** código del webhook (firma, parseo, idempotencia); con el Inbox abierto lo verás llegar solo en el siguiente poll.

## Mensajería: cambiar de proveedor

Toda la integración con WhatsApp vive en [messaging/](messaging/) detrás de una abstracción de proveedor ([messaging/providers/base.py](messaging/providers/base.py)). El proveedor activo lo decide **una sola variable**:

```
MESSAGING_PROVIDER=fake    # hoy
MESSAGING_PROVIDER=twilio  # cuando haya credenciales de Twilio
MESSAGING_PROVIDER=meta    # cuando Meta desbloquee la cuenta
MESSAGING_PROVIDER=baileys # WhatsApp real ya, sin esperar a Meta (ver abajo)
```

### Mensajería real ya, sin esperar a Meta: `baileys`

Mientras Meta Developers esté bloqueado (verificación de negocio, revisión de
app, etc.), `MESSAGING_PROVIDER=baileys` conecta el Inbox a un número de
WhatsApp real hoy mismo, sin ninguna cuenta de Meta Developers: usa
[Baileys](https://github.com/WhiskeySockets/Baileys) para hablar el mismo
protocolo que WhatsApp Web/Desktop -- se conecta escaneando un código QR
desde el teléfono, no por API oficial.

**Esto no es la Cloud API oficial.** Es útil para levantar una demo o un MVP
en minutos, pero va contra los Términos de Servicio de WhatsApp y Meta puede
suspender el número sin aviso -- no lo dejes así para producción. En cuanto
Meta desbloquee la cuenta, cambia `MESSAGING_PROVIDER` a `meta` (o `twilio`)
y apaga el sidecar; el resto del código no cambia.

Requiere levantar un proceso aparte, el sidecar de Node en
[whatsapp-sidecar/](whatsapp-sidecar/):

```powershell
cd whatsapp-sidecar
npm install
cp .env.example .env
npm start
```

Escanea el código QR que aparece en la terminal desde **WhatsApp > Ajustes >
Dispositivos vinculados > Vincular un dispositivo**, con el número que quieres
usar para el CRM. La sesión queda guardada en `whatsapp-sidecar/auth/`, así
que no hay que volver a escanear en cada reinicio.

Luego, en el `.env` de Django:

```
MESSAGING_PROVIDER=baileys
BAILEYS_SIDECAR_URL=http://localhost:4000
BAILEYS_SIDECAR_SECRET=dev-sidecar-secret   # debe coincidir con el .env del sidecar
```

Arranca Django normalmente (`python manage.py runserver`) -- los mensajes que
lleguen al número vinculado aparecen en el Inbox, y las respuestas enviadas
desde el Inbox salen por WhatsApp real a través del sidecar. Ver
[whatsapp-sidecar/README.md](whatsapp-sidecar/README.md) para más detalle.

Cuando lleguen credenciales reales de Twilio o Meta:

1. Copia [.env.example](.env.example) a `.env` (está en `.gitignore`) y llena las credenciales del proveedor; expórtalas al entorno antes de `runserver` — los settings leen `os.environ` directamente.
2. Implementa los métodos de [messaging/providers/twilio.py](messaging/providers/twilio.py) o [messaging/providers/meta.py](messaging/providers/meta.py) — el docstring de cada módulo describe exactamente qué endpoint, firma y formato de webhook usa cada uno. Nada fuera de ese archivo cambia: ni vistas, ni modelos, ni templates.
3. Cambia `MESSAGING_PROVIDER` y registra la URL del webhook en la consola del proveedor: `https://tu-dominio/webhooks/messaging/twilio/` o `.../meta/` (Meta verifica primero con un GET; el endpoint ya responde el `hub.challenge`).

El webhook verifica la firma antes de tocar el payload (401 si es inválida), es idempotente por `provider_message_id` (los reintentos del proveedor no duplican mensajes) y siempre responde 200 tras autenticar, registrando errores en el log en lugar de provocar tormentas de reintentos. El envío de texto libre está bloqueado fuera de la ventana de 24 horas ([messaging/services.py](messaging/services.py)) — fuera de ella solo cabe `send_template`, igual que en la plataforma real.

## Tests

```powershell
python manage.py test
```

Cada sección tiene su propio archivo de tests en [core/](core/) (`tests.py`, `tests_crm.py`, `tests_embudos.py`, etc.); la capa de mensajería (idempotencia del webhook, rechazo de firmas, ventana de 24h) se prueba en [messaging/tests.py](messaging/tests.py).

## Estructura

| Ruta | Qué contiene |
|---|---|
| [config/](config/) | Settings y URLs del proyecto |
| [core/](core/) | Vistas, modelos, navegación y datos de cada sección |
| [messaging/](messaging/) | Conversaciones, mensajes, webhook y proveedores (fake/twilio/meta) |
| [templates/sections/](templates/sections/) | Pantalla completa de cada sección |
| [templates/partials/](templates/partials/) | Fragmentos que htmx intercambia |
| [static/](static/) | CSS por sección y `shell.js` |
