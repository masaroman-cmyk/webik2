import sqlite3
import tempfile
import unittest
from pathlib import Path

from importer.aubiri import ROOT, canonical, export_sql, ingest, open_db, parse_page, price_minor

CARD = '''<html><h1>Díly IVECO</h1><div id="ProductsHost">
<div class="ProductDefault"><h2><a href="/dil-p123/?cid=42">Testovací díl</a></h2>
<div class="code"><span class="value">TEST-001</span></div>
<span class="price primary vat"><span class="value">1 234,50 Kč</span></span>
<img class="makerLogo" alt="TEST"><a class="image"><img data-src="https://cdn.aubiri.cz/example.jpg"></a>
<div class="AvailabilityInfo">Skladem 2 ks</div></div></div>
<div class="ProductPaging"><a href="?f=30">2</a><a href="?f=60">3</a></div></html>'''.encode()


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_db(Path(self.tmp.name) / 'test.sqlite')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_money_and_missing_price(self):
        self.assertEqual(price_minor('1\u00a0234,50 Kč'), 123450)
        self.assertIsNone(price_minor('Na dotaz'))
        self.assertIsNone(price_minor('1.234.50 Kč'))

    def test_url_identity_and_pagination(self):
        self.assertEqual(canonical('/dil-p123/?cid=42'), 'https://www.aubiri.cz/dil-p123/')
        page = parse_page(CARD, ROOT)
        self.assertEqual(page[4], {ROOT + '?f=30', ROOT + '?f=60'})
        self.assertEqual(page[5][0]['sku'], 'TEST-001')
        with self.assertRaises(ValueError):
            canonical('https://example.com/dil-p123/')

    def test_deduplication_and_preserved_category_evidence(self):
        ingest(self.db, CARD, ROOT)
        ingest(self.db, CARD, ROOT + '?f=30')
        ingest(self.db, CARD, 'https://www.aubiri.cz/jina-kategorie-c999/')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM products').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM product_categories').fetchone()[0], 2)

    def test_failed_page_does_not_erase_products(self):
        ingest(self.db, CARD, ROOT)
        with self.assertRaises(ValueError):
            ingest(self.db, b'<html>Access denied</html>', ROOT)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM products').fetchone()[0], 1)

    def test_custom_slug_identity(self):
        raw = CARD.replace(b'/dil-p123/', b'/custom-product-slug/')
        ingest(self.db, raw, ROOT)
        ingest(self.db, raw, ROOT + '?f=30')
        source_id = self.db.execute('SELECT source_id FROM products').fetchone()[0]
        self.assertTrue(source_id.startswith('aubiri:url:'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM products').fetchone()[0], 1)

    def test_d1_export_roundtrip_and_repeat(self):
        ingest(self.db, CARD, ROOT)
        output = Path(self.tmp.name) / 'catalog.sql'
        export_sql(self.db, output)
        other = open_db(Path(self.tmp.name) / 'other.sqlite')
        try:
            other.executescript(output.read_text())
            other.executescript(output.read_text())
            self.assertEqual(other.execute('SELECT price_minor FROM products').fetchone()[0], 123450)
            self.assertEqual(other.execute('SELECT COUNT(*) FROM products').fetchone()[0], 1)
            self.assertEqual(other.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            other.close()


if __name__ == '__main__':
    unittest.main()
