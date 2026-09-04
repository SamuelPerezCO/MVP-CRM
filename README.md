# MVP-CRM

MVP de un CRM omnicanal para comercios, inspirado en plataformas tipo Treble/Leadsales: una bandeja de entrada unificada para los canales de mensajería (WhatsApp, Messenger, Instagram, Facebook, TikTok), gestión de clientes y embudos de venta, todo dentro de un shell de una sola página con barra lateral de iconos.

## Funcionalidades

- **Inbox** — conversaciones reales filtradas por canal y asignación, con lista, chat en vivo (polling htmx), compositor con la regla de 24 horas de WhatsApp y panel de detalles del cliente.
- **CRM** — tabla de clientes (nombre, teléfono con bandera de país, mail, canal) y listas de clientes.
- **Embudos** — panel de embudos de venta con creación de nuevos embudos.
- **Automatizaciones** — flujos de chatbots y banner de Academy.
- **Mi comercio** — catálogo de productos con creación e importación.
- **Campañas, Estadísticas y Mensajería** — métricas de mensajería y plantillas de WhatsApp.
- **Envío a clientes nuevos** — escribir primero con una plantilla desde la fila del cliente o desde el Inbox, con el precio del envío a la vista y el gasto del mes registrado.

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

## Agentes (personas)

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
bandeja común. Todos los agentes tienen las mismas capacidades por ahora — no
hay roles.

Si `APP_AGENTS` no está definida se usa el par antiguo
`APP_LOGIN_USERNAME`/`APP_LOGIN_PASSWORD` como lista de un solo agente, así que
un entorno anterior a esto sigue funcionando sin tocar nada.

## Mensajería: cambiar de proveedor

Toda la integración con WhatsApp vive en [messaging/](messaging/) detrás de una abstracción de proveedor ([messaging/providers/base.py](messaging/providers/base.py)). El proveedor activo lo decide **una sola variable**:

```
MESSAGING_PROVIDER=fake    # hoy
MESSAGING_PROVIDER=twilio  # cuando haya credenciales de Twilio
MESSAGING_PROVIDER=meta    # cuando Meta desbloquee la cuenta
```

Cuando lleguen credenciales reales de Twilio o Meta:

1. Copia [.env.example](.env.example) a `.env` (está en `.gitignore`) y llena las credenciales del proveedor; expórtalas al entorno antes de `runserver` — los settings leen `os.environ` directamente.
2. Implementa los métodos de [messaging/providers/twilio.py](messaging/providers/twilio.py) o [messaging/providers/meta.py](messaging/providers/meta.py) — el docstring de cada módulo describe exactamente qué endpoint, firma y formato de webhook usa cada uno. Nada fuera de ese archivo cambia: ni vistas, ni modelos, ni templates.
3. Cambia `MESSAGING_PROVIDER` y registra la URL del webhook en la consola del proveedor: `https://tu-dominio/webhooks/messaging/twilio/` o `.../meta/` (Meta verifica primero con un GET; el endpoint ya responde el `hub.challenge`).

El webhook verifica la firma antes de tocar el payload (401 si es inválida), es idempotente por `provider_message_id` (los reintentos del proveedor no duplican mensajes) y siempre responde 200 tras autenticar, registrando errores en el log en lugar de provocar tormentas de reintentos. El envío de texto libre está bloqueado fuera de la ventana de 24 horas ([messaging/services.py](messaging/services.py)) — fuera de ella solo cabe `send_template`, igual que en la plataforma real.

## Escribir primero a un cliente nuevo (y lo que cuesta)

Un cliente nuevo nunca ha escrito, así que su ventana de 24 horas jamás
estuvo abierta: WhatsApp solo entrega **plantillas** — las mismas que se
crean en Configuración de mensajería › Plantillas de WhatsApp — y **cobra
cada una**. Ese envío tiene dos entradas, las dos abren el mismo diálogo:

- **CRM › Clientes** — el icono de mensaje en la fila del cliente. Aparece
  para cualquiera con teléfono cuyo canal sea WhatsApp o esté en blanco (el
  cliente que alguien acaba de escribir a mano), no para los que llegaron por
  Instagram o Messenger: las plantillas son un mecanismo de WhatsApp.
- **Inbox** — cuando la ventana está cerrada, el compositor se cambia por el
  botón «Enviar plantilla» de esa misma conversación.

El diálogo elige plantilla, rellena sus variables `{{n}}` (lo que se deje en
blanco sale con el valor de ejemplo), muestra la vista previa y **el precio
del envío en el propio botón**, junto al total gastado en el mes. Al enviar,
si el cliente no tenía conversación, se le crea una — el mensaje y su
eventual respuesta quedan en el mismo hilo del Inbox.

El precio se congela en la fila del mensaje (`billed_amount`,
`billed_category`, `billed_currency`), así que un cambio de tarifas nunca
reescribe lo que costó el pasado, y el hilo etiqueta cada envío con su
plantilla y su importe. Un envío fallido no cobra nada.

