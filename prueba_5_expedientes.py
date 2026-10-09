# -*- coding: utf-8 -*-
"""
Sistema de Seguimiento Legislativo - HCD 25 de Mayo
Módulo 1 (PRUEBA): consulta 5 expedientes reales de 2026 y genera CSV
con los datos tal como figuran en el sitio oficial, más sus enlaces.

Reglas de esta prueba:
- Primero comprueba que el sitio responde y que su robots.txt permite el acceso.
- Consulta despacio (una página cada pocos segundos) para no sobrecargar el sitio.
- No inventa datos: lo que no figura queda como "No informado".
- Registra todos los errores en datos/registro_errores.csv.
- Guarda una copia de cada página consultada (datos/html_originales/) para poder verificar.
"""

import csv
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
SITIO = "https://www.hcd25demayo.gob.ar"
URL_FICHA = SITIO + "/component/sobi2/?sobi2Task=sobi2Details&sobi2Id={id}"

# Cinco expedientes de 2026 verificados a mano el 9/10/2026 (número interno de la ficha)
IDS_PRUEBA = [2854, 2855, 2861, 2867, 2868]

PAUSA_SEGUNDOS = 4          # espera entre una consulta y la siguiente
TIEMPO_MAXIMO = 30          # segundos máximos de espera por página
REINTENTOS = 2              # reintentos ante fallas transitorias
AGENTE = ("SeguimientoLegislativoHCD25/0.1 "
          "(uso civico, consulta lenta; repositorio en github.com)")

CARPETA = "datos"
CARPETA_HTML = os.path.join(CARPETA, "html_originales")
NO_INFORMADO = "No informado"
PENDIENTE = "Pendiente de verificar"

HORA_ARG = timezone(timedelta(hours=-3))


def ahora():
    return datetime.now(HORA_ARG).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Registro de errores
# ---------------------------------------------------------------------------
errores = []


def registrar_error(sobi2_id, url, etapa, detalle):
    errores.append({
        "fecha_consulta": ahora(),
        "id_ficha": sobi2_id,
        "url": url,
        "etapa": etapa,
        "detalle": detalle,
    })
    print(f"  [ERROR] {etapa}: {detalle}")


# ---------------------------------------------------------------------------
# Descarga respetuosa
# ---------------------------------------------------------------------------
sesion = requests.Session()
sesion.headers.update({"User-Agent": AGENTE, "Accept-Language": "es-AR,es;q=0.9"})


def descargar(url, sobi2_id=""):
    """Descarga una página con pausas y pocos reintentos. Devuelve la respuesta o None."""
    for intento in range(1, REINTENTOS + 2):
        try:
            r = sesion.get(url, timeout=TIEMPO_MAXIMO)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 503):
                # el sitio pide que bajemos el ritmo: esperamos más
                espera = PAUSA_SEGUNDOS * 5 * intento
                print(f"  El sitio pidió esperar (código {r.status_code}). Esperando {espera} s...")
                time.sleep(espera)
                continue
            registrar_error(sobi2_id, url, "descarga", f"Código HTTP {r.status_code}")
            return None
        except requests.RequestException as e:
            if intento <= REINTENTOS:
                time.sleep(PAUSA_SEGUNDOS * 2 * intento)
                continue
            registrar_error(sobi2_id, url, "descarga", f"{type(e).__name__}: {e}")
            return None
    registrar_error(sobi2_id, url, "descarga", "Se agotaron los reintentos")
    return None


# ---------------------------------------------------------------------------
# Paso 0: comprobación de acceso
# ---------------------------------------------------------------------------
def comprobar_acceso():
    global PAUSA_SEGUNDOS
    informe =[f"Comprobación de acceso - {ahora()}", f"Sitio: {SITIO}", ""]
    ok = True

    r = descargar(SITIO + "/")
    if r is None:
        informe.append("Portada: NO se pudo acceder.")
        ok = False
    else:
        informe.append(f"Portada: accesible (código {r.status_code}, "
                       f"{len(r.content)} bytes, {r.elapsed.total_seconds():.1f} s).")
    time.sleep(PAUSA_SEGUNDOS)

    rp = robotparser.RobotFileParser()
    try:
        rr = sesion.get(SITIO + "/robots.txt", timeout=TIEMPO_MAXIMO)
        if rr.status_code == 200:
            rp.parse(rr.text.splitlines())
            permitido = rp.can_fetch(AGENTE, URL_FICHA.format(id=IDS_PRUEBA[0]))
            demora = rp.crawl_delay(AGENTE)
            informe.append(f"robots.txt: encontrado. Fichas de expedientes permitidas: "
                           f"{'SÍ' if permitido else 'NO'}.")
            if demora:
                informe.append(f"robots.txt pide esperar {demora} s entre consultas.")
                PAUSA_SEGUNDOS = max(PAUSA_SEGUNDOS, float(demora))
            if not permitido:
                ok = False
        else:
            informe.append(f"robots.txt: no publicado (código {rr.status_code}). "
                           f"Se aplican igualmente pausas de {PAUSA_SEGUNDOS} s.")
    except requests.RequestException as e:
        informe.append(f"robots.txt: no se pudo leer ({type(e).__name__}). "
                       f"Se aplican igualmente pausas de {PAUSA_SEGUNDOS} s.")
    time.sleep(PAUSA_SEGUNDOS)

    informe.append("")
    informe.append("RESULTADO: " + ("ACCESO CORRECTO" if ok else "ACCESO NO DISPONIBLE"))
    return ok, informe


