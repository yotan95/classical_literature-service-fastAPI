"""Claude API 호출 래퍼 모듈.  # (Claude API client)

ANTHROPIC_API_KEY/CLAUDE_MODEL을 '환경변수에서만' 읽어 Anthropic API를 호출한다.
키는 코드/로그/git에 절대 남기지 않는다. 긴 출력으로 인한 타임아웃을 피하려고
스트리밍 후 get_final_message()로 완성 메시지를 받는다. 실패/타임아웃은 ClaudeError로
감싸 호출부(SSE)가 error 이벤트로 surface 할 수 있게 한다.
"""

from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
)

from app.config import get_settings


class ClaudeError(Exception):
    """Claude 호출 실패 예외.  # (Claude call failure)

    인증/연결/타임아웃/응답오류 등을 사람이 읽을 수 있는 메시지로 감싼다.
    """


async def _invoke(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    thinking: dict,
    output_schema: dict | None,
    effort: str | None = None,
) -> str:
    """Claude 호출 공용 코어 기능.  # (Shared Claude call core)

    키 확인 → 스트리밍 호출 → 완성 메시지 취득 → stop_reason 처리 → 텍스트 추출까지를
    한 곳에 모은다(창작/단어풀이 등 모든 호출이 동일한 오류 변환·키 보호를 공유). 모델 문자열은
    설정(CLAUDE_MODEL)에서 읽고 코드에 하드코딩하지 않는다. 실패/타임아웃/빈 응답/거부/
    잘림은 모두 ClaudeError로 감싸 호출부(SSE 등)가 surface 한다.

    Args:
        system_prompt: 역할/규칙/스키마 지시.
        user_prompt: 입력(원작·옵션 또는 단어·문맥 등).
        max_tokens: 최대 출력 토큰.
        thinking: thinking 설정 dict(예: {"type": "adaptive"} | {"type": "disabled"}).
        output_schema: structured outputs용 JSON 스키마(없으면 일반 텍스트).
        effort: 사고/생성 깊이(low|medium|high|max). None이면 모델 기본(Sonnet 4.6=high).
            창작처럼 오래 걸리는 호출은 medium으로 낮춰 생성 지연을 줄인다.
    Returns:
        Claude가 출력한 원시 텍스트.
    Raises:
        ClaudeError: 키 미설정 또는 호출 실패/타임아웃/빈 응답/거부/잘림 시.
    """
    settings = get_settings()
    # 키는 env에서만 — 없으면 즉시 명확히 실패
    if not settings.anthropic_api_key:
        raise ClaudeError("ANTHROPIC_API_KEY가 설정되지 않았습니다(.env에서 설정 필요).")

    # structured outputs(스키마로 출력 강제)와 effort(생성 깊이)는 모두 output_config 안에 들어간다.
    # effort=medium은 Sonnet 4.6 기본값 high보다 사고·생성을 줄여 긴 창작 호출의 지연을 낮춘다.
    output_config: dict = {}
    if output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}
    if effort is not None:
        output_config["effort"] = effort
    extra: dict = {}
    if output_config:
        extra["output_config"] = output_config

    try:
        async with AsyncAnthropic(api_key=settings.anthropic_api_key) as client:
            # 스트리밍으로 호출하되, 토큰 단위가 아니라 완성 메시지만 받는다(타임아웃 회피).
            async with client.messages.stream(
                model=settings.claude_model,  # 코드에 모델 하드코딩 금지
                max_tokens=max_tokens,
                thinking=thinking,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                **extra,
            ) as stream:
                message = await stream.get_final_message()
    except AuthenticationError as e:
        raise ClaudeError("Claude 인증 실패: ANTHROPIC_API_KEY를 확인하세요.") from e
    except APITimeoutError as e:
        raise ClaudeError("Claude 호출이 시간 초과되었습니다.") from e
    except APIConnectionError as e:
        raise ClaudeError("Claude 서버 연결에 실패했습니다.") from e
    except APIStatusError as e:
        raise ClaudeError(f"Claude API 오류(status={e.status_code}).") from e
    except APIError as e:  # 그 외 SDK 오류 포괄
        raise ClaudeError("Claude 호출 중 오류가 발생했습니다.") from e

    # 길이/안전 한도를 먼저 명확히 처리. max_tokens 초과면 thinking만 남고 본문이 없거나
    # JSON이 중간에 잘려 파싱이 깨지므로, '잘림'임을 분명히 알린다.
    if message.stop_reason == "refusal":
        raise ClaudeError("Claude가 안전상의 이유로 응답을 거부했습니다(stop_reason=refusal).")
    if message.stop_reason == "max_tokens":
        raise ClaudeError(
            "Claude 응답이 max_tokens 한도에 도달해 잘렸습니다(max_tokens를 늘려 재시도)."
        )

    # 텍스트 블록만 모은다(thinking 블록 등은 제외)
    parts = [b.text for b in message.content if getattr(b, "type", None) == "text"]
    text = "".join(parts).strip()
    if not text:
        raise ClaudeError("Claude 응답에서 텍스트를 찾지 못했습니다.")
    return text


