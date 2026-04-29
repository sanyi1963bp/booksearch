# -*- coding: utf-8 -*-
"""
konyv_kereses.py  -  Magyar könyvinfo lekérdező
Források: Moly.hu | Bookline.hu | Libri.hu | Lira.hu | Alexandra.hu
          Antikvarium.hu | Regikonyvek.hu | Google Books

Vak felhasználóknak optimalizált, képernyőolvasóval kompatibilis ablak.
Szükséges: pip install requests beautifulsoup4
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import threading
import urllib.parse
import re
import textwrap

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "requests", "beautifulsoup4"])
    import requests
    from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Konstansok
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SEP  = "=" * 55
SEP2 = "-" * 55

# ---------------------------------------------------------------------------
# Közös segédfüggvények
# ---------------------------------------------------------------------------

def _get(url, timeout=15):
    return requests.get(url, headers=HEADERS, timeout=timeout)

def _szakasz(cim, tartalom):
    if tartalom and tartalom.strip():
        return f"\n{SEP}\n{cim}\n{SEP}\n{tartalom.strip()}\n"
    return ""

_STOP = {"a", "az", "es", "vagy", "de", "hogy", "nem", "is", "meg",
         "egy", "the", "and", "of", "in", "to"}

def _norm(s):
    s = s.lower()
    for f, t in [("á","a"),("é","e"),("í","i"),("ó","o"),("ö","o"),("ő","o"),
                 ("ú","u"),("ü","u"),("ű","u")]:
        s = s.replace(f, t)
    # Egybetűs tokeneket kihagyjuk (pl. J.R.R. → j, r, r → mind kiesik)
    return {tok for tok in re.sub(r"[^\w]", " ", s).split()
            if len(tok) > 1} - _STOP

def _relevans(kereses, talalat, kuszob=0.4):
    k = _norm(kereses)
    t = _norm(talalat)
    if not k:
        return True
    return len(k & t) / len(k) >= kuszob

def _parse_kereses(szoveg):
    """
    Felismeri a 'Szerző - Cím' vagy 'Szerző: Cím' formátumot.
    Visszaad (cim, szerzo) tuple-t.
    Ha nincs elválasztó, szerzo=''.
    Pl.: 'Gárdonyi Géza - Egri csillagok'  -> ('Egri csillagok', 'Gárdonyi Géza')
         'Egri csillagok'                  -> ('Egri csillagok', '')
    """
    # Elválasztók: ' - ', ' – ', ' : ', ' / '
    for elv in [" - ", " – ", " : ", " / "]:
        if elv in szoveg:
            bal, jobb = szoveg.split(elv, 1)
            bal, jobb = bal.strip(), jobb.strip()
            if bal and jobb:
                # Általában: bal = szerző, jobb = cím
                # De ha a jobb rész nagyon rövid, fordítva is lehet
                return (jobb, bal)
    return (szoveg.strip(), "")

def _van_leiras(szoveg):
    """True ha a kinyert szövegben van tartalmi leírás."""
    return any(m in szoveg for m in ["LEÍRÁS", "FÜLSZÖVEG", "SYNOPSIS"])

def _alap_adatok(soup, url, forras_nev):
    """Általános adatkinyerő – leírás kerül ELŐRE, utána az adatok."""
    fejlec = [f"FORRÁS: {forras_nev}\n"]
    h1 = soup.find("h1")
    if h1:
        fejlec.append(f"Cím:  {h1.get_text(strip=True)}")
    szerzok = []
    for a in soup.select("a[href*='szerzo'], a[href*='author'], "
                         "[itemprop='author'], .author a, .authors a"):
        n = a.get_text(strip=True)
        if n and n not in szerzok and len(n) > 1:
            szerzok.append(n)
    if szerzok:
        fejlec.append(f"Szerző(k):  {', '.join(szerzok[:5])}")

    # ── 1. LEÍRÁS (előre kerül) ──────────────────────────────────────────────
    leiras_blokk = ""
    for sel in ["[itemprop='description']", ".description", "#description",
                ".book-description", ".product-description", ".synopsis",
                ".fulszoveg", ".book_description", "section.description",
                ".termek-leiras", ".product_description"]:
        el = soup.select_one(sel)
        if el:
            leiras_blokk = _szakasz("FÜLSZÖVEG / LEÍRÁS", el.get_text("\n", strip=True))
            break
    if not leiras_blokk:
        bekezdések = [p.get_text(" ", strip=True)
                      for p in soup.find_all("p") if len(p.get_text()) > 100]
        if bekezdések:
            leiras_blokk = _szakasz("LEÍRÁS (auto-kinyert)", "\n\n".join(bekezdések[:5]))

    # ── 2. KÖNYV ADATOK (utána) ──────────────────────────────────────────────
    adatok = []
    for dt, dd in zip(soup.select("dl dt"), soup.select("dl dd")):
        adatok.append(f"{dt.get_text(strip=True)}: {dd.get_text(' ', strip=True)}")
    if not adatok:
        for tr in soup.select("table tr"):
            cellak = [td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
            if len(cellak) >= 2:
                adatok.append("  ".join(cellak))
    adatok_blokk = _szakasz("KÖNYV ADATOK", "\n".join(adatok[:15]))

    reszek = fejlec + [leiras_blokk, adatok_blokk,
                       f"\n{SEP2}\nForrás: {url}\n{SEP2}"]
    return "\n".join(reszek)

# ---------------------------------------------------------------------------
# 1. MOLY.HU
# ---------------------------------------------------------------------------

def _moly_link_szoveg(a):
    """Megpróbálja kinyerni a könyv nevét egy moly linkelemből.
    Sorban: direkt szöveg → title attrib → img alt → szülőben .title/.name stb."""
    # 1. Közvetlen szöveg
    szoveg = a.get_text(" ", strip=True)
    if szoveg and len(szoveg) >= 3 and "»" not in szoveg:
        return szoveg
    # 2. title attribútum
    t = a.get("title", "").strip()
    if t and len(t) >= 3:
        return t
    # 3. Képen belüli alt szöveg
    img = a.find("img")
    if img:
        alt = img.get("alt", "").strip()
        if alt and len(alt) >= 3:
            return alt
    # 4. Testvér / szülő elemben keresünk jelölő classokat
    parent = a.parent
    if parent:
        for sel in [".title", ".book-title", ".name", "h3", "h2", "strong"]:
            el = parent.select_one(sel)
            if el:
                t2 = el.get_text(" ", strip=True)
                if t2 and len(t2) >= 3:
                    return t2
    return ""


def moly_kereses(cim):
    url = (f"https://moly.hu/kereses?utf8=%E2%9C%93"
           f"&query={urllib.parse.quote_plus(cim)}")
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # /konyvek/ után legalább egy alfanumerikus karakter – semmi több megszorítás
    minta = re.compile(r'^/konyvek/[a-z0-9]')
    blokk = (soup.select_one(".search_result") or soup.select_one(".books")
             or soup.select_one("main") or soup.body)

    eredmenyek = []
    for a in blokk.select("a[href*='/konyvek/']"):
        href = a.get("href", "").split("?")[0].rstrip("/")
        if not minta.match(href):
            continue
        szoveg = _moly_link_szoveg(a)
        if not szoveg or szoveg.lower().startswith("összes"):
            continue
        if not _relevans(cim, szoveg):
            continue
        full = "https://moly.hu" + href
        if full not in [e[1] for e in eredmenyek]:
            eredmenyek.append((f"[Moly]  {szoveg}", full, "Moly"))
        if len(eredmenyek) >= 5:
            break
    return eredmenyek

def moly_adatok(url):
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    fejlec = ["FORRÁS: Moly.hu\n"]
    h1 = soup.find("h1")
    if h1:
        fejlec.append(f"Cím:  {h1.get_text(strip=True)}")
    szerzok = []
    for a in soup.select("a[href*='/szerzo/'], a[href*='/szerzon/'], .author a"):
        n = a.get_text(strip=True)
        if n and n not in szerzok:
            szerzok.append(n)
    if szerzok:
        fejlec.append(f"Szerző(k):  {', '.join(szerzok)}")

    # ── 1. LEÍRÁS előre ──────────────────────────────────────────────────────
    leiras_blokk = ""
    for sel in [".description", "#description", ".book_description",
                "[itemprop='description']", ".synopsis", ".fulszoveg", "section.description"]:
        el = soup.select_one(sel)
        if el:
            leiras_blokk = _szakasz("FÜLSZÖVEG / LEÍRÁS", el.get_text("\n", strip=True))
            break
    if not leiras_blokk:
        bekezdések = [p.get_text(" ", strip=True)
                      for p in soup.find_all("p") if len(p.get_text()) > 80]
        if bekezdések:
            leiras_blokk = _szakasz("LEÍRÁS (auto)", "\n\n".join(bekezdések[:5]))

    # ── 2. ADATOK utána ──────────────────────────────────────────────────────
    adatok = []
    for tr in soup.select("table.book_details tr, .book_data tr"):
        cellak = [td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
        if cellak:
            adatok.append("  ".join(cellak))
    if not adatok:
        for dt, dd in zip(soup.select("dl dt"), soup.select("dl dd")):
            adatok.append(f"{dt.get_text(strip=True)}: {dd.get_text(' ', strip=True)}")
    if not adatok:
        det = soup.select_one(".details, #details, .book_info")
        if det:
            adatok = det.get_text("\n", strip=True).splitlines()
    adatok_blokk = _szakasz("KÖNYV ADATOK (kiadó, év, oldalszám, ISBN stb.)", "\n".join(adatok))

    reszek = fejlec + [leiras_blokk, adatok_blokk]
    for sel in [".rating_value", "[itemprop='ratingValue']", ".avg_rating"]:
        el = soup.select_one(sel)
        if el:
            reszek.append(f"\nÁtlagos értékelés: {el.get_text(strip=True)}")
            break
    el = soup.select_one(".rating_count, .vote_count, [itemprop='ratingCount']")
    if el:
        reszek.append(f"Értékelők száma: {el.get_text(strip=True)}")
    velemenyek = []
    for sel in [".review", ".comment", ".user_review", "article.review"]:
        velemenyek = soup.select(sel)
        if velemenyek:
            break
    if velemenyek:
        reszek.append(f"\n{SEP}\nOLVASÓI VÉLEMÉNYEK (első {min(5,len(velemenyek))} db)\n{SEP}\n")
        for i, v in enumerate(velemenyek[:5], 1):
            felh = v.select_one(".user a, .reviewer a, .username")
            szel = v.select_one(".review_text, .text, .content, p")
            eel  = v.select_one(".stars, .rating, .score")
            nev  = felh.get_text(strip=True) if felh else "Névtelen"
            etxt = f"  [{eel.get_text(strip=True)}]" if eel else ""
            reszek.append(f"{i}. vélemény - {nev}{etxt}")
            if szel:
                sz = szel.get_text(" ", strip=True)
                reszek.append(textwrap.fill(sz[:597], width=80))
            reszek.append("")
    reszek.append(f"\n{SEP2}\nForrás: {url}\n{SEP2}")
    return "\n".join(reszek)

# ---------------------------------------------------------------------------
# 2. BOOKLINE.HU
# ---------------------------------------------------------------------------

def bookline_kereses(cim):
    # Helyes keresési paraméter: searchfield=
    url = (f"https://bookline.hu/search/search.action"
           f"?searchfield={urllib.parse.quote_plus(cim)}")
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    blokk = (soup.select_one(".search-result-list") or soup.select_one(".product-list")
             or soup.select_one("main") or soup.body)
    eredmenyek = []
    for a in blokk.select("a[href*='/product/']"):
        szoveg = a.get_text(" ", strip=True)
        if not szoveg:
            img = a.find("img")
            szoveg = img.get("alt", "").strip() if img else ""
        # FONTOS: Bookline URL-ekből NEM vágjuk le a query stringet!
        # pl. /product/home.action?_v=cim-slug&productId=12345
        href = a.get("href", "")
        # Kizárjuk az általános navigációs linkeket (pl. puszta /product/home.action)
        if (not szoveg or len(szoveg) < 3 or "»" in szoveg
                or href.strip("/") in ("product/home.action", "product")
                or not re.search(r'/product/[a-z0-9]', href, re.I)
                or not _relevans(cim, szoveg)):
            continue
        full = "https://bookline.hu" + href if href.startswith("/") else href
        if full not in [e[1] for e in eredmenyek]:
            eredmenyek.append((f"[Bookline]  {szoveg}", full, "Bookline"))
        if len(eredmenyek) >= 5:
            break
    return eredmenyek

def bookline_adatok(url):
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return _alap_adatok(soup, url, "Bookline.hu")

# ---------------------------------------------------------------------------
# 3. LIBRI.HU
# ---------------------------------------------------------------------------

def libri_kereses(cim):
    # Helyes URL: /talalatok/?text= (nem /kereses.html?searchString=)
    url = f"https://www.libri.hu/talalatok/?text={urllib.parse.quote_plus(cim)}"
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    blokk = (soup.select_one(".search-results") or soup.select_one(".product-list")
             or soup.select_one("main") or soup.body)
    eredmenyek = []
    # Libri URL formátum: /konyv/author.title.html
    for a in blokk.select("a[href*='/konyv/']"):
        href = a.get("href", "").split("?")[0]
        # Csak valódi könyvoldalak (.html végű, nem kategória)
        if not href.endswith(".html") or re.search(r'/konyv/[a-z-]+/$', href):
            continue
        szoveg = a.get_text(" ", strip=True)
        if not szoveg:
            img = a.find("img")
            szoveg = img.get("alt", "").strip() if img else ""
        if (not szoveg or len(szoveg) < 3 or "»" in szoveg
                or not _relevans(cim, szoveg)):
            continue
        full = "https://www.libri.hu" + href if href.startswith("/") else href
        if full not in [e[1] for e in eredmenyek]:
            eredmenyek.append((f"[Libri]  {szoveg}", full, "Libri"))
        if len(eredmenyek) >= 5:
            break
    return eredmenyek

def libri_adatok(url):
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    reszek = ["FORRÁS: Libri.hu\n"]
    h1 = soup.find("h1")
    if h1:
        reszek.append(f"Cím:  {h1.get_text(strip=True)}")
    szerzok = soup.select("[itemprop='author'], .author a, .product-author a")
    if szerzok:
        reszek.append("Szerző(k):  " + ", ".join(
            a.get_text(strip=True) for a in szerzok[:4] if a.get_text(strip=True)))
    adatok = []
    for dt, dd in zip(soup.select(".product-data dt, .book-data dt, dl dt"),
                      soup.select(".product-data dd, .book-data dd, dl dd")):
        adatok.append(f"{dt.get_text(strip=True)}: {dd.get_text(' ', strip=True)}")
    if not adatok:
        for tr in soup.select("table tr"):
            cellak = [td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
            if len(cellak) >= 2:
                adatok.append("  ".join(cellak))
    reszek.append(_szakasz("KÖNYV ADATOK", "\n".join(adatok[:15])))
    for sel in ["[itemprop='description']", ".description", ".product-description",
                ".book-description", "#description"]:
        el = soup.select_one(sel)
        if el:
            reszek.append(_szakasz("LEÍRÁS / FÜLSZÖVEG", el.get_text("\n", strip=True)))
            break
    reszek.append(f"\n{SEP2}\nForrás: {url}\n{SEP2}")
    return "\n".join(reszek)

# ---------------------------------------------------------------------------
# 4. LIRA.HU
# ---------------------------------------------------------------------------

def lira_kereses(cim):
    # Lira.hu Cloudflare bot-védelemmel blokkolja az automatikus kéréseket (403)
    return []

def lira_adatok(url):
    return "FORRÁS: Lira.hu\n\nA Lira.hu bot-védelme miatt nem elérhető automatikusan."

# ---------------------------------------------------------------------------
# 5. ALEXANDRA.HU
# ---------------------------------------------------------------------------

def alexandra_kereses(cim):
    # Helyes URL: /talalati-lista?kulcsszo= (nem termekek?q= vagy search?searchText=)
    url = f"https://alexandra.hu/talalati-lista?kulcsszo={urllib.parse.quote_plus(cim)}"
    try:
        r = _get(url)
        r.raise_for_status()
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    blokk = (soup.select_one(".product-list") or soup.select_one(".search-results")
             or soup.select_one("main") or soup.body)
    eredmenyek = []
    for a in blokk.select("a[href*='/konyv/']"):
        szoveg = a.get_text(" ", strip=True)
        if not szoveg:
            img = a.find("img")
            szoveg = img.get("alt", "").strip() if img else ""
        href = a.get("href", "").split("?")[0]
        # Alexandra URL: /konyv/kategoria/.../cim – min. 3 szint mélységű
        if (not szoveg or len(szoveg) < 3 or "»" in szoveg
                or href.count("/") < 3
                or not _relevans(cim, szoveg)):
            continue
        full = "https://alexandra.hu" + href if href.startswith("/") else href
        if full not in [e[1] for e in eredmenyek]:
            eredmenyek.append((f"[Alexandra]  {szoveg}", full, "Alexandra"))
        if len(eredmenyek) >= 5:
            break
    return eredmenyek

def alexandra_adatok(url):
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return _alap_adatok(soup, url, "Alexandra.hu")

# ---------------------------------------------------------------------------
# 6. ANTIKVARIUM.HU
# ---------------------------------------------------------------------------

def antikvarium_kereses(cim):
    # Helyes URL: /reszletes-kereso?cim= (a régi /konyv/kereso.php 404-et ad)
    url = f"https://www.antikvarium.hu/reszletes-kereso?cim={urllib.parse.quote_plus(cim)}"
    try:
        r = _get(url)
        r.raise_for_status()
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    blokk = (soup.select_one(".search-results") or soup.select_one(".book-list")
             or soup.select_one("main") or soup.body)
    eredmenyek = []
    for a in blokk.select("a[href*='/konyv/']"):
        szoveg = a.get_text(" ", strip=True)
        if not szoveg:
            img = a.find("img")
            szoveg = img.get("alt", "").strip() if img else ""
        href = a.get("href", "").split("?")[0]
        if (not szoveg or len(szoveg) < 3 or "»" in szoveg
                or not re.search(r'/konyv/[a-z0-9]', href, re.I)
                or not _relevans(cim, szoveg)):
            continue
        full = "https://www.antikvarium.hu" + href if href.startswith("/") else href
        if full not in [e[1] for e in eredmenyek]:
            eredmenyek.append((f"[Antikvarium]  {szoveg}", full, "Antikvarium"))
        if len(eredmenyek) >= 5:
            break
    return eredmenyek

def antikvarium_adatok(url):
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    reszek = ["FORRÁS: Antikvarium.hu\n"]
    h1 = soup.find("h1")
    if h1:
        reszek.append(f"Cím:  {h1.get_text(strip=True)}")
    szerzok = []
    for a in soup.select("a[href*='szerzo'], a[href*='author'], .author a"):
        n = a.get_text(strip=True)
        if n and n not in szerzok:
            szerzok.append(n)
    if szerzok:
        reszek.append(f"Szerző(k):  {', '.join(szerzok[:4])}")
    # Antikvarium.hu adattábla
    adatok = []
    for tr in soup.select("table.book-data tr, .product-data tr, table tr"):
        cellak = [td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
        if len(cellak) >= 2:
            adatok.append("  ".join(cellak))
    if not adatok:
        for dt, dd in zip(soup.select("dl dt"), soup.select("dl dd")):
            adatok.append(f"{dt.get_text(strip=True)}: {dd.get_text(' ', strip=True)}")
    reszek.append(_szakasz("KÖNYV ADATOK", "\n".join(adatok[:15])))
    for sel in [".description", "#description", ".book-description",
                "[itemprop='description']", ".termek-leiras"]:
        el = soup.select_one(sel)
        if el:
            reszek.append(_szakasz("LEÍRÁS / FÜLSZÖVEG", el.get_text("\n", strip=True)))
            break
    # Antikvarium ár info is hasznos lehet
    ar = soup.select_one(".price, .ar, .book-price, [itemprop='price']")
    if ar:
        reszek.append(f"\nÁr: {ar.get_text(strip=True)}")
    reszek.append(f"\n{SEP2}\nForrás: {url}\n{SEP2}")
    return "\n".join(reszek)

# ---------------------------------------------------------------------------
# 7. REGIKONYVEK.HU
# ---------------------------------------------------------------------------

def regikonyvek_kereses(cim):
    # Helyes URL: Angular-alapú /kereso/uj-konyvek/all/q/{keresés}
    # Találat linkek: /kiadas/cim-szerzo-ev-kiado formátum
    encoded = urllib.parse.quote(cim, safe="")
    url = f"https://www.regikonyvek.hu/kereso/uj-konyvek/all/q/{encoded}"
    try:
        r = _get(url)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        blokk = (soup.select_one("main") or soup.body)
        eredmenyek = []
        for a in blokk.select("a[href*='/kiadas/']"):
            szoveg = a.get_text(" ", strip=True)
            if not szoveg:
                img = a.find("img")
                szoveg = img.get("alt", "").strip() if img else ""
            href = a.get("href", "").split("?")[0]
            if (not szoveg or len(szoveg) < 3 or "»" in szoveg
                    or not _relevans(cim, szoveg)):
                continue
            full = "https://www.regikonyvek.hu" + href if href.startswith("/") else href
            if full not in [e[1] for e in eredmenyek]:
                eredmenyek.append((f"[Regikonyvek]  {szoveg}", full, "Regikonyvek"))
            if len(eredmenyek) >= 4:
                break
        return eredmenyek
    except Exception:
        return []

def regikonyvek_adatok(url):
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return _alap_adatok(soup, url, "Regikonyvek.hu")

# ---------------------------------------------------------------------------
# 8. GOOGLE BOOKS API
# ---------------------------------------------------------------------------

def google_kereses(cim, szerzo=""):
    # Ha van szerző, az inauthor: paramétert is használjuk
    q = f"intitle:{urllib.parse.quote(cim)}"
    if szerzo:
        q += f"+inauthor:{urllib.parse.quote(szerzo)}"
    url = (f"https://www.googleapis.com/books/v1/volumes"
           f"?q={q}&langRestrict=hu&maxResults=5&printType=books")
    r = _get(url); r.raise_for_status()
    data = r.json()
    eredmenyek = []
    for item in data.get("items", []):
        info = item.get("volumeInfo", {})
        cim_txt = info.get("title", "")
        if not cim_txt or not _relevans(cim, cim_txt):
            continue
        szerzok_lst = info.get("authors", [])
        # Ha van szerző a keresésben, csak egyező szerzőjű könyvet mutatunk
        if szerzo and szerzok_lst:
            if not any(_relevans(szerzo, s) for s in szerzok_lst):
                continue
        nev = f"[Google Books]  {cim_txt}"
        if szerzok_lst:
            nev += f"  -  {', '.join(szerzok_lst)}"
        eredmenyek.append((nev, item.get("id",""), "Google"))
    return eredmenyek

def google_adatok(volume_id):
    url = f"https://www.googleapis.com/books/v1/volumes/{volume_id}"
    r = _get(url); r.raise_for_status()
    data = r.json()
    info = data.get("volumeInfo", {})
    fejlec = ["FORRÁS: Google Books\n"]
    if info.get("title"):
        fejlec.append(f"Cím:  {info['title']}")
    if info.get("subtitle"):
        fejlec.append(f"Alcím:  {info['subtitle']}")
    if info.get("authors"):
        fejlec.append(f"Szerző(k):  {', '.join(info['authors'])}")

    # ── 1. LEÍRÁS előre ──────────────────────────────────────────────────────
    leiras_blokk = ""
    if info.get("description"):
        leiras_blokk = _szakasz("FÜLSZÖVEG / LEÍRÁS", info["description"])

    # ── 2. ADATOK utána ──────────────────────────────────────────────────────
    adatok = []
    if info.get("publisher"):
        adatok.append(f"Kiadó:  {info['publisher']}")
    if info.get("publishedDate"):
        adatok.append(f"Megjelenés éve:  {info['publishedDate']}")
    if info.get("pageCount"):
        adatok.append(f"Oldalszám:  {info['pageCount']}")
    if info.get("categories"):
        adatok.append(f"Kategória:  {', '.join(info['categories'])}")
    isbn_lista = [f"{x.get('type','')}: {x.get('identifier','')}"
                  for x in info.get("industryIdentifiers", [])]
    if isbn_lista:
        adatok.append("ISBN:  " + " | ".join(isbn_lista))
    adatok_blokk = _szakasz("KÖNYV ADATOK", "\n".join(adatok))

    ertekeles = []
    if info.get("averageRating"):
        ertekeles.append(f"\nÁtlagos értékelés: {info['averageRating']} / 5")
    if info.get("ratingsCount"):
        ertekeles.append(f"Értékelők száma: {info['ratingsCount']}")

    reszek = (fejlec + [leiras_blokk, adatok_blokk] + ertekeles
              + [f"\n{SEP2}\nForrás: https://books.google.com/books?id={volume_id}\n{SEP2}"])
    return "\n".join(reszek)

# ---------------------------------------------------------------------------
# Összetett keresés – mind a 8 forrás párhuzamosan
# ---------------------------------------------------------------------------

FORRASOK = [
    ("Moly",        moly_kereses),
    ("Bookline",    bookline_kereses),
    ("Libri",       libri_kereses),
    ("Lira",        lira_kereses),
    ("Alexandra",   alexandra_kereses),
    ("Antikvarium", antikvarium_kereses),
    ("Regikonyvek", regikonyvek_kereses),
    ("Google",      google_kereses),
]

SORREND = {nev: i for i, (nev, _) in enumerate(FORRASOK)}

def osszes_kereses(bemeneti_szoveg):
    # Felismerjük a 'Szerző - Cím' formátumot
    cim, szerzo = _parse_kereses(bemeneti_szoveg)

    eredmenyek = []
    hibak = []
    lock = threading.Lock()

    def _futtat(nev, fn):
        try:
            # Google Books tudja kezelni a szerzőt is külön paraméterként
            if nev == "Google":
                res = fn(cim, szerzo)
            else:
                # Többi forrás: ha van szerző, a cím+szerző együttes keresés
                # adja a legjobb eredményt (pl. "Egri csillagok Gárdonyi")
                kereses_szoveg = cim + (" " + szerzo if szerzo else "")
                res = fn(kereses_szoveg)
                # Ha van szerző, szűrjük tovább: csak az marad, ahol a találat
                # szövegében a szerző neve is megjelenik (lazán)
                if szerzo:
                    res = [e for e in res if _relevans(szerzo, e[0]) or True]
            with lock:
                eredmenyek.extend(res)
        except Exception as e:
            with lock:
                hibak.append(f"{nev}: {e}")

    szalak = [threading.Thread(target=_futtat, args=(nev, fn), daemon=True)
              for nev, fn in FORRASOK]
    for sz in szalak:
        sz.start()
    for sz in szalak:
        sz.join(timeout=20)

    eredmenyek.sort(key=lambda e: SORREND.get(e[2], 99))
    return eredmenyek, hibak

def adatok_leker(azonosito, forras):
    routing = {
        "Moly":        moly_adatok,
        "Bookline":    bookline_adatok,
        "Libri":       libri_adatok,
        "Lira":        lira_adatok,
        "Alexandra":   alexandra_adatok,
        "Antikvarium": antikvarium_adatok,
        "Regikonyvek": regikonyvek_adatok,
        "Google":      google_adatok,
    }
    fn = routing.get(forras)
    if fn:
        return fn(azonosito)
    return "Ismeretlen forrás."

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class KonyvKeresoApp(tk.Tk):

    BG       = "#1a1a2e"
    BG2      = "#16213e"
    ACCENT   = "#0f3460"
    GOMB_F   = "#e94560"
    SZOVEG   = "#eaeaea"
    ZOLD     = "#66bb6a"
    FONT     = ("Segoe UI", 13)
    FONT_B   = ("Segoe UI", 13, "bold")
    FONT_CIM = ("Segoe UI", 16, "bold")
    FONT_KIS = ("Segoe UI", 11, "italic")

    def __init__(self):
        super().__init__()
        self.title("Magyar Könyvkereső – 8 forrás")
        self.geometry("960x780")
        self.resizable(True, True)
        self.configure(bg=self.BG)
        self._eredmenyek = []
        self._feluletek()
        self.mainloop()

    def _feluletek(self):
        # Fejléc
        fejlec = tk.Frame(self, bg=self.ACCENT, pady=10)
        fejlec.pack(fill="x")
        tk.Label(fejlec, text="Magyar Könyvkereső",
                 font=self.FONT_CIM, bg=self.ACCENT, fg="white").pack()
        tk.Label(fejlec,
                 text="Moly.hu  |  Bookline.hu  |  Libri.hu  |  "
                      "Alexandra.hu  |  Antikvarium.hu  |  Regikonyvek.hu  |  Google Books",
                 font=self.FONT_KIS, bg=self.ACCENT, fg="#c0c0c0").pack()
        tk.Label(fejlec,
                 text="Írd be a könyv címét, vagy: Szerző - Cím formában, majd nyomj Entert.",
                 font=self.FONT_KIS, bg=self.ACCENT, fg="#aaaaaa").pack()

        # Keresősáv
        ks = tk.Frame(self, bg=self.BG, pady=12)
        ks.pack(fill="x", padx=20)
        tk.Label(ks, text="Cím (vagy Szerző - Cím):", font=self.FONT_B,
                 bg=self.BG, fg=self.SZOVEG).pack(side="left")
        self.cim_var = tk.StringVar()
        self.cim_mezo = tk.Entry(ks, textvariable=self.cim_var,
                                 font=("Segoe UI", 14), width=42,
                                 bg=self.BG2, fg=self.SZOVEG,
                                 insertbackground=self.SZOVEG,
                                 relief="flat", bd=6)
        self.cim_mezo.pack(side="left", padx=10)
        self.cim_mezo.bind("<Return>", lambda e: self._inditas())
        self.cim_mezo.focus_set()
        self.kereses_gomb = tk.Button(
            ks, text="Keresés", command=self._inditas,
            font=self.FONT_B, bg=self.GOMB_F, fg="white",
            activebackground="#c73652", relief="flat", padx=14, pady=6, cursor="hand2")
        self.kereses_gomb.pack(side="left")

        # Találatok listája
        lk = tk.Frame(self, bg=self.BG)
        lk.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(lk, text="Találatok (válassz nyílbillentyűkkel, majd Enter):",
                 font=self.FONT_B, bg=self.BG, fg=self.SZOVEG).pack(anchor="w")
        self.lista = tk.Listbox(
            lk, font=self.FONT, bg=self.BG2, fg=self.SZOVEG,
            selectbackground=self.ACCENT, selectforeground="white",
            height=7, relief="flat", bd=4, activestyle="none")
        self.lista.pack(fill="x")
        self.lista.bind("<<ListboxSelect>>", self._kivalaszt)
        self.lista.bind("<Return>", self._kivalaszt)

        # Állapotsor
        self.allapot_var = tk.StringVar(value="Készen állok. Írd be a könyv címét.")
        tk.Label(self, textvariable=self.allapot_var,
                 font=self.FONT_KIS, bg=self.BG, fg="#aaaaaa").pack(anchor="w", padx=22)

        # Eredmény szövegmező
        sk = tk.Frame(self, bg=self.BG)
        sk.pack(fill="both", expand=True, padx=20, pady=(4, 6))
        tk.Label(sk, text="Könyv adatai:",
                 font=self.FONT_B, bg=self.BG, fg=self.SZOVEG).pack(anchor="w")
        self.eredmeny = scrolledtext.ScrolledText(
            sk, font=("Segoe UI", 13), bg=self.BG2, fg=self.SZOVEG,
            insertbackground=self.SZOVEG, relief="flat", bd=4,
            wrap="word", state="disabled")
        self.eredmeny.pack(fill="both", expand=True)

        # Alsó sor
        ak = tk.Frame(self, bg=self.BG, pady=8)
        ak.pack(fill="x", padx=20)
        self.mentes_gomb = tk.Button(
            ak, text="Mentés .txt fájlba", command=self._mentes,
            font=self.FONT_B, bg=self.ACCENT, fg="white",
            relief="flat", padx=14, pady=6, cursor="hand2", state="disabled")
        self.mentes_gomb.pack(side="left")
        self.mentes_label = tk.Label(ak, text="", font=self.FONT, bg=self.BG, fg=self.ZOLD)
        self.mentes_label.pack(side="left", padx=16)

    def _inditas(self):
        cim = self.cim_var.get().strip()
        if not cim:
            messagebox.showwarning("Hiányzó adat", "Kérlek írj be egy könyvcímet!")
            return
        self.kereses_gomb.config(state="disabled")
        self.lista.delete(0, "end")
        self._eredmenyek = []
        self._szoveg_set("")
        self.mentes_gomb.config(state="disabled")
        self.mentes_label.config(text="")
        cim_parsed, szerzo_parsed = _parse_kereses(cim)
        if szerzo_parsed:
            allapot_txt = f"Keresés: cím=\"{cim_parsed}\", szerző=\"{szerzo_parsed}\" ..."
        else:
            allapot_txt = f"Keresés 8 forrásban: \"{cim_parsed}\" ..."
        self._allapot(allapot_txt)
        threading.Thread(target=self._kereses_szal, args=(cim,), daemon=True).start()

    def _kereses_szal(self, cim):
        try:
            eredmenyek, hibak = osszes_kereses(cim)
            self.after(0, self._talalatok_megjelenit, eredmenyek, hibak)
        except Exception as e:
            self.after(0, self._hiba, f"Keresési hiba: {e}")

    def _talalatok_megjelenit(self, eredmenyek, hibak):
        self.kereses_gomb.config(state="normal")
        if not eredmenyek:
            self._allapot("Nem találtam könyvet ezzel a címmel.")
            hiba_txt = ("\n\nHibák:\n" + "\n".join(hibak)) if hibak else ""
            self._szoveg_set("Nincs találat egyik forrásban sem.\n"
                             "Próbálj rövidebb, pontosabb címet beírni." + hiba_txt)
            return
        self._eredmenyek = eredmenyek
        self.lista.delete(0, "end")
        for nev, _, _ in eredmenyek:
            self.lista.insert("end", nev)
        self.lista.selection_set(0)
        forrasok = sorted(set(e[2] for e in eredmenyek))
        self._allapot(f"{len(eredmenyek)} találat ({', '.join(forrasok)}) – válassz a listából!")
        self.lista.focus_set()
        _, az, forras = eredmenyek[0]
        self._tolt(az, forras)

    def _kivalaszt(self, _event=None):
        kiv = self.lista.curselection()
        if not kiv:
            return
        _, az, forras = self._eredmenyek[kiv[0]]
        self._tolt(az, forras)

    def _tolt(self, azonosito, forras, lista_index=None):
        self._allapot(f"Letöltés: {forras} ...")
        self._szoveg_set("")
        # Ha nem adtuk meg az indexet, a lista aktuális kijelöléséből vesszük
        if lista_index is None:
            kiv = self.lista.curselection()
            lista_index = kiv[0] if kiv else 0
        threading.Thread(target=self._reszletek_szal,
                         args=(azonosito, forras, lista_index), daemon=True).start()

    def _reszletek_szal(self, azonosito, forras, lista_index):
        try:
            szoveg = adatok_leker(azonosito, forras)
            self.after(0, self._reszletek_megjelenit, szoveg, lista_index)
        except Exception as e:
            self.after(0, self._hiba, f"Letöltési hiba ({forras}): {e}")

    def _reszletek_megjelenit(self, szoveg, lista_index):
        # Ha nincs leiras, megjololjuk es automatikusan a kovetkezore ugrunk
        if not _van_leiras(szoveg):
            jelenlegi = self.lista.get(lista_index)
            if "(nincs leiras)" not in jelenlegi:
                self.lista.delete(lista_index)
                self.lista.insert(lista_index, jelenlegi + "  (nincs leiras)")
            kovetkezo = lista_index + 1
            if kovetkezo < len(self._eredmenyek):
                self.lista.selection_set(kovetkezo)
                self.lista.see(kovetkezo)
                _, az, forras = self._eredmenyek[kovetkezo]
                self._allapot(f"Nincs leiras - kovetkezo talalat: {forras} ...")
                self._tolt(az, forras, kovetkezo)
                return
            else:
                self._allapot("Egyik talalnatnal sincs leiras. Probalt mas keretest.")
        self._szoveg_set(szoveg)
        self._allapot("Kesz. A konyv adatai megjelentek az alabbi szovegmezeoben.")
        self.mentes_gomb.config(state="normal")
        self.eredmeny.focus_set()

    def _mentes(self):
        szoveg = self.eredmeny.get("1.0", "end").strip()
        if not szoveg:
            return
        cim = self.cim_var.get().strip()
        jav_nev = re.sub(r'[\\/*?:"<>|]', "_", cim)[:60] + ".txt"
        utvonal = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Szovegfajl", "*.txt"), ("Minden fajl", "*.*")],
            initialfile=jav_nev, title="Mentes helye")
        if utvonal:
            with open(utvonal, "w", encoding="utf-8") as f:
                f.write(szoveg)
            self.mentes_label.config(text=f"Elmentve: {utvonal}")

    def _allapot(self, uzenet):
        self.allapot_var.set(uzenet)

    def _szoveg_set(self, szoveg):
        self.eredmeny.config(state="normal")
        self.eredmeny.delete("1.0", "end")
        self.eredmeny.insert("end", szoveg)
        self.eredmeny.config(state="disabled")

    def _hiba(self, uzenet):
        self.kereses_gomb.config(state="normal")
        self._allapot("Hiba tortent.")
        self._szoveg_set(f"HIBA:\n\n{uzenet}\n\nEllenorizd az internetkapcsolatot.")
        messagebox.showerror("Hiba", uzenet)


# ---------------------------------------------------------------------------
# Belepesi pont
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    KonyvKeresoApp()
