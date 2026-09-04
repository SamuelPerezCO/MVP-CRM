# MVP-CRM

MVP de un CRM omnicanal para comercios, inspirado en plataformas tipo Treble/Leadsales: una bandeja de entrada unificada para los canales de mensajería (WhatsApp, Messenger, Instagram, Facebook, TikTok), gestión de clientes y embudos de venta, todo dentro de un shell de una sola página con barra lateral de iconos.

## Funcionalidades

- **Inbox** — conversaciones reales filtradas por canal y asignación, con lista, chat en vivo (polling htmx), compositor con la regla de 24 horas de WhatsApp (fuera de la ventana ofrece enviar una plantilla), **Nuevo chat** para escribirle primero a un cliente, respuestas rápidas (texto o imagen) que se envían de un clic y panel de detalles del cliente.
- **CRM** — clientes con alta, edición, ficha y baja desde la tabla (nombre, teléfono con bandera de país, mail, canal), buscador, exportación a Excel, listas de clientes, calendario con el cliente visible en cada evento, y el equipo (usuarios) que un usuario maestro administra.
- **Embudos** — panel de embudos de venta con creación de nuevos embudos.
- **Automatizaciones** — flujos de chatbots y banner de Academy.
- **Mi comercio** — catálogo de productos con creación e importación.
- **Campañas, Estadísticas y Mensajería** — métricas de mensajería, plantillas de WhatsApp y respuestas rápidas propias (texto e imagen) para el compositor.

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

`seed_conversations` crea un usuario `asesor` / `asesor123` y le asigna conversaciones de demo. `simulate_inbound` empuja un mensaje entrante por el **mismo** código del webhook (firma, parseo, idempotencia); con el Inbox abierto lo verás llegar solo en el siguiente poll.

Los dos **solo corren contra la base local (SQLite)**. Como `.env` define `DATABASE_URL`, sin esa barrera un `python manage.py seed_conversations` escribiría clientes inventados en la base de producción ([messaging/management/local_only.py](messaging/management/local_only.py)). Si tu `.env` apunta a Neon, pásales la base local:

```bash
DATABASE_URL= python manage.py seed_conversations
```

## Salir a producción: dejar el CRM vacío

Antes de conectar el número real de WhatsApp, `go_live` vacía la aplicación y **conserva al equipo**: borra contactos, conversaciones, mensajes, etiquetas, eventos de calendario, listas, productos, plantillas, respuestas rápidas y las cuentas de prueba (las que solo existen como asignatario, p. ej. `asesor`), y deja intactas las cuentas que pueden iniciar sesión — las creadas en CRM > Equipo > Usuarios, las de `APP_AGENTS` y cualquier superusuario.

```bash
python manage.py go_live          # simulación: dice qué borraría y no toca nada
python manage.py go_live --yes    # lo borra de verdad
```

Igual que `reset_conversations`: es simulación por defecto, nombra la base a la que apunta antes de tocarla y borra dentro de una sola transacción. `--keep-catalog` conserva productos, plantillas y respuestas rápidas (útil si las plantillas de WhatsApp ya están aprobadas por Meta). No borra los archivos ya subidos a Vercel Blob, solo las filas que apuntaban a ellos.

Crea tu cuenta en **CRM > Equipo > Usuarios** *antes* de correrlo con `--yes`: si ninguna cuenta sobrevive, la simulación te avisa.

`reset_conversations` sigue existiendo para lo de siempre — vaciar solo el Inbox (conversaciones, mensajes y contactos) sin tocar etiquetas, plantillas ni calendario.

## Agentes (personas) y la pantalla Equipo

Un **agente** es a la vez un login y un asignatario: la misma identidad que
pasa la puerta de entrada es la que puede aparecer como responsable de una
conversación en el Inbox. La lista vive en el entorno, no en la base de datos
— agregar un compañero es editar una variable y volver a desplegar, sin
pantalla de gestión de usuarios ni registro:

```
APP_AGENTS=Admin:cambia-esta-clave:Admin,Samuel:1234:Samuel
```

Entradas separadas por coma, cada una `usuario:contraseña:Nombre` (el nombre
visible es opcional y por defecto es el usuario). Las contraseñas no pueden
llevar `:` ni `,`, que son los separadores.

Al iniciar sesión se abre una sesión real de `django.contrib.auth` contra un
`User` espejo de ese agente ([core/agents.py](core/agents.py)), creado bajo
demanda y con contraseña inutilizable: existe para que `assigned_to` y
`sent_by` tengan a quién apuntar, nunca para autenticar — el entorno sigue
siendo la única vía de entrada. Eso es lo que hace que el filtro "Tu inbox"
funcione y que cada mensaje enviado registre quién lo escribió.

