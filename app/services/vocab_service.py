"""어려운 단어 풀이 오케스트레이션 모듈.  # (Vocab definition orchestration)

POST /vocab의 핵심 로직: 국립국어원 사전(근거) + Claude(문맥·난이도 맞춤 친근체)로 단어 풀이를
만든다(decisions: 2026-06-24, 잠긴 결정 '클라이언트 직접 호출'을 서버 경유로 변경).

흐름:
  1) krdict_client.lookup(word) — 사전에서 뜻풀이/한자어원을 best-effort로 가져온다(활용형 등은
     None일 수 있음). 사전이 막혀도 가용성 우선으로 진행.
  2) Claude(generate_json) — 사전 근거 + 단어 + 문맥(context) + 난이도(level)로 어린이/학습자
     눈높이의 친근체 meaning과 (있으면) hanja/note를 structured outputs로 생성한다. 사전이 비면
     Claude가 기본형을 추론해 보완한다.
  3) 결과는 (word, level) 키로 메모리 LRU 캐시 — 같은 단어 반복 탭/사전 일일 한도·Claude 비용 절감.

키 보호: 사전/Claude 키는 각 클라이언트가 env에서만 읽는다. Claude 호출 실패는
ClaudeError로 surface 되어 라우터가 502로 변환한다(아래 router).
관련: app/services/krdict_client.py, app/services/claude_client.py.
"""

import asyncio
import json
import logging
from collections import OrderedDict

from app.models.response import CreationResult, VocabEntry
from app.services import claude_client, krdict_client

logger = logging.getLogger(__name__)

# 창작 결과에 임베드할 어휘 사전 상한/동시성. 한 작품에 너무 많은 단어를 풀지 않도록 캡을 두고,
# 단어별 풀이(사전+Claude)는 동시 실행으로 finalize 지연을 줄인다(병렬 + define 캐시).
_GLOSSARY_MAX_WORDS = 20
_GLOSSARY_CONCURRENCY = 6

# 난이도 라벨 → Claude에 줄 '눈높이' 지시(요청 level 문자열을 사람이 읽는 지시로 변환).
# Difficulty Enum 값과 동일 키를 쓰되, 미지의 값이 와도 안전하게 기본 지시로 처리한다.
_LEVEL_HINT: dict[str, str] = {
    "children": "초등 저학년 어린이가 이해할 수 있게 아주 쉽고 다정하게.",
    "korean_learner": "한국어를 배우는 외국인 학습자가 이해할 수 있게 쉬운 표현으로.",
    "youth": "청소년이 이해할 수 있게 간결하고 자연스럽게.",
    "original": "원작의 분위기를 살리되 뜻은 분명하게.",
}
_DEFAULT_LEVEL_HINT = "초등·중등 학생이 이해할 수 있게 쉽고 다정하게."

# Claude 출력 강제용 JSON 스키마(Flutter VocabEntry와 동일 필드). null 허용으로 한자/보충설명 생략 가능.
_VOCAB_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["meaning", "hanja", "note"],
    "properties": {
        "meaning": {"type": "string", "description": "친근체(~이에요/~예요) 뜻풀이. 1~2문장."},
        "hanja": {"type": ["string", "null"], "description": "한자어원(없으면 null). 예: 完然"},
        "note": {"type": ["string", "null"], "description": "보충 설명(현대어 대응·쓰임 등, 없으면 null)."},
    },
}

_SYSTEM_PROMPT = (
    "너는 한국 고전문학을 어린이·청소년에게 풀어주는 친절한 사전이야. "
    "주어진 단어를 문장 속 쓰임에 맞게 풀이해. 규칙:\n"
    "1) meaning은 반드시 '~이에요/~예요' 같은 다정한 친근체 1~2문장으로.\n"
    "2) 한자어면 hanja에 한자(예: 完然)를, 순우리말이면 null.\n"
    "3) 현대어 대응이나 쓰임 같은 보충이 도움되면 note에, 아니면 null.\n"
    "4) 사전 근거가 주어지면 그 뜻을 토대로 하되 문맥에 맞는 한 가지 뜻만 쉽게 풀어. "
    "근거가 없으면 단어의 기본형을 추론해 네 지식으로 풀이해.\n"
    "5) 추측이 불확실하면 지어내지 말고 가장 일반적인 뜻으로.\n"
    "반드시 주어진 JSON 스키마(meaning/hanja/note)로만 답해."
)

# 메모리 LRU 캐시(프로세스 내). 같은 단어·난이도 반복 조회를 줄여 사전 일일 한도/Claude 비용을 아낀다.
_CACHE_MAX = 512
_cache: "OrderedDict[tuple[str, str], VocabEntry]" = OrderedDict()
_cache_lock = asyncio.Lock()


def _build_user_prompt(word: str, context: str, level: str, entry: dict | None) -> str:
    """Claude 유저 프롬프트 구성 기능.  # (Build user prompt for vocab)

    단어·문맥·난이도 지시와 (있으면) 사전 근거를 합쳐 Claude 입력 문자열을 만든다.
    관련: app.services.vocab_service.define.

    Args:
        word: 조회 단어.
        context: 단어가 등장한 문장(없으면 빈 문자열).
        level: 난이도 라벨.
        entry: krdict_client.lookup 결과(없으면 None).
    Returns:
        Claude 유저 프롬프트 문자열.
    """
    hint = _LEVEL_HINT.get(level, _DEFAULT_LEVEL_HINT)
    lines = [f"단어: {word}", f"눈높이: {hint}"]
    if context.strip():
        lines.append(f"문장 속 쓰임: {context.strip()}")
    if entry and entry.get("definitions"):
        lines.append(f"사전 표제어: {entry.get('headword') or word}")
        if entry.get("pos"):
            lines.append(f"품사: {entry['pos']}")
        if entry.get("origin"):
            lines.append(f"한자/어원: {entry['origin']}")
        defs = "; ".join(entry["definitions"][:3])
        lines.append(f"사전 뜻풀이(근거): {defs}")
    else:
        lines.append("사전 근거: 없음(기본형을 추론해 네 지식으로 풀이해).")
    return "\n".join(lines)


