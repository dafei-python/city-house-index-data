#!/usr/bin/env python3
"""生成 price_text.json：按「误差≤1000 元给元级、否则给万级」的规则，
从百城样本均价（新房 / 二手）生成展示文本。

数据来源：中指云 百城价格指数页（2026-10），一次请求内含 100 城。
"""
import glob
import json
import re
import sys

NEW_URL = "https://www.cih-index.com/data/index/newHouse.html"
ESF_URL = "https://www.cih-index.com/data/index/esfHouse.html"

# 已用官方成交均价核验、误差≤1000 元，保持精确展示（不写入 price_text）
EXACT = {"沈阳", "长沙"}

# 元级展示阈值：样本价 15% 误差仍 ≤1000 元 → 6,666 元以下
YUAN_MAX = 8000


def parse(path):
    s = open(path, encoding="utf-8", errors="ignore").read()
    out = {}
    for city, avg in re.findall(r'"city":"([^"]{2,10})","average":([0-9.]+)', s):
        if city != "全国(百城)":
            out[city] = float(avg)
    return out


def display(price, kind):
    """返回 (value, unit, note)。价格是全市挂牌/公示均价，note 给出成交估算区间。"""
    lo, hi = round(price * 0.85), round(price * 0.95)
    tag = "新房" if kind == "NEW" else "二手"
    note = f"安居客全市{tag}挂牌均价（2026-11）；实际成交约 {lo:,}–{hi:,} 元/㎡（估算）"
    if price <= YUAN_MAX:
        return f"{round(price / 10) * 10:,}", "元/㎡", note
    return f"{price / 10000:.1f}", "万/㎡", note


def main():
    aj = json.load(open("data-pipeline/input/anjuke_prices_202611.json"))
    idx = json.load(open(sorted(glob.glob("data/index_70cities_*.json"))[-1]))
    cities = [c["city"] if isinstance(c, dict) else c for c in idx.get("cities", [])]

    items = []
    covered = {"NEW": 0, "SECOND": 0}
    for city in cities:
        rec = aj.get(city) or {}
        if rec.get("new") and city not in EXACT:
            v, u, n = display(rec["new"], "NEW")
            items.append({"city": city, "kind": "NEW", "value": v, "unit": u, "note": n})
            covered["NEW"] += 1
        if rec.get("used"):
            v, u, n = display(rec["used"], "SECOND")
            items.append({"city": city, "kind": "SECOND", "value": v, "unit": u, "note": n})
            covered["SECOND"] += 1

    doc = {
        "period": "2026-10",
        "source": "安居客城市房价页 2026 年 11 月：新房公示均价 + 二手挂牌均价（同源同口径）",
        "source_url": "https://m.anjuke.com/<城市代码>/trendency/",
        "rule": "新房与二手均为全市挂牌/公示均价，同源同口径；≤8000 元给元级约数，否则「X.X 万/㎡」。",
        "items": items,
    }
    with open("data/price_text.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("cities", len(cities), "NEW", covered["NEW"], "SECOND", covered["SECOND"], "items", len(items))
    missing = [c for c in cities if not (aj.get(c) or {}).get("new")]
    print("no_sample:", ",".join(missing))


if __name__ == "__main__":
    sys.exit(main())
