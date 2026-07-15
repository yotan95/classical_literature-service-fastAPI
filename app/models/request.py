"""요청 파라미터 스키마 + Enum 모듈.  # (Request schema + Enums)

POST /create 요청 바디(bookId/mode/difficulty/scope/sceneIds/ideaText)와
관련 Enum(Mode/Difficulty/Scope)을 pydantic으로 '단일 출처'로 정의한다.
잘못된 Enum 값은 pydantic이 422로 거부한다. 필드명은 Flutter가 보내는
JSON과 정확히 일치시키기 위해 camelCase를 그대로 사용한다.

창작 범위(scope): full=전체 줄거리(서버가 book.json의 summary 사용),
scene=장면별 선택(앱이 sceneIds만 보내고 서버가 book.json에서 해당 장면의 segments를 읽음).
"""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class Mode(str, Enum):
    """창작 모드 Enum.  # (Creation mode)

    dialogue=대화극, audio=오디오극(서버가 단일 MP3 + timepoints 생성).
    """

    dialogue = "dialogue"
    audio = "audio"


class Difficulty(str, Enum):
    """난이도 Enum.  # (Difficulty)

    LLM 생성 문체/어휘 수준을 결정한다. 구체 규칙은 prompt_builder가 보유.
    children=그림책, korean_learner=한국어 학습, youth=청소년, original=원작 보존.
    """

    children = "children"
    korean_learner = "korean_learner"
    youth = "youth"
    original = "original"


class Scope(str, Enum):
    """창작 범위 Enum.  # (Scope)

    full=전체 줄거리(book.json summary 사용), scene=장면 선택(sceneIds로 대상 장면 지정).
    """

    full = "full"
    scene = "scene"


class CreateRequest(BaseModel):
    """창작 요청 바디 스키마.  # (POST /create request body)

    Flutter가 보내는 1 모드 + 1 난이도 + 1 책 + 줄거리 범위 + 아이디어(프롬프트)를 담는다.
    장면별 선택(scope=scene)이면 선택한 sceneIds만 보내고 서버가 book.json에서 해당 장면의
    segments를 읽는다(서버=데이터 단일 출처).
    잘못된 Enum 값은 pydantic이 422로 거부한다.

    Attributes:
        bookId: 원작 식별자(book.json 폴더 슬러그). 빈 값 불가.
        mode: 창작 모드(dialogue|audio).
        difficulty: 난이도(children|korean_learner|youth|original).
        scope: 줄거리 범위(full|scene). 기본 full.
        sceneIds: scope=scene일 때 선택한 장면 id 목록(예: ["scene-1","scene-3"]).
        ideaText: 추가 아이디어/프롬프트 자유 텍스트(비면 모드/난이도/책만으로 생성).
    """

    bookId: str = Field(min_length=1)
    mode: Mode
    difficulty: Difficulty
    scope: Scope = Scope.full
    sceneIds: list[str] = Field(default_factory=list)
    ideaText: str = ""

    @model_validator(mode="after")
    def _check_scope_consistency(self) -> "CreateRequest":
        """scope ↔ sceneIds 정합성 검증 기능.  # (scope/sceneIds consistency)

        scope=scene이면 처리할 대상이 반드시 있어야 하므로 sceneIds가 비면 거부한다(→422).

        Returns:
            검증을 통과한 자기 자신.
        Raises:
            ValueError: scope=scene인데 sceneIds가 비어 있을 때.
        """
        if self.scope is Scope.scene and not self.sceneIds:
            raise ValueError("scope=scene 인 경우 sceneIds는 최소 1개 이상이어야 합니다.")
        return self


class VocabRequest(BaseModel):
    """단어 풀이 요청 바디 스키마.  # (POST /vocab request body)

    대사극/오디오극에서 사용자가 어려운 단어를 탭하면 Flutter가 보내는 요청. 필드명은 Flutter
    api_client(POST /vocab {word, context, level})와 정확히 일치시킨다. 서버는 국립국어원
    사전(근거) + Claude(문맥·난이도 맞춤 친근체 풀이)로 응답한다(decisions: 2026-06-24).
    관련: app/services/vocab_service.py.

    Attributes:
        word: 사용자가 탭한 단어(활용형일 수 있음, 예: "완연한"). 빈 값 불가.
        context: 그 단어가 등장한 문장(동형이의어 구분·문맥 맞춤 풀이용). 비어도 됨.
        level: 난이도 라벨(children|korean_learner|youth|original 등). 풀이 눈높이 조절용. 비어도 됨.
    """

    word: str = Field(min_length=1)
    context: str = ""
    level: str = ""


class RewriteLineRequest(BaseModel):
    """대사 한 줄 고쳐쓰기 요청 바디 스키마.  # (POST /rewrite-line request body)

    대사극 결과 화면에서 사용자가 한 줄을 골라 'AI로 바꾸기'를 누르면 Flutter가 보내는 요청.
    필드명은 Flutter api_client(POST /rewrite-line {line, context, instruction, level})와
    정확히 일치시킨다. 서버는 고른 '한 줄만' 지시·읽기 수준에 맞게 새로 써서 평문으로 돌려준다
    (앞뒤 대사는 바꾸지 않음). 빈 line은 pydantic이 422로 거부한다.
    관련: app/services/rewrite_service.py.

    Attributes:
        line: 바꿀 대사 한 줄. 빈 값 불가.
        context: 앞뒤 맥락(전체 대본 줄을 \n으로 이어 보냄, 톤·흐름 유지용). 비어도 됨.
        instruction: 고쳐쓰기 지시(예: "더 쉽게", "더 짧게", "감정을 더 분명하게"). 비어도 됨.
        level: 읽기 수준 라벨(앱이 보내는 한글/Enum 라벨). 눈높이 조절용. 비어도 됨.
    """

    line: str = Field(min_length=1)
    context: str = ""
    instruction: str = ""
    level: str = ""
