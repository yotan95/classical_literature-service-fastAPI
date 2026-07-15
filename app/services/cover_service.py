"""표지 생성 서비스 모듈.  # (Book cover generation service)

book.json 메타 → 표지 프롬프트(cover_prompt_builder) → OpenAI 이미지(PNG) → **webp 변환** →
app/data/images/<slug>.webp 저장. 수동 스크립트(scripts/generate_covers.py)와 (선택적인) 서버
시작 시 자동 생성이 이 한 곳을 공유한다. 표지는 GET /books·창작 결과의 source.coverImageUrl로 제공된다.

OpenAI 호출은 비용이 들므로 '없는 표지만' 만드는 헬퍼(generate_missing)를 제공한다. 키가 없으면
명확히 실패한다(OpenAIImageError). google/anthropic과 무관하며 ARM(Pillow webp)에서 동작한다.
"""

import logging
from pathlib import Path

from app.config import get_settings
from app.services import data_loader
from app.services.cover_prompt_builder import build_book_cover_prompt
from app.services.image_codec import png_to_webp
from app.services.openai_image_client import generate_cover_image

logger = logging.getLogger(__name__)

# 새로 생성하는 표지 포맷(기존 6권과 동일 webp 통일).
_COVER_FORMAT = "webp"


def _images_dir() -> Path:
    """표지 저장 폴더 경로 반환 기능.  # (Resolve images dir)"""
    return Path(get_settings().data_dir) / "images"


async def generate_cover(book_slug: str) -> Path:
    """한 책의 표지를 생성·저장 기능.  # (Generate & save one cover)

    book.json → 프롬프트 → OpenAI(PNG) → webp → app/data/images/<slug>.webp 저장 후 경로 반환.

    Args:
        book_slug: 책 슬러그(book.json 필요).
    Returns:
        저장된 표지 파일 경로.
    Raises:
        FileNotFoundError: book.json이 없을 때.
        OpenAIImageError: 키 없음/생성 실패 시.
    """
    book = data_loader.load_book(book_slug)
    prompt = build_book_cover_prompt(book)
    png_bytes = await generate_cover_image(prompt)
    webp_bytes = png_to_webp(png_bytes)

    images_dir = _images_dir()
    images_dir.mkdir(parents=True, exist_ok=True)
    out = images_dir / f"{book_slug}.{_COVER_FORMAT}"
    tmp = out.with_suffix(f".{_COVER_FORMAT}.part")
    tmp.write_bytes(webp_bytes)
    tmp.replace(out)  # 원자적 교체(부분 파일 방지)
    return out


async def generate_missing(slugs: list[str] | None = None) -> list[str]:
    """표지가 없는 책들의 표지를 생성 기능.  # (Generate covers for books missing one)

    서버 시작 시 자동 생성 옵션(백그라운드) 또는 배치에서 쓴다. 이미 표지가 있는 책은 건너뛴다.
    한 권 실패가 전체를 막지 않도록 예외는 경고로 남기고 계속한다(견고성).

    Args:
        slugs: 대상 슬러그(None이면 전체 책).
    Returns:
        새로 생성한 책 슬러그 목록.
    """
    targets = slugs if slugs is not None else data_loader.list_book_slugs()
    created: list[str] = []
    for slug in targets:
        if data_loader.cover_file(slug) is not None:
            continue  # 이미 표지 있음
        try:
            await generate_cover(slug)
            created.append(slug)
            logger.info("표지 생성 완료: %s", slug)
        except Exception as e:  # 키 없음/생성 실패 등 — 전체를 막지 않음
            logger.warning("표지 생성 실패 → 건너뜀: %s (%s)", slug, e)
    return created
