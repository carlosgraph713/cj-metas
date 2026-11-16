#!/usr/bin/env python3
"""AFI PRO CJ — actualiza fondos.json con la rentabilidad publicada por las administradoras.
Fuentes: BHD Fondos (Informacion Diaria), AFI Universal (ficha tecnica diaria) y
Alcanza (plan de ahorro de AFP Siembra: cifras publicadas en su pagina principal, actualizacion mensual).

Lo ejecuta GitHub Actions una vez al dia. Para cada fondo abierto de BHD Fondos:
  1. abre la pagina publica del fondo,
  2. localiza el PDF "Informacion Diaria" vigente,
  3. extrae fecha de corte, valor cuota y rentabilidad a 30/90/180/365 dias.
Si un fondo falla, se conserva su ultimo dato bueno (con su fecha) y se anota el error.
Fuente: https://bhdfondos.com.do  (informes diarios publicos de cada fondo).
"""
import io, json, os, re, sys, time, datetime, unicodedata
import requests
from urllib.parse import urljoin
import pdfplumber

BASE = "https://bhdfondos.com.do/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "es-DO,es;q=0.9"}
OUT = "fondos.json"
DEBUG_DIR = "fondos_debug"
MESES_AB = {"ene":1,"feb":2,"mar":3,"abr":4,"may":5,"jun":6,"jul":7,"ago":8,"sep":9,"set":9,"oct":10,"nov":11,"dic":12,
            "jan":1,"apr":4,"aug":8,"dec":12}
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

SEMILLAS = ["fondo-mutuo-renta-fija-nacional-bhd-liquidez", "fondo-mutuo-renta-fija-bhd-plazo-30-dias",
            "fondo-mutuo-renta-fija-bhd-tu-futuro"]

def enlaces_fondos(html):
    slugs = set(re.findall(r'bhdfondos\.com\.do/(fondo-[a-z0-9\-]+)', html, flags=re.I))
    slugs |= set(re.findall(r'href=["\']/(fondo-[a-z0-9\-]+)', html, flags=re.I))
    return {s.lower() for s in slugs}

def descubrir_paginas():
    """Paginas de fondos: las conocidas + las enlazadas desde la portada y desde cada fondo."""
    slugs = set(SEMILLAS)
    for u in [BASE, BASE + SEMILLAS[0] + "/"]:
        try:
            html = get(u)
            nuevos = enlaces_fondos(html)
            print("Enlaces de fondos en", u, ":", len(nuevos))
            slugs |= nuevos
        except Exception as e:
            print("Aviso:", u, e)
    return sorted(BASE + s + "/" for s in slugs)

def pdf_diario(html):
    cands = re.findall(r'href=["\']([^"\']+\.pdf)["\']', html, flags=re.I)
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
    m = re.search(r'\b(\d{1,2})[-/ ]([a-z]{3})[a-z]*[-/ ](\d{2,4})\b', t)
    if m and m.group(2) in MESES_AB:
        y = int(m.group(3)); y = y + 2000 if y < 100 else y
        return "%04d-%02d-%02d" % (y, MESES_AB[m.group(2)], int(m.group(1)))
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
    """Valor de la cuota del dia: el primer importe con 6 decimales del informe."""
    t = norm(texto)
    m = re.search(r'(?:rd\$|us\$|\$)\s*([\d,]+\.\d{6})\b', t)
    if not m:
        m = re.search(r'valor\s+(?:de\s+la\s+)?cuota[^\d]{0,60}([\d,]+\.\d{2,})', t)
    return num(m.group(1)) if m else None

# ─── AFI Universal ───
UNI_BASE = "https://www.afiuniversal.com.do/"
UNI_FONDOS = {  # pagina: nombre (fondos abiertos)
    "universal-liquidez": "Universal Liquidez",
    "deposito-financiero-flexible": "Depósito Financiero Flexible",
    "renta-valores": "Renta Valores",
    "renta-futuros": "Renta Futuro",
    "plazo-mensual-dolar": "Plazo Mensual Dólar",
}

def uni_ficha(html):
    """La ficha tecnica mas reciente enlazada en la pagina (nombre: ficha_tecnica_DD_MM_AAAA_...)."""
    best, bd = None, None
    for href in re.findall(r'href=["\']([^"\']+\.pdf)["\']', html, flags=re.I):
        m = re.search(r'ficha[_-]?tecnica[_-]?(\d{1,2})[_-](\d{1,2})[_-](\d{4})', href, flags=re.I)
        if not m:
            continue
        try:
            d = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        if bd is None or d > bd:
            best, bd = href, d
    return best

