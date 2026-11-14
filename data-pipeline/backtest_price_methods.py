#!/usr/bin/env python3
"""估算方法量化回测：年鉴法 / 指数外推 / 二手挂牌折算。

只读现有数据 + 外部公开对照值，不修改任何已入库数据。
输出：data-pipeline/backtest/*.csv 与 summary.json

外部对照值均记录来源 URL 与口径，避免"凭经验"。
"""
import csv
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(__file__).resolve().parent / "backtest"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- 输入 1：现有基准价
base = json.loads((ROOT / "data/base_prices.json").read_text(encoding="utf-8"))
prices = base["cities"]

# ---------------------------------------------------------------- 输入 2：70 城指数（2026-09）
idx = json.loads((ROOT / "data/index_70cities_2026-09.json").read_text(encoding="utf-8"))
idx_by_city = {c["city"]: c for c in idx["cities"]}

# ---------------------------------------------------------------- 输入 3：中指院 2026-10 百城价格（公开页仅前 10 城）
# 来源：https://m.fang.com/fdc/index/baichengindex.html（新建住宅，样本平均价格）
#      https://m.fang.com/fdc/index/ershoufangindex.html（二手住宅，样本平均价格）
# 期间以中指院 2026-11-02 通稿为准（页面标签写 2026-09，数值与其 10 月通稿一致）
#       https://finance.sina.com.cn/cj/2026-11-02/doc-inixmha9962890.shtml
ZSY = {
    # city: (新建住宅样本均价, 新建同比%, 二手住宅样本均价, 二手同比%)
    "上海": (65386, 7.74, 55322, -4.47),
    "北京": (47451, 2.02, 60564, -8.94),
    "南京": (26104, 1.89, 24613, -11.27),
    "天津": (15715, 1.48, 19914, -8.14),
    "广州": (25470, 1.73, 32313, -7.38),
    "成都": (15574, 8.24, 16094, -6.55),
    "杭州": (34306, 6.82, 31382, -6.84),
    "武汉": (13469, 1.71, 12714, -9.50),
    "深圳": (53699, 2.62, 62889, -4.55),
    "重庆": (11350, -1.28, 10433, -7.80),
}
ZSY_PERIOD = "2026-10"

# ---------------------------------------------------------------- 输入 4：官方二手成交均价（含口径最小的样本）
# 河源：2026 年 1-4 月，二手住房交易均价 3418 元/㎡
#   http://www.heyuan.gov.cn/bmjy/hyszfhcxjsj/zwgk/content/mpost_702870.html
# 青岛城阳区：2026 Q1，存量房成交均价 8285 元/㎡
#   http://www.chengyang.gov.cn/rmzt/zfbz/esf/202604/t20260429_10580276.shtml
# 桐城市：2026 年 1-6 月，住宅平均成交价 4081.18 元/㎡
#   https://www.tongcheng.gov.cn/public/2000002151/2024672869.html
# 三四线/城区口径的独立对照（官方或行业协会转述，均注明口径）
# 襄阳：2025 年城区商品房网签 205.33 万㎡、158.04 亿元；商品住房成交均价 8427.29 元/㎡（-2.65%）
#   https://m.xfsfc.com.cn/news/21875.html（襄阳房地产信息网，协会/媒体口径，非政府原文）
TIER34 = {
    "襄阳": {"price": 8427.29, "period": "2025", "scope": "城区商品住房",
             "source_level": "媒体/行业协会", "url": "https://m.xfsfc.com.cn/news/21875.html"},
}

OFFICIAL_SECOND = [
    {"city": "河源", "price": 3418, "period": "2026-01~04", "scope": "地级市",
     "caliber": "二手住房成交均价（官方）"},
    {"city": "青岛城阳区", "price": 8285, "period": "2026-Q1", "scope": "市辖区",
     "caliber": "存量房成交均价（官方，区级）"},
    {"city": "桐城", "price": 4081.18, "period": "2026-01~06", "scope": "县级市",
     "caliber": "住宅平均成交价（官方）"},
]


def pct(a, b):
    return (a - b) / b * 100


def q(vals):
    vals = sorted(vals)
    if len(vals) < 4:
        return {"min": vals[0], "median": st.median(vals), "max": vals[-1], "n": len(vals)}
    qs = st.quantiles(vals, n=4)
    return {"min": vals[0], "q1": qs[0], "median": st.median(vals), "q3": qs[2], "max": vals[-1], "n": len(vals)}