async def generate_creation(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 64000,
    output_schema: dict | None = None,
) -> str:
    """Claude 창작 호출 기능.  # (Call Claude for creation)

    시스템/유저 프롬프트로 Claude를 호출하고, 응답의 텍스트(= JSON 문자열)를 돌려준다.
    모델 문자열은 설정(CLAUDE_MODEL=claude-sonnet-4-6)에서 읽는다. Sonnet 4.6의
    adaptive thinking + effort=medium을 사용한다(기본 high는 생성이 지나치게 길어져 SSE 연결이
    끊김 → medium으로 지연 단축). output_schema가 주어지면 structured outputs로 출력을
    그 JSON 스키마에 강제해, 긴 응답에서 문법이 깨지는 파싱 오류 클래스를 제거한다.
    키가 없으면 명확한 ClaudeError를 던진다.

    Args:
        system_prompt: 역할/규칙/스키마 지시.
        user_prompt: 원작·옵션·아이디어.
        max_tokens: 최대 출력 토큰. 스트리밍이라 타임아웃 걱정 없이 크게 잡는다
            (기본 64000=Sonnet 4.6 상한; adaptive thinking + 전체 창작 JSON이 잘리지 않게).
        output_schema: structured outputs용 JSON 스키마(없으면 일반 텍스트 출력).
    Returns:
        Claude가 출력한 원시 텍스트(JSON 문자열 기대).
    Raises:
        ClaudeError: 키 미설정 또는 호출 실패/타임아웃/빈 응답 시.
    """
    # Sonnet 4.6 권장 adaptive thinking으로 전체 창작 JSON을 생성한다(공용 코어 위임).
    # effort=medium: 기본값 high는 사고/생성이 길어 writing 단계가 수 분~10분+까지 출렁이고,
    # 그 사이 클라이언트가 SSE 연결을 끊어 "연결 실패"가 났다. medium으로 낮춰 지연을 줄인다(decisions 2026-06-26).
    return await _invoke(
        system_prompt,
        user_prompt,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_schema=output_schema,
        effort="medium",
    )


async def generate_json(
    system_prompt: str,
    user_prompt: str,
    output_schema: dict,
    max_tokens: int = 1024,
) -> str:
    """짧은 JSON 응답 생성 기능(단어 풀이 등).  # (Short structured-JSON call)

    단어 풀이(POST /vocab)처럼 한 번에 작은 JSON 하나만 필요한 호출용. 창작과 달리 thinking을
    끄고(탭 한 번당 지연을 줄이려고) 작은 max_tokens로 빠르게 받는다. 출력은 output_schema로
    강제되어 파싱이 안전하다. 키 보호·오류 변환은 generate_creation과 동일 코어를 공유한다.

    Args:
        system_prompt: 역할/규칙/스키마 지시.
        user_prompt: 단어·문맥·난이도 등 입력.
        output_schema: 강제할 JSON 스키마(필수).
        max_tokens: 최대 출력 토큰(기본 1024 — 짧은 풀이에 충분).
    Returns:
        스키마를 따르는 JSON 문자열.
    Raises:
        ClaudeError: 키 미설정 또는 호출 실패/타임아웃/빈 응답 시.
    """
    return await _invoke(
        system_prompt,
        user_prompt,
        max_tokens=max_tokens,
        thinking={"type": "disabled"},  # 짧은 조회 — thinking 비활성으로 지연 최소화
        output_schema=output_schema,
    )


async def generate_text(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
) -> str:
    """짧은 평문 응답 생성 기능(대사 한 줄 고쳐쓰기 등).  # (Short plain-text call)

    대사 고쳐쓰기(POST /rewrite-line)처럼 JSON이 아니라 '평문 한 줄'만 필요한 호출용. JSON
    스키마 강제 없이(generate_json과 달리) thinking을 끄고 작은 max_tokens로 빠르게 받는다.
    모델 문자열은 설정(CLAUDE_MODEL)에서 읽어 /vocab과 동일 모델을 공유한다(비용·일관성).
    키 보호·오류 변환은 generate_creation/generate_json과 동일 코어(_invoke)를 공유한다.
    관련: app/services/rewrite_service.py.

    Args:
        system_prompt: 역할/규칙 지시.
        user_prompt: 입력(고칠 대사·맥락·지시·읽기 수준 등).
        max_tokens: 최대 출력 토큰(기본 1024 — 한 줄 응답에 충분).
    Returns:
        Claude가 출력한 평문 텍스트(앞뒤 공백 제거됨).
    Raises:
        ClaudeError: 키 미설정 또는 호출 실패/타임아웃/빈 응답 시.
    """
    return await _invoke(
        system_prompt,
        user_prompt,
        max_tokens=max_tokens,
        thinking={"type": "disabled"},  # 한 줄 고쳐쓰기 — thinking 비활성으로 지연 최소화
        output_schema=None,  # 평문 출력(스키마 강제 없음)
    )
