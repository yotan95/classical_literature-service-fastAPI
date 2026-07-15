"""결과 정제 모듈.  # (Result formatter)

Claude 원시 응답(텍스트) → 스키마 JSON으로 파싱/정제/검증한다.
SQLite 정규화 제약(id 중복 금지·order 누락/중복 금지·speaker 정합성)을 프로그램적으로
보정한 뒤, response.CreationResult로 구성한다(구성 시 model_validator가 다시 검증).
오디오극이면 characters[].voiceProfile을 voice_map 닫힌 어휘로 정규화한다(라인엔 음성 필드 없음).
파싱/검증 실패 시 명확한 FormatterError를 던진다.
"""

import json
import re
import uuid

from app.models.request import CreateRequest, Mode
from app.models.response import CreationResult
from app.services.voice_map import resolve_profile


class FormatterError(Exception):
    """결과 정제/검증 실패 예외.  # (Formatting failure)

    JSON 파싱 실패 또는 보정 후에도 스키마를 만족하지 못할 때 사용한다.
    """


def _extract_json(text: str) -> dict:
    """원시 텍스트에서 JSON 객체 추출 기능.  # (Extract JSON object)

    Claude가 실수로 코드펜스/머리말을 붙여도 첫 '{'~마지막 '}' 구간을 파싱해 복구한다.

    Args:
        text: Claude 원시 응답.
    Returns:
        파싱된 dict.
    Raises:
        json.JSONDecodeError: 어떤 방법으로도 JSON을 못 찾을 때.
    """
    text = text.strip()
    # ```json ... ``` 코드펜스 제거
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 본문에 섞인 경우: 첫 '{' ~ 마지막 '}' 만 떼어 재시도
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _repair(data: dict, request: CreateRequest) -> dict:
    """제약을 만족하도록 보정 기능.  # (Repair to satisfy constraints)

    상위 메타 보충(creationId/bookId/mode/difficulty 등) + id 중복 제거 + order 순차 재부여 +
    speaker 정합성(누락 화자는 characters[]에 자동 등록) + 오디오극 voiceProfile/tts 채움.

    Args:
        data: Claude가 준 원시 dict.
        request: 창작 요청(mode/difficulty/bookId 기준값).
    Returns:
        보정된 dict.
    """
    # 1) 상위 메타 — 누락/불일치 보충. mode/difficulty/bookId는 요청을 신뢰원으로 강제.
    if not data.get("creationId"):
        data["creationId"] = str(uuid.uuid4())
    data["bookId"] = data.get("bookId") or request.bookId
    data["mode"] = request.mode.value
    data["difficulty"] = request.difficulty.value
    data["title"] = data.get("title") or request.bookId
    data.setdefault("tags", [])
    data["intro"] = data.get("intro") or ""

    is_audio = request.mode is Mode.audio

    # 2) characters — id 중복 제거 / 빈 id 보정. 오디오극이면 voiceProfile을 닫힌 어휘로 정규화.
    raw_chars = data.get("characters") or []
    fixed_chars: list[dict] = []
    char_ids: set[str] = set()
    for i, c in enumerate(raw_chars):
        if not isinstance(c, dict):
            continue
        cid = (c.get("characterId") or "").strip() or f"char-{i + 1}"
        while cid in char_ids:  # 중복이면 접미사로 유일화
            cid = f"{cid}-{i + 1}"
        char_ids.add(cid)
        # 오디오극: VOICE_MAP에 없거나 빈 값이면 기본 프로필로 폴백. 대화극: 원값 유지(미사용).
        vp = resolve_profile(c.get("voiceProfile")) if is_audio else c.get("voiceProfile")
        fixed_chars.append(
            {
                "characterId": cid,
                "name": c.get("name") or cid,
                "voiceProfile": vp,
            }
        )

    # 3) scenes/lines — id 중복 제거, order 순차 재부여, speaker 정합성, 오디오 필드 채움
    fixed_scenes: list[dict] = []
    seen_scene: set[str] = set()
    seen_line: set[str] = set()
    for si, s in enumerate(data.get("scenes") or []):
        if not isinstance(s, dict):
            continue
        sid = (s.get("sceneId") or "").strip() or f"scene-{si + 1}"
        while sid in seen_scene:
            sid = f"{sid}-{si + 1}"
        seen_scene.add(sid)

        fixed_lines: list[dict] = []
        for li, ln in enumerate(s.get("lines") or []):
            if not isinstance(ln, dict):
                continue
            lid = (ln.get("lineId") or "").strip() or f"{sid}-l{li + 1}"
            while lid in seen_line:
                lid = f"{lid}-{li + 1}"
            seen_line.add(lid)

            # speaker 정합성: characters[]에 없으면 자동 등록(내레이터 포함)
            speaker = (ln.get("speaker") or "").strip() or "narrator"
            speaker_name = ln.get("speakerName") or speaker
            if speaker not in char_ids:
                # 오디오극이면 자동 등록 화자도 기본 프로필을 받아 합성 가능하게 한다.
                auto_vp = resolve_profile(None) if is_audio else None
                fixed_chars.append(
                    {"characterId": speaker, "name": speaker_name, "voiceProfile": auto_vp}
                )
                char_ids.add(speaker)

            # line에는 음성 필드가 없다(오디오 합성 음성은 화자 character.voiceProfile로 결정,
            # 재생 위치는 결과 audio.timepoints가 lineId로 가리킨다).
            fixed_lines.append(
                {
                    "lineId": lid,
                    "order": li + 1,  # 순차 재부여 → 누락/중복 방지
                    "speaker": speaker,
                    "speakerName": speaker_name,
                    "direction": ln.get("direction"),
                    "text": ln.get("text") or "",
                }
            )

        fixed_scenes.append(
            {
                "sceneId": sid,
                "order": si + 1,  # 장면 order 순차 재부여
                "title": s.get("title") or sid,
                "lines": fixed_lines,
            }
        )

    data["characters"] = fixed_chars
    data["scenes"] = fixed_scenes
    return data


