"""대사 한 줄 고쳐쓰기 오케스트레이션 모듈.  # (Single-line rewrite orchestration)

POST /rewrite-line의 핵심 로직: 대사극 결과에서 사용자가 고른 '한 줄'만 지시(더 쉽게/더 짧게/
감정 강조 등)와 읽기 수준에 맞게 Claude로 새로 써서 평문으로 돌려준다. 앞뒤 대사는 바꾸지 않고,
원작의 인물·상황·말투 톤은 유지한다. JSON이 아니라 '한 줄 평문'만 필요하므로 claude_client의
generate_text(스키마 강제 없음, thinking off)를 쓴다 — /vocab과 동일 모델(CLAUDE_MODEL) 공유.

키 보호: Claude 키는 claude_client가 env에서만 읽는다. Claude 호출 실패는
ClaudeError로 surface 되어 라우터가 502로 변환한다(아래 router).
관련: app/services/claude_client.py, app/routers/rewrite.py.
"""

from app.services import claude_client

# 난이도/읽기 수준 라벨 → Claude에 줄 '눈높이' 지시. Flutter가 한글 라벨("청소년용")이나
# Difficulty Enum 값(youth 등)을 보낼 수 있으므로 둘 다 매핑하고, 미지의 값은 기본 지시로 처리한다.
_LEVEL_HINT: dict[str, str] = {
    "children": "초등 저학년 어린이가 이해할 수 있게 아주 쉽고 다정한 말로.",
    "어린이용": "초등 저학년 어린이가 이해할 수 있게 아주 쉽고 다정한 말로.",
    "korean_learner": "한국어를 배우는 외국인 학습자가 이해할 수 있게 쉬운 표현으로.",
    "한국어학습자용": "한국어를 배우는 외국인 학습자가 이해할 수 있게 쉬운 표현으로.",
    "youth": "청소년이 이해할 수 있게 간결하고 자연스럽게.",
    "청소년용": "청소년이 이해할 수 있게 간결하고 자연스럽게.",
    "original": "원작의 분위기와 말투를 살리되 자연스럽게.",
    "원작유지": "원작의 분위기와 말투를 살리되 자연스럽게.",
}
_DEFAULT_LEVEL_HINT = "청소년이 이해할 수 있게 간결하고 자연스럽게."

_SYSTEM_PROMPT = (
    "너는 한국 고전문학을 각색한 '극본 대사'를 다듬는 보조자야. "
    "사용자가 고른 딱 한 줄만 지시에 맞게 새로 쓰고, 앞뒤 대사는 절대 바꾸지 마. "
    "원작의 인물·상황·말투 톤은 유지하되 지시(더 쉽게/더 짧게/감정 강조 등)와 "
    "읽기 수준에 맞춰 자연스러운 한국어로 고쳐. "
    "설명·따옴표·머리말 없이 '새로 쓴 대사 한 줄'만 출력해."
)


def _build_user_prompt(line: str, context: str, instruction: str, level: str) -> str:
    """Claude 유저 프롬프트 구성 기능.  # (Build user prompt for rewrite)

    바꿀 대사·맥락·지시·읽기 수준 눈높이를 합쳐 Claude 입력 문자열을 만든다.
    관련: app.services.rewrite_service.rewrite_line.

    Args:
        line: 바꿀 대사 한 줄.
        context: 앞뒤 맥락(전체 대본 줄, 없으면 빈 문자열).
        instruction: 고쳐쓰기 지시(없으면 빈 문자열).
        level: 읽기 수준 라벨.
    Returns:
        Claude 유저 프롬프트 문자열.
    """
    hint = _LEVEL_HINT.get(level.strip(), _DEFAULT_LEVEL_HINT)
    parts = [f"[읽기 수준] {hint}"]
    if instruction.strip():
        parts.append(f"[지시] {instruction.strip()}")
    else:
        parts.append("[지시] 더 자연스럽게 다듬어 줘.")
    if context.strip():
        parts.append(f"\n[전체 대본 맥락]\n{context.strip()}")
    parts.append(f"\n[바꿀 대사]\n{line.strip()}")
    parts.append("\n위 '바꿀 대사' 한 줄만 지시에 맞게 새로 써서, 그 한 줄만 출력해.")
    return "\n".join(parts)


async def rewrite_line(line: str, context: str, instruction: str, level: str) -> str:
    """대사 한 줄 고쳐쓰기 기능.  # (Rewrite one dialogue line)

    고른 한 줄만 지시·읽기 수준에 맞게 Claude로 새로 써서 평문 한 줄로 돌려준다. 앞뒤 대사는
    바꾸지 않는다. JSON이 아닌 평문이 필요하므로 generate_text(스키마 강제 없음)를 쓴다. Claude
    실패는 ClaudeError로 전파되어 라우터가 502로 변환한다. 빈 응답 방어는 라우터에서 처리한다.
    관련: app.services.claude_client.generate_text.

    Args:
        line: 바꿀 대사 한 줄.
        context: 앞뒤 맥락(전체 대본 줄, 비어도 됨).
        instruction: 고쳐쓰기 지시(비어도 됨).
        level: 읽기 수준 라벨(눈높이 조절용, 비어도 됨).
    Returns:
        새로 쓴 대사 한 줄(앞뒤 공백 제거됨). 빈 문자열일 수 있음(라우터가 원문으로 보정).
    Raises:
        claude_client.ClaudeError: Claude 호출 실패/키 미설정 시.
    """
    user_prompt = _build_user_prompt(line, context, instruction, level)
    text = await claude_client.generate_text(_SYSTEM_PROMPT, user_prompt)
    # 여러 줄이 오면 첫 비어있지 않은 줄만 취한다(시스템 지시는 '한 줄'이지만 방어적으로 정규화).
    for raw in text.splitlines():
        cleaned = raw.strip().strip('"').strip("'").strip()
        if cleaned:
            return cleaned
    return text.strip()
