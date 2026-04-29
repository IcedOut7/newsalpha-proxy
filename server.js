import express from "express";
import cors from "cors";
import Parser from "rss-parser";

const app = express();

// Притворяемся настоящим браузером — обходит большинство блокировок
const parser = new Parser({
  timeout: 10000,
  headers: {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
  },
});

app.use(cors());

// ── RSS-источники ────────────────────────────────────────────────────────────
// Google News агрегирует Reuters/AP/WSJ/Politico и не блокирует серверные запросы
const SOURCES = [
  { id: "gnews_world",    name: "Google News: World",    url: "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en", category: "Geopolitics" },
  { id: "gnews_politics", name: "Google News: Politics", url: "https://news.google.com/rss/topics/CAAqIggKIhxDQkFTRHdvSkwyMHZNR1ptZHpWbUVnSmxiaWdBUAE?hl=en-US&gl=US&ceid=US:en",     category: "Elections"   },
  { id: "gnews_business", name: "Google News: Business", url: "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en", category: "Economy"     },
  { id: "gnews_tech",     name: "Google News: Tech",     url: "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en", category: "Tech/AI"     },
  { id: "gnews_us",       name: "Google News: US",       url: "https://news.google.com/rss/topics/CAAqIggKIhxDQkFTRHdvSkwyMHZNRFZxYVdjU0FtVnVJZ0FQAQ?hl=en-US&gl=US&ceid=US:en",       category: "Elections"   },
  // Прямые источники которые не блокируют серверные запросы
  { id: "bbc_world",      name: "BBC World",             url: "https://feeds.bbci.co.uk/news/world/rss.xml",                category: "Geopolitics" },
  { id: "guardian",       name: "The Guardian",          url: "https://www.theguardian.com/world/rss",                     category: "Geopolitics" },
  { id: "guardian_pol",   name: "Guardian Politics",     url: "https://www.theguardian.com/politics/rss",                  category: "Elections"   },
  { id: "guardian_econ",  name: "Guardian Economics",    url: "https://www.theguardian.com/business/economics/rss",        category: "Economy"     },
  { id: "aljazeera",      name: "Al Jazeera",            url: "https://www.aljazeera.com/xml/rss/all.xml",                 category: "Geopolitics" },
  { id: "dw_world",       name: "Deutsche Welle",        url: "https://rss.dw.com/rdf/rss-en-world",                      category: "Geopolitics" },
  { id: "france24",       name: "France 24",             url: "https://www.france24.com/en/rss",                          category: "Geopolitics" },
  { id: "rferl",          name: "Radio Free Europe",     url: "https://www.rferl.org/api/epiqq",                          category: "Geopolitics" },
];

// ── In-memory кэш ────────────────────────────────────────────────────────────
let cache = { articles: [], updatedAt: null };

function stripHtml(str) {
  return (str || "").replace(/<[^>]*>/g, "").replace(/&[a-z]+;/gi, " ").trim();
}

async function fetchSource(source) {
  const feed = await parser.parseURL(source.url);
  return (feed.items || []).slice(0, 10).map((item, i) => ({
    id: `${source.id}_${i}_${Date.now()}`,
    title: stripHtml(item.title || ""),
    description: stripHtml(item.contentSnippet || item.content || item.summary || "").slice(0, 300),
    link: item.link || item.guid || "",
    pubDate: item.pubDate || item.isoDate || new Date().toISOString(),
    source: source.name,
    category: source.category,
  }));
}

async function refreshAll() {
  console.log(`[${new Date().toISOString()}] Refreshing all RSS feeds…`);
  const results = await Promise.allSettled(SOURCES.map(fetchSource));

  const allArticles = results.flatMap((r, i) => {
    if (r.status === "fulfilled") return r.value;
    console.warn(`  ✗ ${SOURCES[i].name}: ${r.reason?.message}`);
    return [];
  });

  const seen = new Set();
  const deduped = allArticles.filter(a => {
    const key = a.title.slice(0, 60).toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  deduped.sort((a, b) => new Date(b.pubDate) - new Date(a.pubDate));

  cache = { articles: deduped.slice(0, 100), updatedAt: new Date().toISOString() };
  console.log(`  ✓ ${cache.articles.length} articles cached from ${results.filter(r => r.status === "fulfilled").length}/${SOURCES.length} sources`);
}

// ── Маршруты ─────────────────────────────────────────────────────────────────

app.get("/news", (req, res) => {
  res.json({ articles: cache.articles, updatedAt: cache.updatedAt, count: cache.articles.length });
});

app.get("/news/refresh", async (req, res) => {
  await refreshAll();
  res.json({ ok: true, count: cache.articles.length, updatedAt: cache.updatedAt });
});

app.get("/health", (req, res) => res.json({ ok: true }));

// ── Запуск ───────────────────────────────────────────────────────────────────
const PORT = process.env.PORT || 3001;

app.listen(PORT, async () => {
  console.log(`NewsAlpha RSS proxy running on port ${PORT}`);
  await refreshAll();
  setInterval(refreshAll, 60 * 1000);
});
