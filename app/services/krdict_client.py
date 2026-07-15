"""국립국어원 사전 Open API 호출 모듈.  # (Korean dict Open API client)

어려운 단어 풀이(POST /vocab)의 '근거 자료'를 국립국어원 한국어기초사전(krdict.korean.go.kr)
Open API에서 가져온다. 이 API는 **XML만 반환**하므로 표준 라이브러리 xml.etree로 파싱한다(새 의존성 없음).
키(KRDICT_API_KEY)는 env에서만 읽고 코드/로그/git에 남기지 않는다.

설계:
- 검색 API(/api/search)로 표제어 뜻풀이(definition)·한자어원(origin)·품사(pos)를 best-effort로 가져온다.
- 표제어가 활용형('완연한','예사롭지')이면 검색이 빗나갈 수 있다. 이때는 None을 돌려주고,
  상위(vocab_service)에서 Claude가 기본형을 추론해 풀이를 보완한다(견고성 > 완벽성).
- 네트워크/파싱/사전 오류는 KrdictError로 감싸지 않고 경고 로그 후 None을 돌려준다 → 사전이
  막혀도 /vocab는 Claude 단독으로라도 응답한다(어린이용 앱의 가용성 우선).
관련: app/services/vocab_service.py.
"""

import logging
from xml.etree import ElementTree as ET

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# 검색 API 엔드포인트 — 호스트는 환경에 무관한 고정 상수라 env로 빼지 않는다(IP/포트 하드코딩 금지와 무관).
_SEARCH_URL = "https://krdict.korean.go.kr/api/search"

# 한 번 호출에 받을 결과 수(첫 표제어만 쓰지만 동형이의어 대비 약간 받는다)와 네트워크 타임아웃(초).
_NUM = 10
_TIMEOUT = 8.0


async def lookup(word: str) -> dict | None:
    """단어를 사전에서 조회하는 기능(best-effort).  # (Look up a word in the dictionary)

    검색 API를 호출해 첫 표제어의 뜻풀이/한자어원/품사를 돌려준다. 결과가 없거나(활용형 등)
    키 미설정·네트워크·파싱 오류면 None을 돌려준다(상위에서 Claude로 보완). 키는 env에서만 읽는다.

    Args:
        word: 조회할 단어(사용자가 탭한 원형 그대로; 활용형일 수 있음).
    Returns:
        {"headword", "pos", "origin", "definitions": [str, ...]} 또는 None.
    """
    key = get_settings().krdict_api_key
    if not key:
        # 키가 없으면 사전 근거 없이 진행(상위에서 Claude 단독). 키는 로그에 남기지 않는다.
        logger.warning("KRDICT_API_KEY 미설정 — 사전 조회를 건너뜁니다(.env 설정 권장).")
        return None

    params = {"key": key, "q": word, "part": "word", "sort": "dict", "num": _NUM}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_SEARCH_URL, params=params)
            resp.raise_for_status()
            return _parse_first_item(resp.text)
    except Exception as e:  # 네트워크/파싱/사전 오류 — 가용성 우선으로 삼키고 None
        logger.warning("사전 조회 실패(무시하고 Claude로 보완): %s", e)
        return None


def _parse_first_item(xml_text: str) -> dict | None:
    """검색 응답 XML에서 첫 표제어를 파싱하는 기능.  # (Parse first item from search XML)

    <channel><item>... 구조에서 첫 item의 word/pos/origin과 모든 sense/definition을 모은다.
    <error> 응답(키 오류·한도 초과 등)이거나 item이 없으면 None을 돌려준다.
    관련: app.services.krdict_client.lookup.

    Args:
        xml_text: 검색 API가 돌려준 XML 문자열.
    Returns:
        파싱된 dict 또는 None.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("사전 XML 파싱 실패: %s", e)
        return None

    # 오류 응답(<error><error_code>020</error_code>...)은 표제어가 없으므로 None.
    if root.tag == "error" or root.find("error_code") is not None:
        code = root.findtext("error_code", default="?")
        logger.warning("사전 API 오류(error_code=%s) — Claude로 보완합니다.", code)
        return None

    item = root.find("item")  # channel/item 또는 root/item 모두 대응
    if item is None:
        item = root.find(".//item")
    if item is None:
        return None  # total=0 (검색 결과 없음 → 활용형 등)

    definitions = [
        d.strip()
        for d in (s.findtext("definition", default="") for s in item.findall("sense"))
        if d.strip()
    ]
    return {
        "headword": (item.findtext("word", default="") or "").strip(),
        "pos": (item.findtext("pos", default="") or "").strip(),
        "origin": (item.findtext("origin", default="") or "").strip(),
        "definitions": definitions,
    }
