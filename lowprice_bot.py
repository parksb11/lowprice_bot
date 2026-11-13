"""다나와(danawa-price-search 스킬) 기반 '갤럭시워치 9' 최저가 모니터링 봇."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

SEARCH_QUERY = "갤럭시워치 9"
TARGET_PRICE = 500_000
TOP_N = 3
ENV_PATH = Path(__file__).resolve().parent / ".env"

AD_KEYWORDS = ("광고", "파워클릭", "스폰서", "AD")
ACCESSORY_KEYWORDS = (
    "액세서리",
    "스트랩",
    "시계줄",
    "밴드",
    "케이스",
    "보호필름",
    "충전기",
    "무선충전",
    "보조배터리",
    "거치대",
    "베젤링",
    "어댑터",
)


@dataclass
class WatchProduct:
    pcode: str
    title: str
    review_count: int
    mall: str
    price: int
    link: str


def fetch_search_html(query: str) -> str:
    """다나와 통합검색 페이지를 조회해 HTML 문자열을 반환한다."""
    encoded = urllib.parse.quote(query)
    url = f"https://search.danawa.com/?query={encoded}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                raise RuntimeError(f"다나와 검색 HTTP 상태 코드 오류: {resp.status}")
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"다나와 검색 페이지 요청 실패: {exc}") from exc


def is_ad_item(li) -> bool:
    """광고 상품 여부를 판별한다."""
    if li.has_attr("data-ad") or li.select_one("[data-ad]"):
        return True
    for el in [li, *li.find_all(True)]:
        for cls in el.get("class", []):
            low = cls.lower()
            if low in ("ad", "ads", "powerclick", "sponsor", "sponsored") or low.startswith(("ad-", "ad_")):
                return True
    for text in li.stripped_strings:
        if text in AD_KEYWORDS:
            return True
    return False


def is_accessory_item(li, title: str) -> bool:
    """스트랩/케이스 등 워치 액세서리 여부를 판별한다."""
    full_text = li.get_text(" ", strip=True)
    if "워치/밴드 액세서리" in full_text:
        return True
    if any(kw in title for kw in ACCESSORY_KEYWORDS):
        return True
    return False


def parse_review_count(li) -> int:
    """상품 항목에서 리뷰 수를 추출한다. 없으면 0을 반환한다."""
    rev_el = li.select_one("a[href*='bookmark=cm_opinion']")
    if not rev_el:
        return 0
    rev_text = rev_el.get_text(" ", strip=True)
    m = re.search(r"리뷰수\s*\(\s*([\d,]+)\s*\)", rev_text)
    if m:
        return int(m.group(1).replace(",", ""))
    m_op = re.search(r"의견\s*([\d,]+)", rev_text)
    if m_op:
        return int(m_op.group(1).replace(",", ""))
    return 0


def parse_search_candidates(html: str) -> list[dict]:
    """검색 HTML에서 광고와 액세서리를 제외한 실제 상품 후보를 추출한다."""
    if "captcha" in html.lower() or "비정상적인 접근" in html:
        raise RuntimeError("다나와 검색 페이지에서 보안 확인(CAPTCHA/접근 제한)이 감지되었습니다.")

    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("li[data-product-seq]")
    if not items:
        raise RuntimeError(
            "다나와 검색 결과에서 상품 목록(li[data-product-seq])을 찾지 못했습니다. "
            "페이지 구조가 변경되었거나 검색 결과가 비어 있습니다."
        )

    candidates: list[dict] = []
    seen_pcodes: set[str] = set()

    for li in items:
        pcode = (li.get("data-product-seq") or "").strip()
        if not pcode.isdigit() or pcode in seen_pcodes:
            continue
        if is_ad_item(li):
            continue

        title_el = li.select_one("p.mb-2 a")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            continue
        if "갤럭시워치9" not in title.replace(" ", ""):
            continue
        if is_accessory_item(li, title):
            continue

        seen_pcodes.add(pcode)
        candidates.append(
            {
                "pcode": pcode,
                "title": title,
                "review_count": parse_review_count(li),
            }
        )

    if not candidates:
        raise RuntimeError(
            "광고 및 액세서리를 제외한 실제 '갤럭시워치 9' 상품 후보를 찾지 못했습니다."
        )
    return candidates


def fetch_lowest_offer_via_skill(pcode: str) -> dict:
    """danawa-price-search 스킬의 offers 명령으로 최저가 오퍼를 조회한다."""
    cmd = [
        "npx",
        "-y",
        "@nomadamas/k-skill@0",
        "exec",
        "danawa-price-search",
        "scripts/danawa_search.py",
        "--",
        "offers",
        pcode,
        "--limit",
        "1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            shell=(sys.platform == "win32"),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pcode={pcode} 스킬 실행 시간 초과: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"pcode={pcode} 스킬 실행 실패: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"pcode={pcode} danawa-price-search 실행 오류 (exit={proc.returncode}): {detail}"
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"pcode={pcode} 스킬 출력 JSON 파싱 실패: {exc}\n원본 출력: {proc.stdout[:300]}"
        ) from exc

    if "error" in data:
        raise RuntimeError(f"pcode={pcode} 스킬 오류 반환: {data['error']}")

    offers = data.get("offers")
    if not isinstance(offers, list) or not offers:
        raise RuntimeError(
            f"pcode={pcode}에 대한 판매처 오퍼(offers)가 비어 있습니다."
        )

    top = offers[0]
    price = top.get("total_price")
    if not isinstance(price, int) or price <= 0:
        raise RuntimeError(
            f"pcode={pcode}의 최저가 값이 올바르지 않습니다: total_price={price!r}"
        )

    mall = (top.get("mall") or "").strip()
    link = (top.get("url") or "").strip()
    if not mall or not link:
        raise RuntimeError(
            f"pcode={pcode}의 쇼핑몰 또는 링크 정보가 누락되었습니다: mall={mall!r}, url={link!r}"
        )

    return {
        "mall": mall,
        "price": price,
        "link": link,
        "title": (data.get("title") or "").strip(),
    }


def load_kakao_credentials(env_path: Path = ENV_PATH) -> tuple[str, str, str]:
    """프로젝트 폴더의 .env 파일 또는 환경 변수(GitHub Secrets 등)에서 카카오 인증 정보를 읽어온다."""
    values: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip('"').strip("'")

    rest_api_key = os.environ.get("KAKAO_REST_API_KEY") or values.get("KAKAO_REST_API_KEY", "")
    client_secret = os.environ.get("KAKAO_CLIENT_SECRET") or values.get("KAKAO_CLIENT_SECRET", "")
    refresh_token = os.environ.get("KAKAO_REFRESH_TOKEN") or values.get("KAKAO_REFRESH_TOKEN", "")

    missing = [
        name
        for name, val in (
            ("KAKAO_REST_API_KEY", rest_api_key),
            ("KAKAO_CLIENT_SECRET", client_secret),
            ("KAKAO_REFRESH_TOKEN", refresh_token),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"환경 변수 또는 .env에 필수 카카오 인증 정보가 없습니다: {', '.join(missing)}"
        )

    return rest_api_key, client_secret, refresh_token


def refresh_kakao_access_token(
    rest_api_key: str, client_secret: str, refresh_token: str
) -> str:
    """리프레시 토큰으로 카카오 액세스 토큰을 발급받는다."""
    payload = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": rest_api_key,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://kauth.kakao.com/oauth/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"카카오 토큰 갱신 HTTP {exc.code} 오류: {err_detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"카카오 토큰 갱신 네트워크 오류: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"카카오 토큰 갱신 응답 JSON 파싱 실패: {exc}") from exc

    access_token = (body.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(f"카카오 액세스 토큰이 응답에 없습니다: {body}")
    return access_token


def send_kakao_memo(item: WatchProduct) -> None:
    """1위 최저가 상품 정보를 카카오톡 '나에게 보내기'로 전송한다."""
    rest_api_key, client_secret, refresh_token = load_kakao_credentials()
    access_token = refresh_kakao_access_token(rest_api_key, client_secret, refresh_token)

    message_text = (
        f"[특가발견] 목표가({TARGET_PRICE:,}원) 이하!\n"
        f"상품명: {item.title}\n"
        f"가격: {item.price:,}원\n"
        f"쇼핑몰: {item.mall}\n"
        f"리뷰수: {item.review_count:,}개"
    )
    template_obj = {
        "object_type": "text",
        "text": message_text[:200],
        "link": {
            "web_url": item.link,
            "mobile_web_url": item.link,
        },
        "button_title": "구매하러 가기",
    }
    payload = urllib.parse.urlencode(
        {"template_object": json.dumps(template_obj, ensure_ascii=False)}
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"카카오톡 전송 HTTP {exc.code} 오류: {err_detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"카카오톡 전송 네트워크 오류: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"카카오톡 전송 응답 JSON 파싱 실패: {exc}") from exc

    if body.get("result_code") != 0:
        raise RuntimeError(f"카카오톡 전송 실패 응답: {body}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    try:
        html = fetch_search_html(SEARCH_QUERY)
        candidates = parse_search_candidates(html)

        products: list[WatchProduct] = []
        for cand in candidates:
            offer = fetch_lowest_offer_via_skill(cand["pcode"])
            title = offer["title"] or cand["title"]
            products.append(
                WatchProduct(
                    pcode=cand["pcode"],
                    title=title,
                    review_count=cand["review_count"],
                    mall=offer["mall"],
                    price=offer["price"],
                    link=offer["link"],
                )
            )

        products.sort(key=lambda p: p.price)
        top_products = products[:TOP_N]
        if not top_products:
            raise RuntimeError("최종 정렬된 상품 목록이 비어 있습니다.")

    except Exception as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1

    print("=" * 72)
    print(f"다나와 '{SEARCH_QUERY}' 최저가 TOP {len(top_products)} (광고 제외)")
    print("=" * 72)
    for idx, item in enumerate(top_products, start=1):
        print(f"{idx}위. {item.title}")
        print(f"    - 가격   : {item.price:,}원")
        print(f"    - 쇼핑몰 : {item.mall}")
        print(f"    - 리뷰수 : {item.review_count:,}개")
        print(f"    - 링크   : {item.link}")
        print("-" * 72)

    best_item = top_products[0]
    best_price = best_item.price
    print(f"1위 최저가: {best_price:,}원 / 목표가: {TARGET_PRICE:,}원")
    if best_price <= TARGET_PRICE:
        print("[특가발견] 목표 가격 이하입니다!")
        try:
            send_kakao_memo(best_item)
            print("[카카오톡 알림 발송 완료] 나에게 보내기 전송에 성공했습니다.")
        except Exception as exc:
            print(f"[카카오톡 알림 실패] {exc}", file=sys.stderr)
            return 1
    else:
        print("[유지] 아직 목표 가격 보다 비싸요!")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
