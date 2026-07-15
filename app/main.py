"""FastAPI 진입점 모듈.  # (App entrypoint)

FastAPI 인스턴스 생성 + CORS 설정 + 헬스체크(GET /health) + 창작 라우터(creation) 등록
+ 오디오극 합성 MP3 정적 서빙(/audio) + 원작 표지 정적 서빙(/images) + 창작물 표지 정적 서빙
(/creation-covers) + 표지 자동 생성(시작 시 generate_missing + watchdog 감시).
호스트/포트/CORS/경로는 코드에 하드코딩하지 않고
config(환경변수)에서 읽는다.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import creation, rewrite, vocab
from app.services import cover_service, cover_watcher
from app.services.job_manager import get_job_manager

logger = logging.getLogger(__name__)

# 설정 로드 (HOST/PORT/CORS_ORIGINS 등) — 코드가 IP를 몰라도 되도록 외부화
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명주기 — 표지 자동 생성(시작 시 + 감시).  # (Auto-cover lifespan)

    시작 시: 표지 없는 책의 표지를 백그라운드로 생성(부팅을 막지 않음, 표지 있으면 no-op).
    또한 app/data를 watchdog으로 감시해 book.json 드롭/변경 시 표지를 자동 생성한다.
    아울러 창작 작업(/create) 보존용 만료 정리 루프를 띄운다(TTL 경과 작업 메모리 회수).
    종료 시: 감시 옵저버와 정리 루프를 정리한다. 모든 단계는 실패해도 서버 기동/종료를 막지 않는다(견고성).
    """
    observer = None
    cleanup_task = None
    try:
        # 시작 시 누락 표지 백그라운드 생성(OPENAI 키 없으면 경고만 남기고 건너뜀)
        asyncio.create_task(cover_service.generate_missing())
        # book.json 드롭/변경 실시간 감지 → 표지 자동 생성
        observer = cover_watcher.start_watcher(Path(settings.data_dir), asyncio.get_running_loop())
        # 완료/오류된 /create 작업을 TTL 경과 후 정리(끊긴 클라이언트 재연결 창 보장 + 메모리 회수)
        cleanup_task = asyncio.create_task(get_job_manager().cleanup_loop())
    except Exception as e:  # 자동화 실패가 서버 기동을 막지 않게(견고성)
        logger.warning("백그라운드 작업 초기화 실패(무시): %s", e)
    try:
        yield
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=2)
        if cleanup_task is not None:
            cleanup_task.cancel()


app = FastAPI(
    title="Classic Literature Creation API",
    description="고전문학 원작을 입력받아 모드별 창작물(JSON)을 생성하는 백엔드 (뼈대)",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 개방 — Flutter(웹/디버그 포함)가 호출할 수 있도록 설정값 기반으로 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,  # 개발 기본 "*", 배포 시 좁힘
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 오디오극 합성 MP3 정적 서빙 — AUDIO_DIR을 /audio 경로로 노출.
# 병합된 단일 MP3(audio.audioUrl)가 이 경로를 가리킨다. 시작 시 폴더가 없으면 만든다(상대경로).
_audio_dir = Path(settings.audio_dir)
_audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(_audio_dir)), name="audio")

# 표지 이미지 정적 서빙 — DATA_DIR/images 를 /images 경로로 노출.
# coverImageUrl(목록·원작 정보 탭)이 이 경로를 가리킨다. StaticFiles가 ETag/Last-Modified를 주므로
# 클라이언트 캐시가 304로 재검증되고, URL의 ?v=수정시각 토큰으로 변경 시 자동 캐시 무효화된다.
_images_dir = Path(settings.data_dir) / "images"
_images_dir.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(_images_dir)), name="images")

# 창작물 고유 표지 정적 서빙 — POST /create finalize에서 생성된 webp가 이 경로를 가리킨다.
# 원작 표지(/images)와 분리해 app/data 스캔·watchdog 정책에 영향을 주지 않는다.
_creation_covers_dir = Path(settings.creation_cover_dir)
_creation_covers_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/creation-covers",
    StaticFiles(directory=str(_creation_covers_dir)),
    name="creation-covers",
)

# 창작 라우터 등록 — POST /create (SSE). /health는 아래에 직접 둔다.
app.include_router(creation.router)

# 단어 풀이 라우터 등록 — POST /vocab(어려운 단어 사전 팝업). 국립국어원 사전 + Claude.
app.include_router(vocab.router)

# 대사 고쳐쓰기 라우터 등록 — POST /rewrite-line(대사극 'AI로 바꾸기'). 한 줄만 Claude로 새로 씀.
app.include_router(rewrite.router)


@app.get("/health")
def health() -> dict:
    """헬스체크 엔드포인트.  # (Health check)

    서버 기동 여부를 확인한다. RPi 배포 검증용으로 사용한다.
    - 요청: 파라미터 없음 (GET /health)
    - 응답: 상태/모델 정보가 담긴 단순 JSON

    Returns:
        {"status": "ok", "model": <CLAUDE_MODEL>} 형태의 dict.
    """
    return {"status": "ok", "model": settings.claude_model}