### Tarifas: las de Meta, ya incluidas

El precio sale de la tarifa **publicada por Meta**, no de una lista inventada:
[messaging/meta_rates.py](messaging/meta_rates.py) trae las tablas que Meta
descarga desde su documentación de precios — la vigente (1 de julio de 2026) y
la ya anunciada (1 de octubre de 2026), 38 y 47 mercados. `card_for()` elige
por fecha, así que el CRM cambia de tarifa solo el día que Meta la aplica.

Meta cobra **por mensaje entregado**, según la **categoría** de la plantilla y
el **mercado** del destinatario. Un mercado es un país con tarifa propia
(Colombia: 0,0125 USD marketing) o un grupo regional: Ecuador, Panamá,
Uruguay, República Dominicana y una docena más pagan «Rest of Latin America»
(0,0740). El mercado se resuelve por el código telefónico, con dos trampas ya
resueltas en [messaging/pricing.py](messaging/pricing.py):

- **Gana el prefijo más largo.** +507 (Panamá) empieza por +50 y +51 es Perú.
- **+1 no es un solo mercado.** República Dominicana, Jamaica y Puerto Rico
  comparten +1 con Estados Unidos y Canadá pero pagan «Rest of Latin America»:
  0,0740 contra 0,0250. Se resuelven por el código de área NANP.

Una plantilla *utility* enviada con la ventana de 24 horas abierta se factura
como mensaje de **servicio**, y eso cambia el **1 de octubre de 2026**: en la
tarifa vigente la columna Service es «n/a» en los 38 mercados (no se cobra),
y en la de octubre Meta le pone precio en los 47 — exactamente la tarifa
utility de cada mercado. El CRM lo lee de la tarjeta, no de una fecha escrita
a mano, así que la cotización sigue siendo correcta a ambos lados del cambio
sin tocar código.

Lo que todavía **no** modela, y por eso la cotización puede quedar por encima
de la factura pero nunca por debajo: los descuentos por volumen (utility y
authentication, según el volumen mensual de todo el portafolio) y la ventana
de free entry point (72 horas en las que *todo* es gratis, tras responder a
un anuncio Click-to-WhatsApp).

Toda cotización es una **estimación** hasta la entrega: Meta cobra al
entregar y con la categoría que *ella* le asignó a la plantilla.

### Traer de Meta el estado y la categoría de las plantillas

```bash
python manage.py sync_templates
```

Trae de `GET /{WABA_ID}/message_templates` dos cosas que solo Meta sabe, y las
dos cuestan dinero si se ignoran:

- **Cuáles se pueden enviar de verdad.** Solo una plantilla `APPROVED` se
  entrega; una `PAUSED` o `DISABLED` la rechaza la API. Una vez sincronizada,
  el diálogo de envío ya no ofrece las que WhatsApp rechazaría. Una plantilla
  que Meta nunca ha visto (sin cuenta de Meta, o creada aquí y no enviada a
  revisión) mantiene la regla laxa del MVP.
- **La categoría que Meta le asignó.** Meta recategoriza plantillas por su
  cuenta — una *utility* que juzga promocional pasa a *marketing* — y cobra
  con **su** categoría. El comando avisa en pantalla de cada recategorización,
  porque cambia el precio de todos los envíos futuros de esa plantilla.

Una plantilla que existe en Meta pero no en el CRM (creada en WhatsApp
Manager) se importa; una que solo existe aquí se deja intacta y se reporta,
porque puede ser un borrador todavía sin enviar a revisión. Requiere
`MESSAGING_PROVIDER=meta`, `META_WABA_ID` y un token con el permiso
`whatsapp_business_management`.

### Contrastar con la contabilidad de Meta

```bash
python manage.py meta_spend                # el mes en curso
python manage.py meta_spend --month 2026-08
```

El CRM lleva su propio libro: un precio congelado en cada envío y corregido
por el acuse de entrega. Eso es por mensaje, y solo ve los mensajes cuyo acuse
llegó. Este barrido le pregunta a Meta (`pricing_analytics` sobre la WABA) qué
cobró en toda una ventana y muestra la diferencia por categoría — así se nota
un webhook perdido, un envío hecho fuera de este CRM o una tarifa mal puesta.

```
  categoría                             Meta           CRM          dif.
  marketing                           0.0375        0.0125        0.0250
  utility                             0.0008        0.0022       -0.0014
```

Dos salvedades que el propio comando imprime: Meta describe estas cifras como
**aproximadas** (manda la factura), y a una cuenta facturada a través de un
socio (BSP) Meta le **oculta el costo** — entonces el comando dice eso, con el
volumen entregado, en vez de reportar cero.

### La verdad final: lo que Meta dice que cobró

