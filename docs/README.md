# Documentación técnica

`MVP-CRM-documentacion-tecnica.pdf` — 133 páginas, 16 capítulos, en español.
Describe la rama `main` en el commit **`6089391`**; el Anexo A del propio PDF
lista los commits posteriores que **no** cubre.

## Qué hay dentro

Capítulos 1–12: arquitectura y shell, autenticación y agentes, Inbox, la capa
de mensajería y sus proveedores, CRM, calendario, plantillas de WhatsApp,
estadísticas, las secciones menores, el modelo de datos y sus migraciones,
configuración y despliegue, y pruebas y convenciones.

Capítulos 13–16: guía para añadir una sección o panel, glosario del dominio,
el sistema visual (tokens y convenciones CSS) y los patrones htmx.

## Cómo se generó

Cada capítulo lo escribió un agente leyendo un snapshot de solo lectura del
código; un verificador independiente intentó refutar cada afirmación concreta
contra ese mismo código, y un corrector aplicó lo que encontró. El Anexo B del
PDF trae el registro por capítulo: 39 correcciones y 96 omisiones cubiertas
sobre 16 capítulos.

## Cómo regenerar el PDF

El contenido verificado vive en `generar/chapters.json`, así que volver a
componer el documento no requiere re-ejecutar los agentes:

```bash
cd docs/generar
uv run --with reportlab python assemble.py ../MVP-CRM-documentacion-tecnica.pdf
```

Para corregir una frase, un dato o una tabla, edita `generar/chapters.json` y
vuelve a ejecutar ese comando. La portada, la introducción y los anexos están
en `generar/front.json`; la maquetación (estilos, tablas, diagramas, índice y
marcadores del PDF) en `generar/render.py`.

Documentar un commit más reciente sí exige volver a lanzar el proceso de
lectura y verificación, y reemplazar `generar/chapters.json` con su resultado.
