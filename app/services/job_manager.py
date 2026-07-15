"""창작 작업 백그라운드 보존/재연결 매니저 모듈.  # (Background job store for /create)

POST /create를 **연결과 분리된 백그라운드 asyncio 작업**으로 돌려, 클라이언트가 끊겨도
(모바일 백그라운드 전환·화면 잠금 등) 생성이 계속 진행되게 한다. 각 작업은 jobId로 식별되고
모든 이벤트(job/progress/result/error)를 버퍼에 쌓아 두므로, 끊겼던 클라이언트가
재연결(SSE)하면 현재 상태 스냅샷부터 이어 받을 수 있고, 폴링으로도 상태/결과를 조회할 수 있다.

이 모듈은 **전송 계층(SSE/HTTP)을 모른다.** 라우터가 이벤트를 어떻게 직렬화/하트비트할지는
app/routers/creation.py가 담당하고, 여기서는 작업 수명주기 + 이벤트 팬아웃 + TTL 정리만 한다.

저장은 인메모리(프로세스 메모리)다 — 서버 재시작 시 진행 중/완료 작업은 유실되며, 그때
조회는 라우터가 404로 처리한다.
완료/오류 작업은 TTL(JOB_TTL_SECONDS, 기본 1시간) 동안만 보관 후 정리한다.
관련: app/routers/creation.py, app/models/response.py.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from functools import lru_cache

from pydantic import BaseModel

from app.config import get_settings
from app.models.response import (
    CreationResult,
    ErrorEvent,
    JobEvent,
    ProgressStatus,
)

logger = logging.getLogger(__name__)

# 완료/오류 작업을 만료 정리하는 주기(초). 만료 즉시성은 조회 시 lazy 검사로도 보장하고,
# 이 주기 스윕은 아무도 조회하지 않는 작업의 메모리를 풀어 주는 용도다.
_CLEANUP_INTERVAL_SECONDS = 300

# 작업 실행 함수 타입 — job을 받아 이벤트를 publish하며 파이프라인을 수행하는 코루틴.
JobRunner = Callable[["Job"], Awaitable[None]]


class Job:
    """창작 작업 1건(상태 + 이벤트 버퍼 + 구독자) 기능.  # (One background creation job)

    백그라운드 파이프라인이 publish하는 이벤트를 순서대로 버퍼(events)에 쌓고, 살아 있는
    구독자(SSE 스트림)에게 큐로 팬아웃한다. 새 구독자는 먼저 현재 버퍼 스냅샷을 받은 뒤
    이후 이벤트를 큐로 이어 받는다(스냅샷→등록 사이에 await가 없어 누락/중복이 없음).
    이벤트에서 status/stage/result/message를 파생해 폴링 응답을 즉시 만들 수 있게 한다.
    관련: app/routers/creation.py(_job_sse).

    Attributes:
        job_id: 작업 식별자(uuid).
        status: queued|running|done|error (이벤트로부터 파생).
        stage: running일 때 현재 단계 문자열(아니면 None).
        result: done일 때의 창작 결과(아니면 None).
        message: error일 때의 오류 메시지(아니면 None).
        events: 지금까지 publish된 모든 이벤트(재연결 스냅샷용 재생 버퍼).
        idempotency_key: 중복 방지 키(있으면). 정리 시 역매핑 제거에 사용.
        finished_at: 종료 시각(monotonic). TTL 만료 판정 기준(미종료면 None).
    """

    def __init__(self, job_id: str, idempotency_key: str | None = None) -> None:
        """작업 객체 초기화 기능.  # (Init a job)

        Args:
            job_id: 작업 식별자(uuid).
            idempotency_key: 중복 방지 키(없으면 None).
        """
        self.job_id = job_id
        self.idempotency_key = idempotency_key
        self.status: str = "queued"
        self.stage: str | None = None
        self.result: CreationResult | None = None
        self.message: str | None = None
        self.events: list[BaseModel] = []
        self.created_at = time.monotonic()
        self.finished_at: float | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    @property
    def terminal(self) -> bool:
        """작업이 종료(완료/오류) 상태인지 반환 기능.  # (Is job finished)

        Returns:
            status가 done/error면 True.
        """
        return self.status in ("done", "error")

    def publish(self, event: BaseModel) -> None:
        """이벤트 1건을 버퍼에 쌓고 모든 구독자에게 전달하는 기능.  # (Append + fan-out one event)

        이벤트 type에서 status/stage/result/message를 파생해 폴링 응답을 갱신하고,
        재생 버퍼에 추가한 뒤 살아 있는 구독자 큐에 넣는다. 동기 함수라 이벤트 루프상에서
        원자적으로 실행되어 구독 등록과의 경합이 없다(누락/중복 방지).
        관련: app/routers/creation.py(_job_sse).

        Args:
            event: JobEvent | ProgressEvent | ResultEvent | ErrorEvent.
        """
        etype = getattr(event, "type", None)
        if etype == "progress":
            self.status = "running"
            # running 이벤트의 단계만 '현재 단계'로 기록(done은 단계 종료라 유지)
            if getattr(event, "status", None) is ProgressStatus.running:
                self.stage = event.stage.value
        elif etype == "result":
            self.status = "done"
            self.result = event.data
            self.finished_at = time.monotonic()
        elif etype == "error":
            self.status = "error"
            self.message = event.message
            self.finished_at = time.monotonic()

        self.events.append(event)
        for q in self._subscribers:
            q.put_nowait(event)

    def subscribe(self) -> tuple[list[BaseModel], "asyncio.Queue"]:
        """구독 시작 — 현재 스냅샷과 이후 이벤트 큐를 반환하는 기능.  # (Snapshot + live queue)

        스냅샷(지금까지의 이벤트 복사본)을 먼저 만든 뒤 큐를 등록한다. 둘 사이에 await가 없어
        그 사이 새 이벤트가 끼어들 수 없으므로, 스냅샷 재생 후 큐를 읽으면 누락/중복이 없다.
        관련: app/routers/creation.py(_job_sse).

        Returns:
            (스냅샷 이벤트 리스트, 이후 이벤트가 들어올 큐).
        """
        snapshot = list(self.events)
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return snapshot, q

    def unsubscribe(self, q: "asyncio.Queue") -> None:
        """구독 해제 기능.  # (Drop a subscriber queue)

        스트림이 닫히면(정상 종료/클라이언트 끊김) 호출해 구독자 집합에서 큐를 제거한다.
        작업 자체는 취소하지 않는다(끊김 = 취소 아님).

        Args:
            q: subscribe()가 돌려준 큐.
        """
        self._subscribers.discard(q)


class JobManager:
    """창작 작업 인메모리 저장소 + 수명주기 관리 기능.  # (In-memory job registry)

    jobId→Job 매핑을 들고, 작업을 백그라운드 태스크로 띄우며, 완료/오류 작업을 TTL 동안만
    보관 후 정리한다. 같은 Idempotency-Key로 들어온 (아직 살아 있는) 작업은 같은 jobId로
    재사용해 중복 생성을 막는다. 단일 스레드 asyncio 전제라 별도 락 없이 동기 연산만 쓴다.
    관련: app/routers/creation.py.

    Attributes:
        ttl_seconds: 완료/오류 작업 보관 시간(초). JOB_TTL_SECONDS에서 주입.
    """

    def __init__(self, ttl_seconds: int) -> None:
        """매니저 초기화 기능.  # (Init manager)

        Args:
            ttl_seconds: 완료/오류 작업 보관 TTL(초).
        """
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, str] = {}  # Idempotency-Key → job_id

    def start_job(
        self, runner: JobRunner, idempotency_key: str | None = None
    ) -> tuple[Job, bool]:
        """작업을 백그라운드로 시작(또는 중복 키면 기존 작업 반환) 기능.  # (Start/dedup a job)

        idempotency_key가 아직 살아 있는 작업을 가리키면 그 작업을 그대로 돌려준다(created=False,
        새 태스크를 띄우지 않음 → 중복 방지). 아니면 새 Job을 만들어 가장 먼저 JobEvent를
        publish하고(스냅샷 맨 앞 보장), runner를 백그라운드 태스크로 실행한다. 끊김과 무관하게
        끝까지 진행한다. 반드시 실행 중인 이벤트 루프 안(async 핸들러)에서 호출해야 한다.
        관련: app/routers/creation.py(POST /create).

        Args:
            runner: job을 받아 이벤트를 publish하며 파이프라인을 수행하는 코루틴 함수.
            idempotency_key: 중복 방지 키(없으면 None).
        Returns:
            (Job, created) — created=False면 기존 작업 재사용.
        """
        if idempotency_key:
            existing = self.get(self._by_key.get(idempotency_key, ""))
            if existing is not None:
                return existing, False

        job = Job(job_id=str(uuid.uuid4()), idempotency_key=idempotency_key)
        self._jobs[job.job_id] = job
        if idempotency_key:
            self._by_key[idempotency_key] = job.job_id

        # 첫 이벤트로 jobId를 알린다(스냅샷·재연결에서도 맨 앞에 실리도록 태스크 시작 전에 publish).
        job.publish(JobEvent(jobId=job.job_id))
        job._task = asyncio.create_task(self._run(job, runner))
        return job, True

    async def _run(self, job: Job, runner: JobRunner) -> None:
        """백그라운드 작업 실행 래퍼 기능.  # (Background task wrapper)

        runner(파이프라인)는 자체적으로 모든 예외를 error 이벤트로 변환한다. 여기서는 만약을
        대비해, runner가 종료했는데도 종료 이벤트가 없으면 error로 마감해 구독자가 영원히
        대기하지 않도록 한다(견고성).

        Args:
            job: 실행할 작업.
            runner: 파이프라인 코루틴 함수.
        """
        try:
            await runner(job)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # runner가 못 잡은 예외도 작업에 surface
            logger.exception("창작 작업 실행 중 예기치 못한 오류: %s", job.job_id)
            if not job.terminal:
                job.publish(ErrorEvent(message=f"알 수 없는 오류: {e}"))
        finally:
            if not job.terminal:  # 종료 이벤트 없이 끝난 비정상 케이스 마감
                job.publish(ErrorEvent(message="작업이 비정상 종료되었습니다."))

    def get(self, job_id: str) -> Job | None:
        """jobId로 작업 조회 기능(만료 시 정리 후 None).  # (Lookup with lazy expiry)

        완료/오류 후 TTL이 지난 작업은 조회 시점에 제거하고 None을 돌려준다(라우터가 404 처리).

        Args:
            job_id: 작업 식별자.
        Returns:
            살아 있는 Job 또는 None.
        """
        if not job_id:
            return None
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.finished_at is not None and (
            time.monotonic() - job.finished_at
        ) > self.ttl_seconds:
            self._remove(job)
            return None
        return job

    def _remove(self, job: Job) -> None:
        """작업과 그 역매핑(Idempotency-Key)을 저장소에서 제거 기능.  # (Drop a job)

        Args:
            job: 제거할 작업.
        """
        self._jobs.pop(job.job_id, None)
        if job.idempotency_key and self._by_key.get(job.idempotency_key) == job.job_id:
            self._by_key.pop(job.idempotency_key, None)

    def cleanup_expired(self) -> int:
        """TTL이 지난 완료/오류 작업을 일괄 정리하는 기능.  # (Sweep expired jobs)

        주기적으로 호출되어, 아무도 조회하지 않아 남아 있는 만료 작업의 메모리를 푼다.

        Returns:
            제거한 작업 수.
        """
        now = time.monotonic()
        expired = [
            job
            for job in self._jobs.values()
            if job.finished_at is not None and (now - job.finished_at) > self.ttl_seconds
        ]
        for job in expired:
            self._remove(job)
        return len(expired)

    async def cleanup_loop(self) -> None:
        """만료 작업을 주기적으로 정리하는 백그라운드 루프 기능.  # (Periodic cleanup loop)

        앱 수명주기(lifespan)에서 태스크로 띄운다. 종료 시 취소된다. 정리 실패가 루프를
        멈추지 않도록 예외를 삼킨다(견고성).
        관련: app/main.py(lifespan).
        """
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            try:
                removed = self.cleanup_expired()
                if removed:
                    logger.info("만료 작업 정리: %d건", removed)
            except Exception as e:  # 정리 실패가 루프를 멈추지 않게
                logger.warning("작업 정리 중 오류(무시): %s", e)


@lru_cache
def get_job_manager() -> JobManager:
    """작업 매니저 싱글턴 반환 기능.  # (Cached job manager accessor)

    라우터(POST/GET /create)와 app/main.py(정리 루프)가 같은 인스턴스를 공유하도록 캐시한다.
    TTL은 설정(JOB_TTL_SECONDS)에서 읽는다.
    관련: app/config.py, app/routers/creation.py, app/main.py.

    Returns:
        캐시된 JobManager 인스턴스.
    """
    return JobManager(ttl_seconds=get_settings().job_ttl_seconds)
