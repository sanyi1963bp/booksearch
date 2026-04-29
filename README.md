# Magyar Könyvkereső

Python-alapú asztali alkalmazás, amely egyszerre keres **7 magyar könyvforrásban** és megjeleníti a könyvek részletes adatait, fülszövegét, leírását.

## Funkciók

- Egyidejű keresés 7 forrásban (párhuzamos szálak)
- `Szerző - Cím` formátum támogatása
- Automatikus szűrés: csak leírással rendelkező találatok jelennek meg
- Mentés `.txt` fájlba
- Sötét, képernyőolvasóval kompatibilis felület

## Források

| Forrás | Típus |
|--------|-------|
| [Moly.hu](https://moly.hu) | Közösségi könyvoldal, értékelések, vélemények |
| [Bookline.hu](https://bookline.hu) | Online könyvesbolt |
| [Libri.hu](https://libri.hu) | Online könyvesbolt |
| [Alexandra.hu](https://alexandra.hu) | Online könyvesbolt |
| [Antikvarium.hu](https://antikvarium.hu) | Antikvár könyvek |
| [Régikönyvek.hu](https://regikonyvek.hu) | Antikvár könyvek |
| [Google Books](https://books.google.com) | Google könyvadatbázis |

## Telepítés

```bash
pip install requests beautifulsoup4
python moly_kereses.py
```

## Használat

- Írd be a könyv **címét**, vagy **Szerző - Cím** formában
- Nyomj **Entert** vagy kattints a **Keresés** gombra
- A találatok listájából válassz nyílbillentyűkkel, majd nyomj **Entert**
- Az első leírással rendelkező könyv automatikusan betöltődik
- A részletes adatokat **Mentés .txt fájlba** gombbal mentheted el

## Rendszerkövetelmények

- Python 3.8+
- Windows / Linux / macOS
- Internet-kapcsolat

## Licenc

MIT License