PAR_PCT = re.compile(r'^\s*(-?\d{1,3}\.\d{1,4})\s*%\s+(-?\d{1,3}\.\d{1,4})\s*%\s*$')

def parse_rent_uni(texto):
    """Ficha de Universal. En el texto, cada par de valores va justo ANTES de sus etiquetas:
         7.64% 7.64%
         VALOR CUOTA ... RENTABILIDAD 30 dias 90 dias
         DIA ANTERIOR EFECTIVA ANUAL
         7.55% 7.39%
         NUMERO DE CUOTAS ... 180 dias 365 dias
    Se lee solo el bloque de RENTABILIDAD (no el de volatilidad ni el eje del grafico)."""
    ls = [norm(l) for l in texto.splitlines()]
    i = next((k for k, l in enumerate(ls) if "rentabilidad" in l and re.search(r'30\s*dias', l)), None)
    if i is None:
        return {}
    j = next((k for k in range(i + 1, min(i + 6, len(ls))) if re.search(r'180\s*dias', ls[k])), None)
    if j is None:
        return {}
    a = next((PAR_PCT.match(ls[k]) for k in range(i - 1, max(i - 4, -1), -1) if PAR_PCT.match(ls[k])), None)
    b = next((PAR_PCT.match(ls[k]) for k in range(j - 1, i, -1) if PAR_PCT.match(ls[k])), None)
    if not a or not b:
        return {}
    return {30: float(a.group(1)), 90: float(a.group(2)), 180: float(b.group(1)), 365: float(b.group(2))}

def parse_cuota_uni(texto):
    """'VALOR CUOTA DEL DIA': el importe en esa linea o en la anterior (el PDF a veces separa los digitos)."""
    ls = texto.splitlines()
    def imp(l):
        m = re.search(r'\$\s*([\d ,]+\.\d{4,})', l)
        return num(m.group(1).replace(" ", "")) if m else None
    for k, l in enumerate(ls):
        if re.search(r'valor\s*cuota\s*del\s*d', norm(l)):
            return imp(l) or (imp(ls[k - 1]) if k else None)
    return None

# ─── Alcanza (AFP Siembra) ───
ALC_URL = "https://www.alcanza.com.do/"

def html_a_texto(html):
    t = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', html)
    t = re.sub(r'(?s)<[^>]+>', ' ', t)
    t = (t.replace('&nbsp;', ' ').replace('&aacute;', 'á').replace('&iacute;', 'í')
          .replace('&oacute;', 'ó').replace('&eacute;', 'é').replace('&uacute;', 'ú').replace('&#37;', '%'))
    return re.sub(r'\s+', ' ', t)

