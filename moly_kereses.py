# -*- coding: utf-8 -*-
"""
konyv_kereses.py  -  Magyar könyvinfo lekérdező
Források: Moly.hu | Bookline.hu | Libri.hu | Lira.hu | Alexandra.hu
          Antikvarium.hu | Regikonyvek.hu | Google Books

Vak felhasználóknak optimalizált, képernyőolvasóval kompatibilis ablak.
TTS hangos felolvasás (pyttsx3) – ki/bekapcsolható checkbox-szal.
Szükséges: pip install requests beautifulsoup4
TTS-hez (opcionális): pip install pyttsx3
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import threading
import urllib.parse
import re
import textwrap
import queue as _queue
import json
import os

# ---------------------------------------------------------------------------
# Hang-konfiguráció – elmenti / betölti a kiválasztott TTS hang ID-ját
# ---------------------------------------------------------------------------

_CONFIG_FAJL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "konyv_kerso_config.json")

def _config_ment(hang_id: str):
    """Elmenti a kiválasztott hang azonosítóját JSON fájlba."""
    try:
        with open(_CONFIG_FAJL, "w", encoding="utf-8") as f:
            json.dump({"hang_id": hang_id}, f, ensure_ascii=False)
    except Exception:
        pass

def _config_tolt() -> str | None:
    """Visszaadja a mentett hang ID-ját, vagy None-t ha nincs."""
    try:
        with open(_CONFIG_FAJL, encoding="utf-8") as f:
            return json.load(f).get("hang_id")
    except Exception:
        return None

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
# TTS – hangos felolvasás (pyttsx3), dedikált háttérszálon fut
# ---------------------------------------------------------------------------

class _TTS:
    """Thread-safe TTS kezelő. Ha a pyttsx3 nincs telepítve, csendben marad."""

    def __init__(self):
        self._q = _queue.Queue()
        self._aktiv = False          # alapból KI van kapcsolva
        self._motor = None
        self._gui_allapot_fn = None  # GUI visszahívó – GUI állítja be
        self._szal = threading.Thread(target=self._fut, daemon=True)
        self._szal.start()

    def _motor_init(self):
        """Csak betölti a pyttsx3-t – NEM telepít automatikusan.
        Prioritás: 1) mentett hang  2) automatikus magyar hang  3) rendszer alapértelmezett."""
        try:
            import pyttsx3
        except ImportError:
            return None
        try:
            m = pyttsx3.init()
            m.setProperty("rate", 165)
            hangok = m.getProperty("voices")

            # 1. Mentett hang visszatöltése
            mentett_id = _config_tolt()
            if mentett_id:
                for v in hangok:
                    if v.id == mentett_id:
                        m.setProperty("voice", v.id)
                        return m

            # 2. Magyar hang automatikus keresése (ha nincs mentett)
            for v in hangok:
                lang = ""
                if v.languages:
                    lang = v.languages[0] if isinstance(v.languages[0], str) else ""
                if "hu" in lang.lower() or "hungarian" in v.name.lower():
                    m.setProperty("voice", v.id)
                    _config_ment(v.id)   # első indításkor elmentjük
                    break

            return m
        except Exception:
            return None

    def _allapot_cb(self, szoveg):
        """Háttérszálból hívható állapotsor-frissítés (ha van GUI)."""
        try:
            if hasattr(self, '_gui_allapot_fn') and self._gui_allapot_fn:
                self._gui_allapot_fn(szoveg)
        except Exception:
            pass

    def _fut(self):
        self._motor = self._motor_init()
        while True:
            szoveg = self._q.get()
            if szoveg is None:          # leállítás jele
                break
            if not self._aktiv or self._motor is None:
                continue
            try:
                self._motor.say(szoveg)
                self._motor.runAndWait()
            except Exception:
                pass

    def beszel(self, szoveg):
        """Kimondja a szöveget – megszakítja az előző felolvasást ha fut."""
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except _queue.Empty:
                break
        # Aktuálisan futó felolvasás leállítása
        if self._motor and self._aktiv:
            try:
                self._motor.stop()
            except Exception:
                pass
        self._q.put(szoveg)

    def be(self):
        self._aktiv = True

    def ki(self):
        self._aktiv = False
        if self._motor:
            try:
                self._motor.stop()
            except Exception:
                pass

    @property
    def elerheto(self):
        return self._motor is not None


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
    for elv in [" - ", " – ", " : ", " / "]:
        if elv in szoveg:
            bal, jobb = szoveg.split(elv, 1)
            bal, jobb = bal.strip(), jobb.strip()
            if bal and jobb:
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
    szoveg = a.get_text(" ", strip=True)
    if szoveg and len(szoveg) >= 3 and "»" not in szoveg:
        return szoveg
    t = a.get("title", "").strip()
    if t and len(t) >= 3:
        return t
    img = a.find("img")
    if img:
        alt = img.get("alt", "").strip()
        if alt and len(alt) >= 3:
            return alt
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
        href = a.get("href", "")
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
    url = f"https://www.libri.hu/talalatok/?text={urllib.parse.quote_plus(cim)}"
    r = _get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    blokk = (soup.select_one(".search-results") or soup.select_one(".product-list")
             or soup.select_one("main") or soup.body)
    eredmenyek = []
    for a in blokk.select("a[href*='/konyv/']"):
        href = a.get("href", "").split("?")[0]
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
    ar = soup.select_one(".price, .ar, .book-price, [itemprop='price']")
    if ar:
        reszek.append(f"\nÁr: {ar.get_text(strip=True)}")
    reszek.append(f"\n{SEP2}\nForrás: {url}\n{SEP2}")
    return "\n".join(reszek)

# ---------------------------------------------------------------------------
# 7. REGIKONYVEK.HU
# ---------------------------------------------------------------------------

def regikonyvek_kereses(cim):
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

    leiras_blokk = ""
    if info.get("description"):
        leiras_blokk = _szakasz("FÜLSZÖVEG / LEÍRÁS", info["description"])

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
    cim, szerzo = _parse_kereses(bemeneti_szoveg)

    eredmenyek = []
    hibak = []
    lock = threading.Lock()

    def _futtat(nev, fn):
        try:
            if nev == "Google":
                res = fn(cim, szerzo)
            else:
                kereses_szoveg = cim + (" " + szerzo if szerzo else "")
                res = fn(kereses_szoveg)
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
        self.geometry("980x900")
        self.resizable(True, True)
        self.configure(bg=self.BG)
        self._eredmenyek = []

        # TTS inicializálás – a callback ELŐBB kerül be, mint a szál elindul
        self._tts = _TTS()
        self._hang_var = tk.BooleanVar(value=False)   # alapból KI
        # A _TTS __init__ már létrehozta a _gui_allapot_fn=None attribútumot;
        # most felülírjuk az igazi callbackkel (a szál még az init szakaszban van)
        self._tts._gui_allapot_fn = lambda s: self.after(0, self._allapot, s)

        self._feluletek()
        self._billentyu_parancsok()
        self.mainloop()

    # -----------------------------------------------------------------------
    # Billentyűparancsok
    # -----------------------------------------------------------------------

    def _billentyu_parancsok(self):
        """Globális billentyűkötések – bármely widgeten működnek."""
        self.bind("<F5>",         lambda e: (self.cim_mezo.focus_set(),
                                             self._tts_beszel("Keresőmező")))
        self.bind("<F6>",         lambda e: (self.lista.focus_set(),
                                             self._tts_beszel("Találatok listája")))
        self.bind("<F7>",         lambda e: self._eredmeny_fokusz())
        self.bind("<Control-s>",  lambda e: self._mentes())
        self.bind("<F1>",         lambda e: self._sugo())
        self.bind("<Escape>",     lambda e: (self.cim_mezo.focus_set(),
                                             self._tts_beszel("Keresőmező")))
        self.bind("<Control-h>",  lambda e: self._pyttsx3_telepites())
        # Listából Enter-rel töltés
        self.lista.bind("<Return>", self._kivalaszt)
        # TTS bejelentés lista navigációnál (nyílbillentyűk)
        self.lista.bind("<<ListboxSelect>>", self._lista_valtozas)

        # A Text widget elnyeli a billentyűket – F-kötések közvetlenül rá is
        for seq, fn in [
            ("<F5>",        lambda e: (self.cim_mezo.focus_set(),
                                       self._tts_beszel("Keresőmező"))),
            ("<F6>",        lambda e: (self.lista.focus_set(),
                                       self._tts_beszel("Találatok listája"))),
            ("<F7>",        lambda e: self._eredmeny_fokusz()),
            ("<Escape>",    lambda e: (self.cim_mezo.focus_set(),
                                       self._tts_beszel("Keresőmező"))),
            ("<F1>",        lambda e: self._sugo()),
            ("<Control-s>", lambda e: self._mentes()),
            ("<Control-h>", lambda e: self._pyttsx3_telepites()),
        ]:
            self.eredmeny.bind(seq, fn)
            self.lista.bind(seq, fn)

    # -----------------------------------------------------------------------
    # TTS segédek
    # -----------------------------------------------------------------------

    def _tts_beszel(self, szoveg):
        """TTS bejelentés, ha be van kapcsolva."""
        if self._hang_var.get():
            self._tts.beszel(szoveg)

    def _hang_valt(self):
        """Checkbox callback: TTS be/ki kapcsolása."""
        if self._hang_var.get():
            self._tts.be()
            reszek = ["Hangos felolvasas bekapcsolva."]
            cim = self.cim_var.get().strip()
            if cim:
                reszek.append(f"Kereszomezzoben: {cim}.")
            if self._eredmenyek:
                kiv = self.lista.curselection()
                idx = kiv[0] if kiv else 0
                reszek.append(
                    f"{len(self._eredmenyek)} talalat. "
                    f"Kivalolve: {self._eredmenyek[idx][0]}.")
                reszek.append(
                    "Nyilbillentyukkel navigalj a listaban, "
                    "Enter betolti a konyvet.")
            else:
                reszek.append(
                    "Nincs keresesi eredmeny. "
                    "Ird be a konyv cimet, majd nyomj Entert.")
            reszek.append(
                "F5 kereszomezo, F6 talalatok, F7 konyv adatai, F1 sugo.")
            self._tts_beszel(" ".join(reszek))
        else:
            self._tts.ki()

    def _magyar_hang_telepites(self):
        """Windows Speech beállítások megnyitása magyar hang telepítéséhez."""
        import subprocess, os
        szoveg = (
            "Magyar hang telepítése Windows rendszeren:\n\n"
            "1. Nyisd meg: Beállítások → Idő és nyelv → Beszéd\n"
            "2. A 'Preferált hangok kezelése' részben kattints\n"
            "   a 'Hang hozzáadása' gombra\n"
            "3. Keress rá: Magyar (Magyarország)\n"
            "4. Telepítsd a 'Microsoft Zita' hangot\n"
            "5. Indítsd újra a könyvkeresőt\n\n"
            "Megnyissuk a Windows Beszéd beállításokat most?"
        )
        if messagebox.askyesno("Magyar hang telepítése", szoveg):
            try:
                subprocess.Popen("start ms-settings:speech", shell=True)
            except Exception:
                try:
                    os.system("control /name Microsoft.Speech")
                except Exception:
                    messagebox.showinfo(
                        "Kézi megnyitás",
                        "Nyisd meg manuálisan:\n"
                        "Beállítások → Idő és nyelv → Beszéd")

    def _hang_valaszto(self):
        if not self._tts.elerheto:
            messagebox.showwarning(
                "TTS nem elerheto",
                "A pyttsx3 nincs inicializalva. Ctrl+H a telepiteshez.")
            return
        try:
            import pyttsx3
            m = pyttsx3.init()
            hangok = m.getProperty("voices")
        except Exception as e:
            messagebox.showerror("Hiba", str(e))
            return
        if not hangok:
            messagebox.showinfo("Hangok", "Nem talalhato TTS hang.")
            return

        # ── Segédfüggvény: hang nyelvének meghatározása ──────────────────────
        def _hang_nyelv(v):
            lang = ""
            if v.languages:
                lang = (v.languages[0] if isinstance(v.languages[0], str)
                        else str(v.languages[0]))
            return lang

        def _magyar_e(v):
            lang = _hang_nyelv(v).lower()
            return "hu" in lang or "hungarian" in v.name.lower()

        dlg = tk.Toplevel(self)
        dlg.title("Hang kiválasztása")
        dlg.configure(bg=self.BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        # ── Fejléc ────────────────────────────────────────────────────────────
        tk.Label(dlg, text="Válassz TTS hangot:",
                 font=self.FONT_B, bg=self.BG, fg=self.SZOVEG).pack(
                 padx=20, pady=(16, 4), anchor="w")

        # ── Nyelvszűrő gombok ─────────────────────────────────────────────────
        szuro_sor = tk.Frame(dlg, bg=self.BG)
        szuro_sor.pack(padx=20, pady=(0, 6), anchor="w")
        tk.Label(szuro_sor, text="Szűrő:", font=("Segoe UI", 10),
                 bg=self.BG, fg="#aaaaaa").pack(side="left", padx=(0, 8))

        _szuro_var = tk.StringVar(value="mind")

        def _szurt_hangok():
            szuro = _szuro_var.get()
            if szuro == "magyar":
                return [v for v in hangok if _magyar_e(v)]
            elif szuro == "egyeb":
                return [v for v in hangok if not _magyar_e(v)]
            return hangok

        def _lista_frissit(*_):
            lb.delete(0, "end")
            for v in _szurt_hangok():
                lang = _hang_nyelv(v)
                prefix = "★ " if _magyar_e(v) else "   "
                lb.insert("end", f"{prefix}{v.name}  [{lang}]")
                if _magyar_e(v):
                    lb.itemconfig("end", fg="#ffe082")   # arany szín a magyar hangoknak
            # jelenlegi hang kijelölése
            jelenlegi = (self._tts._motor.getProperty("voice")
                         if self._tts._motor else None)
            for i, v in enumerate(_szurt_hangok()):
                if v.id == jelenlegi:
                    lb.selection_set(i); lb.see(i); break
            else:
                if lb.size():
                    lb.selection_set(0)

        for ertek, felirat in [("mind", "Mind"), ("magyar", "★ Magyar"), ("egyeb", "Egyéb")]:
            tk.Radiobutton(
                szuro_sor, text=felirat, variable=_szuro_var, value=ertek,
                command=_lista_frissit,
                font=("Segoe UI", 10), bg=self.BG, fg=self.SZOVEG,
                selectcolor=self.BG2, activebackground=self.BG,
                activeforeground=self.SZOVEG
            ).pack(side="left", padx=4)

        # ── Listbox ───────────────────────────────────────────────────────────
        lb = tk.Listbox(dlg, font=self.FONT, bg=self.BG2, fg=self.SZOVEG,
                        selectbackground=self.ACCENT, selectforeground="white",
                        height=min(len(hangok), 10),
                        width=62, relief="flat", bd=4)
        lb.pack(padx=20, pady=4)

        # Ha van magyar hang, alapból arra szűrünk
        if any(_magyar_e(v) for v in hangok):
            _szuro_var.set("magyar")
        _lista_frissit()

        # ── Info label ────────────────────────────────────────────────────────
        info_lbl = tk.Label(dlg, text="★ = Magyar hang  |  arany szín = ajánlott",
                            font=("Segoe UI", 9, "italic"),
                            bg=self.BG, fg="#aaaaaa")
        info_lbl.pack(padx=20, anchor="w")

        def _alkalmaz():
            kiv = lb.curselection()
            if not kiv:
                return
            v = _szurt_hangok()[kiv[0]]
            try:
                self._tts._motor.setProperty("voice", v.id)
                _config_ment(v.id)          # ← elmentjük a választást
                dlg.destroy()
                self._tts_beszel(
                    f"Ez a hang: {v.name}. Ha megfelelo, hasznald igy.")
            except Exception as e:
                messagebox.showerror("Hiba", str(e))

        gs = tk.Frame(dlg, bg=self.BG)
        gs.pack(pady=12)
        tk.Button(gs, text="Alkalmaz + Teszt", command=_alkalmaz,
                  font=self.FONT_B, bg=self.GOMB_F, fg="white",
                  relief="flat", padx=12, pady=6).pack(side="left", padx=8)
        tk.Button(gs, text="Mégse", command=dlg.destroy,
                  font=self.FONT, bg=self.BG2, fg=self.SZOVEG,
                  relief="flat", padx=12, pady=6).pack(side="left")
        lb.bind("<Return>", lambda e: _alkalmaz())
        lb.focus_set()

    # -----------------------------------------------------------------------
    # Sugo ablak
    # -----------------------------------------------------------------------

    def _sugo(self):
        szoveg = (
            "BILLENTYŰPARANCSOK\n\n"
            "F5           –  Keresőmező fókusz\n"
            "F6           –  Találatok listája\n"
            "F7           –  Könyv adatai szövegmező\n"
            "Ctrl + S     –  Mentés .txt fájlba\n"
            "Escape       –  Vissza a keresőmezőbe\n"
            "F1           –  Ez a súgó\n\n"
            "KERESÉS\n\n"
            "Írd be a könyv címét (pl.: Egri csillagok),\n"
            "vagy: Szerző - Cím formában\n"
            "(pl.: Gárdonyi Géza - Egri csillagok),\n"
            "majd nyomj Entert, vagy kattints a Keresés gombra.\n\n"
            "TALÁLATOK\n\n"
            "A listából nyílbillentyűkkel választhatsz,\n"
            "Enter betölti a kijelölt könyvet.\n\n"
            "HANGOS FELOLVASÁS\n\n"
            "Ctrl + H   –  pyttsx3 telepítése (képernyőolvasóval is!)\n"
            "           A párbeszédablakokat az NVDA / JAWS / Narrator\n"
            "           automatikusan felolvassa.\n\n"
            "A 'Hangos felolvasás' checkbox bekapcsolható,\n"
            "ha a pyttsx3 telepítve van."
        )
        self._tts_beszel("Súgó megnyílt. " + szoveg.replace("\n", " "))
        messagebox.showinfo("Súgó – Billentyűparancsok", szoveg)

    # -----------------------------------------------------------------------
    # Felületek felépítése
    # -----------------------------------------------------------------------

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
                 text="Írd be a könyv címét, vagy: Szerző - Cím formában, majd nyomj Entert.  "
                      "  F1 = Súgó",
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
                                 relief="flat", bd=6,
                                 highlightthickness=2,
                                 highlightcolor=self.GOMB_F,
                                 highlightbackground=self.BG2)
        self.cim_mezo.pack(side="left", padx=10)
        self.cim_mezo.bind("<Return>", lambda e: self._inditas())
        self.cim_mezo.focus_set()
        self.kereses_gomb = tk.Button(
            ks, text="Keresés  [Enter]", command=self._inditas,
            font=self.FONT_B, bg=self.GOMB_F, fg="white",
            activebackground="#c73652", relief="flat", padx=14, pady=6, cursor="hand2")
        self.kereses_gomb.pack(side="left")

        # Találatok listája
        lk = tk.Frame(self, bg=self.BG)
        lk.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(lk, text="Találatok – F6 fókusz, nyílbillentyűk, Enter betölt:",
                 font=self.FONT_B, bg=self.BG, fg=self.SZOVEG).pack(anchor="w")
        self.lista = tk.Listbox(
            lk, font=self.FONT, bg=self.BG2, fg=self.SZOVEG,
            selectbackground=self.ACCENT, selectforeground="white",
            height=5, relief="flat", bd=4, activestyle="none",
            highlightthickness=2,
            highlightcolor=self.GOMB_F,
            highlightbackground=self.BG2)
        self.lista.pack(fill="x")
        self.lista.bind("<<ListboxSelect>>", self._kivalaszt)

        # Állapotsor
        self.allapot_var = tk.StringVar(value="Készen állok. Írd be a könyv címét.")
        tk.Label(self, textvariable=self.allapot_var,
                 font=self.FONT_KIS, bg=self.BG, fg="#aaaaaa").pack(anchor="w", padx=22)


        # ── TTS kapcsoló sáv ─────────────────────────────────────────────────
        tts_sor = tk.Frame(self, bg="#0d2240", pady=6)
        tts_sor.pack(fill="x", padx=20, pady=(0, 6))
        # Gomb ELŐBB pack-elve (side=right), hogy ne szoruljon ki
        tk.Button(
            tts_sor, text="Hang kivalasztasa", command=self._hang_valaszto,
            font=("Segoe UI", 10), bg=self.ACCENT, fg="white",
            relief="flat", padx=8, pady=3, cursor="hand2"
        ).pack(side="right", padx=8)
        tk.Button(
            tts_sor, text="Magyar hang telepitese...",
            command=self._magyar_hang_telepites,
            font=("Segoe UI", 10), bg="#2e7d32", fg="white",
            relief="flat", padx=8, pady=3, cursor="hand2"
        ).pack(side="right", padx=4)
        self.hang_cb = tk.Checkbutton(
            tts_sor,
            text="🔊  Hangos felolvasas",
            variable=self._hang_var,
            command=self._hang_valt,
            font=self.FONT_B, bg="#0d2240", fg="#ffe082",
            selectcolor=self.BG2,
            activebackground="#0d2240",
            activeforeground="#ffe082",
            cursor="hand2",
            highlightthickness=2, highlightcolor=self.GOMB_F,
            highlightbackground="#0d2240")
        self.hang_cb.pack(side="left", padx=10)
        self.tts_info = tk.Label(
            tts_sor, text="", font=("Segoe UI", 10, "italic"),
            bg="#0d2240", fg="#aaaaaa")
        self.tts_info.pack(side="left", padx=6)
        self.after(500, self._tts_info_frissit)

        # Eredmény szövegmező
        sk = tk.Frame(self, bg=self.BG)
        sk.pack(fill="both", expand=True, padx=20, pady=(4, 6))
        tk.Label(sk, text="Könyv adatai – F7 fókusz:",
                 font=self.FONT_B, bg=self.BG, fg=self.SZOVEG).pack(anchor="w")
        self.eredmeny = scrolledtext.ScrolledText(
            sk, font=("Segoe UI", 13), bg=self.BG2, fg=self.SZOVEG,
            insertbackground=self.SZOVEG, relief="flat", bd=4,
            wrap="word", state="disabled", takefocus=1,
            highlightthickness=2,
            highlightcolor=self.GOMB_F,
            highlightbackground=self.BG2)
        self.eredmeny.pack(fill="both", expand=True)
        # Szövegmező nem szerkeszthető, de görgethető és fókuszálható
        self.eredmeny.bind("<Key>", lambda e: "break"
                           if e.keysym not in ("Up","Down","Left","Right",
                                               "Prior","Next","Home","End") else None)

        # ── Alsó sor 1: Mentés + Súgó ─────────────────────────────────────────
        ak = tk.Frame(self, bg=self.BG, pady=4)
        ak.pack(fill="x", padx=20)

        self.mentes_gomb = tk.Button(
            ak, text="Mentés .txt fájlba  [Ctrl+S]", command=self._mentes,
            font=self.FONT_B, bg=self.ACCENT, fg="white",
            relief="flat", padx=14, pady=6, cursor="hand2", state="disabled",
            highlightthickness=2, highlightcolor=self.GOMB_F,
            highlightbackground=self.BG)
        self.mentes_gomb.pack(side="left")

        self.mentes_label = tk.Label(ak, text="", font=self.FONT, bg=self.BG, fg=self.ZOLD)
        self.mentes_label.pack(side="left", padx=16)

        tk.Button(
            ak, text="Súgó  [F1]", command=self._sugo,
            font=self.FONT, bg=self.BG2, fg=self.SZOVEG,
            relief="flat", padx=10, pady=6, cursor="hand2",
            highlightthickness=2, highlightcolor=self.GOMB_F,
            highlightbackground=self.BG
        ).pack(side="right", padx=(0, 4))

    # -----------------------------------------------------------------------
    # TTS info label frissítése (motor init után)
    # -----------------------------------------------------------------------

    def _tts_info_frissit(self):
        """Megnézi van-e TTS motor, frissíti az info labelt."""
        if self._tts.elerheto:
            self.tts_info.config(
                text="✓ TTS motor kész – kapcsold be a checkboxot",
                fg="#66bb6a")
            self.hang_cb.config(state="normal")
        else:
            self.tts_info.config(
                text="  pyttsx3 nincs telepítve  –  Ctrl+H a telepítéshez"
                     "  (képernyőolvasóval is működik)",
                fg="#aaaaaa")
            self.hang_cb.config(state="disabled")

    def _pyttsx3_telepites(self):
        """Ctrl+H: pyttsx3 telepítése messagebox-okon át.
        Az NVDA/JAWS/Narrator automatikusan felolvassa a párbeszédablakokat –
        vak felhasználó is végig tudja csinálni."""
        if self._tts.elerheto:
            messagebox.showinfo(
                "Hangos felolvasás",
                "A hangos felolvasás (pyttsx3) már telepítve van és kész.\n\n"
                "Kapcsold be a 'Hangos felolvasás' checkboxot a használathoz.")
            return
        valasz = messagebox.askyesno(
            "Hangos felolvasás telepítése  [Ctrl+H]",
            "A hangos felolvasáshoz szükséges a pyttsx3 csomag.\n\n"
            "Telepítsük most? (internetkapcsolat szükséges)\n\n"
            "A telepítés kb. 10–30 másodpercet vesz igénybe.\n\n"
            "Igen = telepítés  |  Nem = mégse")
        if not valasz:
            return
        self._allapot("pyttsx3 telepítése folyamatban… kérlek várj.")
        self.update_idletasks()

        def _install():
            import subprocess, sys
            ret = subprocess.run(
                [sys.executable, "-m", "pip", "install", "pyttsx3"],
                capture_output=True, text=True)
            self.after(0, _install_kesz, ret)

        def _install_kesz(ret):
            if ret.returncode == 0:
                self._tts._motor = self._tts._motor_init()
                if self._tts.elerheto:
                    self._tts_info_frissit()
                    messagebox.showinfo(
                        "Telepítés kész",
                        "A hangos felolvasás (pyttsx3) sikeresen települt!\n\n"
                        "Kapcsold be a 'Hangos felolvasás' checkboxot,\n"
                        "és máris hallhatod a bejelentéseket.")
                    self._allapot("pyttsx3 telepítve – TTS kész!")
                else:
                    messagebox.showerror(
                        "Hiba",
                        "A pyttsx3 települt, de inicializálása sikertelen.\n"
                        "Próbáld meg újraindítani a programot.")
            else:
                hiba = ret.stderr.strip()[-300:] if ret.stderr else "ismeretlen hiba"
                messagebox.showerror(
                    "Telepítési hiba",
                    f"A pyttsx3 telepítése sikertelen.\n\nHiba:\n{hiba}\n\n"
                    "Próbáld manuálisan: pip install pyttsx3")
                self._allapot("pyttsx3 telepítés sikertelen.")

        threading.Thread(target=_install, daemon=True).start()

    def _eredmeny_fokusz(self):
        """F7: fókuszt ad a szövegmezőnek, disabled állapotban is működik."""
        self.eredmeny.config(state="normal")
        self.eredmeny.focus_set()
        self.eredmeny.config(state="disabled")
        self._tts_beszel("Könyv adatai szövegmező")

    def _lista_valtozas(self, event=None):
        """Nyílbillentyűs navigációnál mondja be az aktuális elemet – NE töltsön be."""
        # Ezt csak "KeyRelease" típusú eseményre kellene, de a Listbox nem ad ilyet.
        # A <<ListboxSelect>> mind kattintásra, mind nyílra tüzel.
        # Betöltést a _kivalaszt végzi – itt csak TTS bejelentés.
        kiv = self.lista.curselection()
        if not kiv or not self._eredmenyek:
            return
        idx = kiv[0]
        if idx < len(self._eredmenyek):
            nev = self._eredmenyek[idx][0]
            self._tts_beszel(nev)

    # -----------------------------------------------------------------------
    # Keresés indítása
    # -----------------------------------------------------------------------

    def _inditas(self):
        cim = self.cim_var.get().strip()
        if not cim:
            messagebox.showwarning("Hiányzó adat", "Kérlek írj be egy könyvcímet!")
            self._tts_beszel("Figyelmeztetés: kérlek írj be egy könyvcímet!")
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
            tts_txt = f"Keresés indítva. Cím: {cim_parsed}. Szerző: {szerzo_parsed}."
        else:
            allapot_txt = f"Keresés 8 forrásban: \"{cim_parsed}\" ..."
            tts_txt = f"Keresés indítva: {cim_parsed}."
        self._allapot(allapot_txt)
        self._tts_beszel(tts_txt)
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
            allapot = "Nem találtam könyvet ezzel a címmel."
            self._allapot(allapot)
            self._tts_beszel("Nincs találat. " + allapot)
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
        allapot = (f"{len(eredmenyek)} találat ({', '.join(forrasok)}) "
                   f"– válassz a listából!")
        self._allapot(allapot)
        self._tts_beszel(
            f"{len(eredmenyek)} találat a következő forrásokban: "
            f"{', '.join(forrasok)}. Fókusz a listán, nyílbillentyűkkel válassz, "
            f"Enter betölt.")
        self.lista.focus_set()
        _, az, forras = eredmenyek[0]
        self._tolt(az, forras)

    # -----------------------------------------------------------------------
    # Listából kiválasztás (kattintás vagy Enter)
    # -----------------------------------------------------------------------

    def _kivalaszt(self, _event=None):
        kiv = self.lista.curselection()
        if not kiv:
            return
        _, az, forras = self._eredmenyek[kiv[0]]
        self._tolt(az, forras)

    # -----------------------------------------------------------------------
    # Könyv adatainak betöltése
    # -----------------------------------------------------------------------

    def _tolt(self, azonosito, forras, lista_index=None):
        self._allapot(f"Letöltés: {forras} ...")
        self._tts_beszel(f"Betöltés: {forras}.")
        self._szoveg_set("")
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
        if not _van_leiras(szoveg):
            jelenlegi = self.lista.get(lista_index)
            if "(nincs leírás)" not in jelenlegi:
                self.lista.delete(lista_index)
                self.lista.insert(lista_index, jelenlegi + "  (nincs leírás)")
            kovetkezo = lista_index + 1
            if kovetkezo < len(self._eredmenyek):
                self.lista.selection_set(kovetkezo)
                self.lista.see(kovetkezo)
                _, az, forras = self._eredmenyek[kovetkezo]
                msg = f"Nincs leírás – következő találat: {forras} ..."
                self._allapot(msg)
                self._tts_beszel(f"Ennél a találatnál nincs leírás. Következő: {forras}.")
                self._tolt(az, forras, kovetkezo)
                return
            else:
                msg = "Egyik találatnál sincs leírás. Próbálj más keresést."
                self._allapot(msg)
                self._tts_beszel(msg)

        self._szoveg_set(szoveg)

        # TTS: könyv cím és szerző bejelentése
        elso_sorok = szoveg.split("\n")[:6]
        cim_sor = next((s for s in elso_sorok if s.startswith("Cím:")), "")
        szerzo_sor = next((s for s in elso_sorok if s.startswith("Szerző")), "")
        bejelentes = "Könyv betöltve."
        if cim_sor:
            bejelentes += " " + cim_sor
        if szerzo_sor:
            bejelentes += ". " + szerzo_sor
        bejelentes += ". A könyv adatai megjelentek a szövegmezőben. F7 a szövegmezőre."
        self._allapot("Kész. A könyv adatai megjelentek az alábbi szövegmezőben.")
        self._tts_beszel(bejelentes)
        self.mentes_gomb.config(state="normal")
        self._eredmeny_fokusz()

    # -----------------------------------------------------------------------
    # Mentés
    # -----------------------------------------------------------------------

    def _mentes(self):
        szoveg = self.eredmeny.get("1.0", "end").strip()
        if not szoveg:
            self._tts_beszel("Nincs mentendő szöveg.")
            return
        cim = self.cim_var.get().strip()
        jav_nev = re.sub(r'[\\/*?:"<>|]', "_", cim)[:60] + ".txt"
        utvonal = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Szövegfájl", "*.txt"), ("Minden fájl", "*.*")],
            initialfile=jav_nev, title="Mentés helye")
        if utvonal:
            with open(utvonal, "w", encoding="utf-8") as f:
                f.write(szoveg)
            self.mentes_label.config(text=f"Elmentve: {utvonal}")
            self._tts_beszel("Fájl elmentve.")

    # -----------------------------------------------------------------------
    # Segédek
    # -----------------------------------------------------------------------

    def _allapot(self, uzenet):
        self.allapot_var.set(uzenet)

    def _szoveg_set(self, szoveg):
        self.eredmeny.config(state="normal")
        self.eredmeny.delete("1.0", "end")
        self.eredmeny.insert("end", szoveg)
        self.eredmeny.config(state="disabled")

    def _hiba(self, uzenet):
        self.kereses_gomb.config(state="normal")
        self._allapot("Hiba történt.")
        self._tts_beszel(f"Hiba: {uzenet}")
        self._szoveg_set(f"HIBA:\n\n{uzenet}\n\nEllenőrizd az internetkapcsolatot.")
        messagebox.showerror("Hiba", uzenet)


# ---------------------------------------------------------------------------
# Belépési pont
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    KonyvKeresoApp()
