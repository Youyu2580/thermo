# -*- coding: utf-8 -*-
"""资金温度计：量价口径的行情/风格判定，输出单文件 HTML（无外部依赖）。"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

SINA = {  # 备用源：新浪（无成交额字段，用成交量代替）
    "1.000300": "sh000300", "1.000922": "sh000922", "1.000985": "sh000985",
    "1.588000": "sh588000", "1.512480": "sh512480", "1.515000": "sh515000",
    "1.512660": "sh512660", "0.159992": "sz159992", "1.516160": "sh516160",
    "1.512400": "sh512400", "1.512880": "sh512880", "1.512690": "sh512690",
    "1.512800": "sh512800", "1.515180": "sh515180", "1.510300": "sh510300",
}
USED_SINA = []

HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
TZ = timezone(timedelta(hours=8))

# 宽基 / 风格指数
IDX = {
    "hs300": ("000300", "沪深300"),
    "div": ("000922", "中证红利"),
    "all": ("000985", "中证全指"),
}
# 板块代理统一用中证/国证指数（官方成交额 + PE，口径稳定）
SECTORS = [
    ("000935", "信息技术"), ("H30184", "半导体"), ("000688", "科创50"),
    ("399967", "军工"), ("000933", "医药"), ("000932", "主要消费"),
    ("000928", "能源"), ("000930", "工业"), ("000934", "金融"),
    ("000922", "中证红利"), ("000300", "沪深300"),
]
TENCENT = {"000300": "sh000300", "000922": "sh000922", "000985": "sh000985",
           "000688": "sh000688", "399967": "sz399967", "000935": "sh000935",
           "H30184": "shH30184"}


_LAST = [0.0]


def _cs_get(url):
    """中证官网限速：请求间隔 0.6s，失败退避重试 3 次。"""
    for i in range(3):
        gap = time.time() - _LAST[0]
        if gap < 0.6:
            time.sleep(0.6 - gap)
        _LAST[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": HDR["User-Agent"],
                "Referer": "https://www.csindex.com.cn/"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(1.2 * (i + 1))
    raise RuntimeError("csindex 请求失败: " + url[:80])


def _fetch_cs(code, days=1500):
    start = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now(TZ).strftime("%Y%m%d")
    url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf"
           f"?indexCode={code}&startDate={start}&endDate={end}")
    rows = _cs_get(url).get("data") or []
    out = []
    for x in rows:
        try:
            amt = float(x.get("tradingValue") or 0)
            dt = str(x["tradeDate"])
            if len(dt) == 8:
                dt = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}"
            out.append({"d": dt, "close": float(x["close"]),
                        "vol": amt, "amt": amt})
        except Exception:
            continue
    return out


def _fetch_tencent(code, lmt):
    sym = TENCENT.get(code)
    if not sym:
        return []
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={sym},day,,,{min(lmt, 800)},qfq")
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=15) as r:
        obj = json.loads(r.read().decode("utf-8"))
    node = (obj.get("data") or {}).get(sym) or {}
    ks = node.get("day") or node.get("qfqday") or []
    out = []
    for x in ks[-lmt:]:
        v = float(x[5]) if len(x) > 5 and x[5] not in ("", "-") else 0.0
        out.append({"d": x[0].replace("-", ""), "close": float(x[2]), "vol": v, "amt": v})
    return out


def fetch_index_perf(code, start="20170101"):
    """返回 [(YYYY-MM-DD, 市盈率), ...]（中证官网字段 peg 实为 PE）。"""
    end = datetime.now(TZ).strftime("%Y%m%d")
    url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf"
           f"?indexCode={code}&startDate={start}&endDate={end}")
    rows = _cs_get(url).get("data") or []
    out = []
    for x in rows:
        pe = x.get("peg")
        d = str(x.get("tradeDate") or "")
        if pe and len(d) == 8:
            out.append((f"{d[:4]}-{d[4:6]}-{d[6:8]}", float(pe)))
    return out


def fetch_kline(code, lmt=520):
    for fn in (_fetch_cs, _fetch_tencent):
        for attempt in range(2):
            try:
                out = fn(code, lmt) if fn is _fetch_tencent else fn(code)
                if len(out) >= 60:
                    return out[-lmt:]
            except Exception:
                pass
            time.sleep(1.0)
    print(f"[warn] {code} 取数失败")
    return []


def ma(arr, n):
    return sum(arr[-n:]) / n if len(arr) >= n else None


def pctile(arr, v):
    if not arr:
        return None
    s = sorted(arr)
    lo = sum(1 for x in s if x < v)
    return round(lo / len(s) * 100)


def main():
    data = {}
    # 统一优先腾讯源：成交量口径固定，避免东财/腾讯来回切导致量能分位跳变
    for k, (code, name) in IDX.items():
        data[k] = fetch_kline(code, 520)
    hs, dv, al = data["hs300"], data["div"], data["all"]
    if not hs or not dv:
        raise SystemExit("核心指数取数失败，稍后重试")

    c300 = [x["close"] for x in hs]
    cdiv = [x["close"] for x in dv]
    last = c300[-1]
    ma20, ma60, ma200, ma250 = ma(c300, 20), ma(c300, 60), ma(c300, 200), ma(c300, 250)

    # 量能分位：20日均额 在过去一年 20日均额分布中的分位
    amt = [x["amt"] for x in hs]
    avg20 = [sum(amt[i - 19:i + 1]) / 20 for i in range(19, len(amt))]
    vol_pct = pctile(avg20[-250:] if len(avg20) >= 250 else avg20, avg20[-1])

    # 红利相对强弱：比值与其 60 日线
    n = min(len(cdiv), len(c300))
    rs = [cdiv[len(cdiv) - n + i] / c300[len(c300) - n + i] for i in range(n)]
    rs_ma20, rs_ma60 = ma(rs, 20), ma(rs, 60)
    rs_now = rs[-1]
    rs_5d = round((rs_now / rs[-6] - 1) * 100, 2) if len(rs) > 6 else None
    rs_20d = round((rs_now / rs[-21] - 1) * 100, 2) if len(rs) > 21 else None

    # 红利成交占比（相对全市场）
    share = None
    share_5d = None
    if al:
        m = min(len(dv), len(al))
        r = [dv[len(dv) - m + i]["amt"] / al[len(al) - m + i]["amt"] for i in range(m)]
        if r and r[-1] > 0:
            share = round(ma(r, 20) * 100, 2)
            share_5d = round((r[-1] / r[-6] - 1) * 100, 2) if len(r) > 6 else None

    # 进攻侧：20 日超额收益排名
    r300_20 = c300[-1] / c300[-21] - 1 if len(c300) > 21 else 0
    r300_5 = c300[-1] / c300[-6] - 1 if len(c300) > 6 else 0
    rows = []
    for secid, name in SECTORS:
        try:
            k = fetch_kline(secid, 120)
            if len(k) < 25:
                continue
            c = [x["close"] for x in k]
            r20 = c[-1] / c[-21] - 1
            r5 = c[-1] / c[-6] - 1
            rows.append({
                "name": name,
                "r20": round((r20 - r300_20) * 100, 2),
                "r5": round((r5 - r300_5) * 100, 2),
                "pos": round((c[-1] - min(c[-60:])) / (max(c[-60:]) - min(c[-60:])) * 100) if len(c) >= 60 else None,
            })
        except Exception as e:  # noqa
            print(f"[warn] {name} 失败: {e}")
    rows.sort(key=lambda x: -(x["r20"]))

    # 判定
    below200 = last < ma200
    rs_up = rs_now > rs_ma60
    if below200 and rs_up:
        regime, tone = "防御档", "def"
    elif (not below200) and (not rs_up):
        regime, tone = "进攻档", "off"
    else:
        regime, tone = "中间态", "mix"
    bear = last < ma250

    # ---- 估值：中证全指 PE（含银行），2017 至今 ----
    val = {"pe": None}
    try:
        series = fetch_index_perf("000985")
    except Exception as e:  # noqa
        print(f"[warn] 估值数据失败: {e}")
        series = []
    if len(series) > 200:
        pe = [x[1] for x in series]
        mean = sum(pe) / len(pe)
        sd = (sum((x - mean) ** 2 for x in pe) / len(pe)) ** 0.5
        cur = pe[-1]
        weekly = series[::5]
        if weekly[-1][0] != series[-1][0]:
            weekly.append(series[-1])
        val = {"pe": round(cur, 2), "mean": round(mean, 2),
               "hi": round(mean + sd, 2), "lo": round(mean - sd, 2),
               "pct": pctile(pe, cur), "start": series[0][0], "n": len(series),
               "series": [round(x[1], 2) for x in weekly]}
    if not val.get("pe"):
        try:
            with open("site/data.json", encoding="utf-8") as f:
                prev = (json.load(f) or {}).get("val") or {}
            if prev.get("pe"):
                val = prev
                print("[warn] 估值取数失败，沿用上次数据")
        except Exception:
            pass
    for code, key in (("000300", "pe300"), ("000934", "pe_bank")):
        val[key] = None
        try:
            vv = fetch_index_perf(code)
            if vv:
                val[key] = round(vv[-1][1], 2)
        except Exception as e:  # noqa
            print(f"[warn] {code} 估值失败: {e}")

    payload = {
        "updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "date": hs[-1]["d"],
        "px": round(last, 2),
        "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "ma200": round(ma200, 2), "ma250": round(ma250, 2),
        "above200": round((last / ma200 - 1) * 100, 2),
        "vol_pct": vol_pct,
        "rs": round(rs_now, 4), "rs_ma60": round(rs_ma60, 4),
        "rs_above": rs_now > rs_ma60,
        "rs_5d": rs_5d, "rs_20d": rs_20d,
        "share": share, "share_5d": share_5d,
        "share_unit": "成交额",
        "regime": regime, "tone": tone, "bear": bear,
        "sectors": rows,
        "src_note": "",
        "rs_series": [round(x, 4) for x in rs[-120:]],
        "rs_ma_series": [round(x, 4) for x in [ma(rs[:i + 1], 60) or rs[i] for i in range(len(rs) - 120, len(rs))]],
        "dates": [x["d"][5:] for x in dv[-120:]],
        "val": val,
    }
    os.makedirs("site", exist_ok=True)
    with open("site/data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    html = render(payload)
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"DONE {payload['date']} {regime} px={payload['px']} rs_above={payload['rs_above']} vol={vol_pct}")


def svg_path(vals, w=560, h=120):
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    step = w / max(len(vals) - 1, 1)
    pts = [(round(i * step, 1), round(h - (v - lo) / rng * (h - 8) - 4, 1)) for i, v in enumerate(vals)]
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    return d


def render_val(v):
    if not v.get("pe"):
        return ""
    vals = v["series"]
    lo = min(min(vals), v["lo"])
    hi = max(max(vals), v["hi"])
    rng = (hi - lo) or 1
    W, H, PAD = 1000.0, 220.0, 26.0

    def yy(x):
        return round(H - PAD - (x - lo) / rng * (H - 2 * PAD), 1)

    step = W / max(len(vals) - 1, 1)
    d = "M" + " L".join(f"{round(i * step, 1)},{yy(x)}" for i, x in enumerate(vals))
    cur = v["pe"]
    if cur >= v["hi"]:
        tone, verdict = "bad", "偏贵：已接近/超过高估线，红利以外的仓位要想清楚再上"
    elif cur <= v["lo"]:
        tone, verdict = "good", "偏便宜：接近低估线，是分批加的区间"
    else:
        tone, verdict = "warn", "中性：不便宜也不贵，按各自纪律走"
    pct = v["pct"]
    coef = 0.5 if pct >= 80 else (0.75 if pct >= 60 else (1.25 if pct <= 20 else 1.0))
    return f"""<h2>市场估值 · 中证全指市盈率（{v['start']} 至今）</h2>