En el Inbox, el desplegable junto al estado de la conversación ("Abierta")
cambia el agente asignado y guarda al instante; "Sin asignar" la devuelve a la
bandeja común.

### Usuarios creados en la app (usuario maestro)

Los agentes de `APP_AGENTS` son los **maestros**: desde CRM > Equipo >
Usuarios pueden crear al resto del equipo sin tocar el entorno ni volver a
desplegar. Un usuario creado ahí es un `User` de Django con contraseña real:
inicia sesión por el mismo formulario, aparece en el desplegable de
asignación y en "Tu inbox", y puede marcarse también como maestro. Los
usuarios se desactivan (nunca se borran): su historial de conversaciones y
mensajes sigue apuntando a ellos. Los agentes del entorno se muestran en la
misma tabla pero solo se editan en `APP_AGENTS` ([core/agents.py](core/agents.py)).

El rol maestro vive en el grupo `Maestros` de Django, no en `is_staff`: ese
flag significa "puede entrar a /admin/", que es otra pregunta — el usuario de
demo que crea `seed_conversations` lo tiene y no por eso administra el equipo.

Si `APP_AGENTS` no está definida se usa el par antiguo
`APP_LOGIN_USERNAME`/`APP_LOGIN_PASSWORD` como lista de un solo agente, así que
un entorno anterior a esto sigue funcionando sin tocar nada.

## Mensajería: cambiar de proveedor

Toda la integración con WhatsApp vive en [messaging/](messaging/) detrás de una abstracción de proveedor ([messaging/providers/base.py](messaging/providers/base.py)). El proveedor activo lo decide **una sola variable**:

```
MESSAGING_PROVIDER=fake    # hoy
MESSAGING_PROVIDER=twilio  # cuando haya credenciales de Twilio
MESSAGING_PROVIDER=meta    # cuando Meta desbloquee la cuenta
MESSAGING_PROVIDER=baileys # WhatsApp real ya, sin esperar a Meta (ver abajo)
```

El webhook del proveedor `fake` (`/webhooks/messaging/fake/`) crea contactos y conversaciones y su única llave es `MESSAGING_FAKE_SECRET`, cuyo valor por defecto está publicado en este repositorio. Por eso solo responde donde los datos falsos tienen sentido: con `DEBUG=True` o bajo `manage.py test`. En un despliegue real devuelve 404, así que nadie puede meter clientes inventados en el Inbox ([messaging/providers/registry.py](messaging/providers/registry.py)). Los webhooks de Meta, Twilio y el sidecar no cambian.

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

## Escribir en la base de datos desde fuera (n8n u otra automatización)

Esta base de datos es compartida: además de esta app, una automatización
inserta clientes, conversaciones y mensajes directamente en las tablas, sin
pasar por `messaging/services.py`. Eso funciona, pero hay que respetar el
contrato de abajo, porque **Django rellena sus valores por defecto en Python,
no en la base**: un `INSERT` externo no recibe ninguno. Las columnas de texto
opcionales son `NOT NULL` con `''` como valor vacío — nunca insertes `NULL`
en ellas.

La app ya no se cae con un valor desconocido (hay tests en
[messaging/tests_external_writer.py](messaging/tests_external_writer.py)),
pero *tolerar* no es *mostrar bien*: una conversación con un canal que no
existe sale con un icono genérico, y un mensaje con un estado que no existe
sale con el icono de alerta. El contrato es lo que hace que se vean bien.

### Reglas generales

- Todas las columnas de fecha son `timestamptz`; la app corre con `USE_TZ=True` y `TIME_ZONE='UTC'`. Manda siempre **UTC con offset** (`2026-09-04T15:04:05+00:00`). Una fecha sin zona se reinterpreta en la zona de tu sesión y desplaza la ventana de 24 h y todos los informes.
- Los valores de tipo enum van en **minúsculas, exactos y sin espacios**.
- Teléfonos: `+` + indicativo + dígitos, sin espacios ni guiones (`+573001112233`). El `wa_id` de WhatsApp (`573001112233`) hay que prefijarlo con `+`.

### `core_client`

| columna | valor |
|---|---|
| `first_name` | texto ≤80. El nombre del perfil, o el teléfono si no hay |
| `last_name`, `email`, `country` | `''` si no se conocen (`country` acepta ISO-3166 alfa-2 en mayúsculas, ej. `CO`) |
| `phone` | E.164 exacto, ≤20 |
| `channel` | `''` \| `whatsapp` \| `messenger` \| `instagram` \| `facebook` \| `tiktok` |
| `created_at` | `now()` |

Busca antes de insertar: `SELECT id FROM core_client WHERE phone = $1;`

### `messaging_conversation`

