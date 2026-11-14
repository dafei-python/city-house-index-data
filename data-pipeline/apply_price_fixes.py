#!/usr/bin/env python3
"""按回测结论修正价格数据（2026-11-14）。

规则（依据 data-pipeline/backtest/ 的回测结果）：
1. 10 个有机构样本价的城市的绝对价改用中指院 2026-10 样本均价：误差由 +6%~+70% 收敛到 0（同一来源）。
2. 襄阳：年鉴法（6,454）与城区商品住房成交价（8,427）差 1,973 元（+30.6%），且无更优独立来源 → 移出价格展示（只留指数）。
3. 其余 34 城：保留官方年鉴锚点，标注「未做独立回测、全市商品房口径」。
4. 4 个直辖市的省份值（省=市）同步换成机构样本价，避免 +49%~+70% 的偏差。
5. 成交量数据不动，只更新口径说明（当日累计 vs T+1）。

不修改任何官方原始记录：年鉴值保留在 legacy_yearbook 字段，便于追溯。
"""
import json
import pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ASSETS = ROOT / "app/src/main/assets/data"
STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# 中指院 2026 年 10 月百城新建住宅样本均价（2026-10-31 发布）
# https://m.cih-index.com/research/indexresearch/
ZSY = {
    "上海": (65730, 7.43), "北京": (47594, 2.17), "南京": (26193, 1.85),
    "天津": (15741, 1.53), "广州": (25513, 1.56), "成都": (15594, 7.49),
    "杭州": (34386, 5.80), "武汉": (13536, 1.81), "深圳": (53703, 2.78),
    "重庆": (11341, -1.31),
}
ZSY_URL = "https://m.cih-index.com/research/indexresearch/"
ZSY_LABEL = "中指研究院《百城价格指数》2026 年 10 月（2026-10-31 发布）"

bp = json.loads((DATA / "base_prices.json").read_text(encoding="utf-8"))
cities = bp["cities"]
idx = json.loads((DATA / "index_70cities_2026-09.json").read_text(encoding="utf-8"))
our_yoy = {c["city"]: c["new"]["yoy_pct"] for c in idx["cities"]}

log = {"generated_at": STAMP, "replaced_with_institution": [], "added": [],
       "removed": [], "annotated": 0, "province_fixed": []}

for city, (sample, zyoy) in ZSY.items():
    yoy = our_yoy.get(city)
    if yoy is None:
        raise SystemExit("缺少 %s 的 70 城同比，无法回推锚点" % city)
    anchor = round(sample / (1 + yoy / 100), 1)
    old = cities.get(city)
    entry = {
        "level": "city",
        "base_price": anchor,
        "base_year": 2025,
        "caliber": "城区新建住宅样本均价（在售/挂牌报价，非网签成交价）",
        "source_label": ZSY_LABEL,
        "source_url": ZSY_URL,
        "note": "锚点由 2026-10 样本均价按官方同比回推：展示值 = 锚点 ×（1+最新同比）≈ %d 元/㎡。" % sample,
        "evidence": "百城新建住宅样本平均价格 %d 元/㎡（同比 %+.2f%%，%s）；城区在售样本，非网签成交。" % (sample, zyoy, city),
        "source_level": "机构样本",
        "source_doc": "机构报告",
        "method": "机构样本均价回推的年度锚点 ×（1+最新同比指数）",
        "formula": "%d ÷ (1%+.2f%%) = %.1f 元/㎡；展示 %d × (1%+.2f%%) = %d" % (
            sample, yoy, anchor, anchor, yoy, round(anchor * (1 + yoy / 100))),
        "confidence": "verified_institution",
        "backtest": "data-pipeline/backtest/backtest_1_yearbook.csv",
    }
    if old:
        entry["legacy_yearbook"] = {
            "base_price": old.get("base_price"),
            "base_year": old.get("base_year"),
            "source_label": old.get("source_label"),
            "source_url": old.get("source_url"),
            "caliber": old.get("caliber"),
            "note": "年鉴法原值，回测显示与城区住宅价相差较大，已停用。",
        }
        log["replaced_with_institution"].append({"city": city, "old": old.get("base_price"), "new": anchor})
    else:
        log["added"].append({"city": city, "anchor": anchor})
    cities[city] = entry

