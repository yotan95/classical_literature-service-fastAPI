"""창작물 고유 표지 생성 서비스 모듈.  # (Per-creation cover generation service)

POST /create 결과마다 생성된 창작물 내용(제목/intro/장면/대사)을 바탕으로 OpenAI 이미지를 만들고,
webp로 변환해 정적 폴더에 저장한 뒤 `CreationResult.creationCoverImageUrl`에 상대경로를 채운다.
이미지 생성은 보조 기능이므로 실패해도 창작 전체를 실패시키지 않고 대표 이모티콘으로 폴백한다.
관련: app/routers/creation.py, app/models/response.py, app/services/cover_prompt_builder.py.
"""

import logging
import re
from pathlib import Path

from app.config import get_settings
from app.models.response import CreationResult
from app.services import data_loader
from app.services.cover_prompt_builder import build_creation_cover_prompt
from app.services.image_codec import png_to_webp
from app.services.openai_image_client import generate_cover_image

logger = logging.getLogger(__name__)

_COVER_FORMAT = "webp"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _creation_covers_dir() -> Path:
    """창작물 표지 저장 폴더 경로를 반환하는 기능.  # (Resolve creation cover dir)

    설정의 CREATION_COVER_DIR 값을 pathlib 경로로 만든다. 기본값은 app/creation_covers이다.
    관련: app/config.py, app/main.py.

    Returns:
        창작물 표지 저장 폴더 Path.
    """
    return Path(get_settings().creation_cover_dir)


def _safe_cover_name(creation_id: str) -> str:
    """창작 id를 안전한 표지 파일명으로 바꾸는 기능.  # (Build safe cover filename)

    creationId가 파일 경로 문자를 포함해도 정적 폴더 밖으로 나가지 않도록 허용 문자 외에는
    하이픈으로 치환한다.
    관련: app/models/response.py CreationResult.creationId.

    Args:
        creation_id: 창작물 id.
    Returns:
        creation_<id>.webp 형태의 안전한 파일명.
    """
    safe_id = _SAFE_NAME_RE.sub("-", creation_id).strip(".-") or "untitled"
    return f"creation_{safe_id}.{_COVER_FORMAT}"


def _cover_url(path: Path) -> str:
    """창작물 표지 정적 경로를 만드는 기능.  # (Build static path for a creation cover)

    `/creation-covers/<file>?v=<mtime>` 형식으로 반환해 같은 creationId 파일이 갱신되어도
    클라이언트 캐시가 자동으로 무효화되게 한다. 호스트는 클라이언트의 BASE_URL을 따른다.
    관련: app/main.py 정적 마운트.

    Args:
        path: 저장된 표지 파일 경로.
    Returns:
        정적 상대경로 문자열.
    """
    version = int(path.stat().st_mtime)
    return f"/creation-covers/{path.name}?v={version}"


def _representative_emoji(book: dict) -> str:
    """책 대표 이모티콘을 반환하는 기능.  # (Resolve representative emoji)

    창작물 표지 이미지 생성 실패 시 앱이 보여줄 fallback 이모티콘이다.
    book.json의 최상위 emoji를 우선 쓰고, 없으면 연극/창작물에 어울리는 기본값을 쓴다.
    관련: app/data/<slug>/book.json.

    Args:
        book: 원작 book.json dict.
    Returns:
        대표 이모티콘 문자열.
    """
    emoji = (book.get("emoji") or "").strip()
    return emoji or "🎭"


async def attach_cover(result: CreationResult) -> CreationResult:
    """창작 결과에 고유 표지 경로/대표 이모티콘을 채우는 기능.  # (Attach per-creation cover)

    매 /create마다 OpenAI 이미지 1장을 생성해 app/creation_covers에 저장하고,
    `result.creationCoverImageUrl`에 정적 상대경로를 넣어 반환한다. OPENAI_API_KEY 없음/생성 실패/
    저장 실패 등은 모두 경고로 남기고 `result.creationCoverEmoji`만 남긴다.
    관련: app/routers/creation.py finalize 단계.

    Args:
        result: formatter가 만든 창작 결과(source가 채워진 상태).
    Returns:
        creationCoverImageUrl 또는 creationCoverEmoji가 채워진 CreationResult.
    """
    result.creationCoverImageUrl = None
    result.creationCoverEmoji = "🎭"

    try:
        book = data_loader.load_book(result.bookId)
        result.creationCoverEmoji = _representative_emoji(book)
        prompt = build_creation_cover_prompt(result, book)
        png_bytes = await generate_cover_image(prompt)
        webp_bytes = png_to_webp(png_bytes)

        out_dir = _creation_covers_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / _safe_cover_name(result.creationId)
        tmp = out.with_suffix(f".{_COVER_FORMAT}.part")
        tmp.write_bytes(webp_bytes)
        tmp.replace(out)
        result.creationCoverImageUrl = _cover_url(out)
    except Exception as e:  # 표지는 보조 데이터라 실패해도 창작 결과는 보존한다.
        logger.warning("창작물 표지 생성 실패(대표 이모티콘으로 폴백): %s (%s)", result.creationId, e)

    return result
