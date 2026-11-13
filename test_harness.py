"""lowprice_bot.py 동작 검증용 단위 테스트 (실제 네트워크 요청 없이 가상 데이터 사용)."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import lowprice_bot


class _FakeHTTPResponse:
    def __init__(self, body: str, status: int = 200) -> None:
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _build_fake_html(include_ads: bool = True) -> str:
    ad_block = ""
    if include_ads:
        ad_block = """
        <li data-product-seq="999001" data-ad="true">
          <p class="mb-2"><a href="https://prod.danawa.com/info/?pcode=999001">삼성전자 갤럭시워치9 40mm 블루투스</a></p>
          <span>광고</span>
          <a href="https://prod.danawa.com/info/?pcode=999001&bookmark=cm_opinion">리뷰수 ( 999 )</a>
        </li>
        <li data-product-seq="999002" class="powerclick">
          <p class="mb-2"><a href="https://prod.danawa.com/info/?pcode=999002">삼성전자 갤럭시워치9 44mm 블루투스</a></p>
          <a href="https://prod.danawa.com/info/?pcode=999002&bookmark=cm_opinion">리뷰수 ( 500 )</a>
        </li>
        """

    return f"""
    <html>
      <body>
        <ul>
          {ad_block}
          <li data-product-seq="1001" class="border-b leading-22">
            <p class="mb-2"><a href="https://prod.danawa.com/info/?pcode=1001">삼성전자 갤럭시워치9 40mm 블루투스</a></p>
            <a href="https://prod.danawa.com/info/?pcode=1001&bookmark=cm_opinion">4.9 리뷰수 ( 12 )</a>
          </li>
          <li data-product-seq="1002" class="border-b leading-22">
            <p class="mb-2"><a href="https://prod.danawa.com/info/?pcode=1002">삼성전자 갤럭시워치9 44mm 블루투스</a></p>
            <a href="https://prod.danawa.com/info/?pcode=1002&bookmark=cm_opinion">4.8 리뷰수 ( 34 )</a>
          </li>
          <li data-product-seq="1003" class="border-b leading-22">
            <p class="mb-2"><a href="https://prod.danawa.com/info/?pcode=1003">삼성전자 갤럭시워치9 40mm LTE</a></p>
            <a href="https://prod.danawa.com/info/?pcode=1003&bookmark=cm_opinion">4.7 리뷰수 ( 56 )</a>
          </li>
        </ul>
      </body>
    </html>
    """


def _make_fake_subprocess_run(price_map: dict[str, tuple[str, int, str]]):
    def _fake_run(cmd, **kwargs):
        # cmd: [..., "offers", pcode, "--limit", "1"]
        pcode = cmd[cmd.index("offers") + 1]
        if pcode not in price_map:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=f"unexpected pcode={pcode}",
            )
        mall, price, title = price_map[pcode]
        payload = {
            "pcode": pcode,
            "title": title,
            "offers": [
                {
                    "mall": mall,
                    "total_price": price,
                    "url": f"https://prod.danawa.com/bridge/loadingBridge.html?pcode={pcode}",
                }
            ],
        }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload, ensure_ascii=False),
            stderr="",
        )

    return _fake_run


def _run_bot_with_mocks(html: str, price_map: dict[str, tuple[str, int, str]]) -> tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with (
        patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(html)),
        patch("subprocess.run", side_effect=_make_fake_subprocess_run(price_map)),
        redirect_stdout(stdout_buf),
        redirect_stderr(stderr_buf),
    ):
        exit_code = lowprice_bot.main()
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def test_1_under_or_equal_target_price() -> tuple[bool, str]:
    price_map = {
        "1001": ("가상몰A", 359_000, "삼성전자 갤럭시워치9 40mm 블루투스"),
        "1002": ("가상몰B", 380_000, "삼성전자 갤럭시워치9 44mm 블루투스"),
        "1003": ("가상몰C", 410_000, "삼성전자 갤럭시워치9 40mm LTE"),
    }
    code, out, err = _run_bot_with_mocks(_build_fake_html(include_ads=False), price_map)
    ok = (
        code == 0
        and "[특가발견] 목표 가격 이하입니다!" in out
        and "[유지] 아직 목표 가격 보다 비싸요!" not in out
        and not err
    )
    detail = f"exit={code}" if ok else f"exit={code}, out={out!r}, err={err!r}"
    return ok, detail


def test_2_over_target_price() -> tuple[bool, str]:
    price_map = {
        "1001": ("가상몰A", 361_000, "삼성전자 갤럭시워치9 40mm 블루투스"),
        "1002": ("가상몰B", 390_000, "삼성전자 갤럭시워치9 44mm 블루투스"),
        "1003": ("가상몰C", 420_000, "삼성전자 갤럭시워치9 40mm LTE"),
    }
    code, out, err = _run_bot_with_mocks(_build_fake_html(include_ads=False), price_map)
    ok = (
        code == 0
        and "[유지] 아직 목표 가격 보다 비싸요!" in out
        and "[특가발견] 목표 가격 이하입니다!" not in out
        and not err
    )
    detail = f"exit={code}" if ok else f"exit={code}, out={out!r}, err={err!r}"
    return ok, detail


def test_3_filter_out_ads() -> tuple[bool, str]:
    # 광고 상품(999001, 999002)이 100,000원이라는 더 싼 가격을 갖도록 설정해도
    # 필터링되어 결과에 포함되지 않아야 함.
    price_map = {
        "999001": ("광고몰1", 100_000, "광고 갤럭시워치9 1"),
        "999002": ("광고몰2", 120_000, "광고 갤럭시워치9 2"),
        "1001": ("가상몰A", 430_000, "삼성전자 갤럭시워치9 40mm 블루투스"),
        "1002": ("가상몰B", 450_000, "삼성전자 갤럭시워치9 44mm 블루투스"),
        "1003": ("가상몰C", 480_000, "삼성전자 갤럭시워치9 40mm LTE"),
    }
    code, out, err = _run_bot_with_mocks(_build_fake_html(include_ads=True), price_map)
    ok = (
        code == 0
        and "광고몰1" not in out
        and "광고몰2" not in out
        and "999001" not in out
        and "999002" not in out
        and "가상몰A" in out
        and "가상몰B" in out
        and "가상몰C" in out
        and not err
    )
    detail = "광고 상품 2건 제외 확인" if ok else f"exit={code}, out={out!r}, err={err!r}"
    return ok, detail


def test_4_error_on_empty_or_invalid_data() -> tuple[bool, str]:
    # 1) HTML에 상품이 전혀 없을 때 멈추지 않고(예외 미전파) 오류 메시지 출력 후 종료코드 1 반환
    code1, out1, err1 = _run_bot_with_mocks("<html><body><div>상품 없음</div></body></html>", {})
    # 2) offers가 비어 있을 때
    def _empty_offers_run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"pcode": "1001", "offers": []}),
            stderr="",
        )

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with (
        patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(_build_fake_html(include_ads=False))),
        patch("subprocess.run", side_effect=_empty_offers_run),
        redirect_stdout(stdout_buf),
        redirect_stderr(stderr_buf),
    ):
        code2 = lowprice_bot.main()
    err2 = stderr_buf.getvalue()

    ok = (
        code1 == 1
        and "[오류]" in err1
        and code2 == 1
        and "[오류]" in err2
    )
    detail = "오류 감지 및 정상 중단 확인" if ok else f"code1={code1}, err1={err1!r}, code2={code2}, err2={err2!r}"
    return ok, detail


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    tests = [
        ("1. 36만원 이하일 때 특가 메시지 출력", test_1_under_or_equal_target_price),
        ("2. 36만원 초과일 때 유지 메시지 출력", test_2_over_target_price),
        ("3. 광고 상품이 섞여 있을 때 필터링", test_3_filter_out_ads),
        ("4. 값을 가져오지 못했을 때 오류 안내 및 중단", test_4_error_on_empty_or_invalid_data),
    ]

    all_passed = True
    print("=" * 60)
    print("lowprice_bot.py 가상 데이터 검사 결과")
    print("=" * 60)
    for name, fn in tests:
        passed, detail = fn()
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"[{status}] {name} ({detail})")
    print("=" * 60)
    print("최종 결과:", "ALL PASS" if all_passed else "FAIL")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
