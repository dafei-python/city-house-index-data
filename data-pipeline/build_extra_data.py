#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从已下载的官方原文 HTML 中抽取「省级/城市级 商品房销售面积与销售额」，生成 App 资产：
  app/src/main/assets/data/provinces.json
  app/src/main/assets/data/base_prices.json
  app/src/main/assets/data/deals.json

原则：
- 只使用 *.gov.cn 官方统计公报原文（缓存文件由采集阶段保存，脚本本身不抓新页面）；
- 抽不到绝对值的省份/城市，宁可标记未接入，也不估算；
- 所有价格都能回溯到「面积/销售额」两个官方数字与来源 URL；
- 输出里保留原始匹配句子（evidence），便于人工抽查。
"""

import json
import os
import re
from datetime import datetime

from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")
ASSETS = os.path.abspath(os.path.join(ROOT, "..", "app", "src", "main", "assets", "data"))
CACHE_PROV = os.path.join(ROOT, "cache", "prov")
CACHE_PROV25 = os.path.join(ROOT, "cache", "prov2025")
CACHE_CITY = os.path.join(ROOT, "cache", "city")
CACHE_DEALS = os.path.join(ROOT, "cache", "deals")

PROVINCES = ["北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
             "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
             "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
             "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆"]


def load_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            html = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        html = raw.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")
    t = soup.get_text("", strip=False)
    t = t.replace("\u3000", "").replace("\xa0", "")
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"\[[0-9]{1,3}\]", "", t)  # 去掉脚注编号
    return t


AREA_RE = re.compile(r"(?:新建)?商品房销售面积(?:为)?([0-9]+(?:\.[0-9]+)?)(?:万平方米|万平米|万m2|万m²)")
AMOUNT_RE = re.compile(r"(?:新建)?商品房销售额(?:为)?([0-9]+(?:\.[0-9]+)?)亿元")
GROWTH_RE = re.compile(r"(下降|增长|持平|上升)([0-9]+(?:\.[0-9]+)?)?%")


def growth_after(t, end, window=26):
    seg = t[end:end + window]
    m = GROWTH_RE.search(seg)
    if not m:
        return None
    if m.group(1) == "持平":
        return 0.0
    v = float(m.group(2)) if m.group(2) else None
    if v is None:
        return None
    return -v if m.group(1) in ("下降",) else v


def extract(t):
    """返回 area(万㎡)、amount(亿元)、area_yoy、amount_yoy、原文证据"""
    area = AREA_RE.search(t)
    amount = AMOUNT_RE.search(t)
    out = {
        "area": float(area.group(1)) if area else None,
        "amount": float(amount.group(1)) if amount else None,
        "area_yoy": growth_after(t, area.end()) if area else None,
        "amount_yoy": growth_after(t, amount.end()) if amount else None,
        "evidence": "",
    }
    if area:
        out["evidence"] += t[max(0, area.start() - 8):area.end() + 24]
    if amount:
        out["evidence"] += (" | " if out["evidence"] else "") + t[max(0, amount.start() - 8):amount.end() + 24]
    return out


def price_of(area, amount):
    if not area or not amount:
        return None
    p = amount / area * 10000.0  # 亿元 / 万㎡ -> 元/㎡
    return round(p, 1) if 1200 <= p <= 160000 else None


def price_yoy_of(area_yoy, amount_yoy):
    if area_yoy is None or amount_yoy is None:
        return None
    if area_yoy <= -100:
        return None
    return round(((1 + amount_yoy / 100.0) / (1 + area_yoy / 100.0) - 1) * 100.0, 1)


def main():
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    prov_src = {x["province"]: x for x in json.load(open(os.path.join(OUT, "province_sources.json")))} \
        if os.path.exists(os.path.join(OUT, "province_sources.json")) else {}
    prov25_src = {x["province"]: x for x in json.load(open(os.path.join(OUT, "province_sources_2025.json")))} \
        if os.path.exists(os.path.join(OUT, "province_sources_2025.json")) else {}

    # ---- 省级：优先 2025，其次 2024 ----
    provinces = []
    for name in PROVINCES:
        chosen = None
        for year, cache_dir, src in ((2025, CACHE_PROV25, prov25_src), (2024, CACHE_PROV, prov_src)):
            path = os.path.join(cache_dir, f"{name}.html")
            if not os.path.exists(path) or os.path.getsize(path) < 20000:
                continue
            info = extract(load_text(path))
            price = price_of(info["area"], info["amount"])
            if price:
                chosen = (year, cache_dir, src.get(name, {}), info, price)
                break
            if chosen is None:
                chosen = (year, cache_dir, src.get(name, {}), info, None)  # 保底记录
        if chosen is None:
            provinces.append({
                "name": name, "year": None, "ok": False,
                "area_wan_sqm": None, "amount_yi": None, "price_yuan_sqm": None,
                "yoy_pct": None, "source_label": "", "source_url": "",
                "note": "未找到可核实的官方统计公报原文（仅PDF/需登录/反爬/正文未披露）"
            })
            continue
        year, cache_dir, meta, info, price = chosen
        ok = price is not None
        note = ""
        if not ok:
            if info["area"] and not info["amount"]:
                note = "公报只披露销售面积，未披露销售额，无法计算均价"
            elif info["amount"] and not info["area"]:
                note = "公报只披露销售额，未披露销售面积，无法计算均价"
            else:
                note = "公报只给出增速、未给出绝对值，无法计算均价"
        provinces.append({
            "name": name, "year": year, "ok": ok,
            "area_wan_sqm": info["area"], "amount_yi": info["amount"],
            "price_yuan_sqm": price, "yoy_pct": price_yoy_of(info["area_yoy"], info["amount_yoy"]),
            "source_label": meta.get("title") or f"{year}年{name}统计公报",
            "source_url": meta.get("url", ""),
            "note": note, "evidence": info["evidence"]
        })

    ok_count = sum(1 for p in provinces if p["ok"])
    print(f"省级：{ok_count}/{len(PROVINCES)} 有可核实均价")

    years = [p["year"] for p in provinces if p["year"]]
    # 全国：优先用国家统计局最新年度「全国房地产市场基本情况」页（独立抓取，见 national.json）
    nat = None
    nat_path = os.path.join(OUT, "national.json")
    if os.path.exists(nat_path):
        nat = json.load(open(nat_path))
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_title": "各省级统计局《国民经济和社会发展统计公报》",
        "source_url": "https://www.stats.gov.cn/sj/tjgb/",
        "year": max(years) if years else None,
        "caliber": "年度 · 商品房口径（住宅+办公楼+商业营业用房），全省/全区/全市（含县市）",
        "provinces": provinces,
        "national": nat or {}
    }
    with open(os.path.join(ASSETS, "provinces.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # ---- 城市基准价：先看市级公报，没有则省级代替市级 ----
    city_src = []
    for fn in ("city_sources_a.json", "city_sources_b.json"):
        p = os.path.join(OUT, fn)
        if os.path.exists(p):
            city_src += json.load(open(p))
    city_meta = {x["city"]: x for x in city_src}

    prov_map = {p["name"]: p for p in provinces}
    # 城市 -> 省（与 App 内 CITY_PROVINCE 保持一致）
    city_to_prov = {
        "北京": "北京", "天津": "天津", "上海": "上海", "重庆": "重庆",
        "唐山": "河北", "秦皇岛": "河北", "石家庄": "河北", "太原": "山西",
        "包头": "内蒙古", "呼和浩特": "内蒙古",
        "丹东": "辽宁", "锦州": "辽宁", "沈阳": "辽宁", "大连": "辽宁",
        "吉林": "吉林", "长春": "吉林", "牡丹江": "黑龙江", "哈尔滨": "黑龙江",
        "无锡": "江苏", "徐州": "江苏", "扬州": "江苏", "南京": "江苏",
        "温州": "浙江", "杭州": "浙江", "金华": "浙江", "宁波": "浙江",
        "蚌埠": "安徽", "合肥": "安徽", "安庆": "安徽",
        "福州": "福建", "泉州": "福建", "厦门": "福建",
        "九江": "江西", "南昌": "江西", "赣州": "江西",
        "济南": "山东", "烟台": "山东", "青岛": "山东", "济宁": "山东",
        "郑州": "河南", "洛阳": "河南", "平顶山": "河南",
        "武汉": "湖北", "宜昌": "湖北", "襄阳": "湖北",
        "长沙": "湖南", "岳阳": "湖南", "常德": "湖南",
        "广州": "广东", "深圳": "广东", "韶关": "广东", "惠州": "广东", "湛江": "广东",
        "南宁": "广西", "桂林": "广西", "北海": "广西",
        "海口": "海南", "三亚": "海南",
        "成都": "四川", "泸州": "四川", "南充": "四川",
        "贵阳": "贵州", "遵义": "贵州",
        "昆明": "云南", "大理": "云南",
        "西安": "陕西", "兰州": "甘肃", "西宁": "青海",
        "银川": "宁夏", "乌鲁木齐": "新疆"
    }
    idx70 = json.load(open(os.path.join(ASSETS, "index_70cities_2026-09.json")))

    base = {}
    level_city, level_prov, none_list = [], [], []
    for city in [c["city"] for c in idx70["cities"]]:
        meta = city_meta.get(city)
        info = None
        if meta and meta.get("status") == "ok":
            path = os.path.join(CACHE_CITY, f"{city}.html")
            if os.path.exists(path) and meta.get("file", "").endswith(".html"):
                info = extract(load_text(path))
        if info and price_of(info["area"], info["amount"]):
            price = price_of(info["area"], info["amount"])
            base[city] = {
                "level": "city", "base_price": price, "base_year": 2024,
                "caliber": "商品房（含商业、办公；全市口径，可能含县市）",
                "source_label": meta.get("title") or f"{city}市2024年统计公报",
                "source_url": meta.get("url", ""),
                "area_wan_sqm": info["area"], "amount_yi": info["amount"],
                "note": "", "evidence": info["evidence"]
            }
            level_city.append(city)
            continue
        prov = city_to_prov.get(city)
        pv = prov_map.get(prov) if prov else None
        if pv and pv.get("ok"):
            base[city] = {
                "level": "province", "base_price": pv["price_yuan_sqm"], "base_year": pv["year"],
                "caliber": f"{pv['year']}年{pv['name']}商品房均价（销售额÷销售面积，含商业办公）",
                "source_label": pv["source_label"], "source_url": pv["source_url"],
                "note": "省级均价代替市级：该市未找到可核实的分城市均价。省会/一线城市的真实价格通常高于全省均值，误差可能很大。",
                "evidence": pv.get("evidence", "")
            }
            level_prov.append(city)
        else:
            none_list.append(city)

    base_payload = {
        "schema_version": 1,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "推算价 = 基准价 ×（1 + 最新同比指数变化）。基准价取官方统计公报「商品房销售额 ÷ 销售面积」。",
        "caliber": "商品房（含商业、办公），与月度住宅指数口径不同，存在口径误差。",
        "cities": base
    }
    with open(os.path.join(ASSETS, "base_prices.json"), "w", encoding="utf-8") as f:
        json.dump(base_payload, f, ensure_ascii=False, indent=1)
    print(f"基准价：市级 {len(level_city)}，省级代替 {len(level_prov)}，无 {len(none_list)}")

    # ---- 成交量（官方当日快照解析）----
    deals = build_deals()
    with open(os.path.join(ASSETS, "deals.json"), "w", encoding="utf-8") as f:
        json.dump(deals, f, ensure_ascii=False, indent=1)

    # ---- 报告 ----
    report = {
        "provinces_ok": ok_count,
        "provinces_detail": [(p["name"], p["year"], p["price_yuan_sqm"], p["yoy_pct"], p["note"]) for p in provinces],
        "base_city_level": level_city,
        "base_province_level": level_prov,
        "base_none": none_list,
        "deals": [(d["city"], d["status"], len(d.get("rows", []))) for d in deals["cities"]],
    }
    json.dump(report, open(os.path.join(OUT, "extra_data_report.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: report[k] for k in ("provinces_ok", "base_city_level", "base_province_level", "base_none")},
                     ensure_ascii=False, indent=1))


def build_deals():
    rows = []

    def add(city, status, portal, url, note, gran, items, fetched):
        rows.append({"city": city, "status": status, "portal": portal, "url": url,
                     "note": note, "granularity": gran, "fetched_at": fetched,
                     "rows": items})

    # 北京：期房/现房/存量房最新一日
    p = os.path.join(CACHE_DEALS, "北京.html")
    if os.path.exists(p):
        t = re.sub(r"\s+", " ", BeautifulSoup(open(p, encoding="utf-8", errors="ignore").read(), "lxml")
                   .get_text(" ", strip=True))
        date = re.search(r"(20\d{2}/\d{1,2}/\d{1,2})期房", t)
        d1 = date.group(1) if date else ""
        items = []
        for label, kw in (("期房", "期房网上签约"), ("现房", "现房网上签约"), ("存量房", "存量房网上签约")):
            seg = t.split(kw)[1][:120] if kw in t else ""
            m1 = re.search(r"网上签约套数\s*(\d+)", seg)
            m2 = re.search(r"网上签约面积\s*(\d+)", seg)
            if m1:
                items.append({"label": f"{label}（{d1}）", "date": d1, "count": int(m1.group(1)),
                              "area": float(m2.group(1)) if m2 else None})
        add("北京", "accessible_static", "北京市住房和城乡建设委员会",
            "http://bjjs.zjw.beijing.gov.cn/eportal/ui?pageId=53607241",
            "官方「房地产数据统计」页，仅最新一日；期房+现房为新房，存量房为二手。", "day", items, "2026-11-07")

    # 杭州：今日商品房累计成交（住宅）
    p = os.path.join(CACHE_DEALS, "杭州.html")
    if os.path.exists(p):
        t = re.sub(r"\s+", " ", BeautifulSoup(open(p, encoding="utf-8", errors="ignore").read(), "lxml")
                   .get_text(" ", strip=True))
        m = re.search(r"住宅\s*(\d+)套\s*([0-9.]+)m²", t)
        items = []
        if m:
            items.append({"label": "新房合计（当日）", "date": "2026-11-07",
                          "count": int(m.group(1)), "area": float(m.group(2))})
        add("杭州", "accessible_static", "杭州透明售房网（数据源：市住保房管网）",
            "https://www.tmsf.com/housingtype/housingtypedetail-4.htm",
            "新房当日成交可接；二手房频道为 WAF 混淆页，未绕行，记未接入。", "day", items, "2026-11-07")

    # 青岛：官方 JSON
    p = os.path.join(CACHE_DEALS, "青岛.json")
    if os.path.exists(p):
        j = json.load(open(p))
        c = j.get("city", {})
        items = [{"label": "新房（全市）", "date": "2026-11-07",
                  "count": c.get("count"), "area": c.get("totalArea")},
                 {"label": "其中住宅", "date": "2026-11-07",
                  "count": c.get("houseCount"), "area": c.get("houseArea")}]
        add("青岛", "accessible_api", "青岛住房网信息发布系统（市不动产登记中心）",
            "https://zhufang.qingdao.gov.cn:8081/getTradeInfo?tradeType=new",
            "官方 AJAX 接口返回 JSON，当日口径；忽略日期参数，无历史回补。", "day", items, "2026-11-07")

    # 苏州：市区合计
    p = os.path.join(CACHE_DEALS, "苏州.html")
    if os.path.exists(p):
        t = re.sub(r"\s+", " ", BeautifulSoup(open(p, encoding="utf-8", errors="ignore").read(), "lxml")
                   .get_text(" ", strip=True))
        d = re.search(r"【\s*(20\d{2}/\d{1,2}/\d{1,2})\s*】", t)
        day = d.group(1) if d else ""
        m = re.search(r"市区合计\s*总计\s*(\d+)\s+([0-9.]+)\s*其中：住宅\s*(\d+)\s+([0-9.]+)", t)
        items = []
        if m:
            items.append({"label": "市区合计（总计）", "date": day, "count": int(m.group(1)), "area": float(m.group(2))})
            items.append({"label": "其中住宅", "date": day, "count": int(m.group(3)), "area": float(m.group(4))})
        add("苏州", "accessible_static", "苏州市商品房销售网上管理系统",
            "http://www.szfcweb.com/", "官方 ASPX 静态表，按区给出小计与住宅，仅当日。", "day", items, "2026-11-07")

    for city, note in (("深圳", "网签明细需政务授权/登录，未获得授权，不展示也不估算。"),
                       ("武汉", "网签明细需政务授权/登录，未获得授权，不展示也不估算。")):
        add(city, "login_required", "深圳市住房和建设局" if city == "深圳" else "武汉市住房和城市更新局",
            "", note, "none", [], "2026-11-07")

    return {
        "schema_version": 1,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "caliber": "当月/当日网签套数与面积；不代表挂牌量，也不代表看房热度；网签有滞后，退房/撤销会回改。",
        "disclaimer": "官方页面只提供最新一日，本 App 保存的是抓取当日快照，不做历史回补、不做任何估算。",
        "cities": rows
    }


if __name__ == "__main__":
    main()
