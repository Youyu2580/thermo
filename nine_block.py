# -*- coding: utf-8 -*-
"""九流派速扫表：把收盘后能观测到的客观数据，按九个流派的关注点各切一刀。

设计原则：
1. 只写「今天发生了什么」，每个数字都能在页脚找到来源，不做预测；
2. 每条动作都是本文件里写死的机械映射（见 build() 的 if/elif），不接大模型；
3. 任何一路数据失败只让对应那行降级，绝不影响主页面。

数据来源：
- 腾讯行情日线：指数、龙头个股的当日涨跌 / 20 日收益 / 60 日位置
- 东财大盘资金流：沪市 + 深市主力净流入（secid=1.000001 / 0.399001）
- 新浪全市场快照：涨跌家数、涨跌停、成交额、市值分层（由 daily_block 提供）
- 中证指数：全指 PE 及分位（由 update.py 提供）
- 新浪期货：IF/IH/IC/IM 基差率（由 basis_block 提供）
- 中债：10 年国债收益率（由 daily_block 提供）
"""
import json
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/122.0 Safari/537.36")
CTX = ssl.create_default_context()

# 指数 / 个股：腾讯代码 -> 展示名
INDEX = {
    "sz": ("sh000001", "上证指数"),
    "cyb": ("sz399006", "创业板指"),
    "kc50": ("sh000688", "科创50"),
    "hs300": ("sh000300", "沪深300"),
    "zz500": ("sh000905", "中证500"),
    "zz1000": ("sh000852", "中证1000"),
    "sz50": ("sh000016", "上证50"),
    "div": ("sh000922", "中证红利"),
    "cons": ("sh000932", "主要消费"),
    "fin": ("sh000934", "金融"),
    "it": ("sh000935", "信息技术"),
    "bank": ("sz399986", "中证银行"),
    "chip": ("sz399363", "国证芯片"),
}
STOCK = {
    "md": ("sz000333", "美的集团"),
    "mt": ("sh600519", "贵州茅台"),
    "cmb": ("sh600036", "招商银行"),
    "pa": ("sh601318", "中国平安"),
    "bf": ("sz002371", "北方华创"),
    "zj": ("sz300308", "中际旭创"),
    "hw": ("sh688256", "寒武纪"),
}


def _get(url, ref, timeout=20, tries=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref})
            with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa
            last = e
    raise last


def kline(sym, n=120):
    """腾讯前复权日线：返回 [{d,o,c,h,l}]，失败返回 []。
    web.ifzq 偶发 501，ifzq 主域更稳，两个都试。"""
    last = None
    for host in ("https://ifzq.gtimg.cn", "https://web.ifzq.gtimg.cn"):
        try:
            u = f"{host}/appstock/app/fqkline/get?param={sym},day,,,{n},qfq"
            j = json.loads(_get(u, "https://gu.qq.com/"))
            node = (j.get("data") or {}).get(sym) or {}
            out = []
            for x in (node.get("qfqday") or node.get("day") or []):
                try:
                    out.append({"d": x[0], "o": float(x[1]), "c": float(x[2]),
                                "h": float(x[3]), "l": float(x[4]), "v": float(x[5])})
                except Exception:
                    continue
            if out:
                return out
        except Exception as e:  # noqa
            last = e
    if last:
        print(f"[warn] {sym} 日线取数失败: {last}")
    return []


