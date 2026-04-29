import express from "express";
import Parser from "rss-parser";
 
const app = express();
 
// ── CORS — разрешаем claude.ai и любой другой origin ─────────────────────────
app.use((req, res, next) => {
  res.header("Access-Control-Allow-Origin", "*");
  res.header("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.header("Access-Control-Allow-Headers", "Content-Type, Authorization");
  if (req.method === "OPTIONS") return res.sendStatus(200);
  next();
});
 
app.use(express.json());
 
const parser = new Parser({
  timeout: 10000,
  headers: {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
  },
});
 
const SOURCES = [
  { id: "gnews_world",    name: "Google News: World",    url: "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en", category: "Geopolitics" },
  { id: "gnews_politics", name: "Google News: Politics", url: "https://news.google.com/rss/topics/CAAqIggKIhxDQkFTRHdvSkwyMHZNR1ptZHpWbUVnSmxiaWdBUAE?hl=en-US&gl=US&ceid=US:en",     category: "Elections"   },
  { id: "gnews_business", name: "Google News: Business", url: "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en", category: "Economy"     },
  { id: "gnews_tech",     name: "Google News: Tech",     url: "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en", category: "Tech/AI"     },
  { id: "bbc_world",      name: "BBC World",             url: "https://feeds.bbci.co.uk/news/world/rss.xml",               category: "Geopolitics" },
  { id: "guardian",       name: "The Guardian World",    url: "https://www.theguardian.com/world/rss",                     category: "Geopolitics" },
  { id: "guardian_pol",   name: "Guardian Politics",     url: "https://www.theguardian.com/politics/rss",                  category: "Elections"   },
  { id: "aljazeera",      name: "Al Jazeera",            url: "https://www.aljazeera.com/xml/rss/all.xml",                 category: "Geopolitics" },
  { id: "dw_world",       name: "Deutsche Welle",        url: "https://rss.dw.com/rdf/rss-en-world",                      category: "Geopolitics" },
  { id: "france24",       name: "France 24",             url: "https://www.france24.com/en/rss",                          category: "Geopolitics" },
];
 
function stripHtml(str) {
  return (str || "").replace(/<[^>]*>/g, "").replace(/&[a-z]+;/gi, " ").trim();
}
 
let cache = { articles: [], updatedAt: null };
 
async function fetchSource(source) {
  const feed = await parser.parseURL(source.url);
  return (feed.items || []).slice(0, 8).map((item, i) => ({
    id: `${source.id}_${i}`,
    title: stripHtml(item.title || ""),
    description: stripHtml(item.contentSnippet || item.summary || "").slice(0, 300),
    link: item.link || item.guid || "",
    pubDate: item.pubDate || item.isoDate || new Date().toISOString(),
    source: source.name,
    category: source.category,
  }));
}
 
async function refreshAll() {
  const results = await Promise.allSettled(SOURCES.map(fetchSource));
  const all = results.flatMap((r, i) => {
    if (r.status === "fulfilled") return r.value;
    console.warn(`✗ ${SOURCES[i].name}: ${r.reason?.message}`);
    return [];
  });
  const seen = new Set();
  const deduped = all.filter(a => {
    const key = a.title.slice(0, 60).toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => new Date(b.pubDate) - new Date(a.pubDate)).slice(0, 80);
 
  cache = { articles: deduped, updatedAt: new Date().toISOString() };
  console.log(`[${cache.updatedAt}] ✓ ${cache.articles.length} articles from ${results.filter(r=>r.status==="fulfilled").length}/${SOURCES.length} sources`);
}
 
app.get("/news", (req, res) => res.json(cache));
app.get("/news/refresh", async (req, res) => { await refreshAll(); res.json({ ok: true, count: cache.articles.length }); });
app.get("/health", (req, res) => res.json({ ok: true, articles: cache.articles.length, updatedAt: cache.updatedAt }));
 
const PORT = process.env.PORT || 3001;
app.listen(PORT, async () => {
  console.log(`NewsAlpha proxy on port ${PORT}`);
  await refreshAll();
  setInterval(refreshAll, 60 * 1000);
});
