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


KLINE_HOSTS = (
    "https://stock2.finance.sina.com.cn",
    "https://stock.finance.sina.com.cn",
)
_ERRS = set()          # 取数失败原因（去重）；全失败时一起抛出去，不许静默


def _kline(sym):
    """单个合约日线：[(date, close, oi), ...]；两个新浪域名轮流试。"""
    for host in KLINE_HOSTS:
        tag = host.split("//")[-1].split(".")[0]
        url = (host + "/futures/api/jsonp.php/var%20_=/"
               "InnerFuturesNewService.getDailyKLine?symbol=" + sym)
        try:
            txt = _get(url, "https://finance.sina.com.cn/", enc="gbk")
        except Exception as e:
            _ERRS.add("新浪期货(%s):%s" % (tag, type(e).__name__))
            continue
        m = re.search(r"\((\[.*\])\)", txt, re.S)
        if not m:
            if "null" in txt[:300]:
                return []          # 该月份合约没挂牌/已退市，属正常，别记成故障
            _ERRS.add("新浪期货(%s):无JSON" % tag)
            continue
        try:
            rows = json.loads(m.group(1))
        except Exception:
            _ERRS.add("新浪期货(%s):JSON坏" % tag)
            continue
        out = []
        for r in rows:
            try:
                out.append((r["d"], float(r["c"]), float(r.get("p") or 0)))
            except Exception:
                continue
        if out:
            return out
        _ERRS.add("新浪期货(%s):空" % tag)
    return []


def _spot_em(code, beg, end):
    """东财指数日线 {date: close}"""
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1." + code +
           "&klt=101&fqt=1&beg=" + beg + "&end=" + end +
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57")
    d = json.loads(_get(url, "https://quote.eastmoney.com/"))["data"]["klines"]
    return {k.split(",")[0]: float(k.split(",")[2]) for k in d}


def _spot_tencent(code, beg, end):
    """腾讯指数日线 {date: close}；800 根够算 250 日分位。"""
    sym = "sh" + code
    for host in ("https://ifzq.gtimg.cn", "https://web.ifzq.gtimg.cn"):
        try:
            url = f"{host}/appstock/app/fqkline/get?param={sym},day,,,800,qfq"
            obj = json.loads(_get(url, "https://gu.qq.com/"))
            node = (obj.get("data") or {}).get(sym) or {}
            ks = node.get("day") or node.get("qfqday") or []
            out = {str(x[0])[:10]: float(x[2]) for x in ks if len(x) > 2}
            out = {k: v for k, v in out.items() if beg <= k <= end}
            if out:
                return out
        except Exception:
            continue
    raise RuntimeError("腾讯指数日线无返回")


def _spot_sina(code, beg, end):
    """新浪快照：只有最新一根，最后兜底（分位样本会不足，标 partial）。"""
    url = "https://hq.sinajs.cn/list=sh" + code
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                              "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        txt = r.read().decode("gbk", "replace")
    f = txt.split('"')[1].split(",")
    c = float(f[3])
    if c <= 0:
        raise RuntimeError("快照无价")
    return {f[30]: c}


SPOT_SOURCES = (("东财", _spot_em), ("腾讯", _spot_tencent), ("新浪", _spot_sina))


def _spot(code, beg, end):
    """现货指数日线：东财 → 腾讯 → 新浪快照。返回 ({date: close}, 源名)。"""
    for name, fn in SPOT_SOURCES:
        try:
            m = fn(code, beg, end)
        except Exception as e:
            _ERRS.add("%s指数(%s)" % (name, type(e).__name__))
            continue
        if m:
            return m, name
        _ERRS.add("%s指数:空" % name)
    raise RuntimeError("现货指数取数失败")


def _pct(vals, v):
    if not vals:
        return None
    return round(100.0 * sum(1 for x in vals if x <= v) / len(vals))


def build():
    months = _months()
    rows, asof, srcs, partial = [], "", set(), False
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
        spot, sname = _spot(code, dates[0], dates[-1])
        srcs.add(sname)
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
        # 分位至少要有 60 个样本才可信；只拿到一根快照就别硬算成 100%
        pct = _pct(hist, rate) if len(hist) >= 60 else None
        if pct is None:
            partial = True
        rows.append({
            "prod": prod, "label": label, "contract": sym,
            "fut": round(c, 2), "spot": round(s, 2), "rate": round(rate, 3),
            "ann": round(rate * 365.0 / days, 2), "days": days,
            "pct": pct, "n": len(hist),
        })
    if not rows:
        raise RuntimeError("期货基差取数失败（%s）" % ("; ".join(sorted(_ERRS)[:6]) or "新浪接口无返回"))
    return {"asof": asof, "window": WINDOW, "rows": rows,
            "src": "+".join(sorted(srcs)), "partial": partial,
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
<div class="sub">口径：主力合约按每日持仓量最大者拼接（不是连续合约），基差率=（期货收盘−现货）/现货；年化按距交割自然日折算；分位取近 {b['window']} 个交易日。5–8 月分红季贴水含分红因素，读数天然偏低，别当情绪。数据源：新浪期货 + {b.get('src') or '东财'}指数{'；现货历史不足，分位未算' if b.get('partial') else ''}。</div>
"""
