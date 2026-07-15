"""대사 고쳐쓰기 라우터 모듈.  # (Rewrite-line router)

엔드포인트:
- POST /rewrite-line — 대사극 결과 화면의 'AI로 바꾸기'. 사용자가 고른 대사 '한 줄만' 지시·읽기
  수준에 맞게 새로 써서 돌려준다(앞뒤 대사는 유지). 요청 {line, context, instruction, level}
  → 응답 {text}(Flutter api_client.rewriteLine과 동일).

파이프라인: rewrite_service.rewrite_line(Claude 평문 한 줄). 빈 line은 pydantic이 422로
거부하고, Claude 호출 실패는 502로 변환한다. Claude가 빈 응답을 주면 원문 line을 그대로 돌려준다.
관련: app/services/rewrite_service.py.
"""

from fastapi import APIRouter, HTTPException

from app.models.request import RewriteLineRequest
from app.models.response import RewriteLineResponse
from app.services import claude_client, rewrite_service

router = APIRouter()


@router.post("/rewrite-line", response_model=RewriteLineResponse)
async def rewrite_line(req: RewriteLineRequest) -> RewriteLineResponse:
    """대사 한 줄 고쳐쓰기 엔드포인트.  # (POST /rewrite-line)

    - 요청: RewriteLineRequest(line/context/instruction/level). 빈 line은 pydantic이 422로 거부.
    - 응답: RewriteLineResponse(text) — 새로 쓴 대사 한 줄. Claude가 빈 응답이면 원문 line 유지.
    대사극 결과 화면에서 한 줄을 골라 'AI로 바꾸기'를 누르면 호출된다. Claude 실패는 502로
    surface 한다 — 앱은 그때 "서버에 연결할 수 없어요" 스낵바를 띄운다.
    관련: app/services/rewrite_service.py, service-flutter-app POST /rewrite-line.

    Args:
        req: 대사 고쳐쓰기 요청.
    Returns:
        RewriteLineResponse(새 대사 한 줄).
    Raises:
        HTTPException: Claude 호출 실패 시 502.
    """
    try:
        text = await rewrite_service.rewrite_line(
            req.line, req.context, req.instruction, req.level
        )
    except claude_client.ClaudeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    # 빈 응답이면 원문을 유지해, 앱이 빈 대사로 덮어쓰지 않게 한다(견고성).
    return RewriteLineResponse(text=text or req.line)
