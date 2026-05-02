import express from "express";
import Parser from "rss-parser";

const app = express();

app.use((req, res, next) => {
  res.header("Access-Control-Allow-Origin", "*");
  res.header("Access-Control-Allow-Methods", "GET, OPTIONS, POST");
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
  { id: "guardian",       name: "The Guardian",          url: "https://www.theguardian.com/world/rss",                     category: "Geopolitics" },
  { id: "guardian_pol",   name: "Guardian Politics",     url: "https://www.theguardian.com/politics/rss",                  category: "Elections"   },
  { id: "aljazeera",      name: "Al Jazeera",            url: "https://www.aljazeera.com/xml/rss/all.xml",                 category: "Geopolitics" },
  { id: "dw_world",       name: "Deutsche Welle",        url: "https://rss.dw.com/rdf/rss-en-world",                      category: "Geopolitics" },
  { id: "france24",       name: "France 24",             url: "https://www.france24.com/en/rss",                          category: "Geopolitics" },
];

function stripHtml(str) {
  return (str || "").replace(/<[^>]*>/g, "").replace(/&[a-z]+;/gi, " ").trim();
}

// ── RSS Cache ─────────────────────────────────────────────────────────────────
let newsCache = { articles: [], updatedAt: null };

async function fetchSource(source) {
  const feed = await parser.parseURL(source.url);
  return (feed.items || []).slice(0, 8).map((item, i) => ({
    id: source.id + "_" + i,
    title: stripHtml(item.title || ""),
    description: stripHtml(item.contentSnippet || item.summary || "").slice(0, 300),
    link: item.link || item.guid || "",
    pubDate: item.pubDate || item.isoDate || new Date().toISOString(),
    source: source.name,
    category: source.category,
  }));
}

async function refreshNews() {
  const results = await Promise.allSettled(SOURCES.map(fetchSource));
  const all = results.flatMap((r, i) => {
    if (r.status === "fulfilled") return r.value;
    console.warn("x " + SOURCES[i].name + ": " + r.reason?.message);
    return [];
  });
  const seen = new Set();
  const deduped = all.filter(a => {
    const key = a.title.slice(0, 60).toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => new Date(b.pubDate) - new Date(a.pubDate)).slice(0, 80);
  newsCache = { articles: deduped, updatedAt: new Date().toISOString() };
  console.log("[" + newsCache.updatedAt + "] news: " + newsCache.articles.length);
}

// ── Polymarket Cache ──────────────────────────────────────────────────────────
let polyCache = { markets: [], updatedAt: null };

async function fetchHistory(tokenId) {
  try {
    const r = await fetch(
      "https://clob.polymarket.com/prices-history?market=" + tokenId + "&interval=1w&fidelity=60",
      { signal: AbortSignal.timeout(6000) }
    );
    if (!r.ok) return [];
    const d = await r.json();
    return (d.history || []).map(p => ({ t: p.t * 1000, p: p.p }));
  } catch { return []; }
}

async function refreshPolymarket() {
  try {
    const res = await fetch(
      "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=30&order=volume24hr&ascending=false",
      { headers: { "User-Agent": "NewsAlpha/1.0" }, signal: AbortSignal.timeout(8000) }
    );
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const base = (data || []).map(m => {
      let yesToken = null;
      try { yesToken = JSON.parse(m.clobTokenIds || "[]")[0] || null; } catch {}
      return {
        id: m.id,
        question: m.question,
        probability: m.outcomePrices ? parseFloat(JSON.parse(m.outcomePrices)[0]) : null,
        volume24h: parseFloat(m.volume24hr || 0),
        liquidity: parseFloat(m.liquidity || 0),
        endDate: m.endDate,
        url: "https://polymarket.com/event/" + ((m.events && m.events[0] && m.events[0].slug) || m.slug),
        yesToken,
      };
    });
    const histories = await Promise.allSettled(
      base.map(m => m.yesToken ? fetchHistory(m.yesToken) : Promise.resolve([]))
    );
    polyCache = {
      markets: base.map((m, i) => ({
        ...m,
        history: histories[i].status === "fulfilled" ? histories[i].value : [],
      })),
      updatedAt: new Date().toISOString(),
    };
    console.log("[Polymarket] " + polyCache.markets.length + " markets, history points: " + polyCache.markets.reduce((s, m) => s + m.history.length, 0));
  } catch (e) {
    console.warn("[Polymarket] x " + e.message);
  }
}

// ── API Routes ────────────────────────────────────────────────────────────────
app.get("/news", (req, res) => res.json(newsCache));
app.get("/polymarket", (req, res) => res.json(polyCache));
app.get("/health", (req, res) => res.json({ ok: true, articles: newsCache.articles.length, markets: polyCache.markets.length }));

// Anthropic proxy
app.post("/ai", async (req, res) => {
  try {
    const apiKey = process.env.ANTHROPIC_API_KEY;
    if (!apiKey) return res.status(500).json({ error: "No API key" });
    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(req.body),
    });
    const data = await response.json();
    res.json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Frontend ──────────────────────────────────────────────────────────────────
app.get("/", (req, res) => {
  const html = getHTML();
  res.setHeader("Content-Type", "text/html");
  res.send(html);
});

function getHTML() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>NewsAlpha</title>
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f5f5f7;color:#1d1d1f;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh;-webkit-font-smoothing:antialiased}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#d2d2d7;border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:#a1a1a6}
@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.row{animation:fi .3s ease}
.pulse{animation:pulse 1.5s infinite}
a{color:inherit;text-decoration:none}
button{font-family:inherit}
input,textarea{font-family:inherit}
</style>
</head>
<body>
<div id="root"></div>
<script type="text/babel">
const { useState, useEffect, useMemo, useCallback } = React;
const e = React.createElement;
const REFRESH_MS = 60000;
const CATS = ["All","Geopolitics","Elections","Economy","Tech/AI"];
const STOP = new Set(["the","a","an","of","in","to","for","on","and","or","is","are","was","were","be","been","with","by","at","from","that","this","these","those","it","its","as","but","not","no","new","says","said","will","can","would","should","could","has","have","had","more","less","than","over","after","before","into","about","against","amid","why","how","what","when","where","who","which"]);

function timeAgo(d){
  if(!d)return"";
  const s=(Date.now()-new Date(d))/1000;
  if(s<60)return Math.floor(s)+"s";
  if(s<3600)return Math.floor(s/60)+"m";
  if(s<86400)return Math.floor(s/3600)+"h";
  return Math.floor(s/86400)+"d";
}

function tokens(str){
  return (str||"").toLowerCase().replace(/[^a-z0-9\\s]/g," ").split(/\\s+/).filter(w=>w.length>3 && !STOP.has(w));
}

function matchMarket(article, markets){
  if(!markets.length)return null;
  const aTok = new Set(tokens(article.title + " " + (article.description||"")));
  if(aTok.size === 0) return null;
  let best = null;
  let bestScore = 0;
  for(const m of markets){
    const mTok = tokens(m.question);
    let score = 0;
    for(const t of mTok) if(aTok.has(t)) score++;
    if(score > bestScore){ bestScore = score; best = m; }
  }
  return bestScore >= 2 ? best : null;
}

function priceAt(history, ts){
  if(!history || !history.length) return null;
  let best = history[0], bestDiff = Math.abs(history[0].t - ts);
  for(const h of history){
    const d = Math.abs(h.t - ts);
    if(d < bestDiff){ bestDiff = d; best = h; }
  }
  return best.p;
}

function Sparkline({history, newsTs}){
  if(!history || history.length < 2) return null;
  const w = 100, h = 28;
  const points = history.slice(-72);
  const min = Math.min(...points.map(p=>p.p));
  const max = Math.max(...points.map(p=>p.p));
  const range = max - min || 1;
  const t0 = points[0].t, t1 = points[points.length-1].t, tRange = t1-t0 || 1;
  const path = points.map((p,i)=>{
    const x = ((p.t - t0)/tRange)*w;
    const y = h - ((p.p - min)/range)*h;
    return (i===0?"M":"L")+x.toFixed(1)+","+y.toFixed(1);
  }).join(" ");
  const last = points[points.length-1].p;
  const first = points[0].p;
  const stroke = last > first ? "#16a34a" : last < first ? "#dc2626" : "#86868b";
  const newsX = (newsTs && newsTs >= t0 && newsTs <= t1) ? ((newsTs - t0)/tRange)*w : null;
  return e("svg",{viewBox:"0 0 "+w+" "+h, style:{width:"100%",height:h,display:"block"}},
    newsX !== null && e("line",{x1:newsX,x2:newsX,y1:0,y2:h,stroke:"#0071e3",strokeWidth:1,strokeDasharray:"2,2",opacity:.6}),
    e("path",{d:path,fill:"none",stroke,strokeWidth:1.5,strokeLinejoin:"round",strokeLinecap:"round"})
  );
}

function Delta({label, value}){
  if(value === null) return e("div",{style:{flex:1,minWidth:0}},
    e("div",{style:{fontSize:9,color:"#a1a1a6",letterSpacing:.5,textTransform:"uppercase",fontWeight:600}},label),
    e("div",{style:{fontSize:12,color:"#c7c7cc",fontWeight:600}},"–")
  );
  const up = value > 0;
  const flat = Math.abs(value) < 0.5;
  const color = flat ? "#86868b" : up ? "#16a34a" : "#dc2626";
  const arrow = flat ? "→" : up ? "▲" : "▼";
  const sign = value > 0 ? "+" : "";
  return e("div",{style:{flex:1,minWidth:0}},
    e("div",{style:{fontSize:9,color:"#86868b",letterSpacing:.5,textTransform:"uppercase",fontWeight:600}},label),
    e("div",{style:{fontSize:12,color,fontWeight:700}},arrow+" "+sign+value.toFixed(1)+"pp")
  );
}

function MarketCard({m, newsDate}){
  const prob = m.probability !== null ? Math.round(m.probability*100) : null;
  const probColor = prob === null ? "#86868b" : prob > 60 ? "#16a34a" : prob > 40 ? "#ea580c" : "#dc2626";
  const newsTs = newsDate ? new Date(newsDate).getTime() : null;
  const now = Date.now();
  const hist = m.history || [];
  const current = hist.length ? hist[hist.length-1].p : (m.probability||0);
  const price24h = priceAt(hist, now - 24*3600*1000);
  const priceNews = (newsTs && hist.length && newsTs >= hist[0].t) ? priceAt(hist, newsTs) : null;
  const d24 = price24h !== null ? (current - price24h)*100 : null;
  const dNews = priceNews !== null ? (current - priceNews)*100 : null;
  return e("a",{
    href:m.url,target:"_blank",rel:"noreferrer",
    style:{
      flex:"0 0 320px",padding:"14px 16px",background:"#fff",border:"1px solid #e5e5e7",borderRadius:12,
      display:"flex",flexDirection:"column",gap:10,transition:"all .15s",minHeight:120
    },
    onMouseEnter:ev=>{ev.currentTarget.style.borderColor="#0071e3";ev.currentTarget.style.transform="translateY(-1px)"},
    onMouseLeave:ev=>{ev.currentTarget.style.borderColor="#e5e5e7";ev.currentTarget.style.transform="none"}
  },
    e("div",{style:{display:"flex",justifyContent:"space-between",alignItems:"center"}},
      e("div",{style:{fontSize:10,color:"#86868b",letterSpacing:1,fontWeight:600,textTransform:"uppercase"}},"Polymarket"),
      e("div",{style:{fontSize:10,color:"#a1a1a6",fontWeight:500}},"$"+Math.round(m.volume24h/1000)+"k 24h vol")
    ),
    e("div",{style:{fontSize:13,color:"#1d1d1f",lineHeight:1.4,fontWeight:500}},m.question),
    e("div",{style:{display:"flex",alignItems:"center",gap:10}},
      prob !== null && e("span",{style:{fontSize:22,fontWeight:700,color:probColor,lineHeight:1}},prob+"%"),
      e("div",{style:{flex:1,height:5,background:"#f0f0f0",borderRadius:3,overflow:"hidden"}},
        e("div",{style:{width:prob+"%",height:"100%",background:probColor,transition:"width .8s"}})
      )
    ),
    hist.length > 1 && e(Sparkline,{history:hist, newsTs}),
    e("div",{style:{display:"flex",gap:8,paddingTop:4,borderTop:"1px solid #f0f0f0"}},
      e(Delta,{label:"24h", value:d24}),
      e(Delta,{label:"since news", value:dNews})
    )
  );
}

function NewsCard({a}){
  return e("div",{
    style:{
      flex:1,padding:"16px 18px",background:"#fff",border:"1px solid #e5e5e7",borderRadius:12,
      display:"flex",flexDirection:"column",gap:8,transition:"all .15s",minHeight:120
    },
    onMouseEnter:ev=>ev.currentTarget.style.borderColor="#d2d2d7",
    onMouseLeave:ev=>ev.currentTarget.style.borderColor="#e5e5e7"
  },
    e("div",{style:{display:"flex",gap:8,alignItems:"center",fontSize:11,color:"#86868b"}},
      e("span",{style:{fontWeight:600,color:"#0071e3"}},a.source),
      e("span",null,"·"),
      e("span",null,a.category),
      e("span",{style:{marginLeft:"auto",color:"#a1a1a6"}},timeAgo(a.pubDate)+" ago")
    ),
    e("a",{href:a.link||"#",target:"_blank",rel:"noreferrer",
      style:{fontSize:15,fontWeight:600,color:"#1d1d1f",lineHeight:1.4}},a.title),
    a.description && e("p",{style:{fontSize:13,color:"#515154",lineHeight:1.5}},a.description.slice(0,180)+(a.description.length>180?"…":""))
  );
}

function Row({a, market}){
  return e("div",{className:"row",style:{display:"flex",gap:12,marginBottom:12,alignItems:"stretch"}},
    e(NewsCard,{a}),
    e(MarketCard,{m:market, newsDate:a.pubDate})
  );
}

function App(){
  const [articles,setArticles]=useState([]);
  const [markets,setMarkets]=useState([]);
  const [status,setStatus]=useState("loading");
  const [last,setLast]=useState(null);
  const [cat,setCat]=useState("All");
  const [q,setQ]=useState("");

  const load = useCallback(async()=>{
    setStatus("loading");
    try{
      const [n,p]=await Promise.all([
        fetch("/news").then(r=>r.json()),
        fetch("/polymarket").then(r=>r.json())
      ]);
      setArticles(n.articles||[]);
      setMarkets(p.markets||[]);
      setStatus("ok");
      setLast(new Date());
    }catch{setStatus("error");}
  },[]);

  useEffect(()=>{
    load();
    const t=setInterval(load,REFRESH_MS);
    return()=>clearInterval(t);
  },[load]);

  const filtered = useMemo(()=>articles.filter(a=>{
    if(cat!=="All" && a.category!==cat)return false;
    if(q){
      const lq=q.toLowerCase();
      return a.title.toLowerCase().includes(lq)||(a.description||"").toLowerCase().includes(lq);
    }
    return true;
  }),[articles,cat,q]);

  const matched = useMemo(()=>filtered.map(a=>({a, m:matchMarket(a,markets)})).filter(x=>x.m),[filtered,markets]);
  const totalCount = filtered.length;

  return e("div",{style:{minHeight:"100vh"}},
    e("header",{style:{
      position:"sticky",top:0,zIndex:50,background:"rgba(245,245,247,.85)",backdropFilter:"blur(12px)",
      borderBottom:"1px solid #e5e5e7",padding:"14px 24px"
    }},
      e("div",{style:{maxWidth:1200,margin:"0 auto",display:"flex",alignItems:"center",gap:24,flexWrap:"wrap"}},
        e("div",null,
          e("div",{style:{fontSize:20,fontWeight:700,letterSpacing:-.3}},
            "News",e("span",{style:{color:"#0071e3"}},"Alpha")),
          e("div",{style:{fontSize:11,color:"#86868b",marginTop:2}},
            status==="loading"?"Updating…":
            status==="error"?"Connection error":
            "Live · "+matched.length+" tradeable stories ("+totalCount+" scanned)"
          )
        ),
        e("div",{style:{flex:1}}),
        e("input",{
          value:q,onChange:ev=>setQ(ev.target.value),
          placeholder:"Search…",
          style:{padding:"8px 14px",border:"1px solid #d2d2d7",borderRadius:8,fontSize:13,
                 width:200,outline:"none",background:"#fff"}
        }),
        e("button",{onClick:load,
          style:{padding:"8px 14px",border:"1px solid #d2d2d7",borderRadius:8,background:"#fff",
                 fontSize:13,cursor:"pointer",color:"#1d1d1f"}
        },"↻ Refresh")
      ),
      e("div",{style:{maxWidth:1200,margin:"10px auto 0",display:"flex",gap:6,flexWrap:"wrap"}},
        CATS.map(c=>e("button",{key:c,onClick:()=>setCat(c),
          style:{padding:"6px 12px",border:"1px solid "+(cat===c?"#0071e3":"#d2d2d7"),
                 borderRadius:20,fontSize:12,cursor:"pointer",
                 background:cat===c?"#0071e3":"#fff",
                 color:cat===c?"#fff":"#1d1d1f",fontWeight:cat===c?600:400}},c))
      )
    ),
    e("main",{style:{maxWidth:1200,margin:"0 auto",padding:"20px 24px 60px"}},
      status==="loading" && articles.length===0 && [1,2,3,4].map(i=>
        e("div",{key:i,className:"pulse",style:{
          display:"flex",gap:12,marginBottom:12
        }},
          e("div",{style:{flex:1,height:120,background:"#fff",border:"1px solid #e5e5e7",borderRadius:12}}),
          e("div",{style:{flex:"0 0 280px",height:120,background:"#fff",border:"1px solid #e5e5e7",borderRadius:12}})
        )
      ),
      status==="error" && articles.length===0 && e("div",{style:{textAlign:"center",padding:60,color:"#86868b"}},
        "Cannot reach server. ",
        e("button",{onClick:load,style:{color:"#0071e3",background:"none",border:"none",cursor:"pointer",fontSize:14}},"Retry")
      ),
      matched.map(({a,m})=>e(Row,{key:a.id,a,market:m})),
      matched.length===0 && status==="ok" && e("div",{style:{textAlign:"center",padding:60,color:"#86868b",fontSize:14}},
        totalCount===0 ? "No stories match your filters." : "No tradeable predictions found for current news. Refreshing every minute…"
      )
    )
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(e(App));
</script>
</body>
</html>`;
}


const PORT = process.env.PORT || 3001;
app.listen(PORT, async () => {
  console.log("NewsAlpha on port " + PORT);
  await refreshNews();
  await refreshPolymarket();
  setInterval(refreshNews, 60 * 1000);
  setInterval(refreshPolymarket, 2 * 60 * 1000);
});