Con `MESSAGING_PROVIDER=meta`, cada acuse de entrega trae un objeto `pricing`
que dice en qué **bucket de tarifa** cayó el mensaje (nunca el importe: ahí no
viaja dinero). El CRM lo guarda en el mensaje (`meta_pricing_type`,
`meta_pricing_category`, `meta_pricing_model`, `meta_billable`) y **corrige la
estimación** en [messaging/services.py](messaging/services.py):

- Si Meta lo marca gratis (`free_customer_service` o `free_entry_point`), el
  importe baja a cero — así se recupera lo que el CRM no podía saber, como la
  ventana de 72 horas que abre un anuncio Click-to-WhatsApp.
- Si Meta lo cobró en **otra categoría** (recategoriza plantillas por su
  cuenta), se vuelve a tarifar con la categoría de Meta y con la tarjeta que
  estaba vigente el día del envío, no la de hoy.
- Una categoría que este CRM no sabe tarifar (`marketing_lite`,
  `referral_conversion`) se registra pero no se re-tarifa.

`Message.cost_is_confirmed` distingue un importe confirmado por Meta de uno
que sigue siendo la estimación del CRM (todo lo enviado por el proveedor
`fake`, que no reporta facturación).

Limitación conocida y anotada en el código: de los números +1, Meta solo
publica el desvío de República Dominicana, Jamaica y Puerto Rico, así que el
resto de territorios NANP del Caribe se cotizan como Norteamérica y quedan por
debajo. Cerrarlo pide una librería de teléfonos (libphonenumber).

```
MESSAGING_TEMPLATE_RATES=...   # opcional: superpone tus tarifas sobre las de Meta
MESSAGING_CURRENCY=USD
MESSAGING_MONTHLY_BUDGET=0     # 0 = sin tope
```

`MESSAGING_TEMPLATE_RATES` solo hace falta si tu cuenta paga otras tarifas
(contrato con un BSP, precio promocional) o factura en otra moneda; es un JSON
con los nombres de mercado de Meta y se superpone fila por fila.

`MESSAGING_MONTHLY_BUDGET` es un techo por mes calendario: el envío que lo
cruzaría se rechaza en [messaging/services.py](messaging/services.py), antes
de llamar al proveedor, así que ni un bulk ni un POST a mano lo esquivan.

## Deploy en Vercel

El proyecto usa el soporte nativo de Vercel para Django (detecta `manage.py` y el `WSGI_APPLICATION` de [config/settings.py](config/settings.py) automáticamente): conecta el repo en vercel.com o corre `vercel deploy` y no hace falta build script.

En el dashboard del proyecto (Settings → Environment Variables) define, como mínimo:

- `SECRET_KEY` — cualquier string largo y aleatorio (sin esto usa un valor de desarrollo inseguro).
- `DEBUG=False`
- `DATABASE_URL` — Postgres (por ejemplo Vercel Postgres o Neon, desde la pestaña Storage). SQLite no sirve en producción porque las funciones serverless no tienen disco persistente.
- `ALLOWED_HOSTS` — opcional; el dominio del deploy y el alias de producción se confían automáticamente vía `VERCEL_URL` y `VERCEL_PROJECT_PRODUCTION_URL`, agrega aquí solo dominios propios (custom domains).

Con `DATABASE_URL` configurado, corre las migraciones contra la base de producción (por ejemplo con `vercel env pull` + `python manage.py migrate` localmente, o desde una shell con las mismas variables).

Los archivos estáticos (`static/`) se recolectan y sirven automáticamente desde el CDN de Vercel — no requiere WhiteNoise ni configuración adicional. Los uploads de usuario (`media/`, ej. cabeceras de plantillas) sí requieren almacenamiento externo (Vercel Blob, S3, etc.) porque el filesystem de las funciones no persiste entre requests; sin eso, esa funcionalidad puntual no sobrevive en producción.

## Tests

```powershell
python manage.py test
```

Cada sección tiene su propio archivo de tests en [core/](core/) (`tests.py`, `tests_crm.py`, `tests_embudos.py`, etc.); la capa de mensajería (idempotencia del webhook, rechazo de firmas, ventana de 24h) se prueba en [messaging/tests.py](messaging/tests.py), y el envío de plantillas a clientes nuevos en [messaging/tests_pricing.py](messaging/tests_pricing.py) (tarifas y gasto), [messaging/tests_envio_plantillas.py](messaging/tests_envio_plantillas.py) (el envío en sí) y [core/tests_plantilla_envio.py](core/tests_plantilla_envio.py) (el diálogo).

## Estructura

| Ruta | Qué contiene |
|---|---|
| [config/](config/) | Settings y URLs del proyecto |
| [core/](core/) | Vistas, modelos, navegación y datos de cada sección |
| [messaging/](messaging/) | Conversaciones, mensajes, webhook y proveedores (fake/twilio/meta) |
| [templates/sections/](templates/sections/) | Pantalla completa de cada sección |
| [templates/partials/](templates/partials/) | Fragmentos que htmx intercambia |
| [static/](static/) | CSS por sección y `shell.js` |
