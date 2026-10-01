"""Resumable Aubiri IVECO listing import. No account or private API needed."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs, urlencode
from urllib.robotparser import RobotFileParser

from lxml import html
from lxml.etree import ParserError

ROOT = 'https://www.aubiri.cz/nahradni-dily-na-vozidla-iveco-c3444/'
AGENT = 'WebikCatalogImporter/1.0'
PROJECT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def classpath(name):
    return 'contains(concat(" ",normalize-space(@class)," ")," ' + name + ' ")'


def clean(text):
    return ' '.join(text.split())


def canonical(url, base=ROOT, paging=False):
    u = urlsplit(urljoin(base, url))
    if u.scheme != 'https' or u.hostname != 'www.aubiri.cz' or u.port:
        raise ValueError('URL outside Aubiri CZ: ' + url)
    query = parse_qs(u.query)
    # Only observed offset pagination is followed. Tracking/category context removed.
    offset = query.get('f', ['0'])[0]
    if paging and not offset.isdigit():
        raise ValueError('Invalid page offset')
    q = urlencode({'f': offset}) if paging and int(offset) else ''
    return urlunsplit(('https', 'www.aubiri.cz', u.path, q, ''))


def price_minor(text):
    text = re.sub(r'\s+', '', text).replace('Kč', '').replace(',', '.')
    if not re.fullmatch(r'\d+(?:\.\d{1,2})?', text):
        return None
    try:
        return int(Decimal(text) * 100)
    except InvalidOperation:
        return None


def parse_page(raw, url):
    doc = html.fromstring(raw.decode('utf-8-sig'))
    title = clean(' '.join(doc.xpath('//h1//text()')))
    if not title:
        raise ValueError('Missing catalog heading; refusing to mark page complete')
    crumbs = []
    for script in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(script)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get('@type') == 'BreadcrumbList':
            crumbs = data.get('itemListElement', [])
    category_url = canonical(url)
    children = {}
    for a in doc.xpath('//*[@id="SubCategories"]//a[' + classpath('name') + '][@href]'):
        child = canonical(a.get('href'), url)
        if re.search(r'-c\d+/$', urlsplit(child).path):
            children[child] = clean(a.text_content())
    pages = set()
    for a in doc.xpath('//*[' + classpath('ProductPaging') + ']//a[@href]'):
        page = canonical(a.get('href'), url, paging=True)
        if canonical(page) == category_url and page != url:
            pages.add(page)
    products = []
    for card in doc.xpath('//*[' + classpath('ProductDefault') + ']'):
        links = card.xpath('.//h2/a[@href]')
        if not links:
            raise ValueError('Product card missing link')
        link = links[0]
        source_url = canonical(link.get('href'), url)
        match = re.search(r'-p(\d+)/$', urlsplit(source_url).path)
        # Some real product URLs have a custom slug without a numeric product ID.
        source_id = ('aubiri:' + match.group(1)) if match else ('aubiri:url:' + hashlib.sha256(source_url.encode()).hexdigest())
        name = clean(link.text_content())
        if not name:
            raise ValueError('Product missing name')
        def content(xpath):
            return clean(' '.join(card.xpath(xpath))) or None
        prices = card.xpath('.//*[' + classpath('price') + ' and ' + classpath('primary') + ']')
        price = price_minor(prices[0].text_content()) if prices else None
        currency = 'CZK' if prices and 'Kč' in prices[0].text_content() else None
        image = card.xpath('.//a[' + classpath('image') + ']//img')
        image_url = image[0].get('data-src') or image[0].get('src') if image else None
        if image_url and not image_url.startswith('data:'):
            image_url = urljoin(url, image_url)
        else:
            image_url = None
        products.append({
            'source_id': source_id, 'source_url': source_url,
            'name': name, 'sku': content('.//*[' + classpath('code') + ']//*[' + classpath('value') + ']/text()'),
            'brand': content('.//*[' + classpath('makerLogo') + ']/@alt'),
            'price_minor': price, 'currency': currency,
            'price_includes_vat': int('vat' in prices[0].get('class', '').split()) if prices else None,
            'availability': content('.//*[' + classpath('AvailabilityInfo') + ']//text()'),
            'image_url': image_url, 'observed_at': now()
        })
    # If the site changes its rendering, do not silently accept an empty listing.
    if doc.xpath('//*[@id="ProductsHost"]') and not products:
        text = clean(' '.join(doc.xpath('//*[@id="ProductsMaster"]//text()')))
        m = re.search(r'(\d+)\s+produkt', text)
        if m and int(m.group(1)) > 0:
            raise ValueError('Listing reports products but no cards could be parsed')
    return category_url, title, crumbs, children, pages, products


def open_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript((PROJECT / 'migrations/0001_catalog.sql').read_text())
    db.executescript('''
      CREATE TABLE IF NOT EXISTS crawl_pages (
        url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, error TEXT, fetched_at TEXT
      );
      CREATE TABLE IF NOT EXISTS import_runs (
        id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
        status TEXT NOT NULL, pages_processed INTEGER NOT NULL DEFAULT 0
      );
    ''')
    return db


def ingest(db, raw, url):
    category, title, crumbs, children, pages, products = parse_page(raw, url)
    with db:
        db.execute('INSERT INTO categories VALUES (?,?,?) ON CONFLICT(source_url) DO UPDATE SET name=excluded.name,breadcrumbs_json=excluded.breadcrumbs_json',
                   (category, title, json.dumps(crumbs, ensure_ascii=False)))
        for child, name in children.items():
            db.execute('INSERT OR IGNORE INTO categories(source_url,name) VALUES (?,?)', (child, name))
            db.execute('INSERT OR IGNORE INTO category_edges VALUES (?,?)', (category, child))
        for page in set(children) | pages:
            db.execute('INSERT OR IGNORE INTO crawl_pages(url) VALUES (?)', (page,))
        for product in products:
            columns = list(product)
            update = ','.join(f'{c}=excluded.{c}' for c in columns if c != 'source_id')
            db.execute(f'INSERT INTO products ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)}) ON CONFLICT(source_id) DO UPDATE SET {update}', tuple(product.values()))
            db.execute('INSERT INTO product_categories VALUES (?,?,?) ON CONFLICT(product_id,category_url) DO UPDATE SET observed_at=excluded.observed_at',
                       (product['source_id'], category, product['observed_at']))
        db.execute("UPDATE crawl_pages SET status='done',error=NULL,fetched_at=? WHERE url=?", (now(), url))
    return len(products)


def fetch(url):
    canonical(url, paging=True)
    # No cross-domain redirects. Redirects are treated as errors to review explicitly.
    result = subprocess.run(['curl', '--silent', '--show-error', '--fail',
                             '--max-time', '45', '--retry', '2', '--retry-delay', '2',
                             '--user-agent', AGENT, '--write-out', '\n%{http_code}', url],
                            capture_output=True, check=True)
    raw, status = result.stdout.rsplit(b'\n', 1)
    if status != b'200':
        raise ValueError('HTTP ' + status.decode())
    if not raw:
        raise ValueError('Empty response')
    return raw


def crawl(db, start, cache, max_pages=0, delay=1.0, refresh=False):
    robots_url = 'https://www.aubiri.cz/robots.txt'
    robots = RobotFileParser(robots_url)
    robots.parse(fetch(robots_url).decode('utf-8-sig').splitlines())
    cache.mkdir(parents=True, exist_ok=True)
    with db:
        if refresh:
            db.execute("UPDATE crawl_pages SET status='pending',error=NULL,attempts=0")
        else:
            db.execute("UPDATE crawl_pages SET status='pending',attempts=0 WHERE status='error'")
        db.execute('INSERT OR IGNORE INTO crawl_pages(url) VALUES (?)', (canonical(start),))
        run = db.execute("INSERT INTO import_runs(started_at,status) VALUES (?,'running')", (now(),)).lastrowid
    processed = 0
    try:
        while not max_pages or processed < max_pages:
            row = db.execute("SELECT url FROM crawl_pages WHERE status='pending' ORDER BY url LIMIT 1").fetchone()
            if not row:
                break
            url = row[0]
            try:
                if not robots.can_fetch(AGENT, url):
                    raise ValueError('Disallowed by robots.txt')
                raw = fetch(url)
                (cache / (hashlib.sha256(url.encode()).hexdigest() + '.html')).write_bytes(raw)
                count = ingest(db, raw, url)
                print(f'{count:4} products | {url}', flush=True)
            except (ValueError, ParserError, subprocess.CalledProcessError, OSError) as exc:
                error = str(exc)
                if isinstance(exc, subprocess.CalledProcessError):
                    error += ': ' + exc.stderr.decode(errors='replace')[-500:]
                with db:
                    db.execute("UPDATE crawl_pages SET status='error',attempts=attempts+1,error=? WHERE url=?", (error, url))
                print(f'ERROR | {url} | {error}', flush=True)
            processed += 1
            time.sleep(max(delay, robots.crawl_delay(AGENT) or 0))
    finally:
        pending = db.execute("SELECT COUNT(*) FROM crawl_pages WHERE status!='done'").fetchone()[0]
        state = 'incomplete' if pending else 'complete'
        with db:
            db.execute('UPDATE import_runs SET finished_at=?,status=?,pages_processed=? WHERE id=?', (now(), state, processed, run))
    return state


def export_sql(db, target):
    """D1-compatible, idempotent export; crawl state is local only."""
    with Path(target).open('w') as out:
        out.write('-- Apply migrations/0001_catalog.sql first. UTF-8.\n')
        for table in ('products', 'categories', 'category_edges', 'product_categories'):
            cols = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
            pk = [r[1] for r in sorted(db.execute(f'PRAGMA table_info({table})'), key=lambda r: r[5]) if r[5]]
            updates = [c for c in cols if c not in pk]
            suffix = ('DO UPDATE SET ' + ','.join(f'{c}=excluded.{c}' for c in updates)) if updates else 'DO NOTHING'
            for row in db.execute(f'SELECT * FROM {table} ORDER BY {",".join(pk)}'):
                values = ','.join(db.execute('SELECT quote(?)', (v,)).fetchone()[0] for v in row)
                out.write(f'INSERT INTO {table} ({",".join(cols)}) VALUES ({values}) ON CONFLICT({",".join(pk)}) {suffix};\n')


def summary(db):
    latest = db.execute('SELECT status FROM import_runs ORDER BY id DESC LIMIT 1').fetchone()
    return {'products': db.execute('SELECT COUNT(*) FROM products').fetchone()[0],
            'categories': db.execute('SELECT COUNT(*) FROM categories').fetchone()[0],
            'product_category_links': db.execute('SELECT COUNT(*) FROM product_categories').fetchone()[0],
            'pages': dict(db.execute('SELECT status,COUNT(*) FROM crawl_pages GROUP BY status')),
            'latest_run': latest[0] if latest else None}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', default=str(PROJECT / 'data/catalog.sqlite'))
    p.add_argument('--start', default=ROOT)
    p.add_argument('--max-pages', type=int, default=0, help='0 = entire crawl; otherwise a resumable batch')
    p.add_argument('--delay', type=float, default=1.0)
    p.add_argument('--refresh', action='store_true')
    p.add_argument('--export', help='Write D1-compatible product SQL')
    p.add_argument('--status', action='store_true')
    args = p.parse_args()
    if args.max_pages < 0 or args.delay < 0:
        p.error('--max-pages and --delay must be nonnegative')
    db = open_db(args.db)
    try:
        state = None
        if not args.status:
            state = crawl(db, args.start, PROJECT / 'data/cache', args.max_pages, args.delay, args.refresh)
        if args.export:
            export_sql(db, args.export)
        print(json.dumps(summary(db), ensure_ascii=False, indent=2))
        if state == 'incomplete':
            return 2
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
