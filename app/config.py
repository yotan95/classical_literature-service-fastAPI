"""환경변수 기반 설정 모듈.  # (Env-based settings)

설정 키(HOST/PORT/ANTHROPIC_API_KEY/CLAUDE_MODEL/DATA_DIR/CORS_ORIGINS)를
환경변수(.env)에서 읽어 들인다. IP/포트/키를 코드에 하드코딩하지 않기 위함.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 설정값 모음 기능.  # (App settings container)

    .env 또는 OS 환경변수에서 값을 읽고, 없으면 표의 기본값을 사용한다.
    Mac↔RPi 환경 차이를 코드가 몰라도 되도록 모든 가변값을 여기로 외부화한다.

    Attributes:
        host: 바인드 호스트. 기본 0.0.0.0 (휴대폰/태블릿 접근용).
        port: 바인드 포트. 기본 8000.
        anthropic_api_key: Claude API 키. 코드/로그/git에 남기지 않음.
        claude_model: 사용할 모델 문자열. 기본 claude-sonnet-4-6 (결정).
        data_dir: 원작 데이터 루트 경로. 기본 app/data.
        cors_origins: CORS 허용 오리진. 기본 "*" (개발), 콤마 구분 문자열.
        google_application_credentials: Google Cloud 서비스계정 JSON 경로(오디오극 TTS).
            비면 ADC 사용 시도; 둘 다 없으면 합성 시 명확히 실패. 키는 git에 넣지 않음.
        audio_dir: 합성 MP3 캐시/서빙 루트. 기본 app/audio. /audio 정적 마운트와 연결.
        creation_cover_dir: 창작물 고유 표지 webp 저장 루트. 기본 app/creation_covers.
        job_ttl_seconds: 완료/오류된 /create 작업을 jobId로 보관하는 시간(초). 기본 3600(1시간).
            메모리 저장이라 서버 재시작 시 유실되며, 이 시간 후 정리된다.
    """

    host: str = "0.0.0.0"
    port: int = 8000
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"
    data_dir: str = "app/data"
    cors_origins: str = "*"

    # 창작 작업 백그라운드 보존 TTL(초) — 끊긴 클라이언트가 jobId로 결과를 다시 받을 수 있는 창.
    job_ttl_seconds: int = 3600

    # 오디오극 음성 합성(Google Cloud TTS) — 경로/캐시는 env로 외부화
    google_application_credentials: str = ""
    audio_dir: str = "app/audio"
    creation_cover_dir: str = "app/creation_covers"

    # .env 파일을 읽되, 대소문자 무시(HOST==host)·미정의 키는 무시한다.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_image_model: str = "gpt-image-2"
    openai_image_size: str = "800x1120"
    openai_image_quality: str = "low"

    # 어려운 단어 풀이(POST /vocab) — 국립국어원 한국어기초사전(krdict) Open API 인증키.
    # 키는 코드/로그/git에 남기지 않고 .env에서만 읽는다. 비면 /vocab가 명확히 실패한다.
    krdict_api_key: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS 오리진 문자열을 리스트로 변환하는 기능.  # (Parse CORS origins)

        "*" 이거나 콤마로 구분된 문자열을 FastAPI CORS 미들웨어가 쓰는 리스트로 만든다.

        Returns:
            허용 오리진 리스트. "*" 단독이면 ["*"].
        """
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        # 콤마로 나누고 공백/빈 항목 제거
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴 반환 기능.  # (Cached settings accessor)

    Settings 인스턴스를 한 번만 생성해 캐시한다(매 요청마다 .env를 다시 읽지 않도록).

    Returns:
        캐시된 Settings 인스턴스.
    """
    return Settings()