# 襄阳：移出价格
if "襄阳" in cities:
    x = cities.pop("襄阳")
    bp.setdefault("excluded", []).append({
        "city": "襄阳",
        "removed_at": STAMP,
        "reason": "年鉴法 6,454 元/㎡ 与城区商品住房成交价 8,427 元/㎡ 相差 1,973 元（+30.6%），无更优独立来源",
        "backtest": "data-pipeline/backtest/backtest_1b_tier34.csv",
        "legacy_yearbook": {"base_price": x.get("base_price"), "base_year": x.get("base_year"),
                            "source_url": x.get("source_url")},
    })
    log["removed"].append("襄阳")

# 其余：标注置信度
for city, e in cities.items():
    if e.get("confidence"):
        continue
    e["confidence"] = "unverified"
    base_note = e.get("note") or ""
    e["note"] = (base_note + " " if base_note else "") + \
        "口径为全市商品房（含商办/保障房），未做独立回测；与城区住宅成交价可能相差数百至数千元。"
    log["annotated"] += 1

bp["schema_version"] = 3
bp["generated_at"] = STAMP
bp["method"] = ("两类锚点：① 10 城为机构样本均价（中指院 2026-10，城区新建住宅报价）回推的年度锚点；"
                "② 其余为官方年鉴/公报的「商品房销售额÷销售面积」。展示值 = 锚点 ×（1+最新同比指数）。")
bp["caliber"] = ("口径不一致：机构样本为城区新建住宅（不含商办）；年鉴值为全市商品房（含商办、"
                 "可能含县域与保障房）。已按 confidence 字段分级，未验证项不作为精确价承诺。")
bp["updated_note"] = ("2026-11-14 依据回测修正：10 城改用机构样本锚点；襄阳移出价格；"
                      "其余 34 城标注未验证。回测见 data-pipeline/backtest/。")
(DATA / "base_prices.json").write_text(json.dumps(bp, ensure_ascii=False, indent=1), encoding="utf-8")

# ---------------------------------------------------------------- 省份
pv = json.loads((DATA / "provinces.json").read_text(encoding="utf-8"))
for p in pv["provinces"]:
    if p["name"] in ZSY:
        sample, zyoy = ZSY[p["name"]]
        p["legacy_yearbook"] = {"price_yuan_sqm": p.get("price_yuan_sqm"), "year": p.get("year"),
                                "source_label": p.get("source_label")}
        p["price_yuan_sqm"] = float(sample)
        p["year"] = 2026
        p["yoy_pct"] = zyoy
        p["caliber"] = "直辖市：市域=省域，采用城区新建住宅样本均价（非网签成交）"
        p["source_label"] = ZSY_LABEL
        p["source_url"] = ZSY_URL
        p["evidence"] = "百城新建住宅样本平均价格 %d 元/㎡（同比 %+.2f%%，%s）" % (sample, zyoy, p["name"])
        p["note"] = "原年鉴值（全市商品房口径）偏差 +6%~+70%，已停用；保留于 legacy_yearbook。"
        p["confidence"] = "verified_institution"
        log["province_fixed"].append({"province": p["name"], "new": sample})
    else:
        p["confidence"] = "official"
        base_note = p.get("note") or ""
        p["note"] = (base_note + " " if base_note else "") + \
            "全省商品房口径（含商办与县域），与省会价差异中位约 70%；跨省比较请确认年份一致。"
pv["generated_at"] = STAMP
pv["caliber"] = pv.get("caliber", "") + "（2026-11-14 修正：4 个直辖市改用机构样本价，其余保持官方全省口径并标注）"
(DATA / "provinces.json").write_text(json.dumps(pv, ensure_ascii=False, indent=1), encoding="utf-8")

# ---------------------------------------------------------------- 成交量口径说明
dl = json.loads((DATA / "deals.json").read_text(encoding="utf-8"))
dl["disclaimer"] = ("本页数据均为官方页面原文直采，无人工加工；但不等于「最终成交」："
                    "苏州/南京/杭州/青岛为当日累计，早间抓取可能为 0 或少量，以 22:40 一班为准；"
                    "北京/广州为 T+1 日结；网签有滞后，退房会回改历史。")
dl["as_of_policy"] = "daily_cumulative_cities: 苏州/南京/杭州/青岛；t_plus_1: 北京/广州；monthly: 武汉/西安"
dl["schema_version"] = int(dl.get("schema_version", 2)) + 1
(DATA / "deals.json").write_text(json.dumps(dl, ensure_ascii=False, indent=1), encoding="utf-8")

log["final_city_count"] = len(cities)
(DATA.parent / "data-pipeline/backtest/apply_fixes_log.json").write_text(
    json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")

print(json.dumps(log, ensure_ascii=False, indent=1))
