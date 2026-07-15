"""프롬프트 빌더 모듈.  # (Prompt builder)

난이도 스타일 규칙 + 창작 소스(scope별) + 사용자 ideaText(프롬프트)를 합쳐 Claude
프롬프트(system/user)를 만든다. Claude가 스키마 JSON만 출력하도록
structured outputs(OUTPUT_JSON_SCHEMA)로 강제한다.

소스:
- full 스코프: book.json summary(전체 줄거리 3문단)를 확장 창작.
- scene 스코프: 선택한 장면들의 segments를 난이도로 다시 쓰고 모드 형식으로 재구성.
"""

import json

from app.models.request import CreateRequest, Mode
from app.services.voice_map import PROFILE_DESCRIPTIONS

# 난이도별 문체 규칙 — 테스트하며 조정 가능.
_DIFFICULTY_RULES: dict[str, str] = {
    "children": (
        "그림책 톤. 유아~초등 저학년 기초어휘와 의성어/의태어를 적극 쓴다. "
        "문장은 아주 짧게(8~12자) 한 문장에 한 정보만. 한자어는 거의 쓰지 않고 고유어 우선. "
        "다정하고 밝게, 반복과 리듬감을 살린다."
    ),
    "korean_learner": (
        "한국어 학습자용. 기초~중급 표준어휘를 쓰고 관용구는 최소화한다. "
        "문장은 짧고 명확하게(15~25자) 단문 위주. 쉬운 한자어만 쓰고 어려운 건 풀어서 설명한다. "
        "표준적이고 설명적으로, 모호한 표현을 피한다."
    ),
    "youth": (
        "청소년 대상. 청소년 일상어와 현대적 표현을 쓴다. "
        "문장은 보통 길이(25~40자)로 리듬 있게 섞는다. 한자어는 맥락상 자연스러우면 허용. "
        "생동감 있고 공감 가게, 가벼운 유머를 허용한다."
    ),
    "original": (
        "원작 보존. 고전 어휘와 한자어를 풍부하게, 예스러운 표현을 살린다. "
        "문장은 다소 길고 만연체를 허용한다(40자+). 한자어 비율을 높여 원문 느낌을 보존한다. "
        "격식 있는 문어체와 풍자 어조를 유지한다."
    ),
}

# 모드별 출력 요구.
_MODE_RULES: dict[str, str] = {
    "dialogue": (
        "대화극(dialogue): 기본 구조를 사용한다. 각 line에 speaker/speakerName/direction/text를 채운다. "
        "characters[].voiceProfile은 null로 둔다(음성 미사용)."
    ),
    "audio": (
        "오디오극(audio): 기본 구조를 사용한다. 서버가 각 대사를 Google Cloud TTS로 음성 합성해 "
        "하나의 MP3로 병합하므로(audioUrl/timepoints는 서버가 채움), 너는 characters[]의 모든 인물(내레이터 포함)에 "
        "voiceProfile을 아래 [음성 프로필 어휘]에서 정확히 하나 골라 지정한다. 인물의 나이/성별/상황을 고려해 고른다."
    ),
}

# 출력 스키마 사양(모델에게 그대로 지킬 형태를 제시).
_SCHEMA_SPEC = """\
다음 JSON 스키마를 '정확히' 따라 출력한다(키 이름·구조 동일, 추가 설명/마크다운 금지):
{
  "creationId": "<uuid 문자열>",
  "bookId": "<원작 슬러그>",
  "title": "<창작 제목 — 작품 전체를 대표/요약하는 새 제목. 12자 이내, 원작(책) 제목 포함 금지>",
  "mode": "dialogue | audio",
  "difficulty": "children | korean_learner | youth | original",
  "tags": ["<상단 칩 태그>", ...],
  "intro": "<'한눈에 보기' 안내문 — 네가 함께 작성>",
  "characters": [
    { "characterId": "<고유 id>", "name": "<이름>", "voiceProfile": "<음성 프로필 또는 null>" }
  ],
  "scenes": [
    {
      "sceneId": "<고유 id>",
      "order": 1,
      "title": "<장면 제목>",
      "lines": [
        {
          "lineId": "<고유 id>",
          "order": 1,
          "speaker": "<characters[].characterId 중 하나>",
          "speakerName": "<화자 표시 이름>",
          "direction": "<지문/감정 또는 null>",
          "text": "<대사 본문>"
        }
      ]
    }
  ],
  "difficultWords": ["<본문(대사 text)에 실제로 등장한 어려운 단어 표기 그대로>", ...]
}"""

