"""단어 풀이 라우터 모듈.  # (Vocab router)

엔드포인트:
- POST /vocab — 대사극/오디오극에서 어려운 단어를 탭하면 풀이 1건을 돌려준다.
  요청 {word, context, level} → 응답 {hanja?, meaning, note?}(Flutter VocabEntry와 동일).

파이프라인: vocab_service.define(국립국어원 사전 근거 + Claude 친근체 풀이 + 캐시).
잘못된 요청(빈 word 등)은 pydantic이 422로 거부하고, Claude 호출 실패는 502로 변환한다.
관련: app/services/vocab_service.py.
"""

from fastapi import APIRouter, HTTPException

from app.models.request import VocabRequest
from app.models.response import VocabEntry
from app.services import claude_client, vocab_service

router = APIRouter()


@router.post("/vocab", response_model=VocabEntry)
async def vocab(req: VocabRequest) -> VocabEntry:
    """단어 풀이 엔드포인트.  # (POST /vocab)

    - 요청: VocabRequest(word/context/level). 빈 word는 pydantic이 422로 거부.
    - 응답: VocabEntry(meaning + hanja?/note?). 사전이 막혀도 Claude 단독으로 응답.
    이 엔드포인트는 창작 결과에 임베드된 vocab에 없는 단어를 탭했을 때의 on-demand 폴백이다
    (기본 사전은 /create finalize에서 result.vocab로 임베드됨). Claude 실패는 502로 surface.
    관련: app/services/vocab_service.py, service-flutter-app POST /vocab.

    Args:
        req: 단어 풀이 요청.
    Returns:
        VocabEntry.
    Raises:
        HTTPException: Claude 호출 실패 시 502.
    """
    try:
        return await vocab_service.define(req.word, req.context, req.level)
    except claude_client.ClaudeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