def parse_alcanza(html):
    """'Rentabilidad anualizada ultimos 30 dias ... 7.6% ... al 30 de octubre 2026' (y lo mismo con 365)."""
    t = norm(html_a_texto(html))
    out = {}
    for d in (30, 90, 180, 365):
        m = re.search(r'anualizada\s+(?:de\s+los\s+)?ultimos\s+%d\s+dias\D{0,120}?(\d{1,2}(?:[.,]\d{1,3})?)\s*%%' % d, t)
        if m:
            out[d] = float(m.group(1).replace(',', '.'))
    m = re.search(r'\bal\s+(\d{1,2})\s+de\s+([a-z]+)\s+(?:de\s+|del\s+)?(\d{4})', t)
    fecha = None
    if m and m.group(2) in MESES:
        fecha = "%04d-%02d-%02d" % (int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
    return out, fecha, t

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
            pdf = urljoin(url, pdf) if pdf else None
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
            base.update(nombre=nombre, tipo=("cerrado" if "cerrado" in fid else "abierto"), moneda=moneda(nombre, texto), url_pdf=pdf, fecha_corte=fecha,
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
    # ─── AFI Universal ───
    for pag, nombre in UNI_FONDOS.items():
        url = UNI_BASE + pag + "/"
        fid = "universal-" + pag
        base = dict(previo.get(fid, {}), id=fid, afi="AFI Universal", url_pagina=url)
        try:
            html = get(url)
            pdf = uni_ficha(html)
            if not pdf:
                raise RuntimeError("sin ficha tecnica")
            pdf = urljoin(url, pdf)
            raw = get(pdf, binary=True)
            with pdfplumber.open(io.BytesIO(raw)) as doc:
                texto = "\n".join((p.extract_text() or "") for p in doc.pages[:2])
            open(os.path.join(DEBUG_DIR, fid + ".txt"), "w", encoding="utf-8").write(pdf + "\n\n" + texto)
            rent = parse_rent_uni(texto)
            fecha = parse_fecha(texto)
            if len(rent) < 4 or not fecha:
                raise RuntimeError("no se reconocio el formato (rent=%s fecha=%s)" % (rent, fecha))
            if not all(-50 < v < 100 for v in rent.values()):
                raise RuntimeError("valores fuera de rango %s" % rent)
            base.update(nombre=nombre, tipo="abierto", moneda=("USD" if "dolar" in pag else "DOP"), url_pdf=pdf,
                        fecha_corte=fecha, valor_cuota=parse_cuota_uni(texto), r30=rent[30], r90=rent[90],
                        r180=rent[180], r365=rent[365], leido=ahora, ok=True, error=None)
            print("OK", fid, fecha, rent)
        except Exception as e:
            print("ERROR", fid, e)
            errores.append(fid)
            if "r365" not in base:
                continue
            base.update(ok=False, error=str(e)[:200])
        fondos.append(base)
        time.sleep(1.5)
    # ─── Alcanza (AFP Siembra) ───
    fid = "alcanza-afp-siembra"
    base = dict(previo.get(fid, {}), id=fid, afi="Alcanza (AFP Siembra)", url_pagina=ALC_URL)
    try:
        html = get(ALC_URL)
        rent, fecha, texto = parse_alcanza(html)
        i = texto.find("rentabilidad")
        open(os.path.join(DEBUG_DIR, fid + ".txt"), "w", encoding="utf-8").write(ALC_URL + "\n\n" + texto[max(0, i - 300):i + 2500])
        if 30 not in rent or 365 not in rent or not fecha:
            raise RuntimeError("no se reconocio el formato (rent=%s fecha=%s)" % (rent, fecha))
        if not all(-50 < v < 100 for v in rent.values()):
            raise RuntimeError("valores fuera de rango %s" % rent)
        base.update(nombre="Alcanza", tipo="abierto", moneda="DOP", categoria="liquidez", frecuencia="mensual",
                    url_pdf=ALC_URL, fecha_corte=fecha, valor_cuota=None,
                    r30=rent[30], r90=rent.get(90), r180=rent.get(180), r365=rent[365],
                    nota="Plan de Ahorro Complementario de AFP Siembra (regulado por la SIPEN). La pagina no aclara si la cifra es antes o despues de la comision anual de 2%.",
                    leido=ahora, ok=True, error=None,
                    verificar=True)  # fuente nueva: en revision hasta comprobar la lectura
        print("OK", fid, fecha, rent)
    except Exception as e:
        print("ERROR", fid, e)
        errores.append(fid)
        if "r365" in base:
            base.update(ok=False, error=str(e)[:200])
    if "r365" in base:
        fondos.append(base)
    # Conserva fondos que ya existian y hoy no aparecieron en la portada
    vistos = {f["id"] for f in fondos}
    for fid, f in previo.items():
        if fid not in vistos and "r365" in f:
            f.update(ok=False, error="no encontrado hoy")
            fondos.append(f)
    if not fondos and previo:
        print("Sin datos nuevos: no se modifica", OUT)
        sys.exit(0)
    # Fondos de una fuente nueva quedan "en revision" (no los muestra la app) hasta validar el lector
    revision = [f for f in fondos if f.get("verificar")]
    fondos = [f for f in fondos if not f.get("verificar")]
    fondos.sort(key=lambda f: (f.get("afi", ""), f.get("nombre", f["id"])))
    data = {"actualizado": ahora, "fuente": "Informes públicos de cada administradora (BHD Fondos: Información Diaria; AFI Universal: ficha técnica diaria; Alcanza/AFP Siembra: página principal)",
            "fuente_url": BASE, "nota": "Rentabilidades anualizadas publicadas por la administradora.",
            "errores": errores, "fondos": fondos, "en_revision": revision}
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Escrito", OUT, "con", len(fondos), "fondos;", len(errores), "con error")

if __name__ == "__main__":
    main()
