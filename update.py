# -*- coding: utf-8 -*-
"""资金温度计：量价口径的行情/风格判定，输出单文件 HTML（无外部依赖）。"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    import daily_block
except Exception:  # noqa
    daily_block = None

try:
    import basis_block
except Exception:  # noqa
    basis_block = None

try:
    import nine_block
except Exception:  # noqa
    nine_block = None

# 新浪当日快照：只用来拿「最近交易日」和「当日成交额」，
# 给中证官网的日线补最后一根（中证官网要等当晚才出当日数据）。
SNAP = {"000300": "sh000300", "000922": "sh000922", "000985": "sh000985"}
LIVE_JSON = "https://youyu2580.github.io/thermo/data.json"

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
           "000932": "sh000932", "000928": "sh000928", "000930": "sh000930",
           "000934": "sh000934", "000933": "sh000933",
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
    """腾讯日线：收盘后即可拿到当日数据，但只有成交量、没有成交额（amt 置 None，
    绝不能拿成交量当成交额去算占比，两者差 5~6 个数量级）。

    web.ifzq 偶发 501，ifzq 主域更稳，两个都试（nine_block 早已这么干）。
    """
    sym = TENCENT.get(code)
    if not sym:
        return []
    last_err = None
    for host in ("https://ifzq.gtimg.cn", "https://web.ifzq.gtimg.cn"):
        try:
            url = (f"{host}/appstock/app/fqkline/get?"
                   f"param={sym},day,,,{min(lmt, 800)},qfq")
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=15) as r:
                obj = json.loads(r.read().decode("utf-8"))
            node = (obj.get("data") or {}).get(sym) or {}
            ks = node.get("day") or node.get("qfqday") or []
            out = []
            for x in ks[-lmt:]:
                d = str(x[0])[:10]
                if len(d) == 8:
                    d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                v = float(x[5]) if len(x) > 5 and x[5] not in ("", "-") else 0.0
                out.append({"d": d, "close": float(x[2]), "vol": v, "amt": None})
            if out:
                return out
        except Exception as e:  # noqa
            last_err = e
    if last_err:
        raise last_err
    return []


def _cs_amt_map(code):
    """中证官网日线 -> {日期: 成交额(亿元)}。官网当晚才更新当日数据，只当成交额底稿。"""
    return {x["d"]: x["amt"] for x in _fetch_cs(code) if x.get("amt")}


def sina_snaps(syms):
    """新浪当日快照，一次取多个：{sym: {"date","close","amt"}}（amt 单位亿元）。"""
    url = "https://hq.sinajs.cn/list=" + ",".join(syms)
    req = urllib.request.Request(url, headers={
        "User-Agent": HDR["User-Agent"], "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=12) as r:
        txt = r.read().decode("gbk", "replace")
    out = {}
    for line in txt.splitlines():
        if "=" not in line or '"' not in line:
            continue
        sym = line.split("hq_str_")[-1].split("=")[0].strip()
        f = line.split('"')[1].split(",")
        if len(f) < 31 or not f[30]:
            continue
        try:
            out[sym] = {"date": f[30], "close": float(f[3]),
                        "amt": round(float(f[9]) / 1e8, 2) if f[9] else None}
        except Exception:
            continue
    return out


def em_snaps(codes=("000300", "000922", "000985")):
    """东财当日快照：{code: {"close","amt"}}（成交额单位亿元）。新浪快照失败时用它补当日成交额。"""
    secids = ",".join("1." + c for c in codes)
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=" + secids
           + "&fields=f2,f3,f6,f12")
    req = urllib.request.Request(url, headers={
        "User-Agent": HDR["User-Agent"], "Referer": "https://quote.eastmoney.com/"})
    with urllib.request.urlopen(req, timeout=12) as r:
        obj = json.loads(r.read().decode("utf-8"))
    out = {}
    for x in ((obj.get("data") or {}).get("diff") or []):
        try:
            out[str(x.get("f12"))] = {"close": float(x["f2"]),
                                      "amt": round(float(x["f6"]) / 1e8, 2)}
        except Exception:
            continue
    return out


def after_close(mdate):
    """当日快照只在收盘后（15:05 起）才当完整成交额用，盘中拿到的是半天量。"""
    now = datetime.now(TZ)
    d, hm = now.strftime("%Y-%m-%d"), now.strftime("%H%M")
    return not (d == mdate and hm < "1505")


def market_date():
    """最近一个已收盘交易日：取新浪快照的日期字段（盘中/盘后都指向最后一个成交日）。"""
    try:
        s = sina_snaps(["sh000300"]).get("sh000300") or {}
        if s.get("date"):
            return s["date"]
    except Exception:
        pass
    try:
        url = "https://qt.gtimg.cn/q=sh000300"
        req = urllib.request.Request(url, headers={"User-Agent": HDR["User-Agent"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            f = r.read().decode("gbk", "replace").split('"')[1].split("~")
        t = f[30][:8]
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    except Exception:
        return None


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


EM_IDX = {"000300": "1.000300", "000922": "1.000922", "000985": "1.000985"}


def _fetch_em(code, lmt):
    """东财日线（带成交额）：腾讯被限流 501 时的第二选择，只在三条宽基/风格指数上用。"""
    secid = EM_IDX.get(code)
    if not secid:
        return []
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=101&fqt=0&end=20500101&lmt={min(lmt, 1000)}")
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=15) as r:
        obj = json.loads(r.read().decode("utf-8"))
    out = []
    for k in ((obj.get("data") or {}).get("klines") or []):
        p = str(k).split(",")
        if len(p) < 7:
            continue
        try:
            out.append({"d": p[0], "close": float(p[2]), "vol": float(p[5] or 0),
                        "amt": round(float(p[6]) / 1e8, 2) if p[6] else None})
        except Exception:
            continue
    return out


SRC_LOG = {}


def fetch_kline(code, lmt=520, want=None):
    """价格序列，三源串联：腾讯 → 东财 → 中证官网。

    拿到的最后一根如果已经是 want（最近交易日）就立刻收工；否则继续问下一个源，
    最后取「日期最新」的那一条。腾讯只有收盘价没有成交额，成交额走另一条链。

    ⚠ 顺序不能反。2026-11-20 的教训：先打中证官网时，16:41 那次跑只拿到 11-19，
    整页日期跟着退回 T-1，而宽度/基差/上证都是 11-20，同页混两天。
    """
    best = None
    for name, call in (("腾讯", lambda: _fetch_tencent(code, lmt)),
                       ("东财", lambda: _fetch_em(code, lmt)),
                       ("中证官网", lambda: _fetch_cs(code))):
        got = None
        for _ in range(2):
            try:
                out = call()
                if len(out) >= 60:
                    got = out[-lmt:]
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if got is None:
            continue
        d = got[-1]["d"]
        if best is None or d > best[1]:
            best = (name, d, got)
        if not want or d >= want:  # 没指定目标日 / 已经够新了，收工
            break
    if best is None:
        SRC_LOG[code] = "失败"
        print(f"[warn] {code} 取数失败")
        return []
    SRC_LOG[code] = best[0]
    return best[2]


_PREV = [None]


def prev_payload():
    """上一版页面数据。云端 checkout 里没有 site/，所以先本地、再回读线上 Pages。"""
    if _PREV[0] is not None:
        return _PREV[0]
    try:
        with open("site/data.json", encoding="utf-8") as f:
            _PREV[0] = json.load(f) or {}
    except Exception:
        _PREV[0] = {}
    if not _PREV[0]:
        try:
            req = urllib.request.Request(
                LIVE_JSON + "?t=" + str(int(time.time())),
                headers={"User-Agent": HDR["User-Agent"], "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                _PREV[0] = json.loads(r.read().decode("utf-8")) or {}
            print("[info] 上一版数据取自线上 Pages")
        except Exception as e:  # noqa
            print(f"[warn] 上一版数据取不到：{e}")
    return _PREV[0]


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
    # 价格序列统一走腾讯（15:00 后即有 T 日收盘），成交额另走「中证官网日线 + 新浪当日快照」
    # 最近交易日（快照口径）先定下来：价格源要拿到这一天，拿不到就让位给下一个源
    mdate = market_date()
    for k, (code, name) in IDX.items():
        data[k] = fetch_kline(code, 520, want=mdate)
    hs, dv, al = data["hs300"], data["div"], data["all"]
    if not hs or not dv:
        raise SystemExit("核心指数取数失败，稍后重试")

    c300 = [x["close"] for x in hs]
    cdiv = [x["close"] for x in dv]
    last = c300[-1]
    ma20, ma60, ma200, ma250 = ma(c300, 20), ma(c300, 60), ma(c300, 200), ma(c300, 250)

    # 沪深300 与中证红利按「日期」对齐：任何一条落到 T-1 时，比值/涨幅都不会错位一天
    hs_by_d = {x["d"]: x["close"] for x in hs}
    dv_by_d = {x["d"]: x["close"] for x in dv}
    cd = sorted(set(hs_by_d) & set(dv_by_d))

    # 成交额：中证官网日线当底稿（当晚才更新），再用当日快照补上「官网还没出的今天」。
    # 只用成交额（亿元），绝不把成交量混进来——混了会让量能分位/红利占比差好几个数量级。
    amts = {}
    for code in ("000300", "000922", "000985"):
        try:
            amts[code] = _cs_amt_map(code)
        except Exception as e:  # noqa
            print(f"[warn] {code} 成交额取数失败：{e}")
            amts[code] = {}
    if mdate and after_close(mdate):
        patched = set()
        # 东财快照一次给三条指数，数值与中证官网 tradingValue 一致（4411.08 亿对得上）
        try:
            for code, s in (em_snaps() or {}).items():
                if code in amts and s.get("amt") and (not amts[code] or mdate > max(amts[code])):
                    amts[code][mdate] = s["amt"]
                    patched.add(code)
        except Exception as e:  # noqa
            print(f"[warn] 东财快照失败：{e}")
        if "000300" not in patched:  # 新浪只有 000300 有效，兜底用
            try:
                s = sina_snaps([SNAP["000300"]]).get(SNAP["000300"]) or {}
                if s.get("amt") and (not amts["000300"] or mdate > max(amts["000300"])):
                    amts["000300"][mdate] = s["amt"]
                    patched.add("000300")
            except Exception as e:  # noqa
                print(f"[warn] 新浪快照失败：{e}")
        if patched:
            print(f"[info] 成交额补当日 {mdate}：{'、'.join(sorted(patched))}")
    amt_asof = max(amts["000300"]) if amts.get("000300") else None
    ams = [amts["000300"][d] for d in sorted(amts.get("000300") or {})]

    # 量能分位：20日均额 在过去一年 20日均额分布中的分位
    avg20 = [sum(ams[i - 19:i + 1]) / 20 for i in range(19, len(ams))]
    vol_pct = pctile(avg20[-250:] if len(avg20) >= 250 else avg20, avg20[-1]) if avg20 else None

    # 当日快照：涨跌、量比、离均线位置
    today = {}
    try:
        chg = (c300[-1] / c300[-2] - 1) * 100
        base = sum(ams[-21:-1]) / 20 if len(ams) > 21 else (ams[-1] if ams else 0)
        amt_ratio = (ams[-1] / base) if (ams and base) else None
        div_chg = (dv_by_d[cd[-1]] / dv_by_d[cd[-2]] - 1) * 100 if len(cd) > 1 else 0.0
        sig = "常态波动"
        if abs(chg) >= 2 and (amt_ratio or 0) >= 1.3:
            sig = "放量变盘" + ("（向上）" if chg > 0 else "（向下）")
        elif abs(chg) >= 2:
            sig = "单日异动（未放量）"
        today = {"chg": round(chg, 2),
                 "amt_ratio": round(amt_ratio, 2) if amt_ratio is not None else None,
                 "div_chg": round(div_chg, 2), "rel": round(div_chg - chg, 2),
                 "above20": round((last / ma20 - 1) * 100, 2),
                 "above60": round((last / ma60 - 1) * 100, 2),
                 "sig": sig}
    except Exception as e:  # noqa
        print(f"[warn] 当日快照失败: {e}")

    # 红利相对强弱：比值与其 60 日线（按日期对齐，不用位置对齐）
    rs = [dv_by_d[d] / hs_by_d[d] for d in cd] or [1.0]
    rs_ma20, rs_ma60 = ma(rs, 20), ma(rs, 60)
    rs_now = rs[-1]
    rs_5d = round((rs_now / rs[-6] - 1) * 100, 2) if len(rs) > 6 else None
    rs_20d = round((rs_now / rs[-21] - 1) * 100, 2) if len(rs) > 21 else None

    # 红利成交额占比（中证红利 / 中证全指，同一套成交额口径，按日期对齐）
    share = None
    share_5d = None
    share_asof = None
    try:
        a_dv = amts.get("000922") or {}
        a_al = amts.get("000985") or {}
        md = [d for d in sorted(a_dv) if a_al.get(d)]
        r = [a_dv[d] / a_al[d] for d in md]
        if r and r[-1] > 0:
            share = round(ma(r, 20) * 100, 2)
            share_5d = round((r[-1] / r[-6] - 1) * 100, 2) if len(r) > 6 else None
            share_asof = md[-1]
    except Exception as e:  # noqa
        print(f"[warn] 红利成交额占比失败: {e}")

    # 进攻侧：20 日超额收益排名（按日期对齐，某条指数滞后一天时也不会错位）
    hs_close = {x["d"]: x["close"] for x in hs}
    rows = []
    for secid, name in SECTORS:
        try:
            k = fetch_kline(secid, 120)
            if len(k) < 25:
                continue
            sc = {x["d"]: x["close"] for x in k}
            common = sorted(set(sc) & set(hs_close))
            if len(common) < 25:
                continue
            c = [sc[d] for d in common]
            b = [hs_close[d] for d in common]
            r5 = c[-1] / c[-6] - 1 if len(c) > 6 else 0
            b20 = b[-1] / b[-21] - 1 if len(b) > 21 else 0
            b5 = b[-1] / b[-6] - 1 if len(b) > 6 else 0
            r20 = c[-1] / c[-21] - 1 if len(c) > 21 else 0
            hi, lo = max(c[-60:]), min(c[-60:])
            rows.append({
                "name": name,
                "r20": round((r20 - b20) * 100, 2),
                "r5": round((r5 - b5) * 100, 2),
                "pos": round((c[-1] - lo) / (hi - lo) * 100) if len(c) >= 60 and hi > lo else None,
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
               "asof": series[-1][0],
               "series": [round(x[1], 2) for x in weekly]}
    if not val.get("pe"):
        prev = (prev_payload().get("val") or {})
        if prev.get("pe"):
            val = prev
            print("[warn] 估值取数失败，沿用上次数据")
    for code, key in (("000300", "pe300"), ("000934", "pe_bank")):
        val[key] = None
        try:
            vv = fetch_index_perf(code)
            if vv:
                val[key] = round(vv[-1][1], 2)
        except Exception as e:  # noqa
            print(f"[warn] {code} 估值失败: {e}")

    used = sorted({v for v in SRC_LOG.values() if v and v != "失败"})
    payload = {
        "updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "date": hs[-1]["d"],
        "market_date": mdate,
        "amt_asof": amt_asof,
        "px": round(last, 2),
        "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "ma200": round(ma200, 2), "ma250": round(ma250, 2),
        "above200": round((last / ma200 - 1) * 100, 2),
        "vol_pct": vol_pct,
        "rs": round(rs_now, 4), "rs_ma60": round(rs_ma60, 4),
        "rs_above": rs_now > rs_ma60,
        "rs_5d": rs_5d, "rs_20d": rs_20d,
        "share": share, "share_5d": share_5d, "share_asof": share_asof,
        "share_unit": "成交额",
        "regime": regime, "tone": tone, "bear": bear,
        "sectors": rows,
        "src_note": ("（价格源：" + "、".join(used) + "；成交额：中证官网+新浪快照）") if used else "",
        "rs_series": [round(x, 4) for x in rs[-120:]],
        "rs_ma_series": [round(x, 4) for x in [ma(rs[:i + 1], 60) or rs[i] for i in range(len(rs) - 120, len(rs))]],
        "dates": [x["d"][5:] for x in dv[-120:]],
        "val": val,
        "today": today,
    }
    os.makedirs("site", exist_ok=True)
    if daily_block:
        try:
            payload["daily"] = daily_block.build(mdate)
        except Exception as e:  # noqa
            print(f"[warn] 每日体检取数失败: {e}")
    if basis_block:
        try:
            payload["basis"] = basis_block.build()
        except Exception as e:  # noqa
            print(f"[warn] 期指基差取数失败: {e}")
            prev = (prev_payload().get("basis") or {})
            if prev.get("rows"):
                payload["basis"] = prev
                print("[warn] 期指基差沿用上次数据")
    if nine_block:
        try:
            payload["nine"] = nine_block.build(payload)
        except Exception as e:  # noqa
            print(f"[warn] 九流派取数失败: {e}")
            prev = (prev_payload().get("nine") or {})
            if prev.get("rows"):
                payload["nine"] = prev
                print("[warn] 九流派沿用上次数据")
    with open("site/data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    html = render(payload)
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    lag = "" if (not mdate or mdate == payload["date"]) else f" ⚠数据日期≠最近交易日({mdate})"
    print(f"DONE {payload['date']} {regime} px={payload['px']} rs_above={payload['rs_above']} "
          f"vol={vol_pct} amt_asof={amt_asof}{lag}")


def svg_path(vals, w=560, h=120):
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    step = w / max(len(vals) - 1, 1)
    pts = [(round(i * step, 1), round(h - (v - lo) / rng * (h - 8) - 4, 1)) for i, v in enumerate(vals)]
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    return d


def top_cards(p):
    """顶部四卡：量能 / 红利相对强度 / 均线位置 / 估值——一屏看完所有结论"""
    t = p.get("today") or {}
    v = p.get("val") or {}
    vol = p.get("vol_pct")
    tone_vol = "dim" if vol is None else ("good" if vol >= 60 else "warn" if vol >= 35 else "bad")
    vol_txt = "—" if vol is None else f"{vol}%"
    ratio = t.get("amt_ratio")
    ratio_txt = f"今日量比 {ratio:.2f} 倍" if ratio is not None else "今日量比 —"
    rs5, rs20 = p.get("rs_5d"), p.get("rs_20d")
    rs_lbl = "↑ 上方" if p["rs_above"] else "↓ 下方"
    rs_cls = "good" if p["rs_above"] else "bad"
    rs_5 = f"{rs5:+.2f}%" if rs5 is not None else "—"
    rs_20 = f"{rs20:+.2f}%" if rs20 is not None else "—"
    pe, pct = v.get("pe"), v.get("pct")
    share = p.get("share")
    share_txt = f"{share}%" if share is not None else "—"
    s5 = p.get("share_5d")
    share_5d = f"{s5:+.1f}%" if s5 is not None else "—"
    if pe:
        coef = 0.5 if pct >= 80 else (0.75 if pct >= 60 else (1.25 if pct <= 20 else 1.0))
        pe_asof = f" · 截至 {v['asof']}" if v.get("asof") and v.get("asof") != p.get("date") else ""
        pe_txt = f"{pe} · {pct}%"
        pe_note = f"2017 至今分位 · 单笔仓位 ×{coef}{pe_asof}"
    else:
        pe_txt, pe_note = "—", "估值取数失败"
    amt_note = ""
    if p.get("amt_asof") and p.get("amt_asof") != p.get("date"):
        amt_note = f"（成交额截至 {p['amt_asof']}）"
    share_note = ""
    if p.get("share_asof") and p.get("share_asof") != p.get("date"):
        share_note = f" · 截至 {p['share_asof']}"
    return f"""<div class="grid">
  <div class="card"><div class="k">大盘量能（20日均额分位）</div><div class="v {tone_vol}">{vol_txt}<small>{ratio_txt}{amt_note}</small></div></div>
  <div class="card"><div class="k">红利相对强度（vs 60日线）</div><div class="v"><span class="{rs_cls}">{rs_lbl}</span><small>5日 {rs_5} · 20日 {rs_20}</small></div></div>
  <div class="card"><div class="k">红利{p["share_unit"]}占比（20日均）</div><div class="v" style="font-size:20px">{share_txt}<small>20日均 · 5日 {share_5d}{share_note}</small></div></div>
  <div class="card"><div class="k">中证全指 PE / 历史分位</div><div class="v" style="font-size:20px">{pe_txt}<small>{pe_note}</small></div></div>
