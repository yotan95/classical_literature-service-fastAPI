"""창작 라우터 모듈.  # (Creation router)

엔드포인트:
- GET /books — 책 목록/메타(+coverImageUrl).
- GET /books/{bookId} — 장면/캐릭터 상세(장면 선택 UI용).
- POST /create — SSE 단계별 진행 후 결과 전달.

POST /create 파이프라인: data_loader(소스) → prompt_builder → claude_client → formatter
→ 창작물 표지 생성 → (오디오극) tts_client(단일 MP3 병합 + timepoints).
단계 매핑: analysis=소스 로드, structure=프롬프트 빌드, writing=Claude 호출,
finalize=정제/검증, tts=음성 합성·병합.
원작 표지는 /images, 창작물 고유 표지는 /creation-covers 정적 파일로 제공한다.
/health는 app/main.py에 있다.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.models.request import CreateRequest, Mode, Scope
from app.models.response import (
    BookDetail,
    BookListResponse,
    BookOriginal,
    BookSummary,
    CharacterDetail,
    CreationSource,
    ErrorEvent,
    JobStatusResponse,
    ProgressEvent,
    ProgressStatus,
    ResultEvent,
    SceneSummary,
    Stage,
)
from app.services import (
    claude_client,
    creation_cover_service,
    data_loader,
    formatter,
    prompt_builder,
    tts_client,
    vocab_service,
)
from app.services.job_manager import Job, get_job_manager

router = APIRouter()


def _cover_url(book_id: str) -> str | None:
    """표지 정적 경로 생성 기능.  # (Build cover static path with version)

    표지 파일(/images/<slug>.webp 등)이 있으면 **상대경로 + 버전 토큰(?v=수정시각)**을
    돌려준다(파일이 바뀌면 URL이 바뀌어 디바이스 캐시가 자동 무효화). 없으면 None(→ coverColor 폴백).
    호스트/포트를 응답에 박지 않아 Android 에뮬레이터/실기기/운영 도메인에서 모두 재사용된다.

    Args:
        book_id: 책 슬러그.
    Returns:
        표지 상대경로(버전 포함) 또는 None.
    """
    f = data_loader.cover_file(book_id)
    if f is None:
        return None
    version = int(f.stat().st_mtime)  # 파일 수정시각 → 캐시 버스팅 토큰
    return f"/images/{f.name}?v={version}"


@router.get("/books", response_model=BookListResponse)
def list_books() -> BookListResponse:
    """책 목록 엔드포인트.  # (GET /books)

    - 요청: 파라미터 없음.
    - 응답: 사용 가능한 원작 목록/메타(+coverImageUrl). 창작하기 '원작 선택'과 내서재 '원작'이
      이 데이터로 렌더링된다(Flutter는 최초 비어 있고 서버에서 받음).

    Returns:
        BookListResponse(books=[BookSummary, ...]).
    """
    books = [
        BookSummary(**m, coverImageUrl=_cover_url(m["bookId"]))
        for m in data_loader.list_books_meta()
    ]
    return BookListResponse(books=books)


@router.get("/books/{book_id}", response_model=BookDetail)
def get_book(book_id: str) -> BookDetail:
    """책 상세 엔드포인트.  # (GET /books/{bookId})

    - 요청: 경로 파라미터 book_id(책 슬러그).
    - 응답: 장면/캐릭터 목록(장면 선택 UI용). 없으면 404.

    Args:
        book_id: 책 슬러그.
    Returns:
        BookDetail.
    Raises:
        HTTPException: 해당 원작이 없을 때 404.
    """
    try:
        detail = data_loader.load_book_detail(book_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"원작을 찾을 수 없습니다: {book_id}") from e
    return BookDetail(
        bookId=detail["bookId"],
        title=detail["title"],
        emoji=detail["emoji"],
        summary=detail["summary"],
        coverColor=detail["coverColor"],
        coverImageUrl=_cover_url(book_id),
        characters=[CharacterDetail(**c) for c in detail["characters"]],
        scenes=[SceneSummary(**s) for s in detail["scenes"]],
    )


@router.get("/books/{book_id}/original")
def get_book_original(book_id: str) -> dict:
    """원작 원문 엔드포인트.  # (GET /books/{bookId}/original)

    원작 보기용 원문 전체 텍스트를 돌려준다(data/original/<slug>.txt). 없으면 404.

    Args:
        book_id: 책 슬러그(=bookId).
    Returns:
        {bookId, title, text}.
    Raises:
        HTTPException: 원문이 없을 때 404.
    """
    try:
        text = data_loader.load_original_text(book_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"원문을 찾을 수 없습니다: {book_id}") from e
    try:
        title = data_loader.load_book(book_id).get("title") or book_id
    except FileNotFoundError:
        title = book_id
    return {"bookId": book_id, "title": title, "text": text}


def _sse(event: BaseModel) -> str:
    """이벤트를 SSE 'data:' 프레임으로 직렬화 기능.  # (Serialize one SSE frame)

    pydantic 이벤트 모델을 JSON으로 직렬화해 SSE 한 줄(`data: ...\\n\\n`)로 만든다.

    Args:
        event: ProgressEvent | ResultEvent | ErrorEvent.
    Returns:
        SSE 프레임 문자열.
    """
    return f"data: {event.model_dump_json()}\n\n"


# 오래 걸리는 단계(Claude 생성·TTS 합성) 동안 SSE에 흘려보낼 keep-alive 간격(초)과 프레임.
# 이 시간보다 길게 아무 바이트도 안 흐르면 클라이언트/프록시 idle 타임아웃에 연결이 끊길 수 있다.
_HEARTBEAT_INTERVAL_SECONDS = 15
# SSE 주석 라인(콜론 시작)은 표준상 클라이언트가 무시한다 → 이벤트 어휘를 안 늘리고 연결만 유지.
_HEARTBEAT_FRAME = ": keep-alive\n\n"


# SSE 응답 공통 헤더 — 프록시 버퍼링 없이 단계가 즉시 도착하도록. POST/재연결 GET 공용.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # 프록시 버퍼링 방지(단계가 제때 전달되도록)
}

# 종료(스트림을 닫아야 하는) 이벤트 type — 이걸 받으면 SSE 제너레이터가 끝난다.
_TERMINAL_TYPES = ("result", "error")


async def _job_sse(job: Job) -> AsyncIterator[str]:
    """작업 이벤트를 SSE로 흘려보내는 소비자 기능.  # (Stream one job's events as SSE)

    구독 즉시 현재 버퍼 스냅샷(지금까지의 job/progress/result/error)을 재생하고, 이후 이벤트를
    큐로 이어 받는다(재연결도 동일 — 끊겼던 클라이언트가 현재 단계부터 이어 받음). 큐가
    하트비트 간격 동안 비면 keep-alive 주석을 흘려 idle 타임아웃을 막는다. result/error를
    만나면(또는 스냅샷 시점에 이미 종료됐으면) 종료한다. 작업 자체는 취소하지 않는다(끊김 = 취소 아님).
    관련: app/services/job_manager.py.

    Args:
        job: 구독할 작업.
    Yields:
        SSE 프레임 문자열들(이벤트 data + keep-alive 주석).
    """
    snapshot, q = job.subscribe()
    try:
        for ev in snapshot:  # 현재 상태 스냅샷부터 재생
            yield _sse(ev)
        if job.terminal:  # 이미 끝난 작업이면 스냅샷에 result/error가 포함됨 → 종료
            return
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                yield _HEARTBEAT_FRAME  # 진행 이벤트가 없는 긴 단계 동안 연결 유지
                continue
            yield _sse(ev)
            if getattr(ev, "type", None) in _TERMINAL_TYPES:
                return
    finally:
        job.unsubscribe(q)


async def _run_creation_pipeline(job: Job, req: CreateRequest) -> None:
    """창작 파이프라인(백그라운드 작업 본체) 기능.  # (Background creation pipeline)

    각 단계 시작(running)/완료(done) progress 이벤트를 job에 publish하고, 마지막에 result를
    publish한다. 오디오극(mode=audio)이면 finalize 뒤 tts 단계를 추가로 돌린다. 어느 단계든
    예외가 나면 error 이벤트로 변환해 publish한다. **연결과 분리되어** 실행되므로 클라이언트가
    끊겨도 끝까지 진행한다(끊김 = 취소 아님). 하트비트는 소비자(_job_sse)가 담당한다.

    Args:
        job: 이벤트를 publish할 작업.
        req: 창작 요청(이미 422 검증 통과).
    """
    try:
        # 1) analysis: 소스 준비(full=summary, scene=선택 장면 segments). 둘 다 서버가 book.json에서 읽음.
        job.publish(ProgressEvent(stage=Stage.analysis, status=ProgressStatus.running))
        if req.scope is Scope.scene:
            source = data_loader.get_scene_source(req.bookId, req.sceneIds)
        else:
            source = data_loader.get_full_source(req.bookId)
        job.publish(ProgressEvent(stage=Stage.analysis, status=ProgressStatus.done))

        # 2) structure: 프롬프트 빌드
        job.publish(ProgressEvent(stage=Stage.structure, status=ProgressStatus.running))
        system_prompt, user_prompt = prompt_builder.build_prompt(req, source)
        job.publish(ProgressEvent(stage=Stage.structure, status=ProgressStatus.done))

        # 3) writing: Claude 호출. structured outputs로 JSON 문법 강제(파싱 오류 방지).
        # 수 분 걸릴 수 있으나, 작업이 연결과 분리돼 끊겨도 계속 진행된다(소비자가 하트비트로 연결 유지).
        job.publish(ProgressEvent(stage=Stage.writing, status=ProgressStatus.running))
        raw = await claude_client.generate_creation(
            system_prompt, user_prompt, output_schema=prompt_builder.OUTPUT_JSON_SCHEMA
        )
        job.publish(ProgressEvent(stage=Stage.writing, status=ProgressStatus.done))

        # 4) finalize: 정제/검증 + '원작 정보' source 블록 embed
        #    + 어려운 단어 사전(result.vocab) embed + 창작물 고유 표지(result.creationCover*) 생성.
        #    둘 다 보조 데이터라 개별 실패는 삼키고 병렬 실행한다.
        job.publish(ProgressEvent(stage=Stage.finalize, status=ProgressStatus.running))
        result = formatter.format_creation(raw, req)
        source_block = data_loader.get_source_block(req.bookId, req.scope.value, req.sceneIds)
        result.source = CreationSource(**source_block, coverImageUrl=_cover_url(req.bookId))
        vocab, result = await asyncio.gather(
            vocab_service.build_glossary(
                formatter.extract_difficult_words(raw), result, req.difficulty.value
            ),
            creation_cover_service.attach_cover(result),
        )
        result.vocab = vocab
        job.publish(ProgressEvent(stage=Stage.finalize, status=ProgressStatus.done))

        # 5) tts: 오디오극이면 라인별 합성 → 단일 MP3 병합 + timepoints. 대화극은 건너뜀.
        if req.mode is Mode.audio:
            job.publish(ProgressEvent(stage=Stage.tts, status=ProgressStatus.running))
            result = await tts_client.synthesize_creation(result)
            job.publish(ProgressEvent(stage=Stage.tts, status=ProgressStatus.done))

        job.publish(ResultEvent(data=result))
    except FileNotFoundError as e:  # 원작 없음
        job.publish(ErrorEvent(message=f"원작 데이터를 찾을 수 없습니다: {e}"))
    except ValueError as e:  # 잘못된 sceneId / book.json 형식 오류
        job.publish(ErrorEvent(message=str(e)))
    except claude_client.ClaudeError as e:  # Claude 호출 실패/키 미설정
        job.publish(ErrorEvent(message=str(e)))
    except formatter.FormatterError as e:  # 파싱/검증 실패
        job.publish(ErrorEvent(message=str(e)))
    except tts_client.TtsError as e:  # 합성/병합 실패/인증·ffmpeg 없음
        job.publish(ErrorEvent(message=str(e)))
    except Exception as e:  # 예기치 못한 오류도 error 이벤트로 surface
        job.publish(ErrorEvent(message=f"알 수 없는 오류: {e}"))


@router.post("/create")
async def create(
    req: CreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StreamingResponse:
    """창작 생성 엔드포인트.  # (POST /create)

    - 요청: CreateRequest(JSON). 잘못된 값은 pydantic이 422로 거부.
      선택 헤더 `Idempotency-Key`: 같은 키로 재요청 시(작업이 살아 있으면) 같은 jobId로 같은
      작업을 다시 흘려보내 중복 생성을 막는다.
    - 응답: text/event-stream(SSE). **첫 이벤트로 `{type:"job", jobId}`**를 보낸 뒤
      progress 이벤트들 → result(또는 error). 작업은 백그라운드로 실행되어, 클라이언트가
      끊겨도(모바일 백그라운드 전환 등) 계속 진행된다. 끊긴 클라이언트는 jobId로 재연결(events)/폴링한다.
    오디오극이면 단일 audioUrl을 `/audio/...` 상대경로로 만든다(호스트/포트 비하드코딩).

    Args:
        req: 창작 요청.
        idempotency_key: 중복 방지용 선택 헤더(없으면 매번 새 작업).
    Returns:
        SSE 스트리밍 응답(job → progress... → result|error).
    """
    job, _created = get_job_manager().start_job(
        lambda j: _run_creation_pipeline(j, req), idempotency_key=idempotency_key
    )
    return StreamingResponse(_job_sse(job), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/create/{job_id}/events")
async def create_events(job_id: str) -> StreamingResponse:
    """창작 작업 재연결(SSE 재구독) 엔드포인트.  # (GET /create/{jobId}/events)

    끊겼던 클라이언트가 jobId로 같은 작업 스트림에 다시 붙는다. 붙는 즉시 현재 상태
    스냅샷(예: 지금 writing 단계)부터 내려주고 이후 이벤트를 이어 흘린다. 이미 완료된 작업이면
    스냅샷에 담긴 result(또는 error)를 한 번 내려주고 종료한다. 이벤트 포맷은 POST /create와 동일.

    Args:
        job_id: POST /create 첫 이벤트로 받은 작업 식별자.
    Returns:
        SSE 스트리밍 응답.
    Raises:
        HTTPException: 만료/없는 jobId면 404.
    """
    job = get_job_manager().get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail="해당 작업을 찾을 수 없습니다(만료되었거나 존재하지 않음)."
        )
    return StreamingResponse(_job_sse(job), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/create/{job_id}", response_model=JobStatusResponse)
async def create_status(job_id: str) -> JobStatusResponse:
    """창작 작업 상태 폴링 엔드포인트.  # (GET /create/{jobId})

    SSE 재연결이 어려운 클라이언트가 앱 복귀 시 몇 초 간격으로 조회하는 상태 스냅샷.
    status=done이면 result에 창작 결과가, status=error면 message에 오류가 담긴다.
    running일 때는 stage가 현재 단계를 가리킨다. 결과/진행 포맷은 SSE와 동일한 데이터다.

    Args:
        job_id: POST /create 첫 이벤트로 받은 작업 식별자.
    Returns:
        JobStatusResponse(jobId/status/stage/result/message).
    Raises:
        HTTPException: 만료/없는 jobId면 404.
    """
    job = get_job_manager().get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail="해당 작업을 찾을 수 없습니다(만료되었거나 존재하지 않음)."
        )
    return JobStatusResponse(
        jobId=job.job_id,
        status=job.status,
        stage=job.stage if job.status == "running" else None,
        result=job.result if job.status == "done" else None,
        message=job.message if job.status == "error" else None,
    )
