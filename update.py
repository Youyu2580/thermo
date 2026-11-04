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
    "hs300": ("1.000300", "沪深300"),
    "div": ("1.000922", "中证红利"),
    "all": ("1.000985", "中证全指"),
}
# 进攻侧观察池（ETF 作板块代理）
SECTORS = [
    ("1.588000", "科创50"),
    ("1.512480", "半导体"),
    ("1.515000", "科技"),
    ("1.512660", "军工"),
    ("0.159992", "创新药"),
    ("1.516160", "新能源"),
    ("1.512400", "有色"),
    ("1.512880", "证券"),
    ("1.512690", "白酒"),
    ("1.512800", "银行"),
    ("1.515180", "红利ETF"),
    ("1.510300", "300ETF"),
]


def _fetch_em(secid, lmt):
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    )
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=12) as r:
        obj = json.loads(r.read().decode("utf-8"))
    ks = ((obj.get("data") or {}).get("klines")) or []
    out = []
    for line in ks:
        p = line.split(",")
        out.append({"d": p[0], "close": float(p[2]), "vol": float(p[5]),
                    "amt": float(p[6]) if len(p) > 6 and p[6] not in ("", "-") else 0.0})
    return out


def _fetch_sina(secid, lmt):
    """备用源：腾讯行情（index 用 day，ETF 用 qfqday），含成交量。"""
    sym = SINA.get(secid)
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
        out.append({"d": x[0], "close": float(x[2]), "vol": v, "amt": v})
    return out


def fetch_kline(secid, lmt=520):
    last = None
    for attempt in range(2):
        try:
            out = _fetch_em(secid, lmt)
            if out:
                return out
        except Exception as e:  # noqa
            last = e
        time.sleep(0.4)
    time.sleep(0.4)
    try:
        out = _fetch_sina(secid, lmt)
        if out:
            USED_SINA.append(secid)
            return out
    except Exception as e:  # noqa
        last = e
    if last:
        print(f"[warn] {secid} 失败: {last}")
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
    for k, (secid, name) in IDX.items():
        try:
            data[k] = _fetch_sina(secid, 520)
        except Exception:  # noqa
            data[k] = []
    if all(len(v) >= 200 for v in data.values()):
        USED_SINA.append("tx")
    else:
        for k, (secid, name) in IDX.items():
            try:
                data[k] = _fetch_em(secid, 520)
            except Exception as e:  # noqa
                print(f"[warn] {name} 备用源失败: {e}")
                data[k] = []
        USED_SINA.append("em")
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
        "share_unit": "成交量" if USED_SINA else "成交额",
        "regime": regime, "tone": tone, "bear": bear,
        "sectors": rows,
        "src_note": "（部分数据走备用源：成交占比按成交量计）" if USED_SINA else "",
        "rs_series": [round(x, 4) for x in rs[-120:]],
        "rs_ma_series": [round(x, 4) for x in [ma(rs[:i + 1], 60) or rs[i] for i in range(len(rs) - 120, len(rs))]],
        "dates": [x["d"][5:] for x in dv[-120:]],
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


def render(p):
    tone = p["tone"]
    lam = {"def": "#2fd47a", "off": "#ff6b6b", "mix": "#f0b429"}[tone]
    label = {"def": "红利 / 防御占优", "off": "进攻板块占优", "mix": "方向不明 · 降仓观望"}[tone]
    rs_d = svg_path(p["rs_series"])
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
<div class="foot">口径：量能分位=20日均额在过去一年分布中的位置；相对强弱=中证红利收盘/沪深300收盘；成交占比=中证红利成交额/中证全指成交额。ETF 作板块代理，存在跟踪误差。{p['src_note']}</div>
</div></body></html>"""


if __name__ == "__main__":
    main()