<div class="card" style="padding:14px 18px">
<svg viewBox="0 0 1000 220" preserveAspectRatio="none" style="height:210px;width:100%">
<path d="{d}" fill="none" stroke="#58a6ff" stroke-width="1.6"/>
<line x1="0" y1="{yy(v['hi'])}" x2="1000" y2="{yy(v['hi'])}" stroke="#ff6b6b" stroke-width="1.2" stroke-dasharray="7 5"/>
<line x1="0" y1="{yy(v['mean'])}" x2="1000" y2="{yy(v['mean'])}" stroke="#f0b429" stroke-width="1.2" stroke-dasharray="7 5"/>
<line x1="0" y1="{yy(v['lo'])}" x2="1000" y2="{yy(v['lo'])}" stroke="#2fd47a" stroke-width="1.2" stroke-dasharray="7 5"/>
<circle cx="1000" cy="{yy(cur)}" r="4" fill="#58a6ff"/>
<text x="10" y="{yy(v['hi']) - 8}" fill="#ff6b6b" font-size="13">高估线 {v['hi']}</text>
<text x="10" y="{yy(v['mean']) + 18}" fill="#f0b429" font-size="13">均值 {v['mean']}</text>
<text x="10" y="{yy(v['lo']) + 20}" fill="#2fd47a" font-size="13">低估线 {v['lo']}</text>
</svg>
<div class="sub">蓝线=中证全指市盈率（含银行，官方口径）· 红=均值+1σ · 黄=均值 · 绿=均值−1σ · 共 {v['n']} 个交易日</div>
</div>
<div class="grid">
  <div class="card"><div class="k">当前市盈率</div><div class="v {tone}">{v['pe']}</div></div>
  <div class="card"><div class="k">历史分位（2017 至今）</div><div class="v">{v['pct']}%</div></div>
  <div class="card"><div class="k">沪深300 / 中证金融 PE</div><div class="v" style="font-size:20px">{v.get('pe300') or '-'} / {v.get('pe_bank') or '-'}</div></div>
