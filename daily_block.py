# -*- coding: utf-8 -*-
"""每日体检（白话版）数据块：全市场快照 + 国债曲线 + 股指贴水。

只依赖标准库，供 update.py 调用；任何一段失败都不影响主页面。
"""
import hashlib
import json
import ssl
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/122.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
SLEEP = [0.0]


def _get(url, ref, timeout=25, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref})
            with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(0.6 * (i + 1))
    raise last


def _num(x, default=None):
    try:
        v = float(x)
        return v if v == v else default
    except Exception:
        return default


# ---------- 1. 全市场快照 ----------
def fetch_stocks():
    rows = []
    for pn in range(1, 70):
        u = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             f"Market_Center.getHQNodeData?page={pn}&num=100&sort=symbol&asc=1&node=hs_a")
        arr = json.loads(_get(u, "https://finance.sina.com.cn/"))
        if not arr:
            break
        rows.extend(arr)
        if len(arr) < 100:
            break
        time.sleep(0.05)
    return rows


# ---------- 2. 国债曲线 ----------
def fetch_day(dstr):
    """取某一天的整条曲线；非交易日返回 None"""
    p = {"lang": "CN", "reference": "1,2,3", "bondType": "CYCC000",
         "startDate": dstr, "endDate": dstr,
         "termId": "1", "pageNum": "1", "pageSize": "50"}
    u = ("https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/ClsYldCurvHis?"
         + urllib.parse.urlencode(p))
    d = json.loads(_get(u, "https://www.chinamoney.com.cn/chinese/bkcurvclosedyhis/"))
    out = {}
    for r in d.get("records") or []:
        t = _num(r.get("yearTermStr"))
        y = _num(r.get("maturityYieldStr"))
        if t is not None and y is not None:
            out[round(t, 2)] = y
    return out or None


def fetch_curve(need=7):
    """往回找最近 need 个有数据的交易日"""
    out = {}
    d0 = date.today()
    for k in range(0, 20):
        ds = (d0 - timedelta(days=k)).isoformat()
        try:
            c = fetch_day(ds)
        except Exception:
            continue
        if c:
            out[ds] = c
            if len(out) >= need:
                break
    return out


def curve_stats(curves):
    if not curves:
        return None
    ds = sorted(curves)
    last = curves[ds[-1]]
    pick = {}
    for t in (1.0, 2.0, 10.0, 30.0):
        if t in last:
            pick[t] = last[t]
    if 10.0 not in pick:
        return None
    st = {"date": ds[-1], "y10": pick[10.0], "y1": pick.get(1.0), "y30": pick.get(30.0)}
    if st["y1"]:
        st["spread"] = (pick[10.0] - st["y1"]) * 100
    if 5 < len(ds):
        prev = curves[ds[-6]]
        st["d10_5d"] = (pick[10.0] - prev.get(10.0, pick[10.0])) * 100
        st["d1_5d"] = ((st["y1"] - prev.get(1.0, st["y1"])) * 100) if st["y1"] else None
    return st


# ---------- 3. 股指贴水 ----------
def _expiry(y, m):
    """某月第三个周五"""
    d = date(y, m, 1)
    fridays = [d + timedelta(i) for i in range(31)
               if (d + timedelta(i)).month == m and (d + timedelta(i)).weekday() == 4]
    return fridays[2]


def _contracts(today):
    out = []
    y, m = today.year, today.month
    for k in range(0, 14):
        mm = (m - 1 + k) % 12 + 1
        yy = y + (m - 1 + k) // 12
        if k == 0 or k == 1 or mm in (3, 6, 9, 12):
            out.append((yy, mm))
    return out


def fetch_basis(spots):
    """spots: {'IF': 4016.29, 'IC': ...} 返回每个品种最近的远月基差"""
    today = date.today()
    res = {}
    for code, spot in spots.items():
        if not spot:
            continue
        cands = []
        for yy, mm in _contracts(today):
            exp = _expiry(yy, mm)
            days = (exp - today).days
            if days >= 6:
                cands.append((days, exp, f"{code}{str(yy)[2:]}{mm:02d}"))
        cands.sort()
        for days, exp, sym in cands[:2]:
            try:
                txt = _get("https://hq.sinajs.cn/list=nf_" + sym, "https://finance.sina.com.cn/")
                body = txt.split('"')[1] if '"' in txt else ""
                fut = _num(body.split(",")[3]) if body else None
                if not fut:
                    continue
                bpct = (fut / spot - 1) * 100
                ann = bpct * 365.0 / days
                res[code] = {"sym": sym, "fut": round(fut, 1), "spot": round(spot, 1),
                             "bpct": round(bpct, 2), "ann": round(ann, 1), "days": days}
                break
            except Exception:
                continue
    return res