| columna | valor |
|---|---|
| `contact_id` | el `core_client.id` anterior |
| `channel` | `whatsapp` \| `messenger` \| `instagram-dm` \| `facebook` \| `instagram` \| `tiktok-dm` \| `tiktok-coment` |
| `status` | `open` \| `pending` \| `resolved` |
| `assigned_to_id` | `NULL` = «Sin asignar» |
| `last_message_at` | fecha del mensaje más reciente del hilo |
| `last_inbound_at` | fecha del **entrante** más reciente; sin esto el compositor queda cerrado |
| `unread_count` | entero ≥ 0 (hay CHECK); empieza en `0` |
| `created_at` | `now()` |

Reutiliza el hilo abierto antes de crear otro:

```sql
SELECT id FROM messaging_conversation
WHERE contact_id = $1 AND channel = $2 AND status <> 'resolved'
ORDER BY last_message_at DESC NULLS LAST
LIMIT 1;
```

### `messaging_message`

| columna | valor |
|---|---|
| `conversation_id` | una conversación **de ese mismo contacto** |
| `direction` | exactamente `inbound` o `outbound` |
| `body` | texto, `''` si no hay. Para media sin pie: `[imagen]` / `[video]` / `[audio]` / `[documento]` / `[sticker]` |
| `media_url` | `''` o una URL https, **≤200 caracteres** |
| `media_type` | `''` \| `image` \| `video` \| `audio` \| `document` \| `sticker` |
| `status` | `queued` \| `sent` \| `delivered` \| `read` \| `failed`. Para algo ya entregado: `delivered` |
| `provider_message_id` | el id real del proveedor (`wamid....`), ≤255, ÚNICO. Si de verdad no lo hay, `NULL` — nunca `''` (el segundo `''` viola el índice único) |
| `timestamp` | fecha del proveedor, UTC con offset |
| `sent_by_id` | `NULL`. Ojo: `NULL` en un saliente significa «automático» para el informe de Tiempos de Respuesta |

### Después de cada mensaje, en la misma transacción

Entrante:

```sql
UPDATE messaging_conversation
   SET last_message_at = $ts,
       last_inbound_at = $ts,
       unread_count    = unread_count + 1,
       status          = CASE WHEN status = 'resolved' THEN 'open' ELSE status END
 WHERE id = $conversation_id;
```

Saliente (no toques `last_inbound_at`, `unread_count` ni `status`):

```sql
UPDATE messaging_conversation
   SET last_message_at = $ts
 WHERE id = $conversation_id;
```

Envuelve cliente → conversación → mensaje → `UPDATE` en una sola transacción,
para que un fallo no deje un mensaje sin su contabilidad.

## Deploy en Vercel

El proyecto usa el soporte nativo de Vercel para Django (detecta `manage.py` y el `WSGI_APPLICATION` de [config/settings.py](config/settings.py) automáticamente): conecta el repo en vercel.com o corre `vercel deploy` y no hace falta build script.

En el dashboard del proyecto (Settings → Environment Variables) define, como mínimo:

- `SECRET_KEY` — cualquier string largo y aleatorio (sin esto usa un valor de desarrollo inseguro).
- `DEBUG=False`
- `MESSAGING_PROVIDER` — **obligatorio**, y en producción nunca `fake`: `twilio`, `meta` o `baileys`, con las credenciales del proveedor elegido. Sin esta variable el despliegue falla al arrancar, a propósito.
- `DATABASE_URL` — Postgres (por ejemplo Vercel Postgres o Neon, desde la pestaña Storage). SQLite no sirve en producción porque las funciones serverless no tienen disco persistente.
- `ALLOWED_HOSTS` — opcional; el dominio del deploy y el alias de producción se confían automáticamente vía `VERCEL_URL` y `VERCEL_PROJECT_PRODUCTION_URL`, agrega aquí solo dominios propios (custom domains).

Con `DATABASE_URL` configurado, corre las migraciones contra la base de producción (por ejemplo con `vercel env pull` + `python manage.py migrate` localmente, o desde una shell con las mismas variables).

Los archivos estáticos (`static/`) se recolectan y sirven automáticamente desde el CDN de Vercel — no requiere WhiteNoise ni configuración adicional. Los uploads de usuario (`media/`, ej. cabeceras de plantillas) sí requieren almacenamiento externo (Vercel Blob, S3, etc.) porque el filesystem de las funciones no persiste entre requests; sin eso, esa funcionalidad puntual no sobrevive en producción.

`whatsapp-sidecar/` (el conector Baileys) es un proceso Node de larga duración con una conexión WebSocket persistente a WhatsApp — no corre en funciones serverless. Para producción con `MESSAGING_PROVIDER=baileys`, despliégalo aparte en un host con procesos persistentes (Railway, Fly.io, un VPS, etc.); para Vercel, `fake`, `twilio` o `meta` son los proveedores que funcionan tal cual.

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
