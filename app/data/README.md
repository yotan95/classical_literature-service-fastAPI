# app/data — 정적 데이터

서버의 **단일 출처**는 책마다 하나의 `book.json`이다.

```
app/data/
  original/<slug>.txt     # 원작 원문(전문) — 읽기 화면용으로 GET /books/{id}/original 로 서빙. 프롬프트 기본 소스는 아님
  <slug>/book.json        # 단일 출처: 메타 + characters + scenes(+segments) + summary(3문단)
  images/<slug>.webp      # 책 표지(webp, /images 로 서빙; coverImageUrl)
```

- 슬러그(폴더명)는 영문 **언더스코어**(예: `hong_gildong_jeon`).
- `book.json`을 가진 폴더만 책으로 인식 → `GET /books`에 자동 노출(`original`/`images`·잡폴더 제외).
- 새 책 = `book.json` 드롭(또는 `build_books.py`) → 서버가 표지 자동 생성(시작 시 + watchdog) → Flutter refresh 반영.
- 생성 도구: `scripts/build_books.py`(book.json 빌드), `scripts/generate_covers.py`(표지 생성).
- `full` 창작 소스 = `book.json`의 `summary`, `scene` 창작 소스 = 선택 `sceneIds`의 `segments`.
- 원문 읽기: `GET /books/{bookId}/original` → `original/<slug>.txt`를 순번/공백 줄 제거 후 `{paragraphs[], text}`로 반환(없으면 404).
