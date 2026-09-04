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
APP_AGENTS=Admin:pbkdf2_sha256$1500000$SALT$HASH=:Admin
```

Entradas separadas por coma, cada una `usuario:hash:Nombre` (el nombre visible
es opcional y por defecto es el usuario). El campo del medio es un **hash**, no
una contraseña: genéralo con

```bash
python manage.py hashear_clave Admin Samuel
```

que pide cada contraseña por teclado (no quedan en el historial) e imprime la
línea `APP_AGENTS=...` completa, lista para pegar en el `.env` y en el panel de
Vercel. Con un solo usuario imprime solo su entrada `usuario:hash:Nombre`, y sin
ninguno solo el hash. Unir las entradas a mano es justo donde una coma de más
deja al equipo fuera de un despliegue en el que ya nadie puede entrar a
arreglarlo.

Así, quien pueda leer el entorno — el panel de Vercel, un log de CI, un `.env`
compartido — encuentra un hash y no una credencial que funcione. Se verifica con
`check_password`, la misma función y el mismo coste (PBKDF2) que la contraseña
de un usuario creado en la app.

Una contraseña en texto plano ahí **sigue funcionando**, para que ningún
despliegue anterior se quede fuera, pero está desaconsejada: `manage.py check`
avisa por cada agente que siga así (`core.W001`). Ni `:` ni `,` pueden aparecer
en el campo del medio, que son los separadores; los hashes PBKDF2 de Django no
llevan ninguno de los dos.

Al iniciar sesión se abre una sesión real de `django.contrib.auth` contra un
`User` espejo de ese agente ([core/agents.py](core/agents.py)), creado bajo
demanda y con contraseña inutilizable: existe para que `assigned_to` y
`sent_by` tengan a quién apuntar, nunca para autenticar — el entorno sigue
siendo la única vía de entrada, ni siquiera con el hash que guarda. Eso es lo que hace que el filtro "Tu inbox"
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
mensajes sigue apuntando a ellos, y al desactivar se cierran sus sesiones, así
que restaurarlo después no revive el navegador de nadie.

Nadie puede quitarse a sí mismo el rol de maestro ni desactivarse, y el
servicio ([core/agents.py](core/agents.py)) rechaza además dejar al equipo sin
ningún maestro que pueda entrar: cuentan los agentes de `APP_AGENTS` (siempre
maestros), los superusuarios y los maestros de la app que sigan activos y con
contraseña utilizable — un espejo sin contraseña, el que queda al sacar a
alguien de `APP_AGENTS`, no sirve de reemplazo. Los agentes del entorno se muestran en la
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
MESSAGING_PROVIDER=baileys # WhatsApp real ya, sin esperar a Meta (ver abajo)
MESSAGING_PROVIDER=fake    # solo desarrollo local: simula envíos y recibos
```

La variable es **obligatoria**: sin ella la app no arranca. Antes `fake` era
el valor por defecto, y un despliegue al que se le olvidara la variable
corría feliz sobre el simulador -- palomitas moviéndose en pantalla, nada
llegando a un teléfono. Producción es clientes reales; no debe poder caer en
el simulador por accidente.

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
usar para el CRM. La sesión queda guardada en el directorio `auth/` del sidecar,
así que no hay que volver a escanear en cada reinicio.

Luego, en el `.env` de Django:

```
MESSAGING_PROVIDER=baileys
BAILEYS_SIDECAR_URL=http://localhost:4000
BAILEYS_SIDECAR_SECRET=   # genera un valor largo y aleatorio; debe coincidir con el .env del sidecar
```

`BAILEYS_SIDECAR_SECRET` no tiene valor por defecto y mientras esté vacío el
webhook rechaza todo. Genera uno propio (`openssl rand -hex 32`) en vez de
copiar un ejemplo: el slug `/webhooks/messaging/baileys/` responde en todos
los despliegues, así que ese secreto es lo único que impide que cualquiera
escriba mensajes en la base de datos.

Arranca Django normalmente (`python manage.py runserver`) -- los mensajes que
lleguen al número vinculado aparecen en el Inbox, y las respuestas enviadas
desde el Inbox salen por WhatsApp real a través del sidecar. Ver
[whatsapp-sidecar/README.md](whatsapp-sidecar/README.md) para más detalle.

Cuando lleguen credenciales reales de Twilio o Meta:

1. Copia [.env.example](.env.example) a `.env` (está en `.gitignore`) y llena las credenciales del proveedor; expórtalas al entorno antes de `runserver` — los settings leen `os.environ` directamente.
2. Implementa los métodos de [messaging/providers/twilio.py](messaging/providers/twilio.py) o [messaging/providers/meta.py](messaging/providers/meta.py) — el docstring de cada módulo describe exactamente qué endpoint, firma y formato de webhook usa cada uno. Nada fuera de ese archivo cambia: ni vistas, ni modelos, ni templates.
3. Cambia `MESSAGING_PROVIDER` y registra la URL del webhook en la consola del proveedor: `https://tu-dominio/webhooks/messaging/twilio/` o `.../meta/` (Meta verifica primero con un GET; el endpoint ya responde el `hub.challenge`).

El webhook verifica la firma antes de tocar el payload (401 si es inválida), es idempotente por `provider_message_id` (los reintentos del proveedor no duplican mensajes) y siempre responde 200 tras autenticar, registrando errores en el log en lugar de provocar tormentas de reintentos. El envío de texto libre está bloqueado fuera de la ventana de 24 horas ([messaging/services.py](messaging/services.py)) — fuera de ella solo cabe `send_template`, igual que en la plataforma real.

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

El conector Baileys ([repo aparte](https://github.com/SamuelPerezCO/whatsapp-sidecar), ya no vive en este árbol) es un proceso Node de larga duración con una conexión WebSocket persistente a WhatsApp — no corre en funciones serverless. Para producción con `MESSAGING_PROVIDER=baileys`, despliégalo en un host con procesos persistentes (Railway, Fly.io, un VPS, etc.). En Vercel los que funcionan tal cual son `twilio` y `meta`; `fake` no es una opción de producción — simula los envíos y no manda nada a ningún teléfono.

### Seguridad del webhook

La URL del webhook nombra al proveedor (`/webhooks/messaging/<proveedor>/`) para que, durante una migración, un callback de Twilio se siga interpretando como Twilio aunque el proveedor activo ya sea Meta. Dos consecuencias que conviene tener presentes:

- El endpoint del proveedor `fake` **solo responde donde `MESSAGING_PROVIDER=fake`**. En un despliegue real devuelve 404: sin ese candado sería una forma anónima de escribir clientes inventados en la base de datos de producción, indistinguibles después de los reales.
- Cada proveedor real *sí* sigue siendo alcanzable siempre, así que su secreto es lo único que lo protege. Ninguno tiene valor por defecto: `META_APP_SECRET`, `BAILEYS_SIDECAR_SECRET` y `MESSAGING_FAKE_SECRET` rechazan todo mientras estén vacíos. Un secreto escrito en el repositorio no protege nada.

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
