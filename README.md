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
copy .env.example .env       # en Linux/macOS: cp .env.example .env
python manage.py migrate
python manage.py runserver
```

El paso del `.env` no es opcional: `MESSAGING_PROVIDER` es obligatorio y la
app no arranca sin él (ver [Mensajería](#mensajería-cambiar-de-proveedor)).
Para desarrollo local el `.env.example` ya trae `MESSAGING_PROVIDER=fake`.

Abre http://127.0.0.1:8000/ — la pantalla de bienvenida enlaza a Inbox, CRM y Embudos.

El Inbox no trae datos de ejemplo: se llena únicamente con clientes reales,
a medida que escriben por el proveedor configurado o los deja la automatización
que escribe en la misma base de datos. Ya no existe un generador de datos de
demostración; si una base heredó fixtures del antiguo `seed_conversations`
(contactos `+5730000000xx`, eventos "Evento de demostración.", el login
`asesor`), límpialos sin tocar a los clientes reales con:

```powershell
python manage.py reset_conversations --demo-only        # simulación: muestra qué borraría
python manage.py reset_conversations --demo-only --yes  # borra solo eso
```

`reset_conversations` a secas (con `--yes`) vacía el Inbox entero -- todas
las conversaciones, mensajes y contactos -- y es un simulacro hasta que se
pasa `--yes`, porque corre contra la base de producción y no hay deshacer.

Los tres comandos corren contra la base que diga `DATABASE_URL`, así que
todos nombran la base antes de tocarla y ninguno borra nada sin `--yes`. Para
apuntar a la base local en una máquina cuyo `.env` mira a Neon:

```bash
DATABASE_URL= python manage.py reset_conversations
```

## Salir a producción: dejar el CRM vacío

Antes de conectar el número real de WhatsApp, `go_live` vacía la aplicación y **conserva al equipo**: borra contactos, conversaciones, mensajes, etiquetas, eventos de calendario, listas, productos, plantillas, respuestas rápidas y las cuentas de prueba (las que solo existen como asignatario, p. ej. `asesor`), y deja intactas las cuentas que pueden iniciar sesión — las creadas en CRM > Equipo > Usuarios, las de `APP_AGENTS` y cualquier superusuario.

```bash
python manage.py go_live          # simulación: dice qué borraría y no toca nada
python manage.py go_live --yes    # lo borra de verdad
```

Igual que `reset_conversations`: es simulación por defecto, nombra la base a la que apunta antes de tocarla y borra dentro de una sola transacción. `--keep-catalog` conserva productos, plantillas y respuestas rápidas (útil si las plantillas de WhatsApp ya están aprobadas por Meta). No borra los archivos ya subidos a Vercel Blob, solo las filas que apuntaban a ellos.

Cuál de los tres usar:

| Comando | Qué borra |
|---|---|
| `reset_conversations --demo-only` | solo los fixtures del antiguo generador (`+5730000000xx`, "Evento de demostración.", el login `asesor`) — el único seguro si ya hay clientes reales |
| `reset_conversations` | conversaciones, mensajes y contactos; deja etiquetas, plantillas, calendario y equipo |
| `go_live` | todo lo anterior más etiquetas, calendario, listas, catálogo y cuentas de prueba; deja solo al equipo |

Crea tu cuenta en **CRM > Equipo > Usuarios** *antes* de correrlo con `--yes`: si ninguna cuenta sobrevive, la simulación te avisa.

`reset_conversations` sigue existiendo para lo de siempre — vaciar solo el Inbox (conversaciones, mensajes y contactos) sin tocar etiquetas, plantillas ni calendario.

## Agentes (personas) y la pantalla Equipo

Un **agente** es a la vez un login y un asignatario: la misma identidad que
pasa la puerta de entrada es la que puede aparecer como responsable de una
conversación en el Inbox. La lista vive en el entorno, no en la base de datos
— agregar un compañero es editar una variable y volver a desplegar, sin
pantalla de gestión de usuarios ni registro:

```
APP_AGENTS=Admin:pbkdf2_sha256$1500000$SALT$HASH=:Admin,Samuel:pbkdf2_sha256$1500000$SALT$HASH=:Samuel
```

Entradas separadas por coma, cada una `usuario:hash:Nombre` (el nombre visible
es opcional y por defecto es el usuario). El campo del medio es un **hash**, no
la contraseña en claro:

```
python manage.py hashear_clave Samuel
```

pide la contraseña por terminal (no queda en el historial del shell) e imprime
la entrada lista para pegar. Una contraseña en claro ahí sigue funcionando —
un redespliegue nunca puede dejar al equipo fuera — pero `manage.py check`
avisa de cada agente que siga así (`core.W001`): quien pueda leer el entorno
(el panel de Vercel, un log de CI, un `.env` compartido) tiene un login válido.
Ni el hash ni la contraseña pueden llevar `:` ni `,`, que son los separadores.

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
flag significa "puede entrar a /admin/", que es otra pregunta — el login
`asesor` que dejó el antiguo generador lo tiene y no por eso administra el
equipo.

Si `APP_AGENTS` no está definida se usa el par antiguo
`APP_LOGIN_USERNAME`/`APP_LOGIN_PASSWORD` como lista de un solo agente, así que
un entorno anterior a esto sigue funcionando sin tocar nada.

## Mensajería: cambiar de proveedor

Toda la integración con WhatsApp vive en [messaging/](messaging/) detrás de una abstracción de proveedor ([messaging/providers/base.py](messaging/providers/base.py)). El proveedor activo lo decide **una sola variable**:

```
MESSAGING_PROVIDER=twilio  # cuando haya credenciales de Twilio
MESSAGING_PROVIDER=meta    # cuando Meta desbloquee la cuenta
MESSAGING_PROVIDER=fake    # solo desarrollo local: simula envíos y recibos
```

La variable es **obligatoria**: sin ella la app no arranca. Antes `fake` era
el valor por defecto, y un despliegue al que se le olvidara la variable
corría feliz sobre el simulador -- palomitas moviéndose en pantalla, nada
llegando a un teléfono. Producción es clientes reales; no debe poder caer en
el simulador por accidente.

El webhook del proveedor `fake` (`/webhooks/messaging/fake/`) crea contactos y conversaciones y su única llave es `MESSAGING_FAKE_SECRET`, cuyo valor por defecto está publicado en este repositorio. Por eso solo responde donde los datos falsos tienen sentido: con `DEBUG=True` o bajo `manage.py test`. En un despliegue real devuelve 404, así que nadie puede meter clientes inventados en el Inbox ([messaging/providers/registry.py](messaging/providers/registry.py)). Los webhooks de Meta y Twilio no cambian.

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
- `MESSAGING_PROVIDER` — **obligatorio**, y en producción nunca `fake`: `twilio` o `meta`, con las credenciales del proveedor elegido. Sin esta variable el despliegue falla al arrancar, a propósito.
- `DATABASE_URL` — Postgres (por ejemplo Vercel Postgres o Neon, desde la pestaña Storage). SQLite no sirve en producción porque las funciones serverless no tienen disco persistente.
- `ALLOWED_HOSTS` — opcional; el dominio del deploy y el alias de producción se confían automáticamente vía `VERCEL_URL` y `VERCEL_PROJECT_PRODUCTION_URL`, agrega aquí solo dominios propios (custom domains).

Con `DATABASE_URL` configurado, corre las migraciones contra la base de producción (por ejemplo con `vercel env pull` + `python manage.py migrate` localmente, o desde una shell con las mismas variables).

Los archivos estáticos (`static/`) se recolectan y sirven automáticamente desde el CDN de Vercel — no requiere WhiteNoise ni configuración adicional. Los uploads de usuario (`media/`, ej. cabeceras de plantillas) sí requieren almacenamiento externo (Vercel Blob, S3, etc.) porque el filesystem de las funciones no persiste entre requests; sin eso, esa funcionalidad puntual no sobrevive en producción.

En Vercel los proveedores que funcionan tal cual son `twilio` y `meta`; `fake` no es una opción de producción — simula los envíos y no manda nada a ningún teléfono.

### Seguridad del webhook

La URL del webhook nombra al proveedor (`/webhooks/messaging/<proveedor>/`) para que, durante una migración, un callback de Twilio se siga interpretando como Twilio aunque el proveedor activo ya sea Meta. Dos consecuencias que conviene tener presentes:

- El endpoint del proveedor `fake` **solo responde donde `MESSAGING_PROVIDER=fake`**. En un despliegue real devuelve 404: sin ese candado sería una forma anónima de escribir clientes inventados en la base de datos de producción, indistinguibles después de los reales.
- Cada proveedor real *sí* sigue siendo alcanzable siempre, así que su secreto es lo único que lo protege. Ninguno tiene valor por defecto: `META_APP_SECRET` y `MESSAGING_FAKE_SECRET` rechazan todo mientras estén vacíos. Un secreto escrito en el repositorio no protege nada.

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
