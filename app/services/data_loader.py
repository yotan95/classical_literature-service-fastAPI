"""원작 데이터 로더 모듈.  # (Original data loader)

DATA_DIR 아래 각 책의 단일 정본 `app/data/<slug>/book.json`을 읽어
목록(GET /books)·상세(GET /books/{id})·창작 소스(full/scene)를 제공한다. book.json이
서버의 단일 출처다(meta/script/voice_profiles 폐기). 원문 `original/<slug>.txt`는 보관·참고용.

처리 정책:
- full 스코프: book.json의 summary(전체 줄거리 3문단)를 창작 소스로 쓴다(원작 txt 전체 미사용 → 토큰 절약).
- scene 스코프: 요청의 sceneIds로 book.json scenes의 해당 segments를 읽어 소스로 쓴다(서버=단일 출처).

경로는 OS 의존 없이 pathlib + DATA_DIR 상대경로로 다루고, 한글 파일은 utf-8로 읽는다.
"""

import json
import logging
from pathlib import Path

from app.config import get_settings

# 빈/깨진 데이터 등 비정상 상황을 경고로 남기기 위한 로거(견고성).
logger = logging.getLogger(__name__)

# 책 슬러그가 아닌(원문/표지) 예약 폴더 — 목록 스캔에서 제외한다.
_RESERVED_DIRS = {"original", "images"}

# 원문(전문) 텍스트 폴더명 — 파일은 original/<slug>.txt. 읽기 화면(GET /books/{id}/original)용.
_ORIGINAL_DIRNAME = "original"

# 표지 이미지 폴더명(서버는 /images 로 정적 서빙). 파일은 <slug>.<ext>.
_IMAGES_DIRNAME = "images"
# 허용 표지 확장자(우선순위 순). 기존 6권은 webp, 새로 생성하는 표지도 webp 통일.
_COVER_EXTS = ("webp", "png", "jpg", "jpeg")

# 원천 자료/출처/라이선스 — '전 책 공통값'. 책별로 다르면 책마다 두는 구조로 바꾼다.
# 화면(원작 정보 탭): classification 칩 + attribution 문구 + "출처: {provider} · {license}".
SOURCE_INFO: dict = {
    "provider": "한국고전번역원",
    "license": "공공누리 제1유형",
    "classification": ["고전소설", "공공 원전"],  # 전 책 공통(설화 등 책별 장르는 book.json.genre 참고)
    "attribution_template": "이 창작물은 《{title}》 공공 원전 자료를 바탕으로 AI가 새롭게 구성했습니다.",
}


def _data_root() -> Path:
    """원작 데이터 루트 경로 반환 기능.  # (Resolve DATA_DIR root)

    설정의 DATA_DIR을 pathlib 경로로 만든다(상대경로 기본 "app/data"; 절대경로 비하드코딩).

    Returns:
        DATA_DIR Path.
    """
    return Path(get_settings().data_dir)


def _book_dir(book_slug: str) -> Path:
    """특정 책의 데이터 폴더 경로 반환 기능.  # (Book folder path)

    Args:
        book_slug: 책 슬러그(폴더명).
    Returns:
        /data/<book-slug> 경로(존재 여부는 호출부에서 확인).
    """
    return _data_root() / book_slug


def list_book_slugs() -> list[str]:
    """사용 가능한 책 슬러그 목록 반환 기능.  # (List available book slugs)

    DATA_DIR 아래 1차 하위 폴더 중 **book.json을 가진 폴더만** 책으로 본다(예약 폴더 original/images나
    빈/잡폴더는 자동 제외). book.json을 추가하기만 하면 자동으로 목록에 잡힌다(유지보수 용이성).

    Returns:
        슬러그(폴더명) 리스트(정렬). 데이터 루트가 없으면 빈 리스트.
    """
    root = _data_root()
    if not root.is_dir():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and p.name not in _RESERVED_DIRS and (p / "book.json").is_file()
    )


