#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成交量抓取：只抓官方公开页面，按城市分别失败，互不影响。
输出 data/deals.json（App 内置数据 + 远端更新共用）。
口径字段：period=day（单日/即时）或 month_to_date（当月累计），并带 period_start/period_end。
"""
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deals.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TODAY = date.today().isoformat()


def fetch(url: str, referer=None, timeout: int = 30) -> str:
    """用系统 curl 抓取：部分政府站点 TLS 握手/UA 校验对 urllib 不友好。"""
    cmd = ["curl", "-sS", "-L", "--max-time", str(timeout), "-A", UA, "-H", "Accept: */*"]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    cmd.append(url)
    raw = subprocess.run(cmd, capture_output=True, timeout=timeout + 10).stdout
    if not raw:
        raise RuntimeError(f"空响应：{url}")
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" ", strip=True))


def day_rows(city, status, portal, url, note, rows, fetched=None):
    return {
        "city": city, "status": status, "portal": portal, "url": url, "note": note,
        "granularity": "day", "period": "day",
        "period_start": rows[0]["date"] if rows else None,
        "period_end": rows[0]["date"] if rows else None,
        "fetched_at": fetched or TODAY, "rows": rows, "details": [],
    }


def month_rows(city, status, portal, url, note, rows, start, end, fetched=None):
    return {
        "city": city, "status": status, "portal": portal, "url": url, "note": note,
        "granularity": "month_to_date", "period": "month_to_date",
        "period_start": start, "period_end": end,
        "fetched_at": fetched or TODAY, "rows": rows, "details": [],
    }


def beijing():
    url = "http://bjjs.zjw.beijing.gov.cn/eportal/ui?pageId=53607241"
    t = plain(fetch(url))
    m = re.search(r"(20\d{2}/\d{1,2}/\d{1,2})期房", t)
    d = m.group(1) if m else TODAY
    rows = []
    for label, kw in (("期房", "期房网上签约"), ("现房", "现房网上签约"), ("存量房", "存量房网上签约")):
        seg = t.split(kw)[1][:140] if kw in t else ""
        c = re.search(r"网上签约套数\s*(\d+)", seg)
        a = re.search(r"网上签约面积\s*(\d+)", seg)
        if c:
            rows.append({"label": label, "date": d, "count": int(c.group(1)),
                         "area": float(a.group(1)) if a else None})
    if not rows:
        raise RuntimeError("北京页面结构变化")
    return day_rows("北京", "accessible_static", "北京市住房和城乡建设委员会", url,
                    "官方「房地产数据统计」页，仅最新一日；期房+现房为新房，存量房为二手。", rows)


def hangzhou():
    url = "https://www.tmsf.com/housingtype/housingtypedetail-4.htm"
    t = plain(fetch(url, referer="https://www.tmsf.com/"))
    seg = t.split("今日商品房累计成交信息")[1][:400] if "今日商品房累计成交信息" in t else t
    m = re.search(r"住宅\s*(\d+)套\s*([0-9.]+)m²", seg)
    if not m:
        raise RuntimeError("杭州页面结构变化")
    return day_rows("杭州", "accessible_static", "杭州透明售房网（数据源：市住保房管网）", url,
                    "今日商品房累计成交（截至抓取时刻，单日口径；每小时更新一次）。",
                    [{"label": "新房合计（当日）", "date": TODAY,
                      "count": int(m.group(1)), "area": float(m.group(2))}])


def qingdao():
    url = "https://zhufang.qingdao.gov.cn:8081/getTradeInfo?tradeType=new"
    j = json.loads(fetch(url))
    c = j.get("city", {})
    if not c.get("count"):
        raise RuntimeError("青岛接口无数据")
    return day_rows("青岛", "accessible_api", "青岛住房网信息发布系统（市不动产登记中心）", url,
                    "官方 AJAX 接口返回 JSON，当日口径；忽略日期参数，无历史回补。",
                    [{"label": "新房（全市）", "date": TODAY, "count": c.get("count"), "area": c.get("totalArea")},
                     {"label": "其中住宅", "date": TODAY, "count": c.get("houseCount"), "area": c.get("houseArea")}])


def suzhou():
    url = "http://spf.szfcweb.com/szfcweb/DataSerach/XSFWINFO.aspx"
    t = plain(fetch(url, referer="http://www.szfcweb.com/"))
    d = re.search(r"【\s*(20\d{2}/\d{1,2}/\d{1,2})\s*】", t)
    day = d.group(1) if d else TODAY
    m = re.search(r"市区合计\s*总计\s*(\d+)\s+([0-9.]+)\s*其中：住宅\s*(\d+)\s+([0-9.]+)", t)
    if not m:
        raise RuntimeError("苏州页面结构变化")
    return day_rows("苏州", "accessible_static", "苏州市商品房销售网上管理系统", url,
                    "官方 ASPX 静态表，按区给出小计与住宅，仅当日。",
                    [{"label": "市区合计", "date": day, "count": int(m.group(1)), "area": float(m.group(2))},
                     {"label": "其中住宅", "date": day, "count": int(m.group(3)), "area": float(m.group(4))}])


def nanjing():
    url = "https://www.njhouse.com.cn/projectindex.html"
    t = plain(fetch(url))
    sub = re.search(r"认购\s*(\d+)\s*套\s*成交\s*(\d+)\s*套", t)
    month = re.search(r"本月成交\s*([0-9.]+)万m²", t)
    if not sub:
        raise RuntimeError("南京页面结构变化")
    area_txt = f"，本月（{int(TODAY[5:7])} 月 1 日–今日）成交面积 {month.group(1)} 万㎡" if month else ""
    return day_rows("南京", "accessible_static",
                    "南京网上房地产（南京市房地产市场交易中心）", url,
                    "官方首页静态数字，单日成交套数；页面日期由脚本生成，抓取日见抓取时间；" +
                    "本月成交面积另给，未公布当月套数。",
                    [{"label": "今日成交（含住宅/非住宅）" + area_txt, "date": TODAY,
                      "count": int(sub.group(2)), "area": None}])


def guangzhou():
    url = "https://zfcj.gz.gov.cn/ysqgk/Api/WebApi/mrxjspfqyxx.ashx"
    j = json.loads(fetch(url))
    d = j.get("data") or []
    if not d:
        raise RuntimeError("广州接口无数据")
    day = (d[0].get("createTime") or TODAY).split(" ")[0]
    res_c = sum(int(x.get("zhuZaiTaoShu") or 0) for x in d)
    res_a = round(sum(float(x.get("zhuZaiArea") or 0) for x in d), 2)
    all_c = res_c + sum(int(x.get(k) or 0) for x in d for k in ("shangYeTaoShu", "banGongTaoShu", "cheWeiTaoShu"))
    all_a = round(res_a + sum(float(x.get(k) or 0) for x in d for k in ("shangYeArea", "banGongArea", "cheWeiArea")), 2)
    return day_rows("广州", "accessible_api", "广州市住房和城乡建设局（阳光家缘）", url,
                    "官方每日新建商品房签约接口，按区分行；住宅为主，另含商业/办公/车位。",
                    [{"label": "住宅签约", "date": day, "count": res_c, "area": res_a},
                     {"label": "住宅+商业+办公+车位", "date": day, "count": all_c, "area": all_a}])


def xian():
    list_url = "https://zjj.xa.gov.cn/zw/zfxxgkml/zwxx/gsgg/fcscjy/1.html"
    try:
        html = fetch(list_url)
        links = re.findall(r'href="([^"]*fcscjy/[^"/]+\.html)"[^>]*>[^<]*二手房网签情况', html)
        url = ("https://zjj.xa.gov.cn" + links[0]) if links else list_url
    except Exception:
        url = list_url
    t = plain(fetch(url))
    m = re.search(r"(20\d{2})年(\d{1,2})月份[^。]{0,20}存量房（二手房）网签备案面积([0-9.]+)万平方米[；，]" +
                  r"其中，?住宅网签备案(\d+)套，面积([0-9.]+)万平方米", t)
    if not m:
        raise RuntimeError("西安页面结构变化")
    y, mo = int(m.group(1)), int(m.group(2))
    end = f"{y:04d}-{mo:02d}-31" if mo in (1, 3, 5, 7, 8, 10, 12) else f"{y:04d}-{mo:02d}-30"
    return month_rows("西安", "accessible_static", "西安市住房和城乡建设局", url,
                      "每月初发布上月二手房网签备案，整月累计；全市总量为 96.11 万㎡口径。",
                      [{"label": f"{mo} 月住宅网签备案", "date": end, "count": int(m.group(4)),
                        "area": float(m.group(5)) * 10000},
                       {"label": f"{mo} 月存量房合计（仅面积）", "date": end, "count": None,
                        "area": float(m.group(3)) * 10000}],
                      f"{y:04d}-{mo:02d}-01", end)


def wuhan():
    list_url = "https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/spzfxysydjcjb/"
    url = list_url
    try:
        html = fetch(list_url)
        cand = re.findall(r'href="([^"]+\.shtml)"[^>]*>[^<]*新建商品房成交统计情况', html)
        if cand:
            url = cand[0] if cand[0].startswith("http") else "https://zgj.wuhan.gov.cn" + cand[0]
    except Exception:
        pass
    if url == list_url:
        url = "https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/spzfxysydjcjb/202611/t20261103_2873424.shtml"
    html = fetch(url)
    m = re.search(r"(20\d{2})年(\d{1,2})月新建商品房成交统计情况", plain(html))
    if not m:
        raise RuntimeError("武汉页面结构变化")
    y, mo = int(m.group(1)), int(m.group(2))
    rows = []
    for tr in BeautifulSoup(html, "lxml").find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if cells and cells[0] == "合计" and len(cells) >= 10:
            rows = [
                {"label": "商品住房", "date": f"{y:04d}-{mo:02d}-31", "count": int(cells[1]), "area": float(cells[2])},
                {"label": "合计（含写字楼/商业/其他）", "date": f"{y:04d}-{mo:02d}-31",
                 "count": int(cells[9]), "area": float(cells[10])},
            ]
            break
    if not rows:
        raise RuntimeError("武汉合计行未找到")
    end = f"{y:04d}-{mo:02d}-31" if mo in (1, 3, 5, 7, 8, 10, 12) else f"{y:04d}-{mo:02d}-30"
    return month_rows("武汉", "accessible_static", "武汉市住房和城市更新局", url,
                      "每月初发布上月新建商品房成交统计，整月累计；每日明细接口需登录。", rows,
                      f"{y:04d}-{mo:02d}-01", end)


PARTS = [beijing, hangzhou, qingdao, suzhou, nanjing, guangzhou, xian, wuhan]

NOT_INTEGRATED = [
    ("深圳", "login_required", "深圳市住房和建设局 / 深圳市政府数据开放平台",
     "https://opendata.sz.gov.cn/data/dataSet/toDataDetails/29200_01903510",
     "按日成交数据集需在开放平台订阅并申请 appKey，当前未获授权；房地产信息平台另有动态 JS 挑战，不绕行。"),
    ("成都", "not_found", "成都市住房和城乡建设局",
     "https://cdzjryb.com/zwdt/Default.aspx",
     "官网「当日成交」为 Vue 单页应用，未找到可公开 GET 的数据接口；不做浏览器逆向。"),
    ("合肥", "not_found", "合肥市住房保障和房产管理局",
     "https://zfbzfcglj.hefei.gov.cn/public/column/17501?type=4&catId=7034810&action=list",
     "官方只提供单项目分幢查询，没有全市每日成交汇总页。"),
]


def main():
    cities = []
    errors = []
    for fn in PARTS:
        try:
            cities.append(fn())
            print(f"OK {fn.__name__}", file=sys.stderr)
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
            print(f"FAIL {fn.__name__}: {e}", file=sys.stderr)
    for city, status, portal, url, note in NOT_INTEGRATED:
        cities.append({"city": city, "status": status, "portal": portal, "url": url, "note": note,
                       "granularity": "none", "period": "day", "period_start": None, "period_end": None,
                       "fetched_at": TODAY, "rows": [], "details": []})
    cities.sort(key=lambda c: (c["status"] in ("accessible_static", "accessible_api"), len(c["rows"])), reverse=True)
    data = {
        "schema_version": 2,
        "generated_at": TODAY,
        "caliber": "单日或当月累计网签套数与面积；不代表挂牌量；网签有滞后，退房会回改。",
        "disclaimer": "本页不展示任何未经官方发布的成交数据；没有逐套明细的城市，官方只公布汇总。",
        "cities": cities,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {OUT}：{len(cities)} 城，成功 {len(cities) - len(NOT_INTEGRATED) - len(errors)}，失败 {len(errors)}", file=sys.stderr)


if __name__ == "__main__":
    main()
