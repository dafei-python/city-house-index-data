#!/usr/bin/env python3
"""省份均价口径核算：全省商品房均价 vs 省会/直辖市实际住宅价。

只读现有数据 + 公开对照值，不修改入库数据。
输出 data-pipeline/backtest/backtest_4_province.csv 与 summary_province.json
"""
import csv
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(__file__).resolve().parent / "backtest"
OUT.mkdir(exist_ok=True)

prov = json.loads((ROOT / "data/provinces.json").read_text(encoding="utf-8"))
prices = json.loads((ROOT / "data/base_prices.json").read_text(encoding="utf-8"))["cities"]

# 中指院 2026-10 新建住宅样本均价（公开页前 10 城）
ZSY = {"上海": 65730, "北京": 47594, "南京": 26193, "天津": 15741, "广州": 25513,
       "成都": 15594, "杭州": 34386, "武汉": 13536, "深圳": 53703, "重庆": 11341}

# 省 → 该省在 40 城锚点中的城市
PROV_CITIES = {
    "北京": ["北京"], "天津": ["天津"], "上海": ["上海"], "重庆": ["重庆"],
    "河北": ["唐山"], "山西": [], "内蒙古": ["呼和浩特", "包头"],
    "辽宁": ["大连", "沈阳", "锦州", "丹东"], "吉林": ["长春"],
    "黑龙江": ["哈尔滨", "牡丹江"], "江苏": ["南京"], "浙江": [],
    "安徽": ["合肥", "安庆", "蚌埠"], "福建": [], "江西": ["南昌", "九江", "赣州"],
    "山东": ["青岛", "济南", "烟台", "济宁"], "河南": ["平顶山"],
    "湖北": ["武汉", "宜昌", "襄阳"], "湖南": ["长沙", "岳阳", "常德"],
    "广东": ["惠州"], "广西": [], "海南": ["三亚", "海口"],
    "四川": ["泸州"], "贵州": ["遵义"], "云南": ["昆明", "大理"],
    "陕西": [], "甘肃": [], "青海": [], "宁夏": ["银川"], "新疆": ["乌鲁木齐"],
}

# 省会/核心城市 → 中指院价（用于"全省 vs 核心城市"对比）
CAPITAL = {"北京": "北京", "天津": "天津", "上海": "上海", "重庆": "重庆",
           "江苏": "南京", "浙江": "杭州", "广东": "广州", "四川": "成都", "湖北": "武汉"}

rows = []
for p in prov["provinces"]:
    name = p["name"]
    pv = p.get("price_yuan_sqm")
    zsy_city = CAPITAL.get(name)
    zsy_price = ZSY.get(zsy_city) if zsy_city else None
    anchors = [prices[c]["base_price"] for c in PROV_CITIES.get(name, []) if c in prices]
    rows.append({
        "province": name,
        "year": p["year"],
        "province_price": pv,
        "caliber": "全省商品房（含商办/县域）",
        "core_city": zsy_city,
        "core_city_price": zsy_price,
        "diff_vs_core_pct": (round((zsy_price - pv) / pv * 100, 1)
                             if zsy_price and pv else None),
        "anchor_cities": len(anchors),
        "anchor_mean": round(st.mean(anchors), 0) if anchors else None,
        "diff_vs_anchor_pct": (round((st.mean(anchors) - pv) / pv * 100, 1)
                               if anchors and pv else None),
    })

with open(OUT / "backtest_4_province.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

municipal = [r for r in rows if r["province"] in ("北京", "天津", "上海", "重庆")]
core_cmp = [r for r in rows if r["diff_vs_core_pct"] is not None]
anchor_cmp = [r for r in rows if r["diff_vs_anchor_pct"] is not None]

summary = {
    "n_provinces": len(rows),
    "n_with_core_city": len(core_cmp),
    "n_with_anchor": len(anchor_cmp),
    "municipal": municipal,
    "core_city_diff_median": round(st.median([abs(r["diff_vs_core_pct"]) for r in core_cmp]), 1),
    "anchor_diff_median": round(st.median([abs(r["diff_vs_anchor_pct"]) for r in anchor_cmp]), 1) if anchor_cmp else None,
}
(OUT / "summary_province.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

print("=== 4 个直辖市：全省值 = 全市值，可被独立来源直接检验 ===")
for r in municipal:
    print(f"{r['province']}\t{r['year']}\t{r['province_price']:.0f}\t对照 {r['core_city_price']}\t"
          f"差 {r['core_city_price'] - r['province_price']:+.0f}（{r['diff_vs_core_pct']:+.1f}%）")
print()
print("=== 省 vs 省会（地理范围差异，不是错值） ===")
for r in core_cmp:
    print(f"{r['province']}\t{r['province_price']:.0f}\t{r['core_city']} {r['core_city_price']}\t{r['diff_vs_core_pct']:+.1f}%")
print("中位绝对差:", summary["core_city_diff_median"], "%")
print()
print("=== 省 vs 本省城市锚点均值（内部一致性） ===")
for r in anchor_cmp:
    print(f"{r['province']}\t全省 {r['province_price']:.0f}\t锚点均值 {r['anchor_mean']:.0f}"
          f"（{r['anchor_cities']} 城）\t{r['diff_vs_anchor_pct']:+.1f}%")
print("中位绝对差:", summary["anchor_diff_median"], "%")
