# Documentación técnica

`MVP-CRM-documentacion-tecnica.pdf` — en español, dos partes, autor Samuel Pérez Serna.

**Parte I, documento del proyecto**: identificación del problema, pregunta de
investigación, alcance, objetivo general y específicos, árbol de problemas,
hipótesis de solución, requisitos funcionales y no funcionales, modelo de
datos, mapa conceptual, interfaz (capturas reales de la aplicación) y
bibliografía.

**Parte II, documentación técnica del código**: 16 capítulos que describen la
rama `main` en el commit **`6089391`**; el Anexo A del propio PDF lista los
commits posteriores que **no** cubre.

Capítulos 1–12: arquitectura y shell, autenticación y agentes, Inbox, la capa
de mensajería y sus proveedores, CRM, calendario, plantillas de WhatsApp,
estadísticas, las secciones menores, el modelo de datos y sus migraciones,
configuración y despliegue, y pruebas y convenciones.

Capítulos 13–16: guía para añadir una sección o panel, glosario del dominio,
el sistema visual (tokens y convenciones CSS) y los patrones htmx.

## Cómo se generó la segunda parte

Cada capítulo se redactó leyendo un snapshot de solo lectura del código y
después se verificó afirmación por afirmación contra ese mismo código,
corrigiendo lo que no coincidía. El Anexo B del PDF trae el registro por
capítulo: 39 correcciones y 96 omisiones cubiertas sobre 16 capítulos.

## Cómo regenerar el PDF

Todo el contenido vive en `generar/`, así que recomponer el documento es un
solo comando:

```bash
cd docs/generar
uv run --with reportlab python assemble.py ../MVP-CRM-documentacion-tecnica.pdf
```

- `generar/academico.json`: la Parte I (texto, tablas de requisitos, los
  diagramas descritos como filas de cajas o entidades con relaciones, y las
  capturas). Para corregir una frase o un requisito, edita este archivo.
- `generar/chapters.json`: la Parte II verificada. Para corregir un dato o una
  tabla de un capítulo, edita aquí.
- `generar/front.json`: portada (objetivo, autor, fecha), introducción y
  anexos.
- `generar/render.py`: la maquetación (estilos, tablas, diagramas dibujados,
  imágenes, índice y marcadores del PDF).
- `generar/capturas/`: las capturas de pantalla de la sección Interfaz,
  tomadas a 1440×900 con datos de ejemplo. Para renovarlas, arranca la app
  con `MESSAGING_PROVIDER=fake`, crea algunos datos y vuelve a capturar cada
  pantalla con el mismo tamaño.

Documentar un commit más reciente en la Parte II sí exige volver a lanzar el
proceso de lectura y verificación, y reemplazar `generar/chapters.json` con su
resultado.