# 제약(정합성) — 모델이 미리 지키도록 명시.
_CONSTRAINTS = """\
반드시 지킬 제약:
- title(창작 제목): 선택한 장면이나 일부 사건이 아니라 '작품 전체'를 대표/요약하는 새 제목을 짓는다.
  · 길이는 공백 포함 12자 이내로 짧고 간결하게 짓는다.
  · 원작(책) 제목을 그대로 넣거나 'OOO전 - 부제' 형태로 덧붙이지 않는다(예: "허생전 - 가난한 선비" 금지).
  · 특정 장면 제목이나 사용자 아이디어 문구를 그대로 복사하지 않는다(scene이 하나뿐이어도 전체를 아우르는 제목으로 짓는다).
  · 아이디어(ideaText)가 있으면 그 방향성을 녹여내되, 작품 전체를 관통하는 짧고 매력적인 제목으로 만든다.
- 모든 id는 고유해야 한다: characterId, sceneId, lineId 중복 금지.
- scenes[].order, lines[].order는 1부터 빠짐없이 순차로 매긴다.
- 모든 line의 speaker는 characters[]의 characterId에 반드시 존재해야 한다(내레이터도 characters[]에 포함).
- difficultWords: 위 난이도 독자가 어려워할 만한 단어를 5~12개 고른다. 각 항목은 lines[].text에
  '실제로 등장한 표기 그대로'여야 한다(앱이 본문에서 그 단어를 찾아 밑줄 친다 — 기본형/원형으로 바꾸지 말 것).
  난이도가 쉬울수록(children) 더 적극적으로, 어려운 한자어/고어/관용어 위주로 고른다. 없으면 빈 배열.
- 오직 JSON 객체 하나만 출력한다. 설명, 코드펜스, 머리말/꼬리말 금지."""


# 출력을 '문법적으로 항상 유효한 JSON'으로 강제하기 위한 structured outputs 스키마.
# Claude API가 이 스키마에 맞춰 출력을 제약하므로 긴 응답에서 JSON 파싱이 깨지는 오류 클래스가 사라진다.
# 단, id 중복/order/speaker 같은 '관계 제약'은 스키마로 표현할 수 없어 formatter._repair가 책임진다.
# 오디오 트랙(audio/audioUrl/timepoints)은 Claude가 만들지 않으므로 스키마에 없다(서버가 채움).
# structured outputs 제약: 모든 object에 additionalProperties:false + 모든 키를 required로(닫힌 형태).
def _nullable_str() -> dict:
    """null 허용 문자열 스키마 조각.  # (Nullable string schema)"""
    return {"anyOf": [{"type": "string"}, {"type": "null"}]}


