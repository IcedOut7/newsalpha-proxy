import express from "express";
import cors from "cors";
import Parser from "rss-parser";

const app = express();
const parser = new Parser({ timeout: 8000, headers: { "User-Agent": "NewsAlpha/1.0" } });

app.use(cors()); // разрешаем запросы с любого домена (в т.ч. claude.ai)

// ── RSS-источники ────────────────────────────────────────────────────────────
const SOURCES = [
  { id: "reuters_world",  name: "Reuters World",   url: "https://feeds.reuters.com/reuters/worldNews",               category: "Geopolitics" },
  { id: "reuters_pol",    name: "Reuters Politics", url: "https://feeds.reuters.com/Reuters/PoliticsNews",            category: "Elections"   },
  { id: "ap_top",         name: "AP Top News",      url: "https://feeds.apnews.com/rss/apf-topnews",                 category: "Geopolitics" },
  { id: "ap_pol",         name: "AP Politics",      url: "https://feeds.apnews.com/rss/apf-politics",                category: "Elections"   },
  { id: "bbc_world",      name: "BBC World",        url: "https://feeds.bbci.co.uk/news/world/rss.xml",              category: "Geopolitics" },
  { id: "politico",       name: "Politico",         url: "https://www.politico.com/rss/politics08.xml",              category: "Elections"   },
  { id: "guardian",       name: "The Guardian",     url: "https://www.theguardian.com/world/rss",                    category: "Geopolitics" },
  { id: "ft",             name: "Financial Times",  url: "https://www.ft.com/rss/home/uk",                           category: "Economy"     },
  { id: "axios",          name: "Axios",            url: "https://api.axios.com/feed/",                              category: "Geopolitics" },
  { id: "wsj",            name: "WSJ Markets",      url: "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines", category: "Economy" },
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

  // дедупликация по заголовку
  const seen = new Set();
  const deduped = allArticles.filter(a => {
    const key = a.title.slice(0, 60).toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // сортировка по дате, свежие первые
  deduped.sort((a, b) => new Date(b.pubDate) - new Date(a.pubDate));

  cache = { articles: deduped.slice(0, 100), updatedAt: new Date().toISOString() };
  console.log(`  ✓ ${cache.articles.length} articles cached`);
}

// ── Маршруты ─────────────────────────────────────────────────────────────────

// GET /news — вернуть кэш
app.get("/news", (req, res) => {
  res.json({
    articles: cache.articles,
    updatedAt: cache.updatedAt,
    count: cache.articles.length,
  });
});

// GET /news/refresh — принудительное обновление (можно вызвать вручную)
app.get("/news/refresh", async (req, res) => {
  await refreshAll();
  res.json({ ok: true, count: cache.articles.length, updatedAt: cache.updatedAt });
});

// GET /health — для Railway healthcheck
app.get("/health", (req, res) => res.json({ ok: true }));

// ── Запуск ───────────────────────────────────────────────────────────────────
const PORT = process.env.PORT || 3001;

app.listen(PORT, async () => {
  console.log(`NewsAlpha RSS proxy running on port ${PORT}`);
  await refreshAll();                        // первая загрузка при старте
  setInterval(refreshAll, 60 * 1000);       // обновление каждую минуту
});