def stat(rows):
    """把日线压成一行统计：当日涨跌、5/20 日收益、60 日位置、均线距离。"""
    if len(rows) < 2:
        return None
    c = [r["c"] for r in rows]
    last = rows[-1]
    prev = rows[-2]
    hi60, lo60 = max(c[-60:]), min(c[-60:])
    ma20 = sum(c[-20:]) / 20 if len(c) >= 20 else None
    ma60 = sum(c[-60:]) / 60 if len(c) >= 60 else None
    return {
        "d": last["d"], "c": last["c"], "h": last["h"], "l": last["l"], "o": last["o"],
        "chg": (last["c"] / prev["c"] - 1) * 100 if prev["c"] else 0.0,
        "r5": (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else None,
        "r20": (c[-1] / c[-21] - 1) * 100 if len(c) >= 21 else None,
        "pos": round((c[-1] - lo60) / (hi60 - lo60) * 100) if hi60 > lo60 else None,
        "above20": (c[-1] / ma20 - 1) * 100 if ma20 else None,
        "above60": (c[-1] / ma60 - 1) * 100 if ma60 else None,
    }


def _flow_yi(secid):
    """东财大盘资金流：主力净流入（元）-> 亿元。接口只回最新一行，够用。"""
    u = ("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?"
         f"lmt=0&klt=101&secid={secid}"
         "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65")
    j = json.loads(_get(u, "https://data.eastmoney.com/zjlx/dpzjlx.html", timeout=15))
    ks = ((j.get("data") or {}).get("klines") or [])
    if not ks:
        return None
    parts = str(ks[-1]).split(",")
    if len(parts) < 6:
        return None
    # 字段：日期, 主力, 小单, 中单, 大单, 超大单（主力=大单+超大单）
    return float(parts[1]) / 1e8


def _prev_payload():
    """上一版数据：本地优先（本机调试），否则读线上 Pages（云端没有 site/ 历史）。"""
    try:
        with open("site/data.json", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        pass
    try:
        txt = _get("https://youyu2580.github.io/thermo/data.json",
                   "https://youyu2580.github.io/", timeout=8, tries=1)
        return json.loads(txt) or {}
    except Exception:
        return {}


def _p(v, d=2):
    if v is None:
        return '<span class="dim">—</span>'
    cls = "up" if v >= 0 else "dn"
    return f'<span class="{cls}">{v:+.{d}f}%</span>'


def _yi(v):
    return f"{v:+.0f} 亿" if v is not None else "—"


def _stance(action):
    """动作 -> 立场：bull 看多 / neutral 观望 / bear 看空。复合动作按整体归类。"""
    return {
        "持有": "bull", "减配": "bear", "等确认": "neutral", "回避": "bear",
        "防御/控仓": "bear", "进攻": "bull", "中性": "neutral", "防守": "bear",
        "超配/持有": "bull", "减仓": "bear", "均衡": "neutral",
        "局部参与/控仓": "neutral", "可参与": "bull", "观望": "neutral",
        "持有价值": "bull", "转成长": "neutral", "分化对待": "neutral",
        "观察": "neutral", "卡位": "neutral", "控仓": "neutral",
    }.get(action, "neutral")


def _tone(action):
    return {"bull": "good", "neutral": "warn", "bear": "bad"}[_stance(action)]


def build(payload):
    """payload: update.py 已算好的主数据（daily/sectors/basis/val/today 等）。"""
    d = payload.get("date") or ""
    daily = payload.get("daily") or {}
    val = payload.get("val") or {}
    today = payload.get("today") or {}
    vol_pct = payload.get("vol_pct")
    above200 = payload.get("above200")

    idx = {}
    for k, (sym, _name) in INDEX.items():
        try:
            s = stat(kline(sym))
            if s:
                idx[k] = s
        except Exception:
            continue
    stk = {}
    for k, (sym, _name) in STOCK.items():
        try:
            s = stat(kline(sym, 60))
            if s:
                stk[k] = s
        except Exception:
            continue
    if "hs300" not in idx:
        raise RuntimeError("沪深300 行情取不到，九流派跳过")

    # 资金流：沪市 + 深市（东财大盘口径）
    flow = None
    try:
        hf, sf = _flow_yi("1.000001"), _flow_yi("0.399001")
        if hf is not None and sf is not None:
            flow = hf + sf
    except Exception:
        flow = None

    # 成交额：daily_block 的沪深 A 股合计（亿元）。只有上一版是更早的交易日才做日环比；
    # 同一天手动重跑时 Pages 已经是今天的数据，再比就是自己比自己（0 亿、1.00 倍）。
    amt = daily.get("amt")
    prev = _prev_payload()
    prev_amt = (prev.get("daily") or {}).get("amt")
    prev_date = prev.get("date")
    if amt and prev_amt and prev_date and prev_date != d:
        amt_ratio = amt / prev_amt
        d_amt = amt - prev_amt
    else:
        amt_ratio, d_amt = None, None
    # 日环比拿不到时退回指数口径量比（沪深300 今日成交额 / 20 日均额）
    vol_ratio = amt_ratio if amt_ratio is not None else today.get("amt_ratio")

    cur = daily.get("curve") or {}
    tier = {t.get("name"): t for t in (daily.get("tiers") or [])}
    big = (tier.get(">1000亿") or {}).get("up_ratio")
    small = (tier.get("<50亿") or {}).get("up_ratio")

    basis_rows = {r.get("prod"): r for r in ((payload.get("basis") or {}).get("rows") or [])}
    ifr = basis_rows.get("IF") or {}

    hs = idx.get("hs300")
    div = idx.get("div") or {}
    fin = idx.get("fin") or {}
    cons = idx.get("cons") or {}
    bank = idx.get("bank") or {}
    kc = idx.get("kc50") or {}
    cyb = idx.get("cyb") or {}
    zz1000 = idx.get("zz1000") or {}
    sz50 = idx.get("sz50") or {}
    it = idx.get("it") or {}
    chip = idx.get("chip") or {}
    sz = idx.get("sz") or {}

    pe, pe_pct = val.get("pe"), val.get("pct")
    y10 = cur.get("y10")
    bp = cur.get("d10_5d")

    rows = []

    # A 价值投资：红利 / 金融 / 消费 / 银行
    facts = (f"中证红利 {_p(div.get('chg'))}、金融 {_p(fin.get('chg'))}、"
             f"主要消费 {_p(cons.get('chg'))}、中证银行 {_p(bank.get('chg'))}；"
             f"红利 20 日超额 {_p((div.get('r20') or 0) - (hs.get('r20') or 0))}、"
             f"60 日位置 {div.get('pos', '—')}%")
    if div.get("above60") is not None and div["above60"] > 0:
        act, note = "持有", "低估值托底，红利仍在 60 日线上方"
    else:
        act, note = "减配", "红利跌破 60 日线，防御属性失效"
    rows.append({"k": "A", "name": "价值投资", "facts": facts, "action": act, "note": note})

    # B 成长投资：科创50 / 创业板 / 中证1000
    facts = (f"科创50 {_p(kc.get('chg'))}、创业板 {_p(cyb.get('chg'))}、"
             f"中证1000 {_p(zz1000.get('chg'))}；成长内部价差 {_p((kc.get('chg') or 0) - (cyb.get('chg') or 0))}"
             f"（科创50−创业板），20 日科创50 {_p(kc.get('r20'))}")
    if (kc.get("chg") or 0) > 0 and (cyb.get("chg") or 0) < 0:
        act, note = "等确认", "成长内部一强一弱，别用科创50代表全部成长"
    elif (kc.get("chg") or 0) < 0 and (cyb.get("chg") or 0) < 0 and (zz1000.get("chg") or 0) < 0:
        act, note = "回避", "成长三条线同步走弱"
    elif (kc.get("chg") or 0) < 0 < (cyb.get("chg") or 0):
        act, note = "等确认", "创业板强、科创50弱，成长内部在换手"
    elif (kc.get("chg") or 0) > 0 and (cyb.get("chg") or 0) > 0 and (zz1000.get("chg") or 0) < 0:
        act, note = "等确认", "科创/创业强、中证1000 没跟上，别把小盘当成长"
    else:
        act, note = "持有", "成长内部同向，趋势没坏"
    rows.append({"k": "B", "name": "成长投资", "facts": facts, "action": act, "note": note})

    # C 宏观策略：量 + 资金 + 利率 + 贴水
    parts = []
    if amt:
        if d_amt is not None:
            parts.append(f"两市成交 {amt:.0f} 亿，{_yi(d_amt)}，环比 {amt_ratio:.2f}")
        elif today.get("amt_ratio") is not None:
            parts.append(f"两市成交 {amt:.0f} 亿，沪深300 量比 {today['amt_ratio']:.2f}")
        else:
            parts.append(f"两市成交 {amt:.0f} 亿")
    if vol_pct is not None:
        parts.append(f"20 日量能分位 {vol_pct}%")
    if flow is not None:
        parts.append(f"东财口径主力 {_yi(flow)}")
    if y10:
        parts.append(f"10Y 国债 {y10:.2f}%（5 日 {bp:+.0f}bp）" if bp is not None else f"10Y 国债 {y10:.2f}%")
    if ifr.get("rate") is not None:
        parts.append(f"IF 基差 {ifr['rate']:+.2f}%（{ifr.get('pct', '—')}% 分位）")
    facts = "；".join(parts) if parts else "成交额 / 利率 / 基差均未取到"
    if flow is not None and flow < 0 and vol_ratio is not None and vol_ratio < 1:
        act, note = "防御/控仓", "缩量 + 主力净流出，先控仓等放量"
    elif flow is not None and flow > 0 and vol_ratio is not None and vol_ratio > 1.1:
        act, note = "进攻", "放量 + 主力净流入，风险偏好回升"
    elif flow is not None and flow <= -100:
        act, note = "防御/控仓", "主力净流出超百亿，先控仓"
    elif flow is not None and flow >= 100:
        act, note = "进攻", "主力净流入超百亿，风险偏好回升"
    else:
        act, note = "中性", "量价不共振，等方向"
    rows.append({"k": "C", "name": "宏观策略", "facts": facts, "action": act, "note": note})

    # D 技术分析：上证日内形态 + 均线位置
    if sz:
        rng = (sz["h"] - sz["l"]) or 1
        rng_pos = (sz["c"] - sz["l"]) / rng * 100
        facts = (f"上证 {sz['c']:.2f}（{_p(sz['chg'])}），日内高 {sz['h']:.2f} / "
                 f"低 {sz['l']:.2f}，收盘落在日内 {rng_pos:.0f}% 位置；"
                 f"距 20 日线 {_p(sz.get('above20'))}，沪深300 距 200 日线 {_p(above200)}")
    else:
        rng_pos = None
        facts = f"上证行情未取到；沪深300 距 200 日线 {_p(above200)}"
    if above200 is not None and above200 < 0:
        act, note = "防守", "指数在年线下方，反抽不追"
    elif rng_pos is not None and rng_pos < 35 and sz.get("chg", 0) < 0:
        act, note = "等确认", "冲高回落收在日内低位，方向未明"
    elif (sz.get("above20") or -1) > 0:
        act, note = "持有", "收盘在 20 日线上方，短线没走坏"
    else:
        act, note = "等确认", "20 日线仍在上方压着"
    rows.append({"k": "D", "name": "技术分析", "facts": facts, "action": act, "note": note})

    # E 中国机构：必选消费 + 大金融 + 龙头
    lip = f"主要消费 {_p(cons.get('chg'))}、金融 {_p(fin.get('chg'))}、红利 {_p(div.get('chg'))}"
    heads = "、".join(
        f"{STOCK[k][1]} {_p(stk[k]['chg'])}" for k in ("md", "mt", "cmb", "pa") if k in stk)
    if heads:
        lip += f"；龙头：{heads}"
    if (cons.get("chg") or 0) > 0 and (fin.get("chg") or 0) > 0:
        act, note = "超配/持有", "必选消费 + 大金融同步走强"
    elif (cons.get("chg") or 0) < 0 and (fin.get("chg") or 0) < 0:
        act, note = "减仓", "机构两条主线同时走弱"
    else:
        act, note = "均衡", "主线分化，别单押一头"
    rows.append({"k": "E", "name": "中国机构", "facts": lip, "action": act, "note": note})

    # F 游资短线：涨跌停 + 赚钱效应
    lu, ld = daily.get("lu"), daily.get("ld")
    facts = (f"涨停 {lu if lu is not None else '—'} 家、跌停 {ld if ld is not None else '—'} 家；"
             f"全市场涨 {daily.get('up', '—')} / 跌 {daily.get('dn', '—')}"
             f"（涨家占比 {daily.get('up_ratio', '—')}%），中位 {_p(daily.get('med'))}")
    if lu is not None and ld is not None and ld > lu:
        act, note = "局部参与/控仓", "跌停多于涨停，容错低"
    elif lu is not None and lu >= 40 and (daily.get("up_ratio") or 0) >= 55:
        act, note = "可参与", "涨停扩散、赚钱效应回升"
    else:
        act, note = "观望", "题材分化，等赚钱效应确认"
    rows.append({"k": "F", "name": "游资短线", "facts": facts, "action": act, "note": note})

    # G 量化因子：价值 vs 成长、小盘 vs 大盘
    spread = (div.get("r20") or 0) - (kc.get("r20") or 0)
    spread5 = None
    try:
        dv_c = [r["c"] for r in kline("sh000922")]
        kc_c = [r["c"] for r in kline("sh000688")]
        if len(dv_c) >= 26 and len(kc_c) >= 26:
            spread5 = ((dv_c[-6] / dv_c[-26] - 1) - (kc_c[-6] / kc_c[-26] - 1)) * 100
    except Exception:
        pass
    small_big = (zz1000.get("r20") or 0) - (sz50.get("r20") or 0)
    trend = ""
    if spread5 is not None:
        trend = "收敛" if abs(spread) < abs(spread5) else "走阔"
    facts = (f"红利−科创50 的 20 日价差 {spread:+.2f}pct"
             + (f"（5 日前 {spread5:+.2f}，{trend}）" if spread5 is not None else "")
             + f"；中证1000−上证50 20 日 {small_big:+.2f}pct；全指 PE {pe if pe else '—'}"
             + (f"（{pe_pct}% 分位）" if pe_pct is not None else ""))
    if spread > 0:
        act, note = "持有价值", f"价值占优，价差在{trend or '维持'}"
    else:
        act, note = "转成长", "成长占优，价值别再加"
    rows.append({"k": "G", "name": "量化因子", "facts": facts, "action": act, "note": note})

    # H 科技领袖：芯片 / 信息技术 + 龙头
    facts = (f"国证芯片 {_p(chip.get('chg'))}、信息技术 {_p(it.get('chg'))}、科创50 {_p(kc.get('chg'))}")
    tech = "、".join(
        f"{STOCK[k][1]} {_p(stk[k]['chg'])}" for k in ("bf", "zj", "hw") if k in stk)
    if tech:
        facts += f"；龙头：{tech}"
    if (chip.get("chg") or 0) > 0 and (it.get("chg") or 0) > 0:
        act, note = "分化对待", "设备/材料强，算力看龙头"
    elif (chip.get("chg") or 0) < 0 and (it.get("chg") or 0) < 0:
        act, note = "回避", "科技整体走弱"
    else:
        act, note = "观察", "内部强弱不一，等主线"
    rows.append({"k": "H", "name": "科技领袖", "facts": facts, "action": act, "note": note})

    # I 卡位猎手：大小盘分化 + 拥挤度 + 位置
    facts = (f">1000 亿市值涨家占比 {big if big is not None else '—'}%、"
             f"<50 亿 {small if small is not None else '—'}%"
             + (f"（差 {big - small:+.1f}pct）" if big is not None and small is not None else "")
             + f"；前 5% 个股吃掉 {daily.get('conc', '—')}% 成交额；"
             f"红利 60 日位置 {div.get('pos', '—')}%、科创50 {kc.get('pos', '—')}%")
    if big is not None and small is not None and big - small >= 20:
        act, note = "卡位", "钱在大票，小票别接"
    elif big is not None and small is not None and small - big >= 20:
        act, note = "卡位", "钱在小票，别恋战"
    elif (daily.get("conc") or 0) >= 45:
        act, note = "控仓", "成交太挤，别追高"
    else:
        act, note = "卡位", "等低位分批，不追高"
    rows.append({"k": "I", "name": "卡位猎手", "facts": facts, "action": act, "note": note})

    for r in rows:
        r["stance"] = _stance(r["action"])
        r["tone"] = {"bull": "good", "neutral": "warn", "bear": "bad"}[r["stance"]]

    bull = [r["name"] for r in rows if r["stance"] == "bull"]
    bear = [r["name"] for r in rows if r["stance"] == "bear"]
    neu = len(rows) - len(bull) - len(bear)
    # 共识公式借自 UZI-Skill：neutral 按 0.6 计权（既不坑、也不是心头好）
    cons = round((len(bull) + 0.6 * neu) / len(rows) * 100)
    if cons >= 80:
        verdict, vc = "进攻", "good"
    elif cons >= 65:
        verdict, vc = "偏多", "good"
    elif cons >= 50:
        verdict, vc = "中性", "warn"
    elif cons >= 35:
        verdict, vc = "谨慎", "bad"
    else:
        verdict, vc = "防守", "bad"
    if bull and bear:
        split = f"{'、'.join(bull)} 看多 vs {'、'.join(bear)} 看空 —— 分歧本身才是今天的信号"
    elif bull:
        split = f"{'、'.join(bull)} 看多，其余观望"
    elif bear:
        split = f"{'、'.join(bear)} 看空，其余观望"
    else:
        split = "九派全在观望，没有方向"

    macro_bits = []
    if vol_pct is not None:
        macro_bits.append(f"量能分位 {vol_pct}%")
    if flow is not None:
        macro_bits.append(f"主力 {flow:+.0f} 亿")
    if ifr.get("rate") is not None:
        macro_bits.append(f"IF 基差 {ifr['rate']:+.2f}%")
    if y10:
        macro_bits.append(f"10Y {y10:.2f}%")
    gates = {
        "market": (payload.get("regime") or "—")
                  + ("（沪深300 在年线下方）" if payload.get("bear") else "（沪深300 在年线上方）"),
        "macro": " · ".join(macro_bits) or "—",
        "stop": ("年线下方：底仓≤3成，站回 20 日线补 1/3" if payload.get("bear")
                 else "年线上方：按计划持有，单笔止损 −8%"),
    }
    return {
        "date": d,
        "rows": rows,
        "summary": {"cons": cons, "verdict": verdict, "vc": vc,
                    "bull": bull, "bear": bear, "neu": neu,
                    "split": split, "gates": gates},
        "src_flow": flow,
        "ts": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
    }


def html(d):
    if not d or not d.get("rows"):
        return ('<h2>九流派速扫表</h2><div class="chk">'
                '<div class="n">今天的九流派数据没取到（休市或接口异常），明天再看。</div></div>')
    s = d.get("summary") or {}
    g = s.get("gates") or {}
    tr = ""
    for r in d["rows"]:
        tr += (f'<tr><td class="nine-k"><span class="nine-dot {r["tone"]}"></span>'
               f'<span class="nine-id">{r["k"]}</span>{r["name"]}</td>'
               f'<td class="nine-f">{r["facts"]}</td>'
               f'<td class="nine-a"><span class="badge {r["tone"]}">{r["action"]}</span>'
               f'<div class="nine-note">{r["note"]}</div></td></tr>')
    return f"""<h2>九流派速扫表 · 每日大盘（{d['date']}）</h2>
<div class="nine-sum"><b class="{s.get('vc', 'warn')}">九派共识 {s.get('cons', 0)} / 100 · {s.get('verdict', '—')}</b>
<span>看多 {len(s.get('bull') or [])} · 观望 {s.get('neu', 0)} · 看空 {len(s.get('bear') or [])}</span>
<span>{s.get('split', '')}</span></div>
<div class="nine-gates">
<div><b>市况闸</b>{g.get('market', '—')}</div>
<div><b>宏观闸</b>{g.get('macro', '—')}</div>
<div><b>止损闸</b>{g.get('stop', '—')}</div>
</div>
<div class="card nine-wrap"><table class="nine">
<thead><tr><th>流派</th><th>今日触发的客观事实（带真实数字）</th><th>动作倾向</th></tr></thead>
<tbody>{tr}</tbody></table></div>
<details class="nine-legend"><summary>九派各看什么（口径说明）</summary>
<div class="sub">A 价值：红利/金融/消费的相对强弱与 60 日线；B 成长：科创50/创业板/中证1000 的内部分化；
C 宏观：成交额、量能分位、主力资金、国债、IF 基差；D 技术：上证日内位置与均线；E 机构：消费+金融+龙头；
F 游资：涨停/跌停与赚钱效应；G 量化：价值−成长价差、大小盘价差、PE 分位；H 科技：芯片/信息技术+龙头；
I 卡位：大小盘涨家差、成交集中度、60 日位置。</div></details>
<div class="sub">口径：涨跌停按板分层（主板 10% / 双创 20% / 北交所 30% / ST 5%）；「涨家占比」不含平盘；成交额为沪深 A 股合计；
基差为主力合约、分位窗口 250 日；动作倾向是规则映射，不是预测。数据源：腾讯行情 / 东财资金流 / 新浪快照 / 中证指数 / 中债。生成 {d.get('ts', '')}</div>"""
