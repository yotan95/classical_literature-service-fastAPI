"""표지 이미지 생성 스크립트(개발 도구).  # (Pre-generate book covers via GPT image)

각 책의 book.json 메타로 표지 프롬프트를 만들고 OpenAI 이미지 API(PNG)로 표지를 생성한 뒤
**webp로 변환**해 app/data/images/<slug>.webp 로 저장한다(기존 6권과 동일 포맷, 모바일 용량↓).
표지는 책마다 1회만 생성하면 되고(정적 URL 제공), 새 책을 추가하면 이 스크립트로 표지만 만들면 된다.

비용이 드는 외부 호출이므로 기본은 '없는 표지만' 생성하고, --force로 전체 재생성한다.
OPENAI_API_KEY가 .env 또는 환경변수에 있어야 한다. 표지 자동 생성(서버 시작 시)은
app/services/cover_service.py가 이 로직을 공유한다.

사용법:
    python scripts/generate_covers.py                 # 표지 없는 책만 생성
    python scripts/generate_covers.py heosaeng_jeon   # 특정 책만
    python scripts/generate_covers.py --force         # 전체 재생성
"""

import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가(스크립트 단독 실행 대비).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import cover_service, data_loader  # noqa: E402
from app.services.openai_image_client import OpenAIImageError  # noqa: E402


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv[1:]
    targets = args or data_loader.list_book_slugs()

    for slug in targets:
        if data_loader.cover_file(slug) is not None and not force:
            print(f"skip {slug} (이미 표지 있음; --force로 재생성)")
            continue
        try:
            out = await cover_service.generate_cover(slug)
            print(f"wrote {out}")
        except (OpenAIImageError, FileNotFoundError, ValueError) as e:
            print(f"FAIL {slug}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
