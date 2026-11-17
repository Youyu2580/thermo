# -*- coding: utf-8 -*-
"""期指基差块：IF/IH/IC/IM 主力合约基差率 + 近 250 交易日分位。

口径
  - 主力合约：按每个交易日持仓量最大的合约拼接（不是"连续合约"，避免换月跳空被当成基差变化）
  - 基差率   = (期货收盘 − 现货指数) / 现货指数 × 100    负值 = 贴水
  - 年化     = 基差率 × 365 / 距交割自然日
  - 分位     = 当前基差率在近 250 个交易日里的位置，越低越贴水
数据源：新浪期货日线（含历史，用于回填）+ 东财指数日线。
注意：5~8 月分红季贴水含分红因素，读数会被压低，别当情绪。
"""
import json
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

SPEC = {
    "IF": ("沪深300", "000300", 300),
    "IH": ("上证50", "000016", 300),
    "IC": ("中证500", "000905", 200),
    "IM": ("中证1000", "000852", 200),
}
WINDOW = 250


def _get(url, ref, enc="utf-8", timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(enc, "ignore")


def _months(back=14, fwd=7):
    d = datetime.now(TZ).date()
    y, m = d.year, d.month
    for _ in range(back):          # 往前退 back 个月
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    out = []
    for _ in range(back + fwd):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _third_friday(y, m):
    d = date(y, m, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)   # 第一个周五
    return d + timedelta(days=14)


def _kline(sym):
    """单个合约日线：[(date, close, oi), ...]"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/"
           "InnerFuturesNewService.getDailyKLine?symbol=" + sym)
    try:
        txt = _get(url, "https://finance.sina.com.cn/", enc="gbk")
    except Exception:
        return []
    m = re.search(r"\((\[.*\])\)", txt, re.S)
    if not m:
        return []
    try:
        rows = json.loads(m.group(1))
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            out.append((r["d"], float(r["c"]), float(r.get("p") or 0)))
        except Exception:
            continue
    return out


def _spot(code, beg, end):
    """现货指数日线 {date: close}"""
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1." + code +
           "&klt=101&fqt=1&beg=" + beg + "&end=" + end +
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57")
    d = json.loads(_get(url, "https://quote.eastmoney.com/"))["data"]["klines"]
    return {k.split(",")[0]: float(k.split(",")[2]) for k in d}


def _pct(vals, v):
    if not vals:
        return None
    return round(100.0 * sum(1 for x in vals if x <= v) / len(vals))


def build():
    months = _months()
    rows, asof = [], ""
    for prod, (label, code, mult) in SPEC.items():
        best = {}
        for (yy, mm) in months:
            sym = "%s%02d%02d" % (prod, yy % 100, mm)
            for d, c, oi in _kline(sym):
                if oi <= 0 or c <= 0:
                    continue
                cur = best.get(d)
                if cur is None or oi > cur[0]:
                    best[d] = (oi, c, sym)
        if not best:
            continue
        dates = sorted(best)
        spot = _spot(code, dates[0].replace("-", ""), dates[-1].replace("-", ""))
        series = []
        for d in dates:
            s = spot.get(d)
            if not s:
                continue
            oi, c, sym = best[d]
            series.append((d, sym, c, s, (c - s) / s * 100.0))
        if not series:
            continue
        d, sym, c, s, rate = series[-1]
        asof = max(asof, d)
        yy, mm = 2000 + int(sym[-4:-2]), int(sym[-2:])
        days = max((_third_friday(yy, mm) - date.fromisoformat(d)).days, 1)
        hist = [x[4] for x in series[-WINDOW:]]
        rows.append({
            "prod": prod, "label": label, "contract": sym,
            "fut": round(c, 2), "spot": round(s, 2), "rate": round(rate, 3),
            "ann": round(rate * 365.0 / days, 2), "days": days,
            "pct": _pct(hist, rate), "n": len(hist),
        })
    if not rows:
        raise RuntimeError("期货基差取数失败（新浪接口无返回）")
    return {"asof": asof, "window": WINDOW, "rows": rows,
            "generated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M")}


def html(b):
    if not b or not b.get("rows"):
        return ""
    cards = ""
    for r in b["rows"]:
        pct = "—" if r["pct"] is None else "%d%%" % r["pct"]
        cards += (
            '<div class="card"><div class="k">%s %s</div>'
            '<div class="v" style="font-size:20px">%+.2f%%'
            '<small>基差率 · %s 日分位 %s · 年化 %+.1f%%</small></div></div>'
            % (r["prod"], r["label"], r["rate"], r["n"], pct, r["ann"])
        )
    brief = " · ".join("%s %+.2f%%（分位 %s）" % (
        r["prod"], r["rate"], "—" if r["pct"] is None else "%d%%" % r["pct"]) for r in b["rows"])
    return f"""<h2>对冲成本 · 期指基差（{b['asof']} 收盘 · 主力合约）</h2>
<div class="card" style="padding:12px 16px;margin-bottom:12px">
<div style="font-size:14px">{brief}</div>
<div class="sub" style="margin-top:6px">负=贴水（期货比现货便宜）。贴水越深＝对冲盘越拥挤、悲观越贵，这是「拥挤度坐标」，不是方向信号：2019–2021 IC 长期深贴水，中证500 照样涨。</div>
</div>
<div class="grid">{cards}</div>
<div class="sub">口径：主力合约按每日持仓量最大者拼接（不是连续合约），基差率=（期货收盘−现货）/现货；年化按距交割自然日折算；分位取近 {b['window']} 个交易日。5–8 月分红季贴水含分红因素，读数天然偏低，别当情绪。数据源：新浪期货 + 东财指数。</div>
"""