def extract_difficult_words(raw_text: str) -> list[str]:
    """Claude 응답에서 어려운 단어 목록을 추출하는 기능.  # (Extract difficultWords)

    Claude가 고른 difficultWords(본문 표기 단어 배열)를 떼어 돌려준다. 이 목록은 finalize에서
    vocab_service.build_glossary로 풀어 result.vocab에 임베드된다. 파싱 실패/필드 없음/형식 이상은
    빈 목록으로 처리한다(어휘 사전은 보조 데이터 — 창작 실패와 분리). 관련: app/routers/creation.py.

    Args:
        raw_text: Claude 원시 응답(JSON 문자열).
    Returns:
        문자열 단어 목록(없거나 파싱 실패면 빈 목록).
    """
    try:
        data = _extract_json(raw_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    words = data.get("difficultWords")
    if not isinstance(words, list):
        return []
    return [w for w in words if isinstance(w, str) and w.strip()]


def format_creation(raw_text: str, request: CreateRequest) -> CreationResult:
    """창작 결과 정제 기능.  # (Format creation result)

    Claude 원시 텍스트를 스키마 CreationResult로 만든다. 파싱 → 보정 → 구성(검증) 순서.
    구성 시 CreationResult의 model_validator가 제약을 재검증하므로, 반환된 결과는 항상
    유효하다. 어느 단계든 실패하면 FormatterError로 명확히 알린다.

    Args:
        raw_text: Claude 원시 응답.
        request: 창작 요청(mode/difficulty/bookId 기준).
    Returns:
        검증을 통과한 CreationResult.
    Raises:
        FormatterError: JSON 파싱 실패 또는 보정 후에도 검증 실패 시.
    """
    try:
        data = _extract_json(raw_text)
    except json.JSONDecodeError as e:
        raise FormatterError(f"Claude 응답을 JSON으로 파싱하지 못했습니다: {e}") from e
    if not isinstance(data, dict):
        raise FormatterError("Claude 응답 최상위가 JSON 객체가 아닙니다.")

    data = _repair(data, request)
    try:
        return CreationResult(**data)
    except Exception as e:  # pydantic ValidationError 등 — 보정으로도 못 고친 경우
        raise FormatterError(f"정제 후에도 스키마 검증에 실패했습니다: {e}") from e
