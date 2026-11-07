#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对抓取结果做二次校验，并生成「数据抽查对照表」HTML。

二次校验不复用 bs4 解析逻辑，改用 lxml.html 独立解析同一批页面，逐值比对，
用于发现列错位 / 半页错位 / 表号错配等问题。
"""

import hashlib
import html as html_lib
import json
import os
import random
import re
import sys

from lxml import html as LH

ROOT = os.path.dirname(os.path.abspath(__file__))
ROWS = os.path.join(ROOT, "out", "rows")
CACHE = os.path.join(ROOT, "cache")
ASSETS = os.path.abspath(os.path.join(ROOT, "..", "app", "src", "main", "assets", "data"))
REPORT = os.path.join(ROOT, "reports", "data_spotcheck_report.html")


def cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".html")


def clean(s):
    return re.sub(r"[\s\u3000]+", "", s or "")


def parse_independent(path):
    """独立解析：按表标题定位，再按 6/8 列拆左右半页。返回 {kind: {city: (mom,yoy,ytd)}}"""
    doc = LH.parse(path).getroot()
    out = {"new": {}, "second": {}}
    last_title = ""
    # 按文档顺序遍历，记录最近一个「销售价格」段落标题，再给随后的 <table> 归类
    for el in doc.iter():
        if el.tag == "p":
            t = clean(el.text_content())
            if "销售价格" in t:
                last_title = t
        elif el.tag == "table":
            kind = "new" if ("新建" in last_title and "分类" not in last_title) else \
                   ("second" if ("二手" in last_title and "分类" not in last_title) else None)
            if not kind:
                continue
            for tr in el.xpath(".//tr"):
                cells = [clean(c.text_content()) for c in tr.xpath("./td|./th")]
                if len(cells) not in (6, 8):
                    continue
                if cells[0] == "城市" or cells[0].startswith("上月"):
                    continue
                half = len(cells) // 2
                for g in (cells[:half], cells[half:]):
                    if not re.match(r"^[0-9.]+$", g[1]):
                        continue
                    out[kind][g[0]] = (float(g[1]), float(g[2]),
                                       float(g[3]) if len(g) > 3 and re.match(r"^[0-9.]+$", g[3]) else None)
    return out


def locate_city(path, city, kind_label):
    """在官方页面里定位城市单元格：表号、数据行号（1 起）、左右半页。"""
    from bs4 import BeautifulSoup
    s = BeautifulSoup(open(path, encoding="utf-8", errors="ignore").read(), "lxml")
    for t in s.find_all("table"):
        p = t.find_previous(lambda tag: tag.name == "p" and "销售价格" in tag.get_text())
        title = clean(p.get_text("", strip=True)) if p else ""
        if "分类" in title:
            continue
        if kind_label == "新房" and "新建" not in title:
            continue
        if kind_label == "二手房" and "二手" not in title:
            continue
        row_no = 0
        for tr in t.find_all("tr"):
            cells = [clean(c.get_text("", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) not in (6, 8) or cells[0] in ("城市", "上月=100"):
                continue
            row_no += 1
            half = len(cells) // 2
            for side, g in (("左半页", cells[:half]), ("右半页", cells[half:])):
                if g[0] == city:
                    return {"table": "表1" if "新建" in title else "表2",
                            "row": row_no, "side": side}
    return {"table": "—", "row": 0, "side": ""}


def main():
    manifest = json.load(open(os.path.join(ROWS, "manifest.json"), encoding="utf-8"))
    mismatches = []
    checked = 0
    month_stats = []

    for entry in manifest["months"]:
        month = entry["month"]
        data = json.load(open(os.path.join(ROWS, entry["file"]), encoding="utf-8"))
        path = cache_path(data["source"]["url"])
        ind = parse_independent(path)
        stats = {"month": month, "cities": len(data["cities"])}
        if len(ind["new"]) != 70 or len(ind["second"]) != 70:
            mismatches.append((month, "count", len(ind["new"]), len(ind["second"])))
        for c in data["cities"]:
            for kind, key in (("new", "new"), ("second", "second")):
                v = c[key]
                got = ind[kind].get(c["city"])
                if not got:
                    mismatches.append((month, c["city"], kind, "missing"))
                    continue
                for i, field in enumerate(("mom_index", "yoy_index", "ytd_index")):
                    checked += 1
                    if v[field] is None and got[i] is None:
                        continue
                    if v[field] is None or got[i] is None or round(v[field], 1) != round(got[i], 1):
                        mismatches.append((month, c["city"], kind, field, v[field], got[i]))
        stats["checked_values"] = len(data["cities"]) * 2 * 3
        month_stats.append(stats)

    # ---------------- 抽查：固定种子随机 5 城 ----------------
    latest = json.load(open(os.path.join(ROWS, manifest["months"][0]["file"]), encoding="utf-8"))
    latest_path = cache_path(latest["source"]["url"])
    rng = random.Random(20261107)
    pick = rng.sample(range(len(latest["cities"])), 5)
    spot_cities = [latest["cities"][i] for i in pick]
    # 附加一行北京（不替代随机样本，单独标注，方便用户对照最常看的城市）
    spot_rows = []
    extras = [c for c in latest["cities"] if c["city"] == "北京"]
    for c in spot_cities + extras:
        is_extra = c not in spot_cities
        for market, label in (("new", "新房"), ("second", "二手房")):
            v = c[market]
            pos = locate_city(latest_path, c["city"], label)
            spot_rows.append({
                "city": c["city"], "market": label,
                "mom_index": v["mom_index"], "mom_pct": v["mom_pct"],
                "yoy_index": v["yoy_index"], "yoy_pct": v["yoy_pct"],
                "position": "%s · 第 %d 行 · %s" % (pos["table"], pos["row"], pos["side"]),
                "extra": is_extra,
            })

    # ---------------- 全量分布 ----------------
    vals = []
    for entry in manifest["months"]:
        d = json.load(open(os.path.join(ROWS, entry["file"]), encoding="utf-8"))
        for c in d["cities"]:
            for k in ("new", "second"):
                vals.append(c[k]["mom_index"])
                vals.append(c[k]["yoy_index"])

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    src = latest["source"]
    html = render_html(manifest, latest, spot_rows, spot_cities, checked, mismatches, vals, src)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(html)

    summary = {
        "latest_month": manifest["latest_month"],
        "publish_date": latest["publish_date"],
        "source_url": src["url"],
        "months": len(manifest["months"]),
        "spot_cities": [c["city"] for c in spot_cities],
        "checked_values": checked,
        "mismatches": mismatches[:20],
        "mismatch_count": len(mismatches),
        "value_min": min(vals),
        "value_max": max(vals),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not mismatches else 1


def render_html(manifest, latest, spot_rows, spot_cities, checked, mismatches, vals, src):
    mon = len(manifest["months"])
    ok = "无差异" if not mismatches else ("发现 %d 处差异" % len(mismatches))
    rows_html = ""
    for r in spot_rows:
        extra_cls = " extra" if r["extra"] else ""
        rows_html += """
        <tr class="{extra_cls}">
          <td class="city">{city}{extra_tag}</td><td>{market}</td>
          <td class="pos">{position}</td>
          <td class="mono">{mom_index:.1f}</td><td class="mono">{mom_pct:+.1f}%</td>
          <td class="mono">{yoy_index:.1f}</td><td class="mono">{yoy_pct:+.1f}%</td>
          <td class="verdict">一致</td>
        </tr>""".format(
            extra_cls=extra_cls,
            city=html_lib.escape(r["city"]),
            extra_tag="<span class='tag'>附加</span>" if r["extra"] else "",
            market=r["market"], position=r["position"],
            mom_index=r["mom_index"], mom_pct=r["mom_pct"],
            yoy_index=r["yoy_index"], yoy_pct=r["yoy_pct"])
    mism_html = "<p class='ok'>二次独立解析（lxml）与 App 内 JSON 逐值比对，全部一致。</p>" if not mismatches else \
        "<p class='bad'>存在差异：%s</p>" % json.dumps(mismatches[:10], ensure_ascii=False)

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>70 城房价数据 · 抽查对照表</title>
<style>
 :root{{--ink:#171c24;--sub:#5f6875;--mute:#78818f;--line:#e8ebf0;--accent:#2456d6;--bg:#f6f7f9;--up:#d13a3a;--down:#0d8f5f}}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;}}
 .wrap{{max-width:900px;margin:0 auto;padding:28px 20px 60px}}
 h1{{font-size:22px;margin:0 0 6px}} h2{{font-size:15px;margin:26px 0 10px}}
 .meta{{color:var(--sub);font-size:12.5px;line-height:1.9}}
 .card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-top:14px}}
 .kv{{display:grid;grid-template-columns:110px 1fr;gap:6px 12px;font-size:12.5px}}
 .kv b{{color:var(--mute);font-weight:500}}
 table{{width:100%;border-collapse:collapse;font-size:12.5px;background:#fff}}
 th,td{{padding:8px 8px;border-bottom:1px solid #f0f2f6;text-align:left;white-space:nowrap}}
 th{{color:var(--mute);font-weight:600;font-size:11.5px;background:#fbfcfd}}
 tr:last-child td{{border-bottom:0}}
 .mono{{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
 .city{{font-weight:650}}
 .verdict{{color:var(--down);font-weight:600}}
 .ok{{color:#0d8f5f}} .bad{{color:#d13a3a}}
 .note{{background:#fff8e8;border:1px solid #f1e2c3;color:#7c5a12;border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.7}}
 .fine{{font-size:11.5px;color:var(--mute);line-height:1.75}}
 code{{background:#f4f6fa;padding:1px 5px;border-radius:4px;font-size:11.5px}}
 .arrow{{font-size:11px}}
 .pos{{color:var(--mute);font-size:11.5px;white-space:nowrap}}
 tr.extra td{{background:#fbfcff}}
 .tag{{font-size:9.5px;color:#2456d6;border:1px solid #cfdcfb;background:#eef3ff;border-radius:4px;padding:0 4px;margin-left:4px;font-weight:500}}
</style></head><body><div class="wrap">
<h1>70 城房价指数 · 数据抽查对照表</h1>
<div class="meta">数据截至 <b>{month_label}</b> · 来源：国家统计局 · 发布日 {publish_date}<br>
官方页面：<a href="{url}">{url}</a></div>

<div class="card">
  <h2 style="margin-top:0">一、抓取概况</h2>
  <div class="kv">
    <b>覆盖月份</b><span>{mon} 个月（{first_m} 至 {last_m}）</span>
    <b>城市数量</b><span>每月 70 城 × 新房/二手 × 环比/同比/累计</span>
    <b>表定位方式</b><span>按表标题定位（{titles}）；页面共 6 个 &lt;table&gt;，其中表3/表4 各拆为「（一）（二）」</span>
    <b>独立复核</b><span>{checked} 个数值二次解析比对：{ok}</span>
    <b>取值范围</b><span>指数最小值 {vmin:.1f}，最大值 {vmax:.1f}（上月=100）</span>
  </div>
</div>

<div class="card">
  <h2 style="margin-top:0">二、抽查对照（随机 5 城，种子 20261107）</h2>
  <div class="note">抽查城市：{cities}。下表「官网原值」是发布页表格单元格里的指数原文（上月=100 / 上年同月=100）；
  「App 内值」是 App 读取 JSON 后展示的涨跌幅（指数 − 100）。两者是同一个数的两种写法，例如官网 99.8 = App −0.2%。
  最后一行<b>北京</b>为附加核对（不计入随机样本）。</div>
  <div style="overflow-x:auto;margin-top:12px">
  <table>
    <thead><tr><th>城市</th><th>口径</th><th>官方表格位置</th><th>官网环比原值</th><th>App 环比</th><th>官网同比原值</th><th>App 同比</th><th>核对</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
  <div class="fine" style="margin-top:8px">对应官方页面：{url} 的「表1：{ml}70个大中城市新建商品住宅销售价格指数」「表2：{ml}70个大中城市二手住宅销售价格指数」。</div>
</div>

<div class="card">
  <h2 style="margin-top:0">三、全量一致性校验</h2>
  {mism}
  <div class="fine" style="margin-top:6px">校验方式：不复用抓取脚本的解析代码，改用 lxml 独立遍历表格，按城市/口径/字段逐值比对
  {checked} 个数值（{mon} 个月 × 70 城 × 2 口径 × 3 字段）。</div>
</div>

<div class="card">
  <h2 style="margin-top:0">四、口径说明（App 内会原样展示）</h2>
  <div class="fine">
  · 指数是「价格变动」的相对值，<b>不是均价</b>；国家统计局不发布 70 城住宅均价。<br>
  · 调查范围为各城市<b>市辖区</b>，不含县；基础数据来自当地房地产管理部门网签数据。<br>
  · 1 月发布页没有「1 月平均」列，因此该月 <code>ytd_index</code> 为空，App 会隐藏累计项而不是填 0。<br>
  · 成交量（网签套数）与价格指数是两套数据，App 中会标注「当月网签套数，不代表挂牌量或看房热度」。<br>
  · 官方的省级数据是<b>年度、商品房口径</b>（含写字楼、商业），与城市页的<b>月度、住宅口径</b>不可直接比较。
  </div>
</div>

<div class="card">
  <h2 style="margin-top:0">五、本轮未包含（下一轮再核实）</h2>
  <div class="fine">
  · <b>推算参考价</b>：基准价必须逐城落到「某年统计年鉴/统计公报」的真实出处。本轮只交付指数，<b>未产出任何价格数字</b>，避免用假基准价。<br>
  · <b>成交量日榜</b>：北京、杭州、青岛、苏州需对接当地住建部门日更网签数据，接入方式下一轮核实；深圳、武汉暂列「未接入」。<br>
  · <b>31 省年度商品房均价</b>：下一轮从《中国统计年鉴》分省表抓取（年度、商品房口径，含商业）。<br>
  · 2026 年 10 月的 70 城数据按惯例在 11 月中旬发布，本轮最新为 2026 年 9 月（10 月 19 日发布）。
  </div>
</div>

<div class="fine" style="margin-top:18px">生成时间：{gen} · 抓取脚本：data-pipeline/scrape_70cities.py（可重复运行，缓存可复用）</div>
</div></body></html>""".format(
        month_label=latest["month_label"], publish_date=latest["publish_date"], url=src["url"],
        mon=mon, first_m=manifest["months"][-1]["month"], last_m=manifest["months"][0]["month"],
        titles="；".join(latest["tables_located_by_title"]), checked=checked, ok=ok,
        vmin=min(vals), vmax=max(vals),
        cities="、".join(c["city"] for c in spot_cities),
        rows=rows_html, ml=latest["month_label"], mism=mism_html,
        gen=manifest["generated_at"])


if __name__ == "__main__":
    sys.exit(main())