</div>"""


def val_chart(v):
    """估值曲线：默认折叠，点开看"""
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
    return f"""<details><summary>市场估值曲线 · 中证全指市盈率（{v['start']} 至今 · {v['n']} 个交易日）</summary>
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
<div class="sub">蓝线=中证全指市盈率（含银行，官方口径）· 红=均值+1σ · 黄=均值 · 绿=均值−1σ · 沪深300 {v.get('pe300') or '-'} · 中证金融 {v.get('pe_bank') or '-'}</div>
</div></details>"""


def render(p):
    tone = p["tone"]
    lam = {"def": "#2fd47a", "off": "#ff6b6b", "mix": "#f0b429"}[tone]
    label = {"def": "红利 / 防御占优", "off": "进攻板块占优", "mix": "方向不明 · 降仓观望"}[tone]
    rs_d = svg_path(p["rs_series"])
    basis_html = basis_block.html(p.get("basis") or {}) if basis_block else ""
    cards_html = top_cards(p)
    vchart = val_chart(p.get("val") or {})
    try:
        daily_html = daily_block.html(p.get("daily") or {}) if daily_block else ""
    except Exception:
        daily_html = ""
    try:
        nine_html = nine_block.html(p.get("nine") or {}) if nine_block else ""
    except Exception:
        nine_html = ""
    # 数据日期 ≠ 最近交易日 → 页首红条。宁可刺眼，也别让人拿着昨天的结论做今天的决定。
    md = p.get("market_date") or ""
    lag_html = ""
    if md and md != p.get("date"):
        lag_html = (f'<div class="alert">⚠ 本页主数据是 <b>{p.get("date")}</b>，最近交易日 <b>{md}</b> 已有收盘'
                    f'——结论是按旧日期算的，今天别照着做。</div>')
    lag_note = ""
    ma_d = svg_path(p["rs_ma_series"])
    lo = min(p["rs_series"]); hi = max(p["rs_series"])
    sec_html = ""
    for i, s in enumerate([x for x in p["sectors"] if x["name"] != "沪深300"][:8], 1):
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
/* 配色口径（A 股）：红=涨/强/热/贵，绿=跌/弱/冷/便宜，黄=中性/注意。
   红绿只表达「强弱」一个维度，不再兼任「好/坏」。 */
.good{{color:#ff6b6b}} .bad{{color:#2fd47a}} .warn{{color:#f0b429}}
table{{width:100%;border-collapse:collapse;margin-top:6px;font-size:14px}}
th{{text-align:left;color:#8b949e;font-weight:500;font-size:12px;padding:8px 6px;border-bottom:1px solid #30363d}}
td{{padding:8px 6px;border-bottom:1px solid #21262d}}
.rk{{color:#8b949e;width:28px}} .up{{color:#ff6b6b}} .dn{{color:#2fd47a}} .dim{{color:#8b949e}}
svg{{width:100%;height:130px;margin-top:8px}}
.foot{{color:#6e7681;font-size:12px;margin-top:28px}}
details{{margin:10px 0;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px 16px}}
summary{{cursor:pointer;color:#8b949e;font-size:13px}}
details[open] summary{{margin-bottom:8px}}
h2{{font-size:14px;color:#8b949e;font-weight:500;margin:26px 0 8px}}
.chk{{margin:14px 0 18px;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px}}
.chk-top{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}}
.chk-act{{font-size:14px;font-weight:600;color:#e6edf3}}
.badge{{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;border:1px solid currentColor}}
.chk .row{{display:flex;justify-content:space-between;gap:14px;padding:10px 0;border-bottom:1px solid #21262d}}
.chk .row:last-child{{border-bottom:0}}
.chk .l{{color:#8b949e;font-size:13px;flex:1 1 40%}}
.chk .r{{font-size:14px;font-weight:600;text-align:right;flex:1 1 55%;color:#e6edf3}}
.chk .r .n{{font-size:12px;color:#8b949e;font-weight:400;margin-top:3px}}
.chk-foot{{color:#6e7681;font-size:11px;margin-top:8px;line-height:1.5}}
.nine-wrap{{padding:6px 14px}}
.nine{{font-size:13px}}
.nine th:nth-child(1){{width:112px}}
.nine th:nth-child(3){{width:138px}}
.nine td{{vertical-align:top}}
.nine-id{{display:inline-block;width:18px;height:18px;line-height:18px;text-align:center;border-radius:4px;background:#21262d;color:#8b949e;font-size:11px;margin-right:6px}}
.nine-dot{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle}}
.nine-dot.good{{background:#ff6b6b}}.nine-dot.bad{{background:#2fd47a}}.nine-dot.warn{{background:#f0b429}}
.nine-gates{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:10px 0}}
.nine-gates>div{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 12px;font-size:12px;color:#c9d1d9;line-height:1.5}}
.nine-gates b{{display:block;color:#8b949e;font-weight:500;font-size:11px;margin-bottom:2px}}
.nine-a .badge{{font-size:12px;white-space:nowrap}}
.nine-note{{color:#8b949e;font-size:11px;margin-top:4px;line-height:1.45}}
.nine-sum{{margin:8px 0 10px;color:#c9d1d9;font-size:13px;display:flex;gap:10px;flex-wrap:wrap}}
.nine-sum span{{color:#8b949e}}
@media(max-width:680px){{.nine thead{{display:none}}.nine tr{{display:block;border-bottom:1px solid #21262d;padding:10px 0}}.nine td{{display:block;border:0;padding:3px 0}}.nine td:nth-child(3){{margin-top:4px}}.nine-gates{{grid-template-columns:1fr}}}}
@media(max-width:480px){{.chk .row{{flex-direction:column;gap:2px}}.chk .r{{text-align:left}}}}
</style></head><body><div class="wrap">
<h1>资金温度计 · 量价版的行情判定</h1>
<div class="sub">数据截至 {p['date']} 收盘{lag_note} · 生成于 {p['updated']} · 仅供自己复盘，不构成投资建议</div>
{lag_html}
<div class="hero">
  <div class="regime">{p['regime']} · {label}</div>
  <div class="verdict">沪深300 {p['px']}（{p['above200']:+.1f}% vs 200日线）· 红利/300 比值{'站上' if p['rs_above'] else '跌破'} 60日线</div>
</div>
    {daily_html}{bear_box}
{cards_html}
{nine_html}
<h2>主线候选排名（20日相对沪深300超额）</h2>
<div class="card">
<table><tr><th>#</th><th>板块</th><th>20日超额</th><th>5日超额</th><th>60日位置</th></tr>{sec_html}</table>
</div>
<details><summary>红利 / 沪深300 相对强弱曲线（近 120 交易日 · 蓝=比值 灰=60日线）</summary>
<div class="card" style="padding:12px 16px">
<svg viewBox="0 0 560 120" preserveAspectRatio="none">
<path d="{ma_d}" fill="none" stroke="#6e7681" stroke-width="1.2" stroke-dasharray="4 3"/>
<path d="{rs_d}" fill="none" stroke="#58a6ff" stroke-width="1.8"/>
</svg>
<div class="sub">区间 {lo:.4f} ~ {hi:.4f} · 上行=钱在往红利躲，下行=钱在往进攻跑</div>
</div></details>
{vchart}
{basis_html}
<div class="foot">口径：量能分位=20日均成交额在过去一年分布中的位置；相对强弱=中证红利收盘/沪深300收盘；基差=期货收盘−现货指数，主力合约按持仓量拼接。数据源：中证指数官网、新浪期货、东方财富。{p['src_note']}</div>
</div></body></html>"""


if __name__ == "__main__":
    main()
