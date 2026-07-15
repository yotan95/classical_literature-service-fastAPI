"""출력(창작 결과) 스키마 + 진행 이벤트 + 책 목록/상세 스키마 모듈.  # (Response schema)

창작 결과 JSON, SSE 진행 이벤트, 책 목록/상세를 pydantic으로
'단일 출처'로 정의한다. Enum(Mode/Difficulty)은 request.py 것을 재사용한다.

핵심: SQLite 정규화 제약을 검증할 수 있는 구조.
  - id 중복 금지(characterId/sceneId/lineId)
  - order 누락 금지(scenes/lines의 order는 필수 필드 → 누락 시 pydantic이 자동 거부)
  - speaker 정합성(lines[].speaker는 characters[].characterId에 존재해야 함)
검증 로직은 CreationResult.integrity_errors()에 모아 두고, 생성 시 model_validator가
이를 호출해 위반 시 ValidationError를 낸다. formatter는 model_construct로 검증 없이 만든 뒤
integrity_errors()로 무엇을 고칠지 파악해 보정한다.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.request import Difficulty, Mode  # Enum 단일 출처 재사용


def _find_duplicates(values: list) -> list:
    """리스트에서 중복 값을 찾는 헬퍼 기능.  # (Find duplicate values)

    id/order 중복 검사에 공용으로 쓴다.

    Args:
        values: 검사할 값 리스트.
    Returns:
        2회 이상 등장한 값들의 리스트(중복 제거, 정렬).
    """
    seen: set = set()
    dups: set = set()
    for v in values:
        if v in seen:
            dups.add(v)
        seen.add(v)
    return sorted(dups)


class Character(BaseModel):
    """등장인물(내레이터 포함) 스키마.  # (Character incl. narrator)

    lines[].speaker가 참조하는 대상이며 voiceProfile로 합성 음성을 구분한다(오디오극).
    오디오극에서는 Claude가 인물의 나이/성별/상황을 고려해 voice_map 닫힌 어휘 중 하나를
    지정하고 formatter가 정규화한다. 대화극에서는 None이다.

    Attributes:
        characterId: 인물 식별자(중복 불가, speaker가 참조).
        name: 표시 이름.
        voiceProfile: 음성 프로필(오디오극: voice_map 어휘, 대화극: None).
    """

    characterId: str = Field(min_length=1)
    name: str
    voiceProfile: str | None = None


class Line(BaseModel):
    """대사 1줄 스키마.  # (One dialogue line)

    대사/지문 분리 구조. 오디오극의 재생 위치는 line이 아니라 결과의 audio.timepoints가
    lineId로 가리킨다(단일 MP3 내 startMs). 역할읽기 탭은 speaker로 필터링해 구현한다.

    Attributes:
        lineId: 라인 식별자(전역 중복 불가, audio.timepoints가 참조).
        order: 장면 내 재생/표시 순서(필수).
        speaker: 화자 = characters[].characterId 참조(정합성 필수).
        speakerName: 화자 표시 이름.
        direction: 지문/감정(없으면 None).
        text: 대사 본문.
    """

    lineId: str = Field(min_length=1)
    order: int
    speaker: str = Field(min_length=1)
    speakerName: str
    direction: str | None = None
    text: str


class Scene(BaseModel):
    """장면 스키마.  # (Scene)

    장면 분절 단위. order로 장면 순서를 정한다.

    Attributes:
        sceneId: 장면 식별자(중복 불가).
        order: 장면 순서(필수).
        title: 장면 제목.
        lines: 장면에 속한 대사 목록.
    """

    sceneId: str = Field(min_length=1)
    order: int
    title: str
    lines: list[Line] = Field(default_factory=list)


class Timepoint(BaseModel):
    """오디오극 라인 재생 위치 스키마.  # (Per-line playback offset)

    단일 MP3(audio.audioUrl) 안에서 한 라인이 시작/끝나는 위치(ms). Flutter는 문장을 탭하면
    startMs로 seek 후 재생한다. 순서 = scenes→lines 재생 순서.

    Attributes:
        lineId: 대상 라인 id(Line.lineId 참조).
        startMs: 라인 시작 위치(ms).
        endMs: 라인 끝 위치(ms).
    """

    lineId: str
    startMs: int
    endMs: int


class AudioTrack(BaseModel):
    """오디오극 합성 결과(단일 MP3 + timepoints) 스키마.  # (Merged audio track)

    오디오극(mode=audio)에서만 채워진다. 서버가 라인별로 인물 음성으로 합성한 뒤 하나의 MP3로
    병합하고(timepoints 누적 계산), 그 정적 URL과 라인별 위치를 담는다. 대화극에서는 None.

    Attributes:
        audioUrl: 병합된 단일 MP3의 정적 상대경로(`/audio/...`, 호스트/포트 비하드코딩).
        totalDurationMs: 전체 재생 길이(ms).
        timepoints: 라인별 시작/끝 위치 목록(재생 순서).
    """

    audioUrl: str
    totalDurationMs: int
    timepoints: list[Timepoint] = Field(default_factory=list)


class SourceScene(BaseModel):
    """원작 정보 — 사용한 장면 1건 스키마.  # (Used source scene for the info tab)

    창작에 사용한 '원작 장면'(생성된 장면이 아니라 선택한 원작 장면). scope=scene일 때만 채워진다.

    Attributes:
        sceneId: 원작 장면 id.
        order: 원작 장면 순서(화면의 '장면 N').
        emoji: 장면 이모지.
        title: 원작 장면 제목(예: "형제의 갈림").
        description: 원작 장면 한 줄 설명.
    """

    sceneId: str
    order: int | None = None
    emoji: str | None = None
    title: str
    description: str | None = None


class CreationSource(BaseModel):
    """창작 결과의 '원작 정보' source 블록 스키마.  # (Embedded source info)

    Flutter '원작 정보' 탭(원천 자료 + 사용한 장면/요약 + 출처 문구)을 자급자족으로 렌더링하기 위해
    /create 결과에 함께 담는다(SQLite 저장/오프라인 안전). Claude가 아니라 서버가 채운다.

    Attributes:
        bookId: 원작 식별자.
        title: 원작 제목(원천 자료 카드).
        classification: 분류 칩(전 책 공통, 예: ["고전소설","공공 원전"]).
        coverColor: 표지 폴백 색.
        coverImageUrl: 표지 이미지 상대경로(버전 토큰 포함; 없으면 None → coverColor 폴백).
        provider: 출처 제공기관(예: 한국고전번역원).
        license: 라이선스(예: 공공누리 제1유형).
        attribution: 출처 안내 문구.
        scope: 창작 범위("full"|"scene").
        scenesUsed: 사용한 원작 장면(scope=scene일 때). full이면 빈 목록.
        summary: 전체 줄거리 3문단(scope=full일 때). scene이면 None.
    """

    bookId: str
    title: str
    classification: list[str] = Field(default_factory=list)
    coverColor: str | None = None
    coverImageUrl: str | None = None
    provider: str | None = None
    license: str | None = None
    attribution: str | None = None
    scope: str
    scenesUsed: list[SourceScene] = Field(default_factory=list)
    summary: str | None = None


class VocabEntry(BaseModel):
    """어려운 단어 풀이 1건 스키마.  # (One glossary entry)

    필드명은 Flutter VocabEntry.fromMap({hanja?, meaning, note?})과 정확히 일치시킨다.
    창작 결과의 vocab 맵(단어→풀이)과 POST /vocab 응답이 같은 모델을 공유한다. 국립국어원
    사전(근거) + Claude(문맥·난이도 맞춤 친근체)로 만들며, 한자가 없거나 부가설명이 불필요하면
    hanja/note는 null이다. 관련: app/services/vocab_service.py, service-flutter-app VocabEntry.

    Attributes:
        meaning: 친근체("~이에요/~예요") 뜻풀이. 항상 채운다.
        hanja: 한자어원(없으면 None). 예: "完然".
        note: 보충 설명(현대어 대응·쓰임 등; 없으면 None).
    """

    meaning: str
    hanja: str | None = None
    note: str | None = None


class RewriteLineResponse(BaseModel):
    """대사 한 줄 고쳐쓰기 응답 스키마.  # (POST /rewrite-line response)

    'AI로 바꾸기' 결과. 새로 쓴 대사 '한 줄'만 담는다. 필드명은 Flutter api_client가 읽는
    res.data['text']와 정확히 일치시킨다. Claude가 빈 응답을 주면 라우터가 원문 line을 채운다.
    관련: app/routers/rewrite.py, service-flutter-app api_client.rewriteLine.

    Attributes:
        text: 새로 쓴 대사 한 줄.
    """

    text: str


class CreationResult(BaseModel):
    """창작 결과(공통 베이스) 스키마.  # (Creation result, base)

    대화극/오디오극 공통 구조 + 오디오극 전용 audio 트랙. 생성 시 SQLite
    정규화 제약을 자동 검증한다.

    Attributes:
        creationId: 창작물 고유 id(uuid).
        bookId: 원작 식별자.
        title: 창작 제목.
        mode: 창작 모드.
        difficulty: 난이도.
        tags: 상단 칩 태그 목록.
        creationCoverImageUrl: 창작물 고유 표지 이미지 상대경로. 생성 실패 시 None.
        creationCoverEmoji: 창작물 표지 이미지가 없을 때 쓸 대표 이모티콘.
        intro: '한눈에 보기' 안내문(Claude가 함께 생성).
        characters: 등장인물(내레이터 포함) 목록.
        scenes: 장면 목록(각 장면에 lines 포함).
        audio: (오디오극) 단일 MP3 + timepoints. 대화극에서는 None.
        source: '원작 정보' 탭용 원천 자료 블록(서버가 채움).
        vocab: 어려운 단어 사전(단어→풀이). Claude가 고른 단어를 서버가 사전+Claude로 풀어
            finalize에서 채운다. 결과에 임베드되어 SQLite 저장/오프라인·무지연 탭에 쓰인다.
            (탭한 단어가 여기 없으면 Flutter가 POST /vocab로 즉석 조회 — 폴백.)
    """

    creationId: str = Field(min_length=1)
    bookId: str = Field(min_length=1)
    title: str
    mode: Mode
    difficulty: Difficulty
    tags: list[str] = Field(default_factory=list)
    creationCoverImageUrl: str | None = None
    creationCoverEmoji: str | None = None
    intro: str = ""
    characters: list[Character] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    audio: AudioTrack | None = None
    source: CreationSource | None = None
    vocab: dict[str, VocabEntry] = Field(default_factory=dict)

    def integrity_errors(self) -> list[str]:
        """SQLite 정규화 제약 위반 목록 반환 기능.  # (Relational integrity check)

        id 중복/ order 중복/ speaker 미정의를 모두 모아 사람이 읽을 수 있는 메시지로
        돌려준다. formatter는 model_construct로 만든 객체에 이 메서드를 호출해 보정 근거로 쓴다.

        Returns:
            위반 메시지 리스트(위반이 없으면 빈 리스트).
        """
        errors: list[str] = []

        # 1) characterId 중복 금지
        char_ids = [c.characterId for c in self.characters]
        dup_chars = _find_duplicates(char_ids)
        if dup_chars:
            errors.append(f"characterId 중복: {dup_chars}")
        known_speakers = set(char_ids)  # speaker 정합성 기준 집합

        # 2) sceneId 중복 + scene order 중복 금지(정렬 안정성)
        dup_scene_ids = _find_duplicates([s.sceneId for s in self.scenes])
        if dup_scene_ids:
            errors.append(f"sceneId 중복: {dup_scene_ids}")
        dup_scene_orders = _find_duplicates([s.order for s in self.scenes])
        if dup_scene_orders:
            errors.append(f"scene order 중복: {dup_scene_orders}")

        # 3) lineId 전역 중복 + (장면 내) line order 중복 + speaker 정합성
        all_line_ids: list[str] = []
        for s in self.scenes:
            dup_line_orders = _find_duplicates([ln.order for ln in s.lines])
            if dup_line_orders:
                errors.append(f"scene '{s.sceneId}' 내 line order 중복: {dup_line_orders}")
            for ln in s.lines:
                all_line_ids.append(ln.lineId)
                if ln.speaker not in known_speakers:  # FK 정합성
                    errors.append(
                        f"line '{ln.lineId}'의 speaker '{ln.speaker}'가 characters[]에 없음"
                    )
        dup_line_ids = _find_duplicates(all_line_ids)
        if dup_line_ids:
            errors.append(f"lineId 중복: {dup_line_ids}")

        return errors

    @model_validator(mode="after")
    def _validate_integrity(self) -> "CreationResult":
        """생성 시 제약 자동 검증 기능.  # (Enforce integrity on construction)

        integrity_errors()가 비어 있지 않으면 ValidationError를 발생시켜, 정상 생성된
        CreationResult는 항상 SQLite 정규화 제약을 만족함을 보장한다.

        Returns:
            검증을 통과한 자기 자신.
        Raises:
            ValueError: 제약 위반이 하나라도 있을 때.
        """
        errors = self.integrity_errors()
        if errors:
            raise ValueError("정합성 위반: " + " / ".join(errors))
        return self


# ---------------------------------------------------------------------------
# 진행 이벤트(SSE) 스키마 (결정: SSE 스트리밍)
# ---------------------------------------------------------------------------


class Stage(str, Enum):
    """진행 단계 Enum.  # (Progress stage)

    UI 매핑: analysis=원작 분석, structure=구조화, writing=대사 작성, finalize=마무리,
    tts=음성 합성·병합(오디오극에서만 emit).
    """

    analysis = "analysis"
    structure = "structure"
    writing = "writing"
    finalize = "finalize"
    tts = "tts"


class ProgressStatus(str, Enum):
    """진행 상태 Enum.  # (Progress status)

    running=단계 시작, done=단계 완료.
    """

    running = "running"
    done = "done"


class JobEvent(BaseModel):
    """작업 식별 이벤트 스키마.  # (SSE job-id handshake event)

    POST /create가 백그라운드 작업을 띄운 직후 **가장 먼저 1회** 보내는 이벤트.
    클라이언트는 여기서 받은 jobId로 끊긴 뒤 재연결(GET /create/{jobId}/events)하거나
    상태를 폴링(GET /create/{jobId})한다. 재연결 스트림의 스냅샷 맨 앞에도 동일하게 실린다.
    관련: app/services/job_manager.py.

    Attributes:
        type: 고정값 "job".
        jobId: 작업 식별자(uuid). 재연결/폴링 키.
    """

    type: Literal["job"] = "job"
    jobId: str


class ProgressEvent(BaseModel):
    """진행 이벤트 스키마.  # (SSE progress event)

    각 단계 시작/완료를 화면에 표시하기 위해 전송한다.

    Attributes:
        type: 고정값 "progress".
        stage: 진행 단계.
        status: 진행 상태(running|done).
    """

    type: Literal["progress"] = "progress"
    stage: Stage
    status: ProgressStatus


class ResultEvent(BaseModel):
    """결과 이벤트 스키마.  # (SSE result event)

    모든 단계 완료 후 최종 창작 결과를 전달한다.

    Attributes:
        type: 고정값 "result".
        data: 창작 결과.
    """

    type: Literal["result"] = "result"
    data: CreationResult


class ErrorEvent(BaseModel):
    """오류 이벤트 스키마.  # (SSE error event)

    호출 실패/타임아웃 등을 화면에 전달한다.

    Attributes:
        type: 고정값 "error".
        message: 오류 메시지.
    """

    type: Literal["error"] = "error"
    message: str


class JobStatusResponse(BaseModel):
    """작업 상태 폴링 응답 스키마.  # (GET /create/{jobId} polling response)

    SSE 재연결 대신(또는 함께) 클라이언트가 앱 복귀 시 몇 초 간격으로 조회하는 스냅샷.
    백그라운드 작업의 현재 상태/단계와, 끝났으면 결과(또는 오류 메시지)를 한 번에 돌려준다.
    만료/없는 jobId는 라우터가 404로 처리한다(메모리 저장, TTL 경과 후 정리).
    관련: app/services/job_manager.py.

    Attributes:
        jobId: 작업 식별자(uuid).
        status: 작업 상태(queued|running|done|error).
        stage: status=running일 때 현재 진행 단계(아니면 None).
        result: status=done일 때만 채워지는 창작 결과(아니면 None).
        message: status=error일 때만 채워지는 오류 메시지(아니면 None).
    """

    jobId: str
    status: Literal["queued", "running", "done", "error"]
    stage: str | None = None
    result: CreationResult | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# 책 목록/상세 응답 스키마 (GET /books, GET /books/{bookId})
# ---------------------------------------------------------------------------


class BookSummary(BaseModel):
    """책 목록 항목 스키마.  # (Book list item)

    GET /books가 돌려주는 원작 1건의 요약/메타. 창작하기 '원작 선택' + 내서재 '원작' 목록이
    이 데이터로 렌더링된다(Flutter는 최초 비어 있고 서버에서 받음).

    Attributes:
        bookId: 책 슬러그(폴더명).
        title: 한글 제목.
        emoji: 책 단위 이모지.
        author: 저자.
        era: 시대.
        difficulty: 원작 난이도 라벨(표시용).
        tags: 분류 태그.
        coverColor: 표지 폴백 색상(hex).
        coverImageUrl: 표지 이미지 정적 상대경로(없으면 None → coverColor 폴백).
        shortDescription: 한 줄 설명.
        sceneCount: 장면 수(장면별 선택 가능 수).
    """

    bookId: str
    title: str
    emoji: str | None = None
    author: str | None = None
    era: str | None = None
    difficulty: str | None = None
    tags: list[str] = Field(default_factory=list)
    coverColor: str | None = None
    coverImageUrl: str | None = None
    shortDescription: str | None = None
    sceneCount: int = 0


class BookListResponse(BaseModel):
    """책 목록 응답 스키마.  # (GET /books response)

    사용 가능한 원작 목록을 books 배열로 감싼다.

    Attributes:
        books: 책 요약 목록.
    """

    books: list[BookSummary] = Field(default_factory=list)


class BookOriginal(BaseModel):
    """원문(전문) 읽기 응답 스키마.  # (GET /books/{bookId}/original response)

    원작 전문을 읽기 화면에 그대로 보여주기 위한 데이터(original/<slug>.txt 기반). 순번/공백 줄을
    걸러 문단 배열과 합본 텍스트를 함께 준다. app은 paragraphs로 페이지네이션하거나 text를 통으로
    렌더링한다.

    Attributes:
        bookId: 책 슬러그.
        title: 한글 제목(book.json title 우선).
        paragraphs: 본문 문단 목록(순번/공백 줄 제외).
        text: 문단을 빈 줄로 이은 합본 텍스트.
    """

    bookId: str
    title: str
    paragraphs: list[str] = Field(default_factory=list)
    text: str = ""


class CharacterDetail(BaseModel):
    """책 상세의 등장인물 스키마.  # (Character detail for book detail)

    book.json characters를 소개/참고용으로 추린다(role/description 포함).

    Attributes:
        characterId: 인물 식별자.
        name: 이름.
        role: 역할(주인공/조연/악역 등, 없으면 None).
        description: 인물 설명(없으면 None).
    """

    characterId: str
    name: str
    role: str | None = None
    description: str | None = None


class SceneSummary(BaseModel):
    """장면 요약 스키마.  # (Scene summary for scene-selection UI)

    GET /books/{bookId}가 돌려주는 장면 1건의 선택 UI용 요약(Flutter kScenes 대체).
    segments는 포함하지 않는다(가벼움; 창작 시 서버가 sceneIds로 직접 읽음).

    Attributes:
        sceneId: 장면 식별자(POST /create의 sceneIds로 사용).
        order: 장면 순서.
        emoji: 장면 이모지.
        title: 장면 제목.
        description: 장면 한 줄 설명.
    """

    sceneId: str
    order: int
    emoji: str | None = None
    title: str
    description: str | None = None


class BookDetail(BaseModel):
    """책 상세 응답 스키마.  # (GET /books/{bookId} response)

    특정 원작의 장면/캐릭터 목록(장면 선택 UI용).

    Attributes:
        bookId: 책 슬러그.
        title: 한글 제목.
        emoji: 책 이모지.
        summary: 전체 줄거리 3문단 요약(없으면 None).
        coverColor: 표지 폴백 색상.
        coverImageUrl: 표지 이미지 정적 상대경로(없으면 None).
        characters: 등장인물 상세 목록.
        scenes: 장면 요약 목록(장면별 선택지).
    """

    bookId: str
    title: str
    emoji: str | None = None
    summary: str | None = None
    coverColor: str | None = None
    coverImageUrl: str | None = None
    characters: list[CharacterDetail] = Field(default_factory=list)
    scenes: list[SceneSummary] = Field(default_factory=list)