# ================================================================ 回测 1：年鉴法
# 年鉴法基准价 = 商品房销售额 ÷ 商品房销售面积（含商办、含远郊）
# 对照 = 中指院新建住宅样本均价（主城区住宅，挂牌/在售样本）
rows1 = []
for city, (p_new, _, _, _) in ZSY.items():
    if city not in prices:
        continue
    p = prices[city]
    p_comm = p["amount_yi"] / p["area_wan_sqm"] * 10000
    yoy = idx_by_city.get(city, {}).get("new", {}).get("yoy_pct")
    rows1.append({
        "city": city,
        "base_year": p["base_year"],
        "area_wan_sqm": round(p["area_wan_sqm"], 2),
        "amount_yi": round(p["amount_yi"], 2),
        "p_comm": round(p_comm, 0),
        "p_res_zsy": p_new,
        "diff_yuan": round(p_new - p_comm, 0),
        "pct": round(pct(p_new, p_comm), 1),
        "our_yoy_202609": yoy,
        "zsy_period": ZSY_PERIOD,
    })

# 时间因素上界：用 70 城同比（近 12 个月）粗估 1 年变化，base_year 到 2026 最多 2 年
for r in rows1:
    y = r["our_yoy_202609"]
    years = 2026 - r["base_year"]
    r["time_effect_pct_max"] = round(abs(y) * years, 1) if y is not None else None
    r["pct_ex_time"] = (round(abs(r["pct"]) - (r["time_effect_pct_max"] or 0), 1)
                        if r["pct"] is not None else None)

