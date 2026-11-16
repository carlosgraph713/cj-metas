#!/usr/bin/env python3
"""AFI PRO CJ — actualiza fondos.json con la rentabilidad publicada por BHD Fondos.

Lo ejecuta GitHub Actions una vez al dia. Para cada fondo abierto de BHD Fondos:
  1. abre la pagina publica del fondo,
  2. localiza el PDF "Informacion Diaria" vigente,
  3. extrae fecha de corte, valor cuota y rentabilidad a 30/90/180/365 dias.
Si un fondo falla, se conserva su ultimo dato bueno (con su fecha) y se anota el error.
Fuente: https://bhdfondos.com.do  (informes diarios publicos de cada fondo).
"""
import io, json, os, re, sys, time, datetime, unicodedata
import requests
import pdfplumber

BASE = "https://bhdfondos.com.do/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "es-DO,es;q=0.9"}
OUT = "fondos.json"
DEBUG_DIR = "fondos_debug"
MESES = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,"agosto":8,
         "septiembre":9,"setiembre":9,"octubre":10,"noviembre":11,"diciembre":12}

def norm(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()

def get(url, binary=False):
    last = None
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            if r.status_code == 200:
                return r.content if binary else r.text
            last = "HTTP %s" % r.status_code
        except Exception as e:  # noqa
            last = str(e)
        time.sleep(3 + 3 * i)
    raise RuntimeError("%s -> %s" % (url, last))

def descubrir_paginas():
    """Devuelve las URL de las paginas de fondos enlazadas desde la portada."""
    html = get(BASE)
    urls = set(re.findall(r'href="(https://(?:www\.)?bhdfondos\.com\.do/fondo-[^"#?]+)"', html))
    return sorted(u if u.endswith("/") else u + "/" for u in urls)

def pdf_diario(html):
    cands = re.findall(r'href="([^"]+\.pdf)"', html, flags=re.I)
    diarios = [c for c in cands if re.search(r'informaci[oó]n[-_ %20]*diaria', c, flags=re.I)]
    return diarios[0] if diarios else None

def titulo(html):
    m = re.search(r'<title>(.*?)</title>', html, flags=re.S | re.I)
    t = re.sub(r'\s+', ' ', m.group(1)).strip() if m else ""
    return re.split(r'\s+[-–|]\s+BHD Fondos', t)[0].strip()

def num(s):
    return float(s.replace(",", ""))

def parse_fecha(texto):
    t = norm(texto)
    m = re.search(r'(\d{1,2})\s+de\s+([a-z]+)\s+(?:de|del)\s+(\d{4})', t)
    if m and m.group(2) in MESES:
        return "%04d-%02d-%02d" % (int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
    m = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', t)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None

def parse_rent(texto):
    """Busca las cuatro rentabilidades (30, 90, 180, 365 dias)."""
    t = norm(texto)
    out = {}
    # Caso A: cada etiqueta seguida (cerca) de su porcentaje
    for d in (30, 90, 180, 365):
        m = re.search(r'\b%d\s*dias?\b[^\d\-%%]{0,40}(-?\d{1,3}(?:\.\d+)?)\s*%%' % d, t)
        if m:
            out[d] = float(m.group(1))
    if len(out) == 4:
        return out
    # Caso B: las cuatro etiquetas en una linea y los cuatro porcentajes despues
    m = re.search(r'30\s*dias?.{0,60}?90\s*dias?.{0,60}?180\s*dias?.{0,60}?365\s*dias?(.{0,400})', t, flags=re.S)
    if m:
        ps = re.findall(r'(-?\d{1,3}(?:\.\d+)?)\s*%', m.group(1))
        if len(ps) >= 4:
            return {30: float(ps[0]), 90: float(ps[1]), 180: float(ps[2]), 365: float(ps[3])}
    return out

def parse_cuota(texto):
    t = norm(texto)
    m = re.search(r'valor\s+(?:de\s+la\s+)?cuota[^\d]{0,60}([\d,]+\.\d{2,})', t)
    return num(m.group(1)) if m else None

def moneda(nombre, texto):
    n = norm(nombre + " " + texto[:1500])
    return "USD" if re.search(r'dolar|us\$|usd', n) else "DOP"

def slug(url):
    return url.rstrip("/").split("/")[-1]

def main():
    previo = {}
    if os.path.exists(OUT):
        try:
            for f in json.load(open(OUT, encoding="utf-8")).get("fondos", []):
                previo[f["id"]] = f
        except Exception:
            pass
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ahora = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        paginas = descubrir_paginas()
    except Exception as e:
        print("No se pudo leer la portada:", e)
        paginas = [f["url_pagina"] for f in previo.values()]
    print("Paginas de fondos:", len(paginas))
    fondos, errores = [], []
    for url in paginas:
        fid = slug(url)
        base = dict(previo.get(fid, {}), id=fid, afi="BHD Fondos", url_pagina=url)
        try:
            html = get(url)
            pdf = pdf_diario(html)
            if not pdf:
                print("-", fid, ": sin informe diario (se omite)")
                continue  # p. ej. fondos cerrados sin informe diario
            raw = get(pdf, binary=True)
            with pdfplumber.open(io.BytesIO(raw)) as doc:
                texto = "\n".join((p.extract_text() or "") for p in doc.pages[:2])
            open(os.path.join(DEBUG_DIR, fid + ".txt"), "w", encoding="utf-8").write(pdf + "\n\n" + texto)
            rent = parse_rent(texto)
            fecha = parse_fecha(texto)
            if len(rent) < 4 or not fecha:
                raise RuntimeError("no se reconocio el formato (rent=%s fecha=%s)" % (rent, fecha))
            if not all(-50 < v < 100 for v in rent.values()):
                raise RuntimeError("valores fuera de rango %s" % rent)
            nombre = titulo(html) or base.get("nombre") or fid
            base.update(nombre=nombre, moneda=moneda(nombre, texto), url_pdf=pdf, fecha_corte=fecha,
                        valor_cuota=parse_cuota(texto), r30=rent[30], r90=rent[90], r180=rent[180],
                        r365=rent[365], leido=ahora, ok=True, error=None)
            print("OK", fid, fecha, rent)
        except Exception as e:
            print("ERROR", fid, e)
            errores.append(fid)
            if "r365" not in base:
                continue  # nunca hubo dato bueno: no se publica
            base.update(ok=False, error=str(e)[:200])
        fondos.append(base)
        time.sleep(1.5)
    # Conserva fondos que ya existian y hoy no aparecieron en la portada
    vistos = {f["id"] for f in fondos}
    for fid, f in previo.items():
        if fid not in vistos and "r365" in f:
            f.update(ok=False, error="no encontrado hoy")
            fondos.append(f)
    if not fondos:
        print("Sin datos: no se modifica", OUT)
        sys.exit(1 if not previo else 0)
    fondos.sort(key=lambda f: f.get("nombre", f["id"]))
    data = {"actualizado": ahora, "fuente": "BHD Fondos — Información Diaria de cada fondo",
            "fuente_url": BASE, "nota": "Rentabilidades anualizadas publicadas por la administradora.",
            "fondos": fondos}
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Escrito", OUT, "con", len(fondos), "fondos;", len(errores), "con error")

if __name__ == "__main__":
    main()