def load_book(book_slug: str) -> dict:
    """단일 정본 book.json 로드 기능.  # (Load book.json — single source)

    /data/<slug>/book.json을 utf-8로 읽어 dict로 돌려준다.

    Args:
        book_slug: 책 슬러그(폴더명).
    Returns:
        book.json 내용 dict.
    Raises:
        FileNotFoundError: book.json이 없을 때.
        ValueError: JSON 파싱 실패/최상위가 객체가 아닐 때.
    """
    path = _book_dir(book_slug) / "book.json"
    if not path.is_file():
        raise FileNotFoundError(f"book.json을 찾을 수 없습니다: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"book.json 파싱 실패: {path} ({e})") from e
    if not isinstance(data, dict):
        raise ValueError(f"book.json 최상위가 객체가 아닙니다: {path}")
    return data


def load_original_text(book_slug: str) -> str:
    """원작 원문 텍스트 로드 기능.  # (Load original full text)

    /data/original/<slug>.txt 를 utf-8로 읽어 돌려준다(원작 보기용). 슬러그는 bookId와 동일.

    Args:
        book_slug: 책 슬러그(=bookId).
    Returns:
        원문 텍스트 전체.
    Raises:
        FileNotFoundError: 원문 txt가 없을 때.
    """
    path = _data_root() / "original" / f"{book_slug}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"원문을 찾을 수 없습니다: {path}")
    return path.read_text(encoding="utf-8")


def cover_file(book_slug: str) -> Path | None:
    """표지 이미지 파일 경로 반환 기능.  # (Resolve cover image file)

    /data/images/<slug>.{webp,png,jpg,jpeg} 중 먼저 발견되는 것을 돌려준다(기존 webp/신규 webp 모두 대응).
    라우터가 이 파일의 이름·수정시각으로 coverImageUrl(상대경로 + 버전 토큰)을 만든다. 없으면 None.

    Args:
        book_slug: 책 슬러그.
    Returns:
        표지 파일 Path, 없으면 None.
    """
    images_dir = _data_root() / _IMAGES_DIRNAME
    for ext in _COVER_EXTS:
        p = images_dir / f"{book_slug}.{ext}"
        if p.is_file():
            return p
    return None


def original_file(book_slug: str) -> Path | None:
    """원문(전문) txt 파일 경로 반환 기능.  # (Resolve original full-text file)

    DATA_DIR/original/<slug>.txt 가 있으면 그 경로를, 없으면 None을 돌려준다(보관·참고용 원문).
    읽기 화면용 GET /books/{id}/original 이 이 파일을 읽는다. 경로는 pathlib + DATA_DIR 상대(비하드코딩).

    Args:
        book_slug: 책 슬러그.
    Returns:
        원문 txt Path, 없으면 None.
    """
    p = _data_root() / _ORIGINAL_DIRNAME / f"{book_slug}.txt"
    return p if p.is_file() else None


def _parse_original_paragraphs(raw: str) -> list[str]:
    """원문 txt → 문단 리스트 변환 기능.  # (Split original text into paragraphs)

    원문 txt에는 문단 사이에 '순번만 있는 줄'(예: "2", "3")과 공백 줄이 섞여 있다. 읽기 화면이
    깔끔하도록 순번 줄과 빈 줄을 걸러내고 실제 본문 줄만 문단으로 모은다(원문 글자는 보존).

    Args:
        raw: 원문 txt 전체 문자열.
    Returns:
        본문 문단 리스트(순번/공백 줄 제외).
    """
    paragraphs: list[str] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.isdigit():  # 공백 줄·순번 전용 줄은 건너뜀
            continue
        paragraphs.append(text)
    return paragraphs


def load_original(book_slug: str) -> dict:
    """원문(전문) 읽기 데이터 반환 기능.  # (Load original full text for reading)

    DATA_DIR/original/<slug>.txt 를 utf-8로 읽어 읽기 화면용으로 돌려준다. 순번/공백 줄을 걸러
    문단 배열(paragraphs)과 합본 텍스트(text)를 함께 준다(app은 문단 단위로 페이지네이션). 제목은
    book.json이 있으면 그 title을, 없으면 첫 문단/슬러그를 쓴다.

    Args:
        book_slug: 책 슬러그.
    Returns:
        {bookId, title, paragraphs[], text} dict.
    Raises:
        FileNotFoundError: original/<slug>.txt 가 없을 때.
    """
    path = original_file(book_slug)
    if path is None:
        raise FileNotFoundError(f"원문을 찾을 수 없습니다: {book_slug}")
    raw = path.read_text(encoding="utf-8")
    paragraphs = _parse_original_paragraphs(raw)

    # 제목: book.json title 우선(없거나 로드 실패면 첫 문단 → 슬러그 폴백). 원문 표시 일관성.
    title = book_slug
    try:
        title = load_book(book_slug).get("title") or title
    except (FileNotFoundError, ValueError):
        if paragraphs:
            title = paragraphs[0]

    return {
        "bookId": book_slug,
        "title": title,
        "paragraphs": paragraphs,
        "text": "\n\n".join(paragraphs),
    }


def _meta_from_book(slug: str, data: dict) -> dict:
    """book.json → 목록 메타(dict) 변환 기능.  # (book.json → list meta)

    GET /books 항목용 메타를 추린다. coverImageUrl은 라우터가 정적 상대경로로 채운다.

    Args:
        slug: 책 슬러그.
        data: book.json dict.
    Returns:
        BookSummary 구성용 dict(coverImageUrl 제외).
    """
    return {
        "bookId": slug,
        "title": data.get("title") or slug,
        "emoji": data.get("emoji"),
        "author": data.get("author"),
        "era": data.get("era"),
        "difficulty": data.get("difficulty"),
        "tags": data.get("tags", []),
        "coverColor": data.get("coverColor"),
        "shortDescription": data.get("shortDescription"),
        "sceneCount": len(data.get("scenes", [])),
    }


def list_books_meta() -> list[dict]:
    """책 목록 메타 반환 기능.  # (List books with meta)

    DATA_DIR 하위 각 책의 book.json을 읽어 목록/메타를 만든다. **한 권의 로드 실패가 목록
    전체를 죽이지 않도록** 예외는 경고로 남기고 그 책만 건너뛴다(견고성).

    Returns:
        책별 메타 dict 리스트(coverImageUrl 제외 — 라우터가 채움).
    """
    metas: list[dict] = []
    for slug in list_book_slugs():
        try:
            data = load_book(slug)
        except (FileNotFoundError, ValueError) as e:
            logger.warning("book.json 로드 실패 → 목록에서 제외: %s (%s)", slug, e)
            continue
        metas.append(_meta_from_book(slug, data))
    return metas


def load_book_detail(book_slug: str) -> dict:
    """책 상세(요약+장면+캐릭터) 반환 기능.  # (Load book detail for scene UI)

    GET /books/{bookId}용. book.json의 characters/scenes를 장면 선택 UI에 맞게 추린다
    (segments는 제외 — 가벼움; 창작 시 서버가 sceneIds로 직접 읽음). coverImageUrl은 라우터가 채움.

    Args:
        book_slug: 책 슬러그.
    Returns:
        {bookId, title, emoji, summary, coverColor, characters[], scenes[]} dict.
    Raises:
        FileNotFoundError: book.json이 없을 때.
    """
    data = load_book(book_slug)

    characters = [
        {
            "characterId": c.get("characterId") or f"char-{i + 1}",
            "name": c.get("name") or "",
            "role": c.get("role"),
            "description": c.get("description"),
        }
        for i, c in enumerate(data.get("characters", []))
        if isinstance(c, dict)
    ]

    scenes = [
        {
            "sceneId": s.get("sceneId") or f"scene-{i + 1}",
            "order": s.get("order", i + 1),
            "emoji": s.get("emoji"),
            "title": s.get("title") or "",
            "description": s.get("description"),
        }
        for i, s in enumerate(data.get("scenes", []))
        if isinstance(s, dict)
    ]

    return {
        "bookId": book_slug,
        "title": data.get("title") or book_slug,
        "emoji": data.get("emoji"),
        "summary": data.get("summary"),
        "coverColor": data.get("coverColor"),
        "characters": characters,
        "scenes": scenes,
    }


def _characters_for_prompt(data: dict) -> list[dict]:
    """프롬프트용 인물 목록(이름/역할/설명) 추출 기능.  # (Characters for prompt)

    Claude가 인물 맥락을 잡도록 name/role/description만 넘긴다(내부 char-N id는 불필요).
    """
    out: list[dict] = []
    for c in data.get("characters", []):
        if isinstance(c, dict):
            out.append(
                {"name": c.get("name"), "role": c.get("role"), "description": c.get("description")}
            )
    return out


def get_full_source(book_slug: str) -> dict:
    """전체 줄거리(full) 창작 소스 반환 기능.  # (Full-plot creation source)

    full 스코프 창작 소스 = book.json의 summary(3문단 흐름+교훈) + 제목 + 인물 맥락.
    원작 txt 전체 대신 요약을 써서 입력 토큰을 줄인다.

    Args:
        book_slug: 책 슬러그.
    Returns:
        {scope:"full", title, summary, characters[]}.
    Raises:
        FileNotFoundError: book.json이 없을 때.
    """
    data = load_book(book_slug)
    return {
        "scope": "full",
        "title": data.get("title") or book_slug,
        "summary": data.get("summary") or "",
        "characters": _characters_for_prompt(data),
    }


def get_scene_source(book_slug: str, scene_ids: list[str]) -> dict:
    """장면별 선택(scene) 창작 소스 반환 기능.  # (Scene-selection creation source)

    요청 sceneIds에 해당하는 book.json scenes(segments 포함)를 읽어 소스로 만든다(서버=단일 출처).
    선택 순서가 아니라 book.json의 scene order대로 정렬해 일관성을 유지한다.

    Args:
        book_slug: 책 슬러그.
        scene_ids: 선택한 장면 id 목록(예: ["scene-1","scene-3"]).
    Returns:
        {scope:"scene", title, characters[], scenes:[{sceneId,title,description,segments}]}.
    Raises:
        FileNotFoundError: book.json이 없을 때.
        ValueError: sceneIds 중 book.json에 없는 id가 있을 때(→ 라우터가 명확한 에러로 변환).
    """
    data = load_book(book_slug)
    by_id = {s.get("sceneId"): s for s in data.get("scenes", []) if isinstance(s, dict)}

    unknown = [sid for sid in scene_ids if sid not in by_id]
    if unknown:
        raise ValueError(f"존재하지 않는 sceneId: {unknown} (book={book_slug})")

    selected = [by_id[sid] for sid in by_id if sid in set(scene_ids)]  # book.json 순서 유지
    scenes = [
        {
            "sceneId": s.get("sceneId"),
            "title": s.get("title"),
            "description": s.get("description"),
            "segments": s.get("segments", []),
        }
        for s in selected
    ]
    return {
        "scope": "scene",
        "title": data.get("title") or book_slug,
        "characters": _characters_for_prompt(data),
        "scenes": scenes,
    }


def get_source_block(book_slug: str, scope: str, scene_ids: list[str]) -> dict:
    """창작 결과의 '원작 정보'용 source 블록 반환 기능.  # (Source block for the result)

    Flutter '원작 정보' 탭(원천 자료 + 사용한 장면/요약 + 출처 문구)에 필요한 데이터를 모은다.
    창작물이 자급자족하도록 /create 결과에 embed 한다(SQLite 저장/오프라인 안전).
    coverImageUrl(상대경로+버전)은 라우터가 채운다. segments는 넣지 않는다(가벼움).

    Args:
        book_slug: 책 슬러그.
        scope: "full" | "scene".
        scene_ids: scope=="scene"일 때 선택한 장면 id(book.json 순서로 정렬해 반환).
    Returns:
        source 블록 dict(coverImageUrl 제외 — 라우터가 채움).
    Raises:
        FileNotFoundError: book.json이 없을 때.
    """
    data = load_book(book_slug)
    title = data.get("title") or book_slug

    block: dict = {
        "bookId": book_slug,
        "title": title,
        "classification": list(SOURCE_INFO["classification"]),
        "coverColor": data.get("coverColor"),
        "provider": SOURCE_INFO["provider"],
        "license": SOURCE_INFO["license"],
        "attribution": SOURCE_INFO["attribution_template"].format(title=title),
        "scope": scope,
        "scenesUsed": [],
        "summary": None,
    }

    if scope == "scene":
        wanted = set(scene_ids)
        block["scenesUsed"] = [
            {
                "sceneId": s.get("sceneId"),
                "order": s.get("order"),
                "emoji": s.get("emoji"),
                "title": s.get("title"),
                "description": s.get("description"),
            }
            for s in data.get("scenes", [])
            if isinstance(s, dict) and s.get("sceneId") in wanted
        ]
    else:  # full → 사용한 장면 자리에 3문단 요약
        block["summary"] = data.get("summary")

    return block
