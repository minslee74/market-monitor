"""시장 데이터 수집기: .env 로드 -> 소스별 수집 -> data/market.json, data.js 생성.
한 소스(또는 한 시리즈)가 실패해도 나머지는 계속 진행하며, 소스별 상태를 status 에 기록한다.
series[key] = {"dates":[...], "close":[...], "freq":"D|W|M|Q", "label":..., "unit":...}"""
import json, os, sys, time, traceback
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "data"
SECRET_KEYS = ("KRX_ID", "KRX_PW", "ECOS_API_KEY", "FRED_API_KEY", "DATA_GO_KR_KEY")
KEEP_DAYS = 365 * 5 + 20


def load_env(path=BASE / ".env"):
    """python-dotenv 없이 KEY=VALUE 파싱. 이미 설정된 환경변수는 덮어쓰지 않는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def setup_ssl():
    """회사망 SSL 검사용: 내보낸 인증서 번들이 있으면 사용 (검증은 끄지 않음)."""
    pem = BASE / "certs" / "ca-bundle.pem"
    if not pem.exists():
        return
    if not str(pem).isascii():
        # libcurl(yfinance)은 한글 등 비ASCII 경로의 인증서 파일을 못 읽음 -> 영문 경로로 복사해 사용
        import shutil, tempfile
        dst_dir = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "market-monitor"
        dst = dst_dir / "ca-bundle.pem"
        if not str(dst).isascii():
            dst = Path(tempfile.gettempdir()) / "market-monitor-ca-bundle.pem"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_mtime < pem.stat().st_mtime:
            shutil.copyfile(pem, dst)
        pem = dst
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        os.environ.setdefault(k, str(pem))


def scrub(text):
    """메시지/트레이스백에 키 값이 섞여 data.js 나 콘솔로 새지 않도록 가린다."""
    text = str(text)
    for k in SECRET_KEYS:
        v = os.environ.get(k)
        if v and len(v) >= 4:
            text = text.replace(v, "***")
    return text


def need(*keys):
    return all(os.environ.get(k) for k in keys)


def http_json(url, params=None, tries=3):
    import requests
    last = None
    for _ in range(tries):
        try:
            r = requests.get(url, params=params, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa
            last = e
            time.sleep(1.5)
    raise last


def trim(dates, vals):
    cut = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    pairs = [(d, v) for d, v in zip(dates, vals) if d >= cut[:len(d)]]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def rnd(v):
    return round(float(v), 4)


# ---------------- 소스: yfinance (키 불필요) ----------------
YF = {
    "kospi": "^KS11", "kosdaq": "^KQ11", "kospi200": "^KS200", "sp500": "^GSPC", "nasdaq": "^IXIC",
    "nikkei": "^N225", "shcomp": "000001.SS",
    "usdkrw": "KRW=X", "jpykrw": "JPYKRW=X", "eurkrw": "EURKRW=X", "dxy": "DX-Y.NYB",
    "wti": "CL=F", "brent": "BZ=F", "gold": "GC=F", "copper": "HG=F", "btc": "BTC-USD",
    "vix": "^VIX",
}


def collect_yf():
    import yfinance as yf
    series, failed = {}, []
    for key, sym in YF.items():
        try:
            df = yf.download(sym, period="5y", auto_adjust=True, progress=False)
            c = df["Close"]
            if hasattr(c, "columns"):
                c = c.iloc[:, 0]
            c = c.dropna()
            if len(c) < 20:
                raise ValueError("데이터 부족")
            series[key] = {"symbol": sym, "freq": "D", "dates": [d.strftime("%Y-%m-%d") for d in c.index],
                           "close": [rnd(v) for v in c.values]}
        except Exception as e:
            failed.append(f"{key}({sym}): {type(e).__name__}")
    return series, failed


# ---------------- 소스: FRED ----------------
# (key, series_id, transform, freq, label, unit)
FRED = [
    ("us2", "DGS2", None, "D", "미국 국채 2Y", "%"),
    ("us10", "DGS10", None, "D", "미국 국채 10Y", "%"),
    ("us30", "DGS30", None, "D", "미국 국채 30Y", "%"),
    ("ust", "T10Y2Y", None, "D", "미국 10Y-2Y", "%p"),
    ("polUS", "DFEDTARU", None, "D", "연준 기준금리 상단", "%"),
    ("usHY", "BAMLH0A0HYM2", None, "D", "미국 하이일드 스프레드", "%p"),
    ("usIG", "BAMLC0A0CM", None, "D", "미국 투자등급 스프레드", "%p"),
    ("fedbs", "WALCL", None, "W", "연준 대차대조표", "백만$"),
    ("us_cpi", "CPIAUCSL", "yoy", "M", "미국 CPI(YoY)", "%"),
    ("us_pce", "PCEPILFE", "yoy", "M", "미국 근원PCE(YoY)", "%"),
    ("us_unemp", "UNRATE", None, "M", "미국 실업률", "%"),
    ("us_nfp", "PAYEMS", "diff", "M", "미국 비농업 고용 증감", "천명"),
    ("us_retail", "RSAFS", "mom", "M", "미국 소매판매(MoM)", "%"),
    ("us_umich", "UMCSENT", None, "M", "미시간 소비자심리", ""),
    ("us_m2", "M2SL", "yoy", "M", "미국 M2(YoY)", "%"),
    ("us_gdp", "A191RL1Q225SBEA", None, "Q", "미국 GDP(QoQ 연율)", "%"),
]


def collect_fred():
    key = os.environ["FRED_API_KEY"]
    start = (datetime.now() - timedelta(days=KEEP_DAYS + 400)).strftime("%Y-%m-%d")
    series, failed = {}, []
    for k, sid, tf, freq, label, unit in FRED:
        try:
            j = http_json("https://api.stlouisfed.org/fred/series/observations",
                          dict(series_id=sid, api_key=key, file_type="json", observation_start=start))
            obs = [(o["date"], float(o["value"])) for o in j["observations"] if o["value"] not in (".", "")]
            d, v = [o[0] for o in obs], [o[1] for o in obs]
            if tf == "yoy":
                d, v = d[12:], [(v[i] / v[i - 12] - 1) * 100 for i in range(12, len(v))]
            elif tf == "mom":
                d, v = d[1:], [(v[i] / v[i - 1] - 1) * 100 for i in range(1, len(v))]
            elif tf == "diff":
                d, v = d[1:], [v[i] - v[i - 1] for i in range(1, len(v))]
            d, v = trim(d, v)
            if len(v) < 5:
                raise ValueError("데이터 부족")
            series[k] = {"symbol": sid, "freq": freq, "label": label, "unit": unit, "dates": d, "close": [rnd(x) for x in v]}
        except Exception as e:
            failed.append(f"{k}({sid}): {type(e).__name__}")
    return series, failed


# ---------------- 소스: 한국은행 ECOS ----------------
# (key, stat, cycle, item, transform, label, unit)
ECOS = [
    ("kr3", "817Y002", "D", "010200000", None, "국고채 3Y", "%"),
    ("kr10", "817Y002", "D", "010210000", None, "국고채 10Y", "%"),
    ("kr30", "817Y002", "D", "010230000", None, "국고채 30Y", "%"),
    ("cd", "817Y002", "D", "010502000", None, "CD 91일", "%"),
    ("aa", "817Y002", "D", "010300000", None, "회사채 3Y AA-", "%"),
    ("polKR", "722Y001", "D", "0101000", None, "한국은행 기준금리", "%"),
    ("cnykrw", "731Y001", "D", "0000053", None, "원/위안", "원"),
    ("kr_cpi", "901Y009", "M", "0", "yoy", "한국 CPI(YoY)", "%"),
    ("kr_unemp", "901Y027", "M", "I61BC|계절조정", None, "한국 실업률(계절조정)", "%"),
    ("kr_export", "901Y118", "M", "T002", "yoy", "한국 수출(YoY)", "%"),
    ("kr_ccsi", "511Y002", "M", "FME", None, "한국 소비자심리지수", ""),
    ("kr_m2", "161Y006", "M", "BBHA00", "yoy", "한국 M2(평잔·원계열, YoY)", "%"),
    ("kr_ca", "301Y017", "M", "SA000", "div100", "한국 경상수지", "억$"),
    ("kr_gdp", "200Y102", "Q", "10211", None, "한국 GDP(YoY)", "%"),
]


def _ecos_date(t, cycle):
    if cycle == "D":
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    if cycle == "M":
        return f"{t[:4]}-{t[4:6]}"
    return t  # 2026Q3


def collect_ecos():
    key = os.environ["ECOS_API_KEY"]
    now = datetime.now()
    s0 = now - timedelta(days=KEEP_DAYS + 400)
    rng = {"D": (s0.strftime("%Y%m%d"), now.strftime("%Y%m%d")),
           "M": (s0.strftime("%Y%m"), now.strftime("%Y%m")),
           "Q": (f"{s0.year}Q1", f"{now.year}Q4")}
    series, failed = {}, []
    for k, stat, cyc, item, tf, label, unit in ECOS:
        try:
            a, b = rng[cyc]
            item, _, sub = item.partition("|")  # "항목코드|ITEM_NAME2 필터" (예: 계절조정)
            j = http_json(f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100000/{stat}/{cyc}/{a}/{b}/{item}")
            if "StatisticSearch" not in j:
                raise RuntimeError(j.get("RESULT", {}).get("MESSAGE", "응답 형식 오류")[:60])
            rows = [r for r in j["StatisticSearch"]["row"] if r.get("DATA_VALUE") not in (None, "")
                    and (not sub or r.get("ITEM_NAME2") == sub)]
            d = [_ecos_date(r["TIME"], cyc) for r in rows]
            v = [float(r["DATA_VALUE"].replace(",", "")) for r in rows]
            if tf == "yoy":
                d, v = d[12:], [(v[i] / v[i - 12] - 1) * 100 for i in range(12, len(v))]
            elif tf == "div100":
                v = [x / 100 for x in v]
            if cyc != "Q":
                d, v = trim(d, v)
            if len(v) < 3:
                raise ValueError("데이터 부족")
            series[k] = {"symbol": f"{stat}/{item}", "freq": cyc, "label": label, "unit": unit, "dates": d, "close": [rnd(x) for x in v]}
        except Exception as e:
            failed.append(f"{k}({stat}/{item}): {type(e).__name__}")
    return series, failed


# ---------------- 소스: 금융투자협회 FREESIS (로그인 불필요) ----------------
# (key, OBJ_NM, 필드, 스케일(백만원 기준), label, unit)
FREESIS = [
    ("dep", "STATSCU0100000060BO", "TMPV2", 1e6, "투자자예탁금", "조원"),
    ("forced", "STATSCU0100000060BO", "TMPV6", 100, "위탁매매 반대매매", "억원"),
    ("credK", "STATSCU0100000070BO", "TMPV3", 1e6, "신용거래융자(코스피)", "조원"),
    ("credQ", "STATSCU0100000070BO", "TMPV4", 1e6, "신용거래융자(코스닥)", "조원"),
]


def _freesis_post(obj_nm, start, end, tries=3):
    url = "http://freesis.kofia.or.kr/meta/getMetaDataList.do"
    headers = {"Content-Type": "application/json", "Referer": "http://freesis.kofia.or.kr/stat/FreeSIS.do",
               "User-Agent": "Mozilla/5.0"}
    body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D", "tmpV45": start, "tmpV46": end, "OBJ_NM": obj_nm}}
    last = None
    import requests
    for _ in range(tries):
        try:
            r = requests.post(url, json=body, headers=headers, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa
            last = e
            time.sleep(1.5)
    raise last


def collect_freesis():
    now = datetime.now()
    start = (now - timedelta(days=KEEP_DAYS + 20)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    cache = {}
    series, failed = {}, []
    for key, obj_nm, field, scale, label, unit in FREESIS:
        try:
            if obj_nm not in cache:
                cache[obj_nm] = _freesis_post(obj_nm, start, end)
            rows = sorted(cache[obj_nm].get("ds1") or [], key=lambda r: r["TMPV1"])
            rows = [r for r in rows if r.get(field) is not None]
            d = [f"{r['TMPV1'][:4]}-{r['TMPV1'][4:6]}-{r['TMPV1'][6:]}" for r in rows]
            v = [float(r[field]) / scale for r in rows]
            d, v = trim(d, v)
            if len(v) < 5:
                raise ValueError("데이터 부족")
            series[key] = {"symbol": f"FREESIS/{obj_nm}", "freq": "D", "label": label, "unit": unit,
                           "dates": d, "close": [rnd(x) for x in v]}
        except Exception as e:
            failed.append(f"{key}({obj_nm}): {type(e).__name__}")
    return series, failed


# ---------------- 소스: KRX (다음 단계) ----------------
SNAP = {}  # 표/요약용 스냅샷 (collect_krx 가 채움)


def _chunked(fn, start, end, *args, years=1):
    """KRX 조회는 긴 기간을 한 번에 받으면 느리거나 실패할 수 있어 1년 단위로 나눠 합친다."""
    import pandas as pd
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    parts, cur = [], s
    while cur <= e:
        nxt = min(cur + timedelta(days=365 * years), e)
        df = fn(cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d"), *args)
        if df is not None and len(df):
            parts.append(df)
        cur = nxt + timedelta(days=1)
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="last")].sort_index()


def _ser(df, col, scale=1.0, freq="D", label="", unit="", symbol=""):
    df = df[col].dropna()
    return {"symbol": symbol, "freq": freq, "label": label, "unit": unit,
            "dates": [d.strftime("%Y-%m-%d") for d in df.index], "close": [rnd(v / scale) for v in df.values]}


def collect_krx():
    from pykrx import stock
    now = datetime.now()
    end = now.strftime("%Y%m%d")
    start5 = (now - timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    start10 = (now - timedelta(days=365 * 10 + 20)).strftime("%Y%m%d")
    series, failed = {}, []
    SNAP.clear()
    last_date = None

    # 1) 지수 OHLCV·거래대금 + PER/PBR/배당 (10년치로 백분위 계산, 저장은 5년)
    for key, code, nm in (("kospi", "1001", "KOSPI"), ("kosdaq", "2001", "KOSDAQ"), ("kospi200", "1028", "KOSPI200")):
        try:
            o = _chunked(stock.get_index_ohlcv, start5, end, code)
            series[key] = _ser(o, "종가", label=nm, symbol="KRX " + code)
            series[key + "_turn"] = _ser(o, "거래대금", 1e12, label=nm + " 거래대금", unit="조원", symbol="KRX " + code)
            series[key + "_mcap"] = _ser(o, "상장시가총액", 1e12, label=nm + " 시가총액", unit="조원", symbol="KRX " + code)
            last_date = max(last_date or "", o.index[-1].strftime("%Y%m%d"))
        except Exception as e:
            failed.append(f"{key}_ohlcv: {type(e).__name__}")
        try:
            f = _chunked(stock.get_index_fundamental, start10, end, code, years=2)
            f = f[f["PER"] > 0]
            for col, nmk, unit in (("PER", "per", "배"), ("PBR", "pbr", "배"), ("배당수익률", "div", "%")):
                if col in f:
                    series[f"{key}_{nmk}"] = _ser(f[f.index >= (now - timedelta(days=KEEP_DAYS))], col, label=f"{nm} {col}", unit=unit, symbol="KRX " + code)
            per = f["PER"]
            SNAP.setdefault("valuation", {})[key] = {
                "per": rnd(per.iloc[-1]), "pbr": rnd(f["PBR"].iloc[-1]), "div": rnd(f["배당수익률"].iloc[-1]),
                "roe": rnd(f["PBR"].iloc[-1] / per.iloc[-1] * 100),
                "per_pct": int(round((per < per.iloc[-1]).mean() * 100)), "pct_years": round(len(per) / 245, 1),
                "per_mean": rnd(per.mean()), "per_std": rnd(per.std()), "date": f.index[-1].strftime("%Y-%m-%d")}
        except Exception as e:
            failed.append(f"{key}_fundamental: {type(e).__name__}")

    # 2) 투자자별 순매수 (KOSPI+KOSDAQ 합산, 억원)
    try:
        import pandas as pd
        s1 = (now - timedelta(days=380)).strftime("%Y%m%d")
        tot = None
        for mk in ("KOSPI", "KOSDAQ"):
            df = _chunked(stock.get_market_trading_value_by_date, s1, end, mk)
            df = df[["외국인합계", "기관합계", "개인"]]
            tot = df if tot is None else tot.add(df, fill_value=0)
        for col, k, nm in (("외국인합계", "flow_foreign", "외국인"), ("기관합계", "flow_inst", "기관"), ("개인", "flow_retail", "개인")):
            series[k] = _ser(tot, col, 1e8, label=f"{nm} 순매수(KOSPI+KOSDAQ)", unit="억원", symbol="KRX")
    except Exception as e:
        failed.append(f"flows: {type(e).__name__}")

    # 3) 시장 폭 · 시총 상위 · 업종 (최근 거래일 기준)
    try:
        d = last_date or end
        breadth = {}
        for mk in ("KOSPI", "KOSDAQ"):
            o = stock.get_market_ohlcv_by_ticker(d, market=mk)
            o = o[o["거래량"] > 0] if "거래량" in o else o
            r = o["등락률"]
            breadth[mk] = {"up": int((r > 0).sum()), "flat": int((r == 0).sum()), "down": int((r < 0).sum()),
                           "upper": int((r >= 29.5).sum()), "lower": int((r <= -29.5).sum())}
        SNAP["breadth"] = dict(breadth, date=f"{d[:4]}-{d[4:6]}-{d[6:]}")
        o = stock.get_market_ohlcv_by_ticker(d, market="KOSPI")
        fu = stock.get_market_fundamental(d, market="KOSPI")
        fr = stock.get_exhaustion_rates_of_foreign_investment(d, market="KOSPI")
        tops = []
        for tk in o.sort_values("시가총액", ascending=False).index[:10]:
            tops.append({"name": stock.get_market_ticker_name(tk), "ticker": tk, "price": float(o.loc[tk, "종가"]),
                         "chg": float(o.loc[tk, "등락률"]), "per": float(fu.loc[tk, "PER"]) if tk in fu.index else None,
                         "pbr": float(fu.loc[tk, "PBR"]) if tk in fu.index else None,
                         "foreign": float(fr.loc[tk, "지분율"]) if tk in fr.index else None,
                         "mcap": round(float(o.loc[tk, "시가총액"]) / 1e12, 1)})
        SNAP["tops"] = tops
    except Exception as e:
        failed.append(f"breadth/tops: {type(e).__name__}")
    try:
        d = last_date or end
        names = {c: stock.get_index_ticker_name(c) for c in stock.get_index_ticker_list(d, "KOSPI")}
        sec = []
        for c in sorted(names):
            if not ("1005" <= c <= "1026"):
                continue
            d0 = (datetime.strptime(d, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
            o = stock.get_index_ohlcv(d0, d, c)  # 최근 2거래일 종가로 등락률 계산 (지수 OHLCV에는 등락률 열이 없음)
            if len(o) >= 2:
                sec.append({"name": names[c], "code": c, "chg": rnd((o["종가"].iloc[-1] / o["종가"].iloc[-2] - 1) * 100),
                            "mcap": round(float(o["상장시가총액"].iloc[-1]) / 1e12, 1)})
        SNAP["sectors"] = sec
    except Exception as e:
        failed.append(f"sectors: {type(e).__name__}")
    return series, failed


def main():
    load_env()
    setup_ssl()
    OUT_DIR.mkdir(exist_ok=True)
    try:
        prev = json.loads((OUT_DIR / "market.json").read_text(encoding="utf-8"))
    except Exception:
        prev = None
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "series": {}, "status": {}}

    def run(name, fn, required=()):
        if required and not need(*required):
            out["status"][name] = {"ok": False, "msg": "키 없음: " + ", ".join(k for k in required if not os.environ.get(k))}
            return
        t = time.time()
        try:
            series, failed = fn()
            out["series"].update(series)
            out["status"][name] = {"ok": not failed, "msg": f"{len(series)}개 수집" + (f", 실패: {failed}" if failed else ""), "sec": round(time.time() - t, 1)}
        except NotImplementedError as e:
            out["status"][name] = {"ok": False, "msg": str(e)}
        except Exception as e:
            out["status"][name] = {"ok": False, "msg": scrub(f"{type(e).__name__}: {e}")[:200]}
            print(scrub(traceback.format_exc()), file=sys.stderr)

    run("yfinance", collect_yf)
    run("fred", collect_fred, ("FRED_API_KEY",))
    run("ecos", collect_ecos, ("ECOS_API_KEY",))
    run("krx", collect_krx, ("KRX_ID", "KRX_PW"))
    run("freesis", collect_freesis)

    out["snapshot"] = dict(SNAP) if SNAP else (prev or {}).get("snapshot", {})
    # 이번에 수집 실패한 시리즈는 이전 값을 유지하되 stale 로 표시 (일시 장애로 화면이 비는 것을 방지)
    if prev:
        kept = [k for k in prev.get("series", {}) if k not in out["series"]]
        for k in kept:
            out["series"][k] = dict(prev["series"][k], stale=True)
        if kept:
            out["status"]["stale_kept"] = {"ok": False, "msg": f"이전 값 유지 {len(kept)}개: {', '.join(kept)}"}
    js = scrub(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    (OUT_DIR / "market.json").write_text(js, encoding="utf-8")
    (BASE / "data.js").write_text("window.DATA=" + js + ";", encoding="utf-8")
    for k, v in out["status"].items():
        print(f"[{'OK' if v['ok'] else '--'}] {k}: {v.get('msg', '완료')}")
    print("저장:", BASE / "data.js")


if __name__ == "__main__":
    main()
