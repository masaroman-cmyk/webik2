PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS products (
  source_id TEXT PRIMARY KEY,
  source_url TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  sku TEXT,
  brand TEXT,
  price_minor INTEGER CHECK(price_minor IS NULL OR price_minor >= 0),
  currency TEXT,
  price_includes_vat INTEGER,
  availability TEXT,
  image_url TEXT,
  observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS products_sku ON products(sku);
CREATE TABLE IF NOT EXISTS categories (
  source_url TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  breadcrumbs_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS category_edges (
  parent_url TEXT NOT NULL REFERENCES categories(source_url),
  child_url TEXT NOT NULL REFERENCES categories(source_url),
  PRIMARY KEY(parent_url, child_url)
);
-- A source listing is evidence of category membership, not certified fitment.
CREATE TABLE IF NOT EXISTS product_categories (
  product_id TEXT NOT NULL REFERENCES products(source_id),
  category_url TEXT NOT NULL REFERENCES categories(source_url),
  observed_at TEXT NOT NULL,
  PRIMARY KEY(product_id, category_url)
);