OUTPUT_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "creationId", "bookId", "title", "mode", "difficulty",
        "tags", "intro", "characters", "scenes", "difficultWords",
    ],
    "properties": {
        "creationId": {"type": "string"},
        "bookId": {"type": "string"},
        "title": {"type": "string"},
        "mode": {"type": "string", "enum": ["dialogue", "audio"]},
        "difficulty": {
            "type": "string",
            "enum": ["children", "korean_learner", "youth", "original"],
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "intro": {"type": "string"},
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["characterId", "name", "voiceProfile"],
                "properties": {
                    "characterId": {"type": "string"},
                    "name": {"type": "string"},
                    "voiceProfile": _nullable_str(),  # 대화극 null, 오디오극 VOICE_MAP 어휘
                },
            },
        },
        # Claude가 고른 어려운 단어(본문 표기 그대로). 서버가 finalize에서 사전+Claude로 풀어
        # result.vocab에 임베드한다(스키마엔 단어만, 풀이는 서버 책임). → app/services/vocab_service.py
        "difficultWords": {"type": "array", "items": {"type": "string"}},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sceneId", "order", "title", "lines"],
                "properties": {
                    "sceneId": {"type": "string"},
                    "order": {"type": "integer"},
                    "title": {"type": "string"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "lineId", "order", "speaker", "speakerName",
                                "direction", "text",
                            ],
                            "properties": {
                                "lineId": {"type": "string"},
                                "order": {"type": "integer"},
                                "speaker": {"type": "string"},
                                "speakerName": {"type": "string"},
                                "direction": _nullable_str(),
                                "text": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


def _build_voice_vocab_section() -> str:
    """오디오극 음성 프로필 어휘 섹션 생성 기능.  # (Voice profile vocabulary)

    Claude가 characters[].voiceProfile에 쓸 '닫힌 어휘'와 설명을 제시한다. 목록 밖 값을 만들지
    않게 해 서버의 VOICE_MAP 매핑이 빗나가지 않도록 한다(formatter도 어휘 밖 값은 폴백 정규화).

    Returns:
        음성 프로필 어휘 안내 문자열.
    """
    items = "\n".join(f'- "{slug}": {desc}' for slug, desc in PROFILE_DESCRIPTIONS.items())
    return (
        "[음성 프로필 어휘] (audio 모드 전용 — characters[].voiceProfile에 아래 값만 사용)\n"
        + items
        + "\n각 인물에 가장 잘 맞는 값 하나를 고른다(내레이터=narrator_calm, 특정하기 어려운 동물/사물=creature). "
        "목록에 없는 새 값은 만들지 않는다."
    )


def _build_system_prompt(request: CreateRequest) -> str:
    """시스템 프롬프트 생성 기능.  # (Build system prompt)

    역할 지시 + 난이도/모드 규칙 + (오디오극이면)음성 어휘 + 스키마 + 제약을 합친다.

    Args:
        request: 창작 요청.
    Returns:
        시스템 프롬프트 문자열.
    """
    difficulty_rule = _DIFFICULTY_RULES.get(request.difficulty.value, "")
    mode_rule = _MODE_RULES.get(request.mode.value, "")
    voice_vocab = ("\n\n" + _build_voice_vocab_section()) if request.mode is Mode.audio else ""
    return (
        "너는 고전문학 원작을 입력받아, 사용자가 고른 옵션에 맞춰 새로운 창작물(대화극/오디오극)을 "
        "만드는 한국어 창작 작가다. 결과는 Flutter 앱이 화면 렌더링과 음성 재생, SQLite 저장에 바로 쓴다.\n\n"
        f"[난이도 문체 규칙]\n{difficulty_rule}\n\n"
        f"[모드 규칙]\n{mode_rule}{voice_vocab}\n\n"
        f"[출력 스키마]\n{_SCHEMA_SPEC}\n\n"
        f"{_CONSTRAINTS}"
    )


def _build_full_source_section(source: dict) -> str:
    """전체 줄거리 소스 섹션 생성 기능(summary 기반).  # (Full-plot source)

    book.json의 summary(3문단 흐름+교훈)와 인물 맥락을 넣고, 이를 바탕으로 풍부한 창작물로
    확장하라고 지시한다(원작 txt 전체 미사용).

    Args:
        source: data_loader.get_full_source(...) 결과.
    Returns:
        프롬프트에 넣을 전체 줄거리 소스 문자열.
    """
    payload = {
        "title": source.get("title"),
        "summary": source.get("summary"),
        "characters": source.get("characters", []),
    }
    return (
        "원작(전체 줄거리 요약):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n[전체 줄거리 처리 지침]\n"
        "- 위 summary의 전체 흐름과 교훈을 유지하면서, 장면을 나누고 대사를 살려 풍부한 창작물로 확장한다.\n"
        "- 원작의 핵심 인물과 사건을 보존하되, 난이도 규칙에 맞는 문체로 새로 쓴다."
    )


def _build_scene_source_section(request: CreateRequest, source: dict) -> str:
    """장면별 선택 소스 섹션 생성 기능(선택 장면 segments 기반).  # (Scene-selection source)

    선택한 장면들의 segments를 Claude 소스로 만든다. segment 텍스트는 난이도로 다시 쓰고
    모드 형식으로 재구성하라는 지침을 함께 넣는다. 대화극은 분량을 적극 확장(8~15줄)하고,
    오디오극은 TTS 비용/재생시간을 고려해 중간 분량(6~10줄)으로 둔다.

    Args:
        request: 창작 요청(모드 분기에 사용).
        source: data_loader.get_scene_source(...) 결과.
    Returns:
        프롬프트에 넣을 장면별 선택 소스 문자열.
    """
    payload = {
        "title": source.get("title"),
        "characters": source.get("characters", []),
        "scenes": source.get("scenes", []),
    }
    # 모드별 분량 지침: 대화극은 적극 확장(8~15줄), 오디오극은 TTS 비용/재생시간 고려해 중간(6~10줄).
    if request.mode is Mode.dialogue:
        length_rule = (
            "- 분량을 충분히 확보한다: 하나의 segment를 한 줄로 축약하지 말고, 그 장면의 상황·감정·인물 관계를\n"
            "  여러 대사와 지문으로 풍부하게 전개한다(인물 간 주고받는 대화, 내레이션 묘사를 더해 살을 붙인다).\n"
            "  각 scene은 최소 8~15줄 이상으로 충실하게 구성해 작품이 빈약하게 느껴지지 않게 한다.\n"
            "- 선택된 장면의 '범위(사건·등장인물)'는 벗어나지 않는다. 새 사건이나 다른 장면 내용을 끌어오지는\n"
            "  말되, 그 범위 안에서의 대사·지문 확장은 적극적으로 한다(ideaText 요청도 반영)."
        )
    else:
        length_rule = (
            "- 분량은 중간 정도로 둔다: segment를 한 줄로 축약하지 말고 상황·감정을 살려 적당히 전개하되,\n"
            "  각 scene은 6~10줄 안팎으로 구성한다(오디오 재생시간을 고려해 과하게 늘리지 않는다).\n"
            "- 선택된 장면의 '범위(사건·등장인물)'는 벗어나지 않는다(새 사건/다른 장면 추가 금지, ideaText 요청은 반영)."
        )
    return (
        "원작(선택한 장면들 — 서버가 book.json에서 읽은 사전 분절 segments):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n[장면별 선택 처리 지침]\n"
        "- 각 scene의 title을 장면 제목으로 쓰고, scene 순서대로 구성한다.\n"
        "- segment.type=narration은 내레이터(narrator)로, dialogue는 segment.speaker를 화자로 쓴다.\n"
        "- 각 segment의 text를 난이도 규칙에 맞게 자연스럽게 다시 쓰고, 모드 형식의 JSON으로 재구성한다.\n"
        + length_rule
    )


def _build_source_section(request: CreateRequest, source: dict) -> str:
    """창작 소스 섹션 디스패처.  # (Source section dispatcher)

    소스 dict의 scope("full"|"scene")에 따라 분기한다.

    Args:
        request: 창작 요청.
        source: data_loader가 만든 소스 dict.
    Returns:
        소스 섹션 문자열.
    """
    if source.get("scope") == "scene":
        return _build_scene_source_section(request, source)
    return _build_full_source_section(source)


def build_prompt(request: CreateRequest, source: dict) -> tuple[str, str]:
    """프롬프트(system, user) 생성 기능.  # (Build full prompt)

    난이도/모드 규칙·스키마·제약(system)과, 요청 파라미터·소스·아이디어(user)를 만든다.
    소스는 data_loader가 scope에 맞게 만들어 넘긴 dict를 사용한다.

    Args:
        request: 창작 요청.
        source: data_loader.get_full_source / get_scene_source 결과.
    Returns:
        (system_prompt, user_prompt) 튜플.
    """
    system_prompt = _build_system_prompt(request)

    idea_block = f"- 추가 아이디어/프롬프트: {request.ideaText}" if request.ideaText else "- (없음)"

    if request.scope.value == "full":
        scope_desc = "전체 줄거리"
    else:
        scope_desc = f"장면별 선택({len(request.sceneIds)}개: {', '.join(request.sceneIds)})"

    user_prompt = (
        "[요청 파라미터]\n"
        f"- bookId: {request.bookId}\n"
        f"- mode: {request.mode.value}\n"
        f"- difficulty: {request.difficulty.value}\n"
        f"- scope: {request.scope.value} ({scope_desc})\n\n"
        f"[사용자 아이디어]\n{idea_block}\n\n"
        f"{_build_source_section(request, source)}\n\n"
        "위 소스와 옵션을 반영해, 시스템 지시의 스키마를 정확히 따르는 JSON 하나만 출력하라."
    )
    return system_prompt, user_prompt