with open(OUT / "backtest_1_yearbook.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows1[0].keys()))
    w.writeheader()
    w.writerows(rows1)

pcts = [abs(r["pct"]) for r in rows1]
dist1 = q(pcts)
dist1_yuan = q([abs(r["diff_yuan"]) for r in rows1])

# ---- 三四线对照组：年鉴法(全市商品房) vs 城区商品住房成交均价
rows1b = []
for city, ref in TIER34.items():
    p = prices.get(city)
    if not p:
        continue
    p_comm = p["amount_yi"] / p["area_wan_sqm"] * 10000
    rows1b.append({
        "city": city,
        "base_year": p["base_year"],
        "p_comm": round(p_comm, 0),
        "p_ref": ref["price"],
        "ref_scope": ref["scope"],
        "ref_period": ref["period"],
        "ref_source_level": ref["source_level"],
        "ref_url": ref["url"],
        "diff_yuan": round(ref["price"] - p_comm, 0),
        "pct": round(pct(ref["price"], p_comm), 1),
    })

with open(OUT / "backtest_1b_tier34.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows1b[0].keys()))
    w.writeheader()
    w.writerows(rows1b)

all_pct = pcts + [abs(r["pct"]) for r in rows1b]
all_yuan = [abs(r["diff_yuan"]) for r in rows1] + [abs(r["diff_yuan"]) for r in rows1b]
dist1_all = q(all_pct)
dist1_all_yuan = q(all_yuan)

# ================================================================ 回测 2：指数外推
# 同一城市、同一 12 个月区间，两个独立来源的同比涨幅差异（百分点）
rows2 = []
for city, (_, z_new, _, z_second) in ZSY.items():
    rec = idx_by_city.get(city)
    if not rec:
        continue
    our_new = rec["new"]["yoy_pct"]
    our_second = rec["second"]["yoy_pct"]
    r = {
        "city": city,
        "period": "2025-09 → 2026-09",
        "our_new_yoy": our_new,
        "zsy_new_yoy": z_new,
        "new_diff_pp": round(z_new - our_new, 2),
        "our_second_yoy": our_second,
        "zsy_second_yoy": z_second,
        "second_diff_pp": round(z_second - our_second, 2),
    }
    # 把百分点差折算成元/㎡（以中指院价格为基数）
    p_new = ZSY[city][0]
    p_second = ZSY[city][2]
    r["new_diff_yuan"] = round(p_new * r["new_diff_pp"] / 100, 0)
    r["second_diff_yuan"] = round(p_second * r["second_diff_pp"] / 100, 0)
    rows2.append(r)

with open(OUT / "backtest_2_index.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows2[0].keys()))
    w.writeheader()
    w.writerows(rows2)

dist2_new = q([abs(r["new_diff_pp"]) for r in rows2])
dist2_second = q([abs(r["second_diff_pp"]) for r in rows2])
dist2_new_yuan = q([abs(r["new_diff_yuan"]) for r in rows2])

# ================================================================ 回测 3：二手挂牌→成交折价率
# 需要「同城、同期」的机构挂牌均价与官方成交均价配对，才能算折价率。
# 本轮只有 3 个官方成交均价样本，均不在中指院公开的 10 城名单内 → 无法配对。
pairs = []
for s in OFFICIAL_SECOND:
    zsy = ZSY.get(s["city"])
    pairs.append({
        "city": s["city"],
        "official_price": s["price"],
        "official_period": s["period"],
        "official_scope": s["scope"],
        "official_caliber": s["caliber"],
        "zsy_listing_price": zsy[2] if zsy else None,
        "discount_ratio": round(s["price"] / zsy[2], 3) if zsy else None,
        "paired": bool(zsy),
    })

with open(OUT / "backtest_3_second.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(pairs[0].keys()))
    w.writeheader()
    w.writerows(pairs)

# 3b：同源（中指院）二手/新房比价，检验"统一折价率"是否可能
ratio_rows = []
for city, (p_new, _, p_second, _) in ZSY.items():
    ratio_rows.append({"city": city, "p_new": p_new, "p_second": p_second,
                       "second_over_new": round(p_second / p_new, 3)})
with open(OUT / "backtest_3b_ratio.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(ratio_rows[0].keys()))
    w.writeheader()
    w.writerows(ratio_rows)
ratio_dist = q([r["second_over_new"] for r in ratio_rows])

summary = {
    "n_prices": len(prices),
    "n_estimate_cities": sum(1 for p in prices.values() if not p.get("amount_yi") or not p.get("area_wan_sqm")),
    "city_level_anchors": sum(1 for p in prices.values() if p.get("level") == "city"),
    "backtest_1": {"rows": rows1, "abs_pct": dist1, "abs_yuan": dist1_yuan,
                   "tier34_rows": rows1b,
                   "all_abs_pct": dist1_all, "all_abs_yuan": dist1_all_yuan},
    "backtest_2": {"abs_new_pp": dist2_new, "abs_second_pp": dist2_second,
                   "abs_new_yuan": dist2_new_yuan},
    "backtest_3": {"pairs": pairs, "paired_samples": sum(1 for p in pairs if p["paired"]),
                   "official_second_in_70": 0,
                   "second_over_new": ratio_dist,
                   "note": "70 城中未发现发布二手成交均价的官方月报；宜昌月报仅有套数/面积（12233 套、128.27 万㎡），无金额无均价。"},
}
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

print("=== 回测 1：年鉴法（商品房均价） vs 中指院新建住宅样本均价 ===")
for r in rows1:
    print(f"{r['city']}\t基准年{r['base_year']}\t年鉴法{r['p_comm']:.0f}\t中指院{r['p_res_zsy']}\t"
          f"差{r['diff_yuan']:+.0f}元({r['pct']:+.1f}%)\t扣除时间因素后约{r['pct_ex_time']:+.1f}%")
print("绝对百分比偏差分布:", dist1)
print("绝对金额偏差分布:", dist1_yuan)
print()
print("=== 回测 1b：三四线对照（年鉴法 vs 城区商品住房成交均价）===")
for r in rows1b:
    print(f"{r['city']}\t基准年{r['base_year']}\t年鉴法{r['p_comm']:.0f}\t对照{r['p_ref']:.0f}"
          f"（{r['ref_scope']}/{r['ref_period']}，{r['ref_source_level']}）\t差{r['diff_yuan']:+.0f}元({r['pct']:+.1f}%)")
print("合并 6 城 绝对百分比偏差:", dist1_all)
print("合并 6 城 绝对金额偏差:", dist1_all_yuan)
print()
print("=== 回测 2：指数法同比差异（70 城 vs 中指院，2025-09→2026-09）===")
for r in rows2:
    print(f"{r['city']}\t新房 {r['our_new_yoy']:+.1f}% vs {r['zsy_new_yoy']:+.1f}% = {r['new_diff_pp']:+.2f}pp"
          f"（{r['new_diff_yuan']:+.0f}元）\t二手 {r['our_second_yoy']:+.1f}% vs {r['zsy_second_yoy']:+.1f}% = {r['second_diff_pp']:+.2f}pp")
print("新房 |pp| 分布:", dist2_new)
print("二手 |pp| 分布:", dist2_second)
print("新房 |元| 分布:", dist2_new_yuan)
print()
print("=== 回测 3：二手挂牌→成交折价率 ===")
print("配对样本数:", summary["backtest_3"]["paired_samples"])
for p in pairs:
    print(p)
print()
print("估算城市数（缺销售额或面积）:", summary["n_estimate_cities"], "／", summary["n_prices"])