async def define(word: str, context: str, level: str) -> VocabEntry:
    """단어 풀이 1건 생성 기능.  # (Define one word → VocabEntry)

    캐시 확인 → 사전 조회(best-effort) → Claude 친근체 풀이(structured outputs) → 캐시 저장.
    사전이 비어도 Claude 단독으로 응답한다(가용성 우선). Claude 실패는 ClaudeError로 전파된다.
    관련: app.services.claude_client.generate_json.

    Args:
        word: 조회할 단어(활용형 가능).
        context: 단어가 등장한 문장(동형이의어 구분·문맥 맞춤용).
        level: 난이도 라벨(children|korean_learner|youth|original 등).
    Returns:
        VocabEntry(meaning, hanja?, note?).
    Raises:
        claude_client.ClaudeError: Claude 호출 실패/키 미설정 시.
    """
    key = (word.strip(), level.strip())
    async with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)  # LRU 갱신
            return cached

    # 1) 사전 근거(best-effort) — 막혀도 None으로 진행
    entry = await krdict_client.lookup(word.strip())

    # 2) Claude 친근체 풀이(스키마 강제 → 파싱 안전)
    user_prompt = _build_user_prompt(word, context, level, entry)
    raw = await claude_client.generate_json(_SYSTEM_PROMPT, user_prompt, _VOCAB_SCHEMA)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:  # 스키마 강제 하에선 드묾 — 명확히 실패시켜 라우터가 처리
        raise claude_client.ClaudeError(f"단어 풀이 JSON 파싱 실패: {e}") from e

    result = VocabEntry(
        meaning=(data.get("meaning") or "").strip(),
        hanja=(data.get("hanja") or None),
        note=(data.get("note") or None),
    )

    # 3) 캐시 저장(LRU 상한 유지)
    async with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result


def _find_context(result: CreationResult, word: str) -> str:
    """결과 본문에서 단어가 처음 등장하는 대사를 찾는 기능.  # (Find the line a word appears in)

    어휘 사전 풀이를 문맥에 맞추려고, 단어가 substring으로 처음 나타나는 line.text를 context로
    돌려준다(없으면 빈 문자열). Claude가 고른 단어는 본문 표기 그대로이므로 대개 매칭된다.
    관련: app.services.vocab_service.build_glossary.

    Args:
        result: 창작 결과(scenes→lines 본문).
        word: 찾을 단어(본문 표기).
    Returns:
        단어가 등장한 대사 문장 또는 빈 문자열.
    """
    for scene in result.scenes:
        for line in scene.lines:
            if word in line.text:
                return line.text
    return ""


async def build_glossary(
    words: list[str], result: CreationResult, level: str
) -> dict[str, VocabEntry]:
    """창작 결과에 임베드할 어휘 사전을 만드는 기능.  # (Build embedded glossary)

    Claude가 고른 '어려운 단어 목록'을 받아, 각 단어를 본문 문맥 + 난이도로 풀이(define)해
    {단어: VocabEntry} 맵으로 돌려준다. finalize에서 result.vocab에 임베드되어 SQLite 저장/
    오프라인·무지연 탭에 쓰인다. 풀이는 병렬로 처리하고 define 캐시를 공유한다(같은 단어 재사용).

    견고성: 어휘 사전은 보조 데이터이므로, 단어 하나가 실패해도 전체 창작을 실패시키지 않고 그
    단어만 건너뛴다(이미 성공한 창작을 잃지 않는다). 빈/중복/공백 단어는 제거하고 상한을 둔다.
    관련: app/routers/creation.py(finalize).

    Args:
        words: Claude가 고른 어려운 단어 목록(본문 표기).
        result: 창작 결과(문맥 탐색용).
        level: 난이도 라벨(풀이 눈높이).
    Returns:
        {단어: VocabEntry} 맵(실패한 단어는 빠짐, 빈 목록이면 빈 맵).
    """
    # 정규화: 공백 제거 + 중복 제거(순서 유지) + 상한
    seen: set[str] = set()
    clean: list[str] = []
    for w in words:
        w = (w or "").strip()
        if w and w not in seen:
            seen.add(w)
            clean.append(w)
        if len(clean) >= _GLOSSARY_MAX_WORDS:
            break
    if not clean:
        return {}

    sem = asyncio.Semaphore(_GLOSSARY_CONCURRENCY)

    async def _one(word: str) -> tuple[str, VocabEntry] | None:
        async with sem:
            try:
                entry = await define(word, _find_context(result, word), level)
                return word, entry
            except Exception as e:  # 단어 하나 실패는 건너뛴다(보조 데이터 — 창작 보존)
                logger.warning("어휘 사전 항목 생성 실패(건너뜀): %s — %s", word, e)
                return None

    pairs = await asyncio.gather(*(_one(w) for w in clean))
    return {w: e for p in pairs if p is not None for w, e in [p]}