def fetch_spots():
    """沪深300 用页面已有值；其余用腾讯日线最后收盘"""
    out = {}
    m = {"IF": "sh000300", "IC": "sh000905", "IM": "sh000852", "IH": "sh000016"}
    for k, sym in m.items():
        try:
            u = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,5,qfq")
            j = json.loads(_get(u, "https://gu.qq.com/"))
            node = (j.get("data") or {}).get(sym) or {}
            ks = node.get("qfqday") or node.get("day") or []
            if ks:
                out[k] = float(ks[-1][2])
        except Exception:
            continue
    return out


# ---------- 4. 汇总 ----------
# 九格矩阵：上涨占比(W) × MA20 宽度(M)，统计口径见 breadth_backtest.py --long（799 日 / 5526 只）
GRID = {
    (0, 0): ("Y", "超跌", "均线下方 + 今天普跌。3 年里这格 -0.5%、基准 -0.6% —— 不必恐慌，但也没便宜到值得抢"),
    (0, 1): ("R", "阴跌", "最差的一格（-1.2%，n=176）：票都在均线下方磨、今天还不温不火。没有恐慌、只有失血，该减的要真减"),
    (0, 2): ("Y", "弱修复", "均线还压着但今天普遍在涨。-0.9%，比阴跌好、仍为负——可以拿，别加"),
    (1, 0): ("G", "偏强回调", "多数票还在均线上方，今天跌一波是回调不是崩，别割（+0.4%，n=21）"),
    (1, 1): ("G", "中性", "跟基准差不多，没有额外信号，照自己节奏"),
    (1, 2): ("G", "偏强", "比基准略好，可以拿"),
    (2, 0): ("G", "强势急跌", "多数票站上均线、今天急跌。历史 +1.6% 但只有 n=11，样本太薄不能当规律 —— 先别动，明天看"),
    (2, 1): ("Y", "强势震荡", "跟基准一样，没有额外信号"),
    (2, 2): ("G", "强势", "唯一稳定正收益的格子（+0.2%，n=118）—— 手上有的拿住"),
}


def _tier_m(m):
    return 0 if m < 40 else (1 if m < 60 else 2)


def _tier_w(w):
    return 0 if w < 20 else (1 if w < 60 else 2)


def _kline_above_ma20(sym):
    """腾讯优先，失败退东财；返回 close>MA20 的布尔或 None"""
    try:
        u = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,25,qfq"
        j = json.loads(_get(u, "https://gu.qq.com/", timeout=5, tries=1))
        node = (j.get("data") or {}).get(sym) or {}
        ks = node.get("qfqday") or node.get("day") or []
        cs = [float(x[2]) for x in ks if x and len(x) > 2]
        if len(cs) >= 21:
            return cs[-1] > sum(cs[-20:]) / 20.0
    except Exception:
        pass
    try:
        code = str(sym)[2:]
        secid = ("1." if str(sym)[:2] == "sh" else "0.") + code
        u = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
             f"secid={secid}&klt=101&fqt=1&lmt=25&fields1=f1&fields2=f51,f53")
        j = json.loads(_get(u, "https://quote.eastmoney.com/", timeout=5, tries=1))
        ks = ((j.get("data") or {}).get("klines") or [])
        cs = [float(x.split(",")[1]) for x in ks if x.count(",") >= 1]
        if len(cs) >= 21:
            return cs[-1] > sum(cs[-20:]) / 20.0
    except Exception:
        pass
    return None


