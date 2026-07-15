"""book.json 감시 → 표지 자동 생성 모듈.  # (Watch app/data for new book.json → auto cover)

app/data/ 아래에 `book.json`이 생기거나 바뀌면 watchdog이 감지해, 표지가 없는 책의 표지를
백그라운드로 생성한다(cover_service). '책 추가(book.json 드롭) → 표지 자동 생성 → Flutter refresh 반영'
자동화의 서버 측 절반(나머지 절반=서버 시작 시 generate_missing, main.py lifespan).

watchdog 핸들러는 별도 스레드에서 돌므로, 코루틴은 run_coroutine_threadsafe로 메인 이벤트 루프에
넘긴다. 같은 책의 중복 동시 생성을 막기 위해 in-flight 슬러그 집합으로 가드한다. 표지가 이미 있으면
generate_missing이 건너뛰므로 비용/재생성 걱정이 없다(book.json 수정만으로 표지를 다시 만들진 않음).
"""

import asyncio
import logging
from pathlib import Path

from app.services import cover_service

logger = logging.getLogger(__name__)

# 동시 중복 생성 방지용 in-flight 슬러그 집합(여러 modify 이벤트가 몰려도 1회만).
_inflight: set[str] = set()


def _schedule_cover(slug: str, loop: asyncio.AbstractEventLoop) -> None:
    """슬러그 표지 생성을 메인 루프에 예약 기능.  # (Schedule cover gen on the loop)

    이미 진행 중이면 건너뛴다. 표지가 있으면 generate_missing이 알아서 no-op 한다.
    """
    if slug in _inflight:
        return
    _inflight.add(slug)

    async def _run() -> None:
        try:
            created = await cover_service.generate_missing([slug])
            if created:
                logger.info("표지 자동 생성(감지): %s", created)
        finally:
            _inflight.discard(slug)

    asyncio.run_coroutine_threadsafe(_run(), loop)


def start_watcher(data_dir: Path, loop: asyncio.AbstractEventLoop):
    """app/data 감시 시작 기능.  # (Start watching app/data for book.json)

    watchdog Observer를 띄워 book.json의 생성/수정/이동을 감지하면 해당 책 표지를 생성 예약한다.
    watchdog 미설치 등 실패 시 None을 반환하고 경고만 남긴다(서버 기동은 막지 않음).

    Args:
        data_dir: 감시할 데이터 루트(app/data).
        loop: 코루틴을 넘길 메인 이벤트 루프.
    Returns:
        실행 중인 Observer(또는 실패 시 None). 종료 시 stop()/join() 한다.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        logger.warning("watchdog 미설치 → 표지 실시간 감지를 건너뜁니다(시작 시 생성은 동작).")
        return None

    class _BookJsonHandler(FileSystemEventHandler):
        """book.json 변경만 골라 표지 생성을 예약하는 핸들러."""

        def _maybe(self, path_str: str, is_dir: bool) -> None:
            if is_dir:
                return
            p = Path(path_str)
            if p.name != "book.json":
                return
            _schedule_cover(p.parent.name, loop)  # 슬러그 = book.json의 상위 폴더명

        def on_created(self, event) -> None:
            self._maybe(event.src_path, event.is_directory)

        def on_modified(self, event) -> None:
            self._maybe(event.src_path, event.is_directory)

        def on_moved(self, event) -> None:  # 에디터의 원자적 저장(temp→rename) 대응
            self._maybe(getattr(event, "dest_path", event.src_path), event.is_directory)

    try:
        observer = Observer()
        observer.schedule(_BookJsonHandler(), str(data_dir), recursive=True)
        observer.start()
        logger.info("표지 감시 시작: %s", data_dir)
        return observer
    except Exception as e:  # 권한/플랫폼 문제 등 — 기동은 계속
        logger.warning("표지 감시 시작 실패(무시): %s", e)
        return None
