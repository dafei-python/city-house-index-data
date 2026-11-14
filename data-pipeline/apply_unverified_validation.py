#!/usr/bin/env python3
"""34 城未验证锚点的处置（2026-11-14）。

规则：
- 找到官方/机构「城区商品住宅成交均价」且能换算到当前口径的 → 升级锚点（长沙、沈阳、大连）。
- 其余无法证明误差 ≤1000 元/㎡ 的 → 移出价格展示，只保留指数（原值存入 excluded，可恢复）。
"""
import json
import pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

bp = json.loads((DATA / "base_prices.json").read_text(encoding="utf-8"))
cities = bp["cities"]
idx = json.loads((DATA / "index_70cities_2026-09.json").read_text(encoding="utf-8"))
yoy = {c["city"]: c["new"]["yoy_pct"] for c in idx["cities"]}

UPGRADE = {
    # 官方原文：2026-02 长沙市房地产高质量发展座谈会通报，2025 年新建纯商品住宅成交均价 10954 元/㎡
    "长沙": {
        "price": 10954, "period": "2025 全年", "caliber": "新建纯商品住宅成交均价（全市，官方）",
        "source_label": "长沙市住建局：长沙市房地产高质量发展座谈会（2026-02-10）",
        "source_url": "http://szjw.changsha.gov.cn/zfxxgk/gzdt/zhdt/202602/t20260210_12282908.html",
        "source_level": "官方原文", "source_doc": "政府通报",
        "evidence": "新建纯商品住宅均价 10954 元/平方米（2025 年）",
        "confidence": "verified_official", "back_computed": False,
    },
    # 官方原文：沈阳市房产局，2025 年 1-9 月商品住房每平方米均价 10257 元
    "沈阳": {
        "price": 10257, "period": "2025 年 1-9 月", "caliber": "商品住房均价（全市，官方）",
        "source_label": "沈阳市房产局：稳增长、保民生工作通报（2026-01-30）",
        "source_url": "https://fcj.shenyang.gov.cn/zwgkzdgz/fdzdgknr/jcbs/202601/t20260130_4979832.html",
        "source_level": "官方原文", "source_doc": "政府通报",
        "evidence": "2025 年 1—9 月份，全市商品住房每平方米均价 10257 元",
        "confidence": "verified_official", "back_computed": False,
    },
    # 机构样本：中指研究院 2025-12 大连新建住宅样本均价 13334 元/㎡
    "大连": {
        "price": 13334, "period": "2025-12", "caliber": "城区新建住宅样本均价（在售报价，机构）",
        "source_label": "中指研究院百城价格指数（2025 年 12 月，大连）",
        "source_url": "http://m.fang.com/fdc/news/detail/bj/54099280.html",
        "source_level": "机构样本", "source_doc": "机构报告",
        "evidence": "2025 年 12 月大连新建住宅样本平均价格 13334 元/平方米",
        "confidence": "verified_institution", "back_computed": True,
    },
}

log = {"generated_at": STAMP, "upgraded": [], "demoted": []}

for city, u in UPGRADE.items():
    old = cities.get(city)
    if not old:
        continue
    anchor = round(u["price"] / (1 + yoy[city] / 100), 1) if u["back_computed"] else u["price"]
    entry = {
        "level": "city",
        "base_price": anchor,
        "base_year": 2025 if "2025" in u["period"] else 2026,
        "caliber": u["caliber"],
        "source_label": u["source_label"],
        "source_url": u["source_url"],
        "note": "锚点期间：%s；展示值 = 锚点 ×（1+最新同比）。" % u["period"],
        "evidence": u["evidence"],
        "source_level": u["source_level"],
        "source_doc": u["source_doc"],
        "method": "官方/机构成交或样本均价 ×（1+最新同比指数）",
        "formula": ("%d ÷ (1%+.2f%%) = %.1f；展示 %.1f × (1%+.2f%%) = %d"
                    % (u["price"], yoy[city], anchor, anchor, yoy[city], round(anchor * (1 + yoy[city] / 100)))),
        "confidence": u["confidence"],
        "legacy_yearbook": {"base_price": old.get("base_price"), "base_year": old.get("base_year"),
                            "source_url": old.get("source_url")},
    }
    cities[city] = entry
    log["upgraded"].append({"city": city, "old": old.get("base_price"), "new": anchor,
                            "diff_vs_display": round(anchor * (1 + yoy[city] / 100) - u["price"])})

for city in list(cities):
    if cities[city]["confidence"] != "unverified":
        continue
    x = cities.pop(city)
    bp.setdefault("excluded", []).append({
        "city": city,
        "removed_at": STAMP,
        "reason": "未找到可核验的城区商品住宅成交均价，无法证明误差 ≤1000 元/㎡（已实测的同类城市偏差 1,429~3,031 元）",
        "legacy_yearbook": {"base_price": x.get("base_price"), "base_year": x.get("base_year"),
                            "source_url": x.get("source_url")},
    })
    log["demoted"].append(city)

bp["schema_version"] = 4
bp["generated_at"] = STAMP
bp["updated_note"] = ("2026-11-14 第二轮：11 城机构样本锚点 + 2 城官方成交均价（长沙、沈阳）；"
                      "57 城无可靠锚点，只显示指数；移出项与原因见 excluded。")
(DATA / "base_prices.json").write_text(json.dumps(bp, ensure_ascii=False, indent=1), encoding="utf-8")

log["price_cities"] = len(cities)
log["index_only_cities"] = 70 - len(cities)
(DATA.parent / "data-pipeline/backtest/apply_unverified_log.json").write_text(
    json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(log, ensure_ascii=False, indent=1))