# ---------------------------------------------------------------------------
# Lectura de la ficha
# ---------------------------------------------------------------------------
RE_FECHA = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def limpiar(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def filas_de_dos_celdas(soup):
    """Devuelve las filas de tabla que tienen exactamente dos celdas propias."""
    vistas = set()
    for tr in soup.find_all("tr"):
        celdas = tr.find_all(["td", "th"], recursive=False)
        if len(celdas) != 2:
            continue
        clave = (limpiar(celdas[0].get_text(" ")), limpiar(celdas[1].get_text(" ")))
        if clave in vistas:
            continue
        vistas.add(clave)
        yield celdas


def enlaces_de(celda):
    lista = []
    for a in celda.find_all("a", href=True):
        lista.append({
            "texto": limpiar(a.get_text(" ")),
            "titulo": limpiar(a.get("title", "")),
            "url": requests.compat.urljoin(SITIO + "/", a["href"]),
        })
    return lista


def leer_ficha(html, sobi2_id, url):
    soup = BeautifulSoup(html, "html.parser")
    titulo = limpiar(soup.title.get_text()) if soup.title else ""

    campos = {}
    enlace_proyecto = ""
    movimientos = []

    for c0, c1 in filas_de_dos_celdas(soup):
        etiqueta = limpiar(c0.get_text(" "))
        valor_celda = c1

        if RE_FECHA.match(etiqueta):
            # fila de movimiento
            enl = enlaces_de(valor_celda)
            movimientos.append({
                "fecha": etiqueta,
                "texto": limpiar(valor_celda.get_text(" ")),
                "enlace_texto": " | ".join(e["texto"] for e in enl),
                "enlace_titulo": " | ".join(e["titulo"] for e in enl),
                "enlace_url": " | ".join(e["url"] for e in enl),
            })
            continue

        if etiqueta.endswith(":") and len(etiqueta) < 40:
            nombre = etiqueta.rstrip(":").strip().lower()
            texto = limpiar(valor_celda.get_text(" "))
            if nombre.startswith("detalle"):
                for e in enlaces_de(valor_celda):
                    if "proyecto completo" in e["texto"].lower():
                        enlace_proyecto = e["url"]
                texto = limpiar(texto.replace("Ver Proyecto Completo", ""))
            campos[nombre] = texto

    def campo(*claves):
        for k in claves:
            for nombre, valor in campos.items():
                if nombre.startswith(k) and valor:
                    return valor
        return NO_INFORMADO

    nro = campo("nº de expediente", "n° de expediente", "no de expediente")
    tipo = campo("proyecto de")

    # Clase de expediente (condición 6): correspondencia y otros, por separado
    t = tipo.lower()
    if tipo == NO_INFORMADO:
        clase = PENDIENTE
    elif "correspondencia" in t:
        clase = "Correspondencia (no es proyecto legislativo)"
    elif any(p in t for p in ("ordenanza", "comunicaci", "resoluci", "decreto", "minuta")):
        clase = "Proyecto legislativo"
    else:
        clase = f"Otro tipo ({PENDIENTE})"

    # Comisiones, dictámenes y resultado final, tal como figuran en los movimientos
    comisiones = [m["texto"].replace("(ver dictamen)", "").strip()
                  for m in movimientos if "comisi" in m["texto"].lower()]
    dictamenes = [m["enlace_url"] for m in movimientos
                  if "dictamen" in m["enlace_texto"].lower() and m["enlace_url"]]
    resultado = [m for m in movimientos if "resultado final" in m["texto"].lower()]

    # Verificación: el número de la ficha debe ser de 2026 y coincidir con el título
    verificado = "Sí"
    if nro == NO_INFORMADO:
        verificado = "No"
        registrar_error(sobi2_id, url, "lectura", "La ficha no muestra número de expediente")
    else:
        if "/2026" not in nro:
            verificado = "No"
            registrar_error(sobi2_id, url, "verificación", f"El expediente {nro} no es de 2026")
        if nro.replace(" ", "") not in titulo.replace(" ", ""):
            verificado = "No"
            registrar_error(sobi2_id, url, "verificación",
                            f"El número '{nro}' no coincide con el título '{titulo}'")
    if not movimientos:
        registrar_error(sobi2_id, url, "lectura", "No se encontraron movimientos")

    num, anio = NO_INFORMADO, NO_INFORMADO
    m = re.search(r"(\d+)\s*/\s*(\d{4})", nro)
    if m:
        num, anio = m.group(1), m.group(2)

    fila = {
        "id_ficha": sobi2_id,
        "expediente": nro,
        "numero": num,
        "anio": anio,
        "fecha_inicio": campo("fecha de inicio"),
        "tipo_proyecto_original": tipo,
        "clase_expediente": clase,
        "iniciado_por_original": campo("iniciado por"),
        "detalle_original": campo("detalle"),
        "comisiones_segun_movimientos": " | ".join(comisiones) if comisiones else NO_INFORMADO,
        "enlaces_dictamen": " | ".join(dictamenes) if dictamenes else NO_INFORMADO,
        "resultado_final_fecha": resultado[-1]["fecha"] if resultado else NO_INFORMADO,
        "resultado_final_titulo_enlace": (resultado[-1]["enlace_titulo"] or NO_INFORMADO)
                                         if resultado else NO_INFORMADO,
        "resultado_final_url": (resultado[-1]["enlace_url"] or NO_INFORMADO)
                               if resultado else NO_INFORMADO,
        "ultimo_movimiento_fecha": movimientos[-1]["fecha"] if movimientos else NO_INFORMADO,
        "ultimo_movimiento_texto": movimientos[-1]["texto"] if movimientos else NO_INFORMADO,
        "cantidad_movimientos": len(movimientos),
        "enlace_ficha": url,
        "enlace_proyecto_completo": enlace_proyecto or NO_INFORMADO,
        "titulo_pagina": titulo or NO_INFORMADO,
        "verificado": verificado,
        "fecha_consulta": ahora(),
    }
    movs = [{"id_ficha": sobi2_id, "expediente": nro, "orden": i + 1, **mv}
            for i, mv in enumerate(movimientos)]
    return fila, movs


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------
def guardar_csv(ruta, filas, columnas):
    # utf-8-sig para que Excel muestre bien las tildes y la ñ
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        for fila in filas:
            w.writerow(fila)


def main():
    os.makedirs(CARPETA_HTML, exist_ok=True)

    print("=== Paso 0: comprobando acceso al sitio oficial ===")
    ok, informe = comprobar_acceso()
    for linea in informe:
        print(linea)

    expedientes, movimientos = [], []

    if ok:
        print("\n=== Paso 1: consultando 5 expedientes de prueba ===")
        for sobi2_id in IDS_PRUEBA:
            url = URL_FICHA.format(id=sobi2_id)
            print(f"- Ficha {sobi2_id}: {url}")
            r = descargar(url, sobi2_id)
            if r is not None:
                with open(os.path.join(CARPETA_HTML, f"ficha_{sobi2_id}.html"), "wb") as f:
                    f.write(r.content)
                try:
                    fila, movs = leer_ficha(r.content, sobi2_id, url)
                    expedientes.append(fila)
                    movimientos.extend(movs)
                    print(f"  OK: {fila['expediente']} - {fila['tipo_proyecto_original']} - "
                          f"{len(movs)} movimientos")
                except Exception as e:  # nunca cortar toda la prueba por una ficha
                    registrar_error(sobi2_id, url, "lectura", f"{type(e).__name__}: {e}")
            time.sleep(PAUSA_SEGUNDOS)

    columnas_exp = ["id_ficha", "expediente", "numero", "anio", "fecha_inicio",
                    "tipo_proyecto_original", "clase_expediente", "iniciado_por_original",
                    "detalle_original", "comisiones_segun_movimientos", "enlaces_dictamen",
                    "resultado_final_fecha", "resultado_final_titulo_enlace",
                    "resultado_final_url", "ultimo_movimiento_fecha", "ultimo_movimiento_texto",
                    "cantidad_movimientos", "enlace_ficha", "enlace_proyecto_completo",
                    "titulo_pagina", "verificado", "fecha_consulta"]
    columnas_mov = ["id_ficha", "expediente", "orden", "fecha", "texto",
                    "enlace_texto", "enlace_titulo", "enlace_url"]
    columnas_err = ["fecha_consulta", "id_ficha", "url", "etapa", "detalle"]

    guardar_csv(os.path.join(CARPETA, "prueba_expedientes.csv"), expedientes, columnas_exp)
    guardar_csv(os.path.join(CARPETA, "prueba_movimientos.csv"), movimientos, columnas_mov)
    guardar_csv(os.path.join(CARPETA, "registro_errores.csv"), errores, columnas_err)

    informe += ["", f"Expedientes leídos: {len(expedientes)} de {len(IDS_PRUEBA) if ok else 0} intentados",
                f"Verificados: {sum(1 for e in expedientes if e['verificado'] == 'Sí')}",
                f"Errores registrados: {len(errores)}"]
    with open(os.path.join(CARPETA, "informe_prueba.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(informe) + "\n")

    print("\n" + "\n".join(informe[-3:]))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
