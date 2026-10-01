# Webik — katalog dílů IVECO

Nový základ pro import veřejného katalogu AUBIRI.cz do SQLite a Cloudflare D1.
Tento balíček obsahuje databázi, importér a samostatný interaktivní náhled e-shopu.
Neobsahuje původní Next.js backend.

## Lokální náhled e-shopu

Otevřete `preview/index.html` přímo v prohlížeči nebo z kořene projektu spusťte:

```bash
python3 -m http.server 8080 --directory preview --bind 127.0.0.1
```

Náhled potom najdete na http://localhost:8080/. Obsahuje úvodní stránku, katalog,
detail, košík, ukázkovou pokladnu a administraci. Vychází z dosavadního stagingu
Vektor Díly, je omezen na IVECO a obsahuje dva ukázkové produkty. Objednávky ani
platby se neodesílají a pokladna není produkční implementace. Náhled není propojen
s importovanými 37 produkty; ty jsou zatím samostatně v databázi a SQL exportu.

## Spuštění

Potřebujete Python 3.10+, curl a závislosti z requirements.txt.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 -m importer.aubiri
```

Výchozí kořen je https://www.aubiri.cz/nahradni-dily-na-vozidla-iveco-c3444/.
Import respektuje robots.txt, prochází pouze odkazy podkategorií a stránkování
v tomto katalogu. Běží postupně, s odstupem alespoň jedné sekundy.
Ve výchozím nastavení pokračuje až do vyčerpání fronty. Velký katalog může trvat
dlouho; průběh se ukládá v `data/catalog.sqlite` a po přerušení lze pokračovat
stejným příkazem. HTML odpovědi jsou lokálně v `data/cache/`.

```bash
python3 -m importer.aubiri --max-pages 50
python3 -m importer.aubiri --status
python3 -m importer.aubiri --refresh
python3 -m importer.aubiri --status --export data/catalog.sql
```

Omezený běh vrací kód 2, pokud zbývají stránky nebo chyby. Stav `complete`
znamená vyčerpání objevené fronty bez chyb, nikoli nezávislé ověření proti
internímu katalogu dodavatele. Chybové stránky se při dalším běhu zkusí znovu.
`--refresh` znovu načte všechny již známé stránky. Import nemaže záznamy, které
ze zdroje zmizely; případnou archivaci je nutné provést po porovnání úplných běhů.

## Datový model

- Identita produktu: `aubiri:<číselné ID>`; u vlastní URL bez ID používáme
  `aubiri:url:<SHA-256 kanonické URL>`. Shodné SKU není důvod ke sloučení.
- Cena je celé číslo v haléřích, měna CZK; neznámá cena zůstává NULL.
- Pole `price_includes_vat` odpovídá třídě `vat` u zobrazované ceny.
- Ukládáme název, kód, značku, cenu, text dostupnosti, URL obrázku a čas pozorování.
- `product_categories` uchovává zdrojovou kategorii, jejíž breadcrumb popisuje
  model/motor. Jde o důkaz zařazení ve zdroji, nikoli ověřenou kompatibilitu.
- Import zatím čte výpisy produktů, nikoli detaily, OEM reference a technické parametry.
- Obrázky se nestahují; ukládají se zdrojové URL.

## Cloudflare D1

Připravena migrace `migrations/0001_catalog.sql` a idempotentní SQL export.
Po vytvoření/výběru cílové databáze lze použít přihlášené Wrangler CLI:

```bash
npx wrangler d1 execute NAZEV_DATABAZE --remote --file migrations/0001_catalog.sql
npx wrangler d1 execute NAZEV_DATABAZE --remote --file data/catalog.sql
```

`NAZEV_DATABAZE` nahraďte skutečným názvem. Tento projekt sám žádnou vzdálenou
databázi nevytváří ani nemění staging Worker `vektor-dily-preview`.
Crawler metadata se do D1 neexportují. SQL export velkého katalogu lze rozdělit
po jednotlivých příkazech do menších souborů podle limitu cílové služby.

## GitHub

Cílový repozitář: https://github.com/masaroman-cmyk/webik2.
Zdrojové soubory a SQL export produktů jsou připravené v místním Git repozitáři.
SQLite databáze je v předaném ZIPu; Git sleduje její přenositelný SQL export
`data/catalog.sql`. Stav dosavadního importu je v `data/import-status.json`.
Úvodní dávka obsahuje 37 produktů z jedné skupiny dílů. Celý katalog zatím
není importovaný. Po zprovoznění GitHub přístupu:

```bash
git push -u origin main
```

Do repozitáře nevkládejte přístupové tokeny ani Cloudflare klíče.