def ma20_breadth(syms, target=2400, budget=420, workers=28, min_cov=0.7):
    """固定样本量的 MA20 宽度：按代码哈希取前 target 只，样本构成逐日稳定，
    避免「今天 1741 只、明天 2612 只」导致档位在阈值附近来回跳。
    覆盖率不足 min_cov 时返回 None，让上层沿用上一次的值。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if not syms:
        return None
    order = sorted(syms, key=lambda x: hashlib.md5(str(x).encode()).hexdigest())
    sample = order[:target]
    ok = hit = 0
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = [ex.submit(_kline_above_ma20, s) for s in sample]
        for f in as_completed(futs, timeout=budget):
            try:
                v = f.result()
            except Exception:
                continue
            if v is None:
                continue
            ok += 1
            hit += 1 if v else 0
    except Exception:
        pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if ok < len(sample) * min_cov:
        return None
    return {"pct": round(hit / ok * 100, 1), "n": ok}

def build():
    d = {"ok": False}
    rows = []
    try:
        rows = fetch_stocks()
    except Exception as e:
        d["err"] = f"stocks:{type(e).__name__}"
    if rows:
        up = dn = 0
        pbv = pbl = 0
        amts = []
        tiers = [(">1000亿", 10_000_000, None), ("300-1000亿", 3_000_000, 10_000_000),
                 ("100-300亿", 1_000_000, 3_000_000), ("50-100亿", 500_000, 1_000_000),
                 ("<50亿", 0, 500_000)]
        tstat = {t[0]: [0, 0, []] for t in tiers}
        for r in rows:
            pct = _num(r.get("changepercent"))
            if pct is None:
                continue
            if pct > 0:
                up += 1
            elif pct < 0:
                dn += 1
            pb = _num(r.get("pb"))
            if pb and pb > 0:
                pbv += 1
                if pb < 1:
                    pbl += 1
            amt = _num(r.get("amount"))
            if amt and amt > 0:
                amts.append(amt)
            mc = _num(r.get("mktcap"))
            if mc:
                for name, lo, hi in tiers:
                    if mc >= lo and (hi is None or mc < hi):
                        s = tstat[name]
                        s[1] += 1
                        s[2].append(pct)
                        if pct > 0:
                            s[0] += 1
                        break
        n = up + dn
        d["n"] = n
        d["up"] = up
        d["dn"] = dn
        d["up_ratio"] = round(up / n * 100, 1) if n else None
        d["pb_ratio"] = round(pbl / pbv * 100, 2) if pbv else None
        amts.sort(reverse=True)
        if amts:
            k = max(1, int(len(amts) * 0.05))
            d["conc"] = round(sum(amts[:k]) / sum(amts) * 100, 1)
        d["tiers"] = [{"name": nm, "n": tstat[nm][1], "up_ratio": round(tstat[nm][0] / tstat[nm][1] * 100, 1)}
                      for nm, _, _ in tiers if tstat[nm][1] > 0]
        d["med"] = round(sorted([_num(r.get("changepercent")) for r in rows
                                 if _num(r.get("changepercent")) is not None])[len(rows)//2], 2)
        try:
            syms = [r.get("symbol") for r in rows
                    if r.get("symbol") and not str(r["symbol"]).startswith("bj")]
            m = ma20_breadth(syms)
            if m and m.get("pct") is not None:
                d["ma20"] = m
        except Exception as e:
            d["err_ma20"] = f"{type(e).__name__}"
        if not d.get("ma20"):
            # 兜底：沿用上一次成功算出的 MA20 占比，并打上 stale 标记，避免档位悄悄变松
            try:
                with open("site/data.json", encoding="utf-8") as f:
                    pm = ((json.load(f) or {}).get("daily") or {}).get("ma20") or {}
                if pm.get("pct") is not None:
                    pm = dict(pm)
                    pm["stale"] = True
                    d["ma20"] = pm
            except Exception:
                pass

    try:
        cs = curve_stats(fetch_curve())
        if cs:
            d["curve"] = cs
    except Exception as e:
        d["err_curve"] = f"{type(e).__name__}"

    d["ok"] = bool(d.get("up_ratio") is not None)
    d["ts"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    return d


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))


# ---------- 5. 白话版 HTML ----------
def _row(label, value, note="", cls=""):
    n = f'<div class="n">{note}</div>' if note else ""
    return (f'<div class="row"><div class="l">{label}</div>'
            f'<div class="r {cls}">{value}{n}</div></div>')


def html(d):
    if not d or not d.get("ok"):
        return ('<h2>每日体检 · 白话版</h2><div class="chk">'
                '<div class="n">今天数据没取到（休市或接口异常），明天再看。</div></div>')

    ur = d.get("up_ratio") or 0
    m20 = (d.get("ma20") or {}).get("pct")
    if m20 is not None:
        lv, wx, act = GRID[(_tier_m(m20), _tier_w(ur))]
        wc = {"R": "bad", "Y": "warn", "G": "good"}[lv]
    elif ur < 20:
        wx, wc, act = "普跌", "bad", "不必恐慌，但也不是买点；先别砍，也别加"
    elif ur < 40:
        wx, wc, act = "偏弱", "warn", "该减的立刻减——慢慢失血的日子最磨人"
    elif ur < 60:
        wx, wc, act = "正常", "dim", "按原计划，不用动"
    else:
        wx, wc, act = "普涨", "good", "拿住，别追高"

    tiers = d.get("tiers") or []
    struct = ""
    if len(tiers) >= 2:
        big, small = tiers[0], tiers[-1]
        diff = big["up_ratio"] - small["up_ratio"]
        if diff >= 20:
            struct = f'<span class="badge bad">钱在大票</span> 小票别接'
        elif diff <= -20:
            struct = f'<span class="badge good">钱在小票</span> 别恋战'
        else:
            struct = f'<span class="badge dim">不分大小</span> 一起涨一起跌'

    rows = [
        _row("今天多少人赚钱",
             f'{d["up"]} 涨 / {d["dn"]} 跌 · <b class="{wc}">{ur:.0f}% 在涨</b>',
             f'中位涨跌 {d.get("med", 0):+.1f}%',
             ""),
    ]
    if m20 is not None:
        stale = " · 今日取数失败，沿用上次" if (d.get("ma20") or {}).get("stale") else ""
        rows.append(_row("多少票站在 20 日线上", f'{m20:.0f}%',
                         f'上涨 {ur:.0f}% × 均线 {m20:.0f}% → 落「{wx}」格 · 样本 {d["ma20"]["n"]} 只{stale}'))
    rows.append(
        _row("跌的是大票还是小票", struct,
             " / ".join(f'{t["name"]} {t["up_ratio"]:.0f}%' for t in tiers)))

    c = d.get("curve")
    if c:
        chg = c.get("d10_5d")
        tone = "几乎没动" if (chg is None or abs(chg) < 5) else ("在变贵" if chg > 0 else "在变便宜")
        rows.append(_row("借钱贵不贵",
                         f'10 年 {c["y10"]:.2f}% · 1 年 {c["y1"]:.2f}%' if c.get("y1") else f'10 年 {c["y10"]:.2f}%',
                         f'5 天 {chg:+.1f}bp（{tone}）· 期限利差 {c.get("spread", 0):.0f}bp' if chg is not None else
                         f'期限利差 {c.get("spread", 0):.0f}bp'))

    if d.get("pb_ratio") is not None:
        rows.append(_row("便宜货多吗",
                         f'破净率 {d["pb_ratio"]:.1f}%',
                         f'100 家里约 {d["pb_ratio"]:.0f} 家跌破净资产；便宜≠见底'))

    if d.get("conc") is not None:
        rows.append(_row("钱挤不挤",
                         f'前 5% 的票吃掉 {d["conc"]:.0f}% 成交额',
                         "钱很挤，别追没人管的票" if d["conc"] >= 42 else "钱比较分散"))

    dt = (d.get("curve") or {}).get("date") or ""
    return (
        '<h2>每日体检 · 白话版（收盘后 3 分钟）</h2>'
        '<div class="chk">'
        f'<div class="chk-top"><span class="badge {wc}">{wx}</span>'
        f'<span class="chk-act">今天：{act}</span></div>'
        + "".join(rows) +
        f'<div class="chk-foot">数据截至 {dt} 收盘 · 全市场 {d.get("n")} 只（含北交所）· '
        f'生成 {d.get("ts")} · 只描述今天发生了什么，不预测涨跌；动作只有「减 / 不动」，没有「买」。'
        '</div></div>'
    )
