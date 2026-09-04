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

`seed_conversations` crea un usuario `asesor` (solo asignatario: sin contraseña, no puede iniciar sesión) y le asigna conversaciones de demo. `simulate_inbound` empuja un mensaje entrante por el **mismo** código del webhook (firma, parseo, idempotencia); con el Inbox abierto lo verás llegar solo en el siguiente poll.

## Agentes y usuarios

Un **agente** es a la vez un login y un asignatario: la misma identidad que
pasa la puerta de entrada es la que puede aparecer como responsable de una
conversación en el Inbox. Hay dos roles:

- **Maestro** — hace todo, incluido crear y gestionar usuarios.
- **Agente** — hace todo *excepto* gestionar usuarios.

Y dos orígenes de cuentas, ambas sobre el mismo `User` de Django
([core/agents.py](core/agents.py)):

### Cuentas del entorno (`APP_AGENTS`)

Las cuentas fundacionales: existen antes de que nadie haya entrado, así que el
equipo nunca puede quedar fuera por una base de datos a la que no llega.

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
Vercel. Con un solo usuario imprime solo su entrada `usuario:hash:Nombre`, y
sin ninguno solo el hash. Unir las entradas a mano es justo donde una coma de
más deja al equipo fuera de un despliegue en el que ya nadie puede entrar a
arreglarlo. Así, quien pueda leer el entorno — el panel
de Vercel, un log de CI, un `.env` compartido — encuentra un hash y no una
credencial que funcione. Se verifica con `check_password`, la misma función y
el mismo coste (PBKDF2) que la contraseña de una cuenta de la app.

Una contraseña en texto plano ahí **sigue funcionando**, para que ningún
despliegue anterior se quede fuera, pero está desaconsejada: `manage.py check`
avisa por cada agente que siga así (`core.W001`).

**Siempre son maestros**, siempre activos y de solo lectura dentro de la app:
su fuente de verdad es el entorno, así que cambiarles la contraseña es editar
la variable y volver a desplegar. Cada uno tiene un `User` espejo con
contraseña inutilizable — existe para que `assigned_to` y `sent_by` tengan a
quién apuntar, nunca para entrar por la base de datos, ni siquiera con el hash
del entorno.

Si `APP_AGENTS` no está definida se usa el par antiguo
`APP_LOGIN_USERNAME`/`APP_LOGIN_PASSWORD` como lista de un solo agente, así que
un entorno anterior a esto sigue funcionando sin tocar nada.

### Cuentas de la app (CRM › Mi cuenta › Equipo › Usuarios)

Un maestro crea el resto desde la app ([core/usuarios.py](core/usuarios.py)):
usuario, nombre, contraseña (mínimo 8 caracteres; se guarda con el hash PBKDF2
de Django, nunca en claro) y rol.
Sobre ellas puede renombrar, cambiar el rol, poner una contraseña nueva,
desactivar/reactivar y eliminar. La página solo aparece en la navegación de
los maestros, y sus endpoints responden 403 a cualquier otro.

Reglas que impiden que el equipo se deje fuera a sí mismo:

- Nadie puede quitarse a sí mismo el rol de maestro, desactivarse ni
  eliminarse.
- No se puede quitar el último maestro que pueda entrar, salvo que el entorno
  garantice uno (con cualquier `APP_AGENTS` configurado, siempre lo hay).
- Un usuario nuevo no puede llamarse como una cuenta del entorno ni como una
  existente (sin distinguir mayúsculas).

**Desactivar** deja al usuario fuera en su siguiente petición, lo saca del
desplegable de asignación y conserva su nombre en las conversaciones que
atendió; es la opción reversible. **Eliminar** borra la cuenta: sus
conversaciones, mensajes, etiquetas y eventos se conservan, pero dejan de
mostrar su nombre (todas las FK a `User` son `SET_NULL`). Cambiar la
contraseña de alguien cierra su sesión actual.

Si quitas un agente de `APP_AGENTS`, su `User` espejo queda como cuenta de la
app sin contraseña ("Sin contraseña" en la tabla): asígnale una para
adoptarlo como cuenta de la app, o elimínalo.

Las cuentas de staff de Django (`is_staff`, las de `createsuperuser` y
`/admin`) no son cuentas de la app: no entran por `/login/`, no aparecen en
Usuarios ni en el desplegable de asignación. Si una instalación sin
`APP_AGENTS` se queda sin ningún maestro que pueda entrar, la puerta de
recuperación es la consola:

```bash
python manage.py crear_master jefa --name "Jefa"
```

(pide la contraseña por teclado; `--password` existe para scripts).

En el Inbox, el desplegable junto al estado de la conversación ("Abierta")
cambia el agente asignado y guarda al instante; "Sin asignar" la devuelve a la
bandeja común.

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

## Deploy en Vercel

El proyecto usa el soporte nativo de Vercel para Django (detecta `manage.py` y el `WSGI_APPLICATION` de [config/settings.py](config/settings.py) automáticamente): conecta el repo en vercel.com o corre `vercel deploy` y no hace falta build script.

En el dashboard del proyecto (Settings → Environment Variables) define, como mínimo:

- `SECRET_KEY` — cualquier string largo y aleatorio (sin esto usa un valor de desarrollo inseguro).
- `DEBUG=False`
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