</div>
<div class="verdict">{verdict}　·　仓位参考：估值分位 {pct}% → 单笔仓位 ×{coef}</div>
"""


def render(p):
    tone = p["tone"]
    lam = {"def": "#2fd47a", "off": "#ff6b6b", "mix": "#f0b429"}[tone]
    label = {"def": "红利 / 防御占优", "off": "进攻板块占优", "mix": "方向不明 · 降仓观望"}[tone]
    rs_d = svg_path(p["rs_series"])
    val_html = render_val(p.get("val") or {})
    ma_d = svg_path(p["rs_ma_series"])
    lo = min(p["rs_series"]); hi = max(p["rs_series"])
    sec_html = ""
    for i, s in enumerate(p["sectors"][:8], 1):
        cls = "up" if s["r20"] > 0 else "dn"
        sec_html += (
            f'<tr><td class="rk">{i}</td><td>{s["name"]}</td>'
            f'<td class="{cls}">{s["r20"]:+.2f}%</td>'
            f'<td class="{"up" if s["r5"]>0 else "dn"}">{s["r5"]:+.2f}%</td>'
            f'<td class="dim">{s["pos"] if s["pos"] is not None else "-"}%</td></tr>'
        )
    bear_box = (
        '<div class="alert">大盘在年线（250 日）下方。按空仓纪律：底仓降到 3 成以下，'
        '不接「低位共振」信号；站回 20 日线先补 1/3，站回 60 日线再补 1/3。</div>'
        if p["bear"] else ""
    )
    share_txt = f'{p["share"]}%' + (f'（5日 {p["share_5d"]:+.1f}%）' if p["share_5d"] is not None else "") if p["share"] is not None else "—"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>资金温度计 {p['date']}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#0d1117;color:#e6edf3;font:15px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:24px 20px 60px}}
h1{{font-size:18px;font-weight:600;margin:0 0 4px}}
.sub{{color:#8b949e;font-size:12px}}
.hero{{margin:20px 0;background:#161b22;border:1px solid #30363d;border-left:6px solid {lam};border-radius:10px;padding:20px 22px}}
.regime{{font-size:30px;font-weight:700;color:{lam};letter-spacing:1px}}
.verdict{{color:#c9d1d9;margin-top:6px;font-size:14px}}
.alert{{margin:14px 0;background:#2d1b1b;border:1px solid #6e2b2b;border-radius:8px;padding:12px 16px;color:#ffb4b4;font-size:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:18px 0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px}}
.k{{color:#8b949e;font-size:12px}}
.v{{font-size:22px;font-weight:600;margin-top:4px}}
.v small{{display:block;font-size:12px;color:#8b949e;font-weight:400;margin-top:6px}}
.good{{color:#2fd47a}} .bad{{color:#ff6b6b}} .warn{{color:#f0b429}}
table{{width:100%;border-collapse:collapse;margin-top:6px;font-size:14px}}
th{{text-align:left;color:#8b949e;font-weight:500;font-size:12px;padding:8px 6px;border-bottom:1px solid #30363d}}
td{{padding:8px 6px;border-bottom:1px solid #21262d}}
.rk{{color:#8b949e;width:28px}} .up{{color:#2fd47a}} .dn{{color:#ff6b6b}} .dim{{color:#8b949e}}
svg{{width:100%;height:130px;margin-top:8px}}
.foot{{color:#6e7681;font-size:12px;margin-top:28px}}
h2{{font-size:14px;color:#8b949e;font-weight:500;margin:26px 0 8px}}
</style></head><body><div class="wrap">
<h1>资金温度计 · 量价版的行情判定</h1>
<div class="sub">数据截至 {p['date']} 收盘 · 生成于 {p['updated']} · 仅供自己复盘，不构成投资建议</div>
<div class="hero">
  <div class="regime">{p['regime']} · {label}</div>
  <div class="verdict">沪深300 {p['px']}（{p['above200']:+.1f}% vs 200日线）· 红利/300 比值{'站上' if p['rs_above'] else '跌破'} 60日线</div>
</div>
{bear_box}
<div class="grid">
  <div class="card"><div class="k">大盘量能（20日均额分位）</div><div class="v {'good' if (p['vol_pct'] or 0)>=60 else 'warn' if (p['vol_pct'] or 0)>=35 else 'bad'}">{p['vol_pct']}%</div></div>
  <div class="card"><div class="k">红利相对强度（比值/60日线）</div><div class="v">{'<span class=good>↑ 上方</span>' if p['rs_above'] else '<span class=bad>↓ 下方</span>'}<small>5日 {p['rs_5d']:+.2f}% · 20日 {p['rs_20d']:+.2f}%</small></div></div>
  <div class="card"><div class="k">红利{p["share_unit"]}占比（20日均）</div><div class="v">{share_txt}</div></div>
  <div class="card"><div class="k">沪深300 均线</div><div class="v" style="font-size:15px">20日 {p['ma20']} · 60日 {p['ma60']}<br>200日 {p['ma200']} · 250日 {p['ma250']}</div></div>
</div>
<h2>红利 / 沪深300 相对强弱（近 120 交易日 · 蓝=比值 灰=60日线）</h2>
<div class="card" style="padding:12px 16px">
<svg viewBox="0 0 560 120" preserveAspectRatio="none">
<path d="{ma_d}" fill="none" stroke="#6e7681" stroke-width="1.2" stroke-dasharray="4 3"/>
<path d="{rs_d}" fill="none" stroke="#58a6ff" stroke-width="1.8"/>
</svg>
<div class="sub">区间 {lo:.4f} ~ {hi:.4f} · 上行=钱在往红利躲，下行=钱在往进攻跑</div>
</div>
<h2>主线候选排名（20日相对沪深300超额）</h2>
<div class="card">
<table><tr><th>#</th><th>板块</th><th>20日超额</th><th>5日超额</th><th>60日位置</th></tr>{sec_html}</table>
</div>
{val_html}<div class="foot">口径：量能分位=20日均成交额在过去一年分布中的位置；相对强弱=中证红利收盘/沪深300收盘；成交占比=中证红利成交额/中证全指成交额；板块以中证/国证行业指数代理。数据源：中证指数官网（指数行情与市盈率）。{p['src_note']}</div>
</div></body></html>"""


if __name__ == "__main__":
    main()
