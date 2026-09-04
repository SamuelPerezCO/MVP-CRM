#!/usr/bin/env python
"""Turn the workflow's result into the final PDF.

Reads chapters.json (the workflow return value), fills Anexo B with the
per-chapter verification record, orders chapters as planned (critic-added
chapters last), and renders. A partial run is reported explicitly -- on the
cover and in Anexo B -- rather than silently shipping a short document.
"""
import json
import subprocess
import sys

PLANNED = [
    ("shell", "Arquitectura y shell de la aplicación"),
    ("auth", "Autenticación y agentes"),
    ("inbox", "Inbox"),
    ("messaging", "Mensajería: modelo, servicios y proveedores"),
    ("crm", "CRM: clientes, listas y etiquetas"),
    ("calendario", "Mi calendario"),
    ("plantillas", "Mensajería: plantillas de WhatsApp"),
    ("estadisticas", "Estadísticas"),
    ("otros", "Embudos, Automatizaciones, Campañas y Mi comercio"),
    ("modelo", "Modelo de datos y migraciones"),
    ("despliegue", "Configuración, despliegue y operación"),
    ("pruebas", "Pruebas y convenciones de desarrollo"),
]
ORDER = [k for k, _ in PLANNED]
TITLES = dict(PLANNED)

with open("chapters.json", encoding="utf-8") as f:
    payload = json.load(f)
with open("front.json", encoding="utf-8") as f:
    front = json.load(f)

entries = [e for e in payload.get("chapters", []) if e and e.get("chapter")]
entries.sort(key=lambda e: ORDER.index(e["key"]) if e["key"] in ORDER else len(ORDER))
extra = [e for e in payload.get("extra", []) if e and e.get("chapter")]
ordered = entries + extra
if not ordered:
    raise SystemExit("No hay capítulos: nada que renderizar.")

present = {e["key"] for e in ordered}
dropped = [k for k in ORDER if k not in present]

rows = []
for e in ordered:
    v = e.get("verdict") or {}
    corr = v.get("corrections") or []
    miss = v.get("missing") or []
    claims = len((e.get("chapter") or {}).get("claims") or [])
    estado = "Sin discrepancias" if not corr and not miss else "Corregido"
    if not v:
        estado = "Sin verificar"
    rows.append([e["chapter"]["title"], str(claims), str(len(corr)), str(len(miss)), estado])

gaps = payload.get("gaps") or []
method_blocks = [
    {"kind": "paragraph", "text": "Cada capítulo pasó por tres etapas independientes: un lector redactó el capítulo a partir del código del snapshot; un verificador escéptico, con acceso al mismo código pero sin conocer al lector, contrastó cada afirmación concreta (rutas, nombres de URL, campos, variables de entorno, valores por defecto, comandos) e intentó refutarla; y, cuando encontró discrepancias u omisiones, un corrector reescribió el capítulo aplicándolas. La tabla resume el resultado."},
    {"kind": "table", "headers": ["Capítulo", "Afirmaciones verificadas", "Correcciones", "Omisiones cubiertas", "Resultado"], "rows": rows,
     "caption": "Registro de verificación por capítulo. \"Afirmaciones verificadas\" es el número de datos concretos que el capítulo expone al verificador."},
]
if extra:
    method_blocks.append({"kind": "paragraph", "text": "Tras los capítulos previstos, un crítico de completitud comparó los archivos del repositorio con lo cubierto y propuso los huecos que dieron lugar a: " + "; ".join(g.get("title", g.get("key", "")) for g in gaps) + ". Esos capítulos pasaron por las mismas tres etapas."})
elif gaps:
    method_blocks.append({"kind": "paragraph", "text": "El crítico de completitud propuso huecos que no llegaron a cubrirse en esta versión: " + "; ".join(g.get("title", g.get("key", "")) for g in gaps) + "."})
else:
    method_blocks.append({"kind": "paragraph", "text": "Tras los capítulos previstos, un crítico de completitud comparó los archivos del repositorio con lo cubierto y no propuso huecos adicionales."})
if dropped:
    method_blocks.append({"kind": "note", "text": "Esta versión del documento está incompleta. Los siguientes capítulos estaban previstos y no llegaron a generarse: " + "; ".join(TITLES.get(k, k) for k in dropped) + ". El resto del documento sí pasó por la verificación descrita."})

for ap in front["appendices"]:
    for sec in ap.get("sections", []):
        for i, b in enumerate(sec.get("blocks", [])):
            if b.get("text") == "__VERIFICATION__":
                sec["blocks"][i:i + 1] = method_blocks

if dropped:
    front["cover_lines"].append(
        "Versión parcial: faltan %d de %d capítulos previstos (ver Anexo B)." % (len(dropped), len(ORDER)))

with open("front.final.json", "w", encoding="utf-8") as f:
    json.dump(front, f, ensure_ascii=False, indent=1)
with open("chapters.final.json", "w", encoding="utf-8") as f:
    json.dump({"chapters": ordered, "extra": []}, f, ensure_ascii=False)

out = sys.argv[1] if len(sys.argv) > 1 else "MVP-CRM-documentacion-tecnica.pdf"
subprocess.run([sys.executable, "render.py", "front.final.json", "chapters.final.json", out], check=True)
print("chapters:", [e["key"] for e in ordered])
if dropped:
    print("FALTAN:", dropped)
