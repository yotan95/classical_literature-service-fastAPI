"""표지 이미지 프롬프트 빌더 모듈.  # (Cover image prompt builder)

book.json 메타(제목/시대/태그/요약/인물)를 바탕으로 '원작 책 표지' 이미지 생성 프롬프트를
만든다. 표지는 책마다 생성해 정적 파일(app/data/images/<slug>.webp)로 두고 GET /books·result.source의
coverImageUrl로 제공한다(cover_service / scripts/generate_covers.py). POST /create 결과마다 만드는
창작물 고유 표지 프롬프트도 함께 담당한다.
관련: app/services/cover_service.py, app/services/creation_cover_service.py.
"""

from app.models.response import CreationResult


def _brief(text: str | None, limit: int) -> str:
    """프롬프트에 넣을 텍스트를 짧게 줄이는 기능.  # (Trim text for prompts)

    이미지 프롬프트가 과도하게 길어지지 않도록 줄바꿈을 공백으로 바꾸고 글자 수를 제한한다.
    관련: build_book_cover_prompt, build_creation_cover_prompt.

    Args:
        text: 원문 문자열.
        limit: 최대 글자 수.
    Returns:
        정리된 짧은 문자열.
    """
    clean = (text or "").replace("\n", " ").strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "..."


def build_book_cover_prompt(book: dict) -> str:
    """원작 표지 이미지 프롬프트 생성 기능.  # (Build a book cover prompt)

    창작 결과가 아니라 '원작(고전)'의 표지를 만든다. 텍스트가 전혀 없는 세로형 표지로,
    모바일 썸네일에서도 읽히도록 상징적 한 장면을 그린다.

    Args:
        book: book.json dict(title/era/tags/shortDescription/summary/characters).
    Returns:
        이미지 생성 프롬프트 문자열.
    """
    characters = ", ".join(
        c.get("name", "") for c in book.get("characters", []) if c.get("name")
    )
    tags = ", ".join(book.get("tags", []))
    # summary는 길 수 있으므로 앞부분만 분위기 참고용으로 넣는다.
    summary_brief = _brief(book.get("summary"), 400)

    return f"""
Create one title-free cover image for a Korean classic literature app.

This is a cover for the original classic work below. Capture its overall mood and
one iconic symbolic moment.

Work (for theme only, do NOT render the title text):
{book.get("title")}

Era/setting: {book.get("era")}
Themes: {tags}
One-line: {book.get("shortDescription")}
Summary (mood reference): {summary_brief}
Key characters: {characters}

Image requirements:
- Title-free cover image.
- Actual API size will be 800x1120, strict 5:7 vertical.
- One clear symbolic scene that represents the work's spirit and main conflict.
- Readable as a small mobile thumbnail.

Style:
Polished Korean traditional storybook illustration, soft ink-and-color painting,
hanji paper texture, Joseon-era atmosphere when appropriate.

Strict no-text rule:
No title, no Korean letters, no English letters, no captions, no speech bubbles,
no signs, no readable book text, no logos, no watermark.
""".strip()


def build_creation_cover_prompt(result: CreationResult, book: dict) -> str:
    """창작물 고유 표지 이미지 프롬프트 생성 기능.  # (Build a per-creation cover prompt)

    원작 전체 표지가 아니라 이번 /create 결과의 제목·intro·장면·대사에서 가장 잘 읽히는
    상징적 한 장면을 그리도록 프롬프트를 만든다. 실제 제목 글자는 이미지에 넣지 않는다.
    관련: app/services/creation_cover_service.py.

    Args:
        result: 생성이 끝난 창작 결과.
        book: 원작 book.json dict(시대/원작 제목 참고용).
    Returns:
        이미지 생성 프롬프트 문자열.
    """
    tags = ", ".join(result.tags)
    characters = ", ".join(c.name for c in result.characters if c.name)
    scene_titles = "; ".join(s.title for s in result.scenes if s.title)

    line_snippets: list[str] = []
    for scene in result.scenes:
        for line in scene.lines:
            text = line.text.strip()
            if text:
                speaker = line.speakerName.strip() or "Narrator"
                direction = f" ({line.direction.strip()})" if line.direction else ""
                line_snippets.append(f"{speaker}{direction}: {text}")
            if len(line_snippets) >= 10:
                break
        if len(line_snippets) >= 10:
            break
    dialogue_reference = "\n".join(f"- {_brief(line, 140)}" for line in line_snippets)

    return f"""
Create one title-free cover image for a newly generated Korean classic-inspired creative work.

This is NOT the generic cover for the original classic. It must represent this generated
creative work's own mood, title, characters, and dramatic turning point.

Generated work (for theme only, do NOT render the title text):
{result.title}

Original source context: {book.get("title")} / {book.get("era")}
Mode and level: {result.mode.value}, {result.difficulty.value}
Tags: {tags}
Intro: {_brief(result.intro, 320)}
Main characters: {characters}
Scene titles: {scene_titles}
Representative lines:
{dialogue_reference}

Image requirements:
- Title-free mobile cover image for this generated work.
- Actual API size will be 800x1120, strict 5:7 vertical.
- Choose ONE symbolic dramatic moment from the generated scenes, not a collage.
- Make the focal figures large and readable as a small mobile thumbnail.
- Use only visual details supported by the generated work above.

Style:
Polished Korean traditional storybook illustration, soft ink-and-color painting,
hanji paper texture, warm theatrical lighting, emotionally clear character poses.

Strict no-text rule:
No title, no Korean letters, no English letters, no captions, no speech bubbles,
no signs, no readable book text, no logos, no watermark.
""".strip()
