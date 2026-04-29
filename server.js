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

async function refreshPolymarket() {
  try {
    const res = await fetch(
      "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=30&order=volume24hr&ascending=false",
      { headers: { "User-Agent": "NewsAlpha/1.0" }, signal: AbortSignal.timeout(8000) }
    );
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    polyCache = {
      markets: (data || []).map(m => ({
        id: m.id,
        question: m.question,
        probability: m.outcomePrices ? parseFloat(JSON.parse(m.outcomePrices)[0]) : null,
        volume24h: parseFloat(m.volume24hr || 0),
        liquidity: parseFloat(m.liquidity || 0),
        endDate: m.endDate,
        url: "https://polymarket.com/event/" + m.slug,
      })),
      updatedAt: new Date().toISOString(),
    };
    console.log("[Polymarket] " + polyCache.markets.length + " markets");
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
body{background:#070707;color:#ddd;font-family:'Helvetica Neue',sans-serif;min-height:100vh}
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-thumb{background:#1a1a1a;border-radius:2px}
@keyframes fi{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
@keyframes si{from{transform:translateX(30px);opacity:0}to{transform:none;opacity:1}}
@keyframes pu{0%,100%{opacity:1}50%{opacity:.25}}
@keyframes sh{0%,100%{opacity:.4}50%{opacity:.9}}
.card{animation:fi .35s ease}
.panel{animation:si .2s ease}
.scoring{animation:pu 1s infinite}
.skel div{animation:sh 1.5s infinite}
</style>
</head>
<body>
<div id="root"></div>
<script type="text/babel">
const { useState, useEffect, useCallback, useRef } = React;
const REFRESH_MS = 60000;
const CATS = ["All","Geopolitics","Elections","Economy","Energy","Tech/AI","Conflicts"];
const IC = {HIGH:"#ff453a",MED:"#ff9f0a",LOW:"#32d74b"};
const DS = {
  BUY:{bg:"#0a1f0f",bd:"#32d74b33",tx:"#32d74b",ic:"▲"},
  SELL:{bg:"#1f0a0a",bd:"#ff453a33",tx:"#ff453a",ic:"▼"},
  HOLD:{bg:"#1a150a",bd:"#ff9f0a33",tx:"#ff9f0a",ic:"◆"},
};

function timeAgo(d){
  if(!d)return"";
  const s=(Date.now()-new Date(d))/1000;
  if(s<60)return Math.floor(s)+"s ago";
  if(s<3600)return Math.floor(s/60)+"m ago";
  if(s<86400)return Math.floor(s/3600)+"h ago";
  return Math.floor(s/86400)+"d ago";
}

async function callAI(body){
  const r=await fetch("/ai",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  return r.json();
}

async function scoreArticle(a){
  const d=await callAI({
    model:"claude-sonnet-4-20250514",max_tokens:200,
    messages:[{role:"user",content:'Prediction market analyst. JSON only, no markdown:\n{"impact":"HIGH"|"MED"|"LOW","direction":"BUY"|"SELL"|"HOLD","confidence":<40-95>,"markets":["market1","market2"],"alpha":"one edge sentence"}\n\nHEADLINE: "' + a.title + '"\nSNIPPET: "' + a.description + '"'}]
  });
  const txt=d.content?.map(b=>b.text||"").join("")||"{}";
  return JSON.parse(txt.replace(/```json|```/g,"").trim());
}

async function deepDive(a){
  const d=await callAI({
    model:"claude-sonnet-4-20250514",max_tokens:800,
    messages:[{role:"user",content:"Expert Polymarket/Kalshi analyst. Concise.\n\nNEWS: " + a.title + "\nSOURCE: " + a.source + "\n" + a.description + "\n\n\u26a1 FIRST-MOVER WINDOW\n\ud83d\udcca MARKET IMPACT\n\ud83e\udde0 SIGNAL vs NOISE\n\u26a0\ufe0f TOP 2 RISKS\n\ud83c\udfaf TRADE CALL\n\ud83d\udd2e CONFIRM IN 24H"}]
  });
  return d.content?.map(b=>b.text||"").join("")||"Error.";
}

function Bar({v}){
  const c=v>74?"#32d74b":v>54?"#ff9f0a":"#ff453a";
  return React.createElement("div",{style:{display:"flex",alignItems:"center",gap:8}},
    React.createElement("div",{style:{width:90,height:3,background:"#1a1a1a",borderRadius:2,overflow:"hidden"}},
      React.createElement("div",{style:{width:v+"%",height:"100%",background:c,transition:"width 1s"}})),
    React.createElement("span",{style:{fontSize:10,color:"#555",fontFamily:"monospace"}},v+"%")
  );
}

function PolyPanel({markets}){
  const [open,setOpen]=useState(false);
  if(!markets.length)return null;
  return React.createElement("div",{style:{borderBottom:"1px solid #0e0e0e",background:"#060606"}},
    React.createElement("div",{onClick:()=>setOpen(o=>!o),style:{padding:"7px 18px",display:"flex",alignItems:"center",gap:10,cursor:"pointer",userSelect:"none"}},
      React.createElement("span",{style:{fontSize:9,color:"#32d74b",fontFamily:"monospace",letterSpacing:1}},"POLYMARKET LIVE · "+markets.length+" MARKETS"),
      React.createElement("span",{style:{fontSize:9,color:"#444",fontFamily:"monospace",marginLeft:"auto"}},open?"▲ hide":"▼ show")
    ),
    open && React.createElement("div",{style:{display:"flex",flexWrap:"wrap",gap:8,padding:"0 18px 12px"}},
      markets.slice(0,20).map(m=>
        React.createElement("a",{
          key:m.id,href:m.url,target:"_blank",rel:"noreferrer",
          style:{display:"flex",flexDirection:"column",gap:4,background:"#0a0a0a",border:"1px solid #1a1a1a",borderRadius:6,padding:"8px 10px",width:210,textDecoration:"none",transition:"border-color .15s"},
          onMouseEnter:e=>e.currentTarget.style.borderColor="#2a2a2a",
          onMouseLeave:e=>e.currentTarget.style.borderColor="#1a1a1a",
        },
          React.createElement("div",{style:{fontSize:10,color:"#bbb",lineHeight:1.4,fontFamily:"monospace"}},m.question.slice(0,55)+(m.question.length>55?"...":"")),
          React.createElement("div",{style:{display:"flex",justifyContent:"space-between",alignItems:"center"}},
            m.probability!==null && React.createElement("div",{style:{display:"flex",alignItems:"center",gap:5}},
              React.createElement("div",{style:{width:45,height:3,background:"#1a1a1a",borderRadius:2,overflow:"hidden"}},
                React.createElement("div",{style:{width:Math.round(m.probability*100)+"%",height:"100%",background:m.probability>0.6?"#32d74b":m.probability>0.4?"#ff9f0a":"#ff453a"}})
              ),
              React.createElement("span",{style:{fontSize:11,fontWeight:700,color:m.probability>0.6?"#32d74b":m.probability>0.4?"#ff9f0a":"#ff453a",fontFamily:"monospace"}},Math.round(m.probability*100)+"%")
            ),
            React.createElement("span",{style:{fontSize:9,color:"#333",fontFamily:"monospace"}},"$"+Math.round(m.volume24h/1000)+"k")
          )
        )
      )
    )
  );
}

function Card({a,onDeep,deepId}){
  const ic=IC[a.impact]||"#333";
  const ds=DS[a.direction];
  return React.createElement("div",{
    className:"card",
    style:{background:"#0c0c0c",border:"1px solid #1a1a1a",borderLeft:"3px solid "+ic,borderRadius:8,padding:"14px 15px",marginBottom:9,transition:"all .2s"},
    onMouseEnter:e=>{e.currentTarget.style.background="#0f0f0f";e.currentTarget.style.borderColor="#282828"},
    onMouseLeave:e=>{e.currentTarget.style.background="#0c0c0c";e.currentTarget.style.borderColor="#1a1a1a"},
  },
    React.createElement("div",{style:{display:"flex",gap:6,alignItems:"center",marginBottom:6,flexWrap:"wrap"}},
      a.impact?React.createElement("span",{style:{fontSize:9,fontWeight:800,color:ic,background:ic+"18",padding:"2px 5px",borderRadius:3,fontFamily:"monospace",letterSpacing:1}},a.impact)
              :a.aiLoading&&React.createElement("span",{className:"scoring",style:{fontSize:9,color:"#555",fontFamily:"monospace"}},"SCORING..."),
      React.createElement("span",{style:{fontSize:9,color:"#555",fontFamily:"monospace"}},(a.category||"").toUpperCase()),
      React.createElement("span",{style:{fontSize:10,color:"#3a3a3a"}},a.source),
      React.createElement("span",{style:{fontSize:9,color:"#2a2a2a",marginLeft:"auto"}},timeAgo(a.pubDate))
    ),
    React.createElement("a",{href:a.link||"#",target:"_blank",rel:"noreferrer",
      style:{fontSize:13,fontWeight:600,color:"#d8d8d8",lineHeight:1.45,textDecoration:"none",fontFamily:"Georgia,serif",display:"block",marginBottom:6}},a.title),
    a.description&&React.createElement("p",{style:{fontSize:11,color:"#4a4a4a",lineHeight:1.6,marginBottom:9}},a.description.slice(0,200)+(a.description.length>200?"...":"")),
    a.aiDone&&ds&&React.createElement("div",{style:{background:ds.bg,border:"1px solid "+ds.bd,borderRadius:6,padding:"8px 10px",marginBottom:9}},
      React.createElement("div",{style:{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:4}},
        React.createElement("span",{style:{fontSize:9,color:"#3a3a3a",letterSpacing:1,fontFamily:"monospace"}},"ALPHA"),
        React.createElement("span",{style:{fontSize:10,fontWeight:700,color:ds.tx,fontFamily:"monospace",padding:"1px 6px",borderRadius:3,border:"1px solid "+ds.bd}},ds.ic+" "+a.direction)
      ),
      a.alpha&&React.createElement("p",{style:{fontSize:11,color:"#999",margin:"0 0 6px",lineHeight:1.5}},a.alpha),
      React.createElement(Bar,{v:a.confidence||0})
    ),
    a.markets&&a.markets.length>0&&React.createElement("div",{style:{marginBottom:9}},
      React.createElement("div",{style:{fontSize:9,color:"#2a2a2a",letterSpacing:1,marginBottom:4,fontFamily:"monospace"}},"MARKETS"),
      React.createElement("div",{style:{display:"flex",flexWrap:"wrap",gap:4}},
        a.markets.map((m,i)=>React.createElement("span",{key:i,style:{fontSize:10,color:"#555",background:"#111",border:"1px solid #1c1c1c",borderRadius:3,padding:"2px 7px"}},"📊 "+m))
      )
    ),
    a.aiDone&&React.createElement("button",{
      onClick:()=>onDeep(a),disabled:deepId===a.id,
      style:{background:"transparent",border:"1px solid #222",borderRadius:4,color:deepId===a.id?"#444":"#555",fontSize:10,padding:"4px 11px",cursor:"pointer",fontFamily:"monospace"},
      onMouseEnter:e=>{if(deepId!==a.id){e.target.style.borderColor="#444";e.target.style.color="#bbb"}},
      onMouseLeave:e=>{e.target.style.borderColor="#222";e.target.style.color="#555"},
    },deepId===a.id?"⟳ ANALYZING...":"⚡ DEEP DIVE")
  );
}

function SidePanel({a,result,onClose}){
  if(!a)return null;
  return React.createElement("div",{
    className:"panel",
    style:{position:"fixed",right:0,top:0,bottom:0,width:390,background:"#080808",borderLeft:"1px solid #161616",zIndex:200,overflowY:"auto",padding:20}
  },
    React.createElement("div",{style:{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14}},
      React.createElement("span",{style:{fontSize:9,color:"#333",letterSpacing:2,fontFamily:"monospace"}},"DEEP DIVE"),
      React.createElement("button",{onClick:onClose,style:{background:"none",border:"none",color:"#444",fontSize:18,cursor:"pointer",lineHeight:1}},"X")
    ),
    React.createElement("div",{style:{fontSize:11,color:"#3a3a3a",marginBottom:12,fontFamily:"monospace"}},a.source+" · "+timeAgo(a.pubDate)),
    React.createElement("div",{style:{fontSize:12,fontWeight:600,color:"#777",marginBottom:14,lineHeight:1.4,fontFamily:"Georgia,serif"}},a.title),
    result
      ?React.createElement("div",{style:{fontSize:12,color:"#bbb",lineHeight:1.9,whiteSpace:"pre-wrap",fontFamily:"monospace"}},result)
      :React.createElement("div",{className:"scoring",style:{fontSize:11,color:"#333",fontFamily:"monospace"}},"Analyzing...")
  );
}

function App(){
  const [articles,setArticles]=useState([]);
  const [polymarkets,setPolymarkets]=useState([]);
  const [status,setStatus]=useState("idle");
  const [last,setLast]=useState(null);
  const [cat,setCat]=useState("All");
  const [q,setQ]=useState("");
  const [deepId,setDeepId]=useState(null);
  const [panel,setPanel]=useState({a:null,result:null});
  const [customQ,setCustomQ]=useState("");
  const [customRes,setCustomRes]=useState("");
  const [customLoad,setCustomLoad]=useState(false);
  const seen=useRef(new Set());
  const queue=useRef([]);
  const scoring=useRef(false);
  const artRef=useRef([]);
  useEffect(()=>{artRef.current=articles;},[articles]);

  const runQueue=useCallback(async()=>{
    if(scoring.current||!queue.current.length)return;
    scoring.current=true;
    const id=queue.current.shift();
    setArticles(p=>p.map(a=>a.id===id?{...a,aiLoading:true}:a));
    try{
      const found=artRef.current.find(x=>x.id===id);
      if(found){
        const r=await scoreArticle(found);
        setArticles(p=>p.map(a=>a.id===id?{...a,aiLoading:false,aiDone:true,impact:r.impact||"MED",direction:r.direction||"HOLD",confidence:r.confidence||60,markets:r.markets||[],alpha:r.alpha||""}:a));
      }
    }catch{setArticles(p=>p.map(a=>a.id===id?{...a,aiLoading:false}:a));}
    scoring.current=false;
    setTimeout(runQueue,600);
  },[]);

  const fetchNews=useCallback(async()=>{
    setStatus("loading");
    try{
      const r=await fetch("/news");
      if(!r.ok)throw new Error();
      const data=await r.json();
      const fresh=(data.articles||[]).filter(a=>!seen.current.has(a.id));
      fresh.forEach(a=>seen.current.add(a.id));
      if(fresh.length){
        const enriched=fresh.map(a=>({...a,impact:null,direction:null,confidence:null,markets:[],alpha:"",aiLoading:false,aiDone:false}));
        setArticles(p=>[...enriched,...p].slice(0,100));
        queue.current.push(...enriched.map(a=>a.id));
        runQueue();
      }
      setStatus("ok");
      setLast(new Date());
    }catch{setStatus("error");}
  },[runQueue]);

  useEffect(()=>{
    fetchNews();
    const t=setInterval(fetchNews,REFRESH_MS);
    const fetchPoly=()=>fetch("/polymarket").then(r=>r.json()).then(d=>setPolymarkets(d.markets||[])).catch(()=>{});
    fetchPoly();
    const p=setInterval(fetchPoly,2*60*1000);
    return()=>{clearInterval(t);clearInterval(p);};
  },[]);

  const handleDeep=async(a)=>{
    setDeepId(a.id);setPanel({a,result:null});
    try{setPanel({a,result:await deepDive(a)});}
    catch{setPanel({a,result:"Failed."});}
    setDeepId(null);
  };

  const runCustom=async()=>{
    if(!customQ.trim()||customLoad)return;
    setCustomLoad(true);setCustomRes("");
    try{
      const d=await callAI({model:"claude-sonnet-4-20250514",max_tokens:800,messages:[{role:"user",content:"Ruthless Polymarket/Kalshi analyst:\n\n"+customQ}]});
      setCustomRes(d.content?.map(b=>b.text||"").join("")||"Error.");
    }catch{setCustomRes("API error.");}
    setCustomLoad(false);
  };

  const filtered=articles.filter(a=>{
    if(cat!=="All"&&a.category!==cat)return false;
    if(q){const lq=q.toLowerCase();return a.title.toLowerCase().includes(lq)||(a.description||"").toLowerCase().includes(lq);}
    return true;
  });

  const hi=articles.filter(a=>a.impact==="HIGH").length;
  const buy=articles.filter(a=>a.direction==="BUY").length;
  const scored=articles.filter(a=>a.aiDone).length;
  const statusLabel={idle:"READY",loading:"FETCHING...",ok:"LIVE · "+(last?last.toLocaleTimeString():""),error:"ERROR"}[status]||"";
  const statusColor={idle:"#333",loading:"#ff9f0a",ok:"#32d74b",error:"#ff453a"}[status];

  return React.createElement("div",{style:{minHeight:"100vh"}},
    React.createElement("div",{style:{position:"sticky",top:0,zIndex:100,background:"#070707ee",borderBottom:"1px solid #141414",padding:"11px 18px",display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:10}},
      React.createElement("div",null,
        React.createElement("div",{style:{fontSize:14,fontWeight:700,letterSpacing:3,fontFamily:"monospace"}},
          "NEWS",React.createElement("span",{style:{color:"#ff453a"}},"ALPHA")),
        React.createElement("div",{style:{fontSize:8,color:statusColor,letterSpacing:1,fontFamily:"monospace",marginTop:2}},statusLabel)
      ),
      React.createElement("div",{style:{display:"flex",gap:16,alignItems:"center"}},
        ...[["TOTAL",articles.length,"#aaa"],["SCORED",scored,"#5b9cf6"],["HIGH",hi,"#ff453a"],["BUY",buy,"#32d74b"]].map(([l,v,c])=>
          React.createElement("div",{key:l,style:{textAlign:"center"}},
            React.createElement("div",{style:{fontSize:15,fontWeight:700,color:c,fontFamily:"monospace",lineHeight:1}},v),
            React.createElement("div",{style:{fontSize:8,color:"#2a2a2a",letterSpacing:1}},l)
          )
        ),
        React.createElement("button",{onClick:fetchNews,style:{background:"#111",border:"1px solid #222",borderRadius:4,color:"#666",fontSize:10,padding:"5px 10px",cursor:"pointer",fontFamily:"monospace"}},"↻")
      )
    ),
    React.createElement(PolyPanel,{markets:polymarkets}),
    React.createElement("div",{style:{padding:"10px 18px",display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",borderBottom:"1px solid #0e0e0e"}},
      React.createElement("input",{value:q,onChange:e=>setQ(e.target.value),placeholder:"Search...",style:{background:"#0d0d0d",border:"1px solid #1a1a1a",borderRadius:5,color:"#bbb",padding:"6px 11px",fontSize:11,width:180,outline:"none",fontFamily:"monospace"}}),
      ...CATS.map(c=>React.createElement("button",{key:c,onClick:()=>setCat(c),style:{background:cat===c?"#1a1a1a":"transparent",border:"1px solid "+(cat===c?"#2e2e2e":"#181818"),borderRadius:4,color:cat===c?"#bbb":"#333",fontSize:9,padding:"4px 8px",cursor:"pointer",fontFamily:"monospace",letterSpacing:.8}},c.toUpperCase()))
    ),
    React.createElement("div",{style:{padding:"12px 18px 20px",maxWidth:panel.a?"calc(100% - 420px)":"100%",transition:"max-width .25s"}},
      articles.length===0&&status==="loading"&&[1,2,3,4].map(i=>
        React.createElement("div",{key:i,className:"skel",style:{background:"#0d0d0d",border:"1px solid #1a1a1a",borderRadius:8,padding:18,marginBottom:9}},
          React.createElement("div",{style:{height:13,width:"100%",background:"#161616",borderRadius:3,marginBottom:9}}),
          React.createElement("div",{style:{height:9,width:"70%",background:"#161616",borderRadius:3,marginBottom:9}}),
          React.createElement("div",{style:{height:9,width:"85%",background:"#161616",borderRadius:3}})
        )
      ),
      articles.length===0&&status==="error"&&React.createElement("div",{style:{textAlign:"center",padding:50}},
        React.createElement("div",{style:{color:"#ff453a",fontSize:12,marginBottom:10,fontFamily:"monospace"}},"CANNOT REACH SERVER"),
        React.createElement("button",{onClick:fetchNews,style:{background:"#111",border:"1px solid #333",borderRadius:4,color:"#888",fontSize:11,padding:"7px 14px",cursor:"pointer",fontFamily:"monospace"}},"RETRY")
      ),
      filtered.map(a=>React.createElement(Card,{key:a.id,a,onDeep:handleDeep,deepId})),
      React.createElement("div",{style:{marginTop:18,border:"1px solid #111",borderRadius:8,padding:14,background:"#090909"}},
        React.createElement("div",{style:{fontSize:9,color:"#222",letterSpacing:2,marginBottom:8,fontFamily:"monospace"}},"CUSTOM QUERY"),
        React.createElement("textarea",{value:customQ,onChange:e=>setCustomQ(e.target.value),placeholder:"Should I buy Balance of Power 2026? Iran ceasefire odds?",rows:3,style:{width:"100%",background:"#0a0a0a",border:"1px solid #181818",borderRadius:5,color:"#aaa",padding:"8px 10px",fontSize:11,outline:"none",resize:"vertical",fontFamily:"monospace",boxSizing:"border-box"}}),
        React.createElement("button",{onClick:runCustom,disabled:customLoad||!customQ.trim(),style:{marginTop:7,background:customLoad?"#111":"#0a1a0a",border:"1px solid "+(customLoad?"#1a1a1a":"#1a3a1a"),borderRadius:4,color:customLoad?"#444":"#32d74b",fontSize:10,padding:"5px 13px",cursor:"pointer",fontFamily:"monospace"}},customLoad?"THINKING...":"ANALYZE"),
        customRes&&React.createElement("div",{style:{marginTop:12,fontSize:12,color:"#bbb",lineHeight:1.9,whiteSpace:"pre-wrap",borderTop:"1px solid #111",paddingTop:12,fontFamily:"monospace"}},customRes)
      )
    ),
    panel.a&&React.createElement(SidePanel,{a:panel.a,result:panel.result,onClose:()=>setPanel({a:null,result:null})})
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(App));
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
