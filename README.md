# 고전문학 재창작 서비스 — Backend (`service-data-pipeline`)

**Classic Literature Re-creation Service — FastAPI Backend**

한국 고전문학(흥부전, 홍길동전 등)의 줄거리·장면을 바탕으로, Claude API를 이용해 대화극(dialogue play)
또는 오디오극(audio play) 형태의 **새로운 창작물**을 생성하는 FastAPI 백엔드입니다.
별도 저장소의 Flutter 앱이 이 서버에서 원작 데이터와 창작 결과를 동기화해 보여줍니다.

A FastAPI backend that turns a Korean classic-literature source (its plot summary or selected scenes) into a
brand-new **dialogue play or audio play**, generated via the Claude API. A companion Flutter app (separate
repo) syncs book data and creation results from this server.

---

## 목차 / Table of Contents

- [개요 / Overview](#개요--overview)
- [핵심 기능 / Key Features](#핵심-기능--key-features)
- [핵심 흐름 / Core Flow](#핵심-흐름--core-flow)
- [기술 스택 / Tech Stack](#기술-스택--tech-stack)
- [디렉토리 구조 / Project Structure](#디렉토리-구조--project-structure)
- [시작하기 / Getting Started](#시작하기--getting-started)
- [환경 변수 / Environment Variables](#환경-변수--environment-variables)
- [API 개요 / API Overview](#api-개요--api-overview)
- [지원 원작 / Available Books](#지원-원작--available-books)
- [테스트 / Testing](#테스트--testing)
- [프로젝트 상태 / Project Status](#프로젝트-상태--project-status)
- [라이선스 / License](#라이선스--license)

---

## 개요 / Overview

**한국어.** 사용자는 Flutter 앱에서 원작(예: 《흥부전》)과 창작 모드(대화극/오디오극), 난이도, 줄거리
범위(전체 줄거리 또는 선택 장면), 추가 아이디어를 입력합니다. 이 서버는 해당 원작 데이터(`book.json`)를
읽어 프롬프트를 구성하고, Claude API를 호출해 새로운 극본을 생성합니다. 오디오극 모드에서는 각 대사를
Google Cloud TTS로 합성해 하나의 MP3로 병합하고, 문장별 재생 구간(`timepoints`)을 계산합니다. 결과는
SSE(Server-Sent Events)로 진행 상황과 함께 스트리밍되며, 창작마다 고유한 표지 이미지와 어려운 단어 풀이도
함께 생성됩니다.

**English.** From the Flutter client, a user picks an original work (e.g. *Heungbujeon*), a creation mode
(dialogue or audio play), a difficulty level, a scope (full plot summary or selected scenes), and an optional
free-text idea. This server loads the matching `book.json`, builds a prompt, and calls the Claude API to
generate a new script. In audio mode, each line is synthesized with Google Cloud TTS, merged into a single
MP3, and given per-line playback `timepoints`. Progress and the final result stream back over SSE, along with
a per-creation cover image and an embedded glossary of difficult words.

## 핵심 기능 / Key Features

- **창작 생성 (`POST /create`)** — 대화극/오디오극, 4단계 난이도, 전체/장면 범위, 자유 아이디어 텍스트를
  받아 Claude로 새 극본을 생성. Structured Outputs로 JSON 형식을 보장하고, `formatter`가 id 중복·order
  누락·화자 무결성을 검증/복구합니다.
  → Generates a new script via Claude with mode/difficulty/scope/idea-text parameters; structured outputs +
  server-side repair guarantee schema integrity (no duplicate ids, sequential `order`, valid speakers).
- **백그라운드 작업 + 재연결** — `/create`는 연결이 끊겨도 계속 진행되는 백그라운드 작업(`jobId`)으로
  실행됩니다. `GET /create/{jobId}/events`(SSE 재연결) 또는 `GET /create/{jobId}`(폴링)로 다시 결과를
  받을 수 있습니다.
  → `/create` runs as a connection-independent background job; disconnected clients can reconnect via SSE
  replay or polling.
- **오디오극 합성** — 캐릭터별 `voiceProfile`(Google Neural2 음성)로 대사를 병렬 합성 후 `ffmpeg`로 무손실
  병합, `mutagen`으로 문장별 재생 구간을 계산합니다.
  → Per-character voice synthesis (Google Cloud TTS Neural2), lossless `ffmpeg` merge into one MP3, and
  `mutagen`-derived per-line timepoints.
- **자동 표지 생성** — 원작 표지는 서버 시작 시 + `book.json` 변경 감지(watchdog)로 자동 생성되고,
  창작물마다 제목 없는 고유 표지가 생성됩니다(OpenAI 이미지).
  → Book covers auto-generate on startup and on `book.json` changes (watchdog); every creation also gets its
  own title-free cover via OpenAI image generation.
- **어려운 단어 풀이** — 창작 결과에 어려운 단어 풀이가 임베드되며(국립국어원 사전 + Claude), 임베드에
  없는 단어는 `POST /vocab`로 온디맨드 조회할 수 있습니다.
  → A difficult-word glossary is embedded in every creation result (KRDict + Claude); `POST /vocab` is the
  on-tap fallback for words not covered.
  Free-text word rewrite for a single line via `POST /rewrite-line` (dialogue result "AI로 바꾸기").
- **원작 데이터 동기화** — Flutter 앱은 빈 상태로 시작해 `GET /books` / `GET /books/{id}`로 모든 원작
  데이터를 동기화하고 SQLite에 저장합니다.
  → The Flutter client starts empty and syncs all book data from `GET /books` / `GET /books/{id}`, caching it
  in local SQLite.

## 핵심 흐름 / Core Flow

```
Flutter 요청 (파라미터)                     Flutter는 빈 상태로 시작 → GET /books 로 목록/표지 동기화
   → 원작 로드                              full: book.json summary | scene: sceneIds로 지정한 장면 segments
   → 프롬프트 구성 (params + source + ideaText)
   → Claude API 호출 (structured outputs)
   → 모드별 JSON으로 정제 (dialogue / audio)
   → (audio) 대사별 합성 → 1개 MP3로 병합 → timepoints 계산
   → SSE로 Flutter에 응답 (progress 이벤트 → result 이벤트)
```

오디오 결과는 `audio = { audioUrl(병합 MP3 1개), totalDurationMs, timepoints[] }` 형태로 내려가며, Flutter는
문장을 탭하면 `seek(startMs) → play()`로 해당 구간부터 재생합니다.

## 기술 스택 / Tech Stack

| 영역 | 사용 기술 |
|---|---|
| 웹 프레임워크 | FastAPI + Uvicorn, Pydantic v2 / pydantic-settings |
| 창작 생성 | [Anthropic Claude API](https://docs.claude.com) (`claude-sonnet-4-6`, structured outputs) |
| 음성 합성 (오디오극) | Google Cloud Text-to-Speech (Neural2) + `ffmpeg`(병합) + `mutagen`(길이 계산) |
| 표지 이미지 | OpenAI 이미지 생성 → Pillow로 WebP 변환 |
| 단어 풀이 | 국립국어원 한국어기초사전(KRDict) Open API + Claude |
| 데이터 변경 감지 | `watchdog` (book.json 드롭/수정 시 표지 자동 생성) |
| 진행 상황 전달 | Server-Sent Events (SSE) + 백그라운드 job 관리 |
| 대상 배포 환경 | Python 3.11, macOS(dev, conda) ↔ Raspberry Pi 4(prod, venv) 동일 동작 |

## 디렉토리 구조 / Project Structure

```
.
├── requirements.txt           # 고정 버전 의존성 (ARM64/RPi4 호환 확인됨)
├── scripts/                   # 개발용 데이터 도구 (book.json 빌드, 표지 일괄 생성)
├── app/
│   ├── main.py                 # FastAPI 앱, CORS, 정적 마운트(/audio,/images,/creation-covers), 라우터 등록
│   ├── config.py                # 환경변수 기반 설정 (유일한 env read 지점)
│   ├── models/                  # 요청/응답 pydantic 스키마
│   ├── routers/                 # creation(/books, /create), vocab(/vocab), rewrite(/rewrite-line)
│   ├── services/                 # data_loader, prompt_builder, claude_client, formatter,
│   │                             # tts_client/voice_map, cover_service/cover_watcher, vocab_service, job_manager 등
│   ├── data/                     # 원작 정적 데이터 (book.json, 표지, 원문 아카이브)
│   ├── audio/                    # 합성/병합 MP3 캐시 (런타임 생성, git 제외)
│   └── creation_covers/          # 창작물별 표지 (런타임 생성, git 제외)
└── tests/
```

## 시작하기 / Getting Started

### 요구 사항 / Prerequisites
- Python **3.11** (`.python-version` 고정)
- 시스템 의존성 **`ffmpeg`** (오디오극 MP3 병합에 필요; pip 패키지 아님)
- API 키: `ANTHROPIC_API_KEY`(필수), `GOOGLE_APPLICATION_CREDENTIALS`(오디오극 TTS), `OPENAI_API_KEY`(표지
  생성), `KRDICT_API_KEY`(단어 풀이) — 없어도 서버는 뜨지만 해당 기능만 실패합니다.

### 설치 / Install

```bash
# macOS (개발) — conda 권장
conda create -n classic-fastapi python=3.11
conda activate classic-fastapi
pip install -r requirements.txt

# Raspberry Pi 4 (배포) — venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ffmpeg 설치 (둘 다 필요)
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Raspberry Pi / Debian
```

### 환경 설정 / Configure

```bash
cp .env.example .env
# .env를 열어 ANTHROPIC_API_KEY 등 실제 값을 채워 넣습니다 (git에 커밋되지 않습니다).
```

### 실행 / Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload   # 개발
uvicorn app.main:app --host 0.0.0.0 --port 8000             # 배포 (RPi)
```

휴대폰/태블릿(Flutter 앱)에서 접근하려면 반드시 `0.0.0.0`에 바인딩해야 합니다. 확인:
`curl http://localhost:8000/health`

## 환경 변수 / Environment Variables

전체 목록/기본값은 [`.env.example`](.env.example) 템플릿을 참고하세요.

| 변수 | 기본값 | 필수 여부 | 설명 |
|---|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8000` | - | 바인드 호스트/포트 |
| `ANTHROPIC_API_KEY` | (없음) | **필수** | Claude API 키 |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | - | 사용 모델 |
| `DATA_DIR` | `app/data` | - | 원작 데이터 루트 |
| `CORS_ORIGINS` | `*` | - | 허용 오리진 (콤마 구분) |
| `GOOGLE_APPLICATION_CREDENTIALS` | (없음) | 오디오극에 필요 | Google Cloud 서비스계정 JSON 경로 |
| `AUDIO_DIR` | `app/audio` | - | 합성 MP3 캐시/서빙 루트 |
| `OPENAI_API_KEY` | (없음) | 표지 생성에 필요 | 표지 이미지 생성 키 |
| `OPENAI_IMAGE_MODEL/SIZE/QUALITY` | `gpt-image-2` / `800x1120` / `low` | - | 표지 생성 옵션 |
| `CREATION_COVER_DIR` | `app/creation_covers` | - | 창작물별 표지 저장 루트 |
| `KRDICT_API_KEY` | (없음) | 단어 풀이에 필요 | 국립국어원 KRDict Open API 키 |
| `JOB_TTL_SECONDS` | `3600` | - | 완료/오류 작업을 `jobId`로 보관하는 시간(초) |

> 키가 비어 있어도 서버는 정상 기동하며, 해당 기능(오디오 합성/표지 생성/단어 풀이)만 명확한 에러로
> 실패합니다. 실제 키 값은 절대 커밋하지 마세요.

## API 개요 / API Overview

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/health` | 헬스체크 |
| `GET` | `/books` | 원작 목록 (표지·요약 포함) |
| `GET` | `/books/{bookId}` | 원작 상세 (등장인물·장면 목록) |
| `GET` | `/books/{bookId}/original` | 원작 원문 아카이브 조회 |
| `POST` | `/create` | 창작 요청 → SSE(progress/result), 백그라운드 `jobId` 발급 |
| `GET` | `/create/{jobId}` | 창작 작업 상태/결과 폴링 |
| `GET` | `/create/{jobId}/events` | 끊긴 SSE 연결 재구독(스냅샷 재생 + 실시간 이벤트) |
| `POST` | `/vocab` | 단어 풀이 온디맨드 조회 (임베드 glossary에 없는 단어) |
| `POST` | `/rewrite-line` | 대사 한 줄 AI로 다시 쓰기 |
| `GET` | `/audio/{file}` | 병합된 오디오극 MP3 정적 서빙 |
| `GET` | `/images/{slug}.webp` | 원작 표지 정적 서빙 |
| `GET` | `/creation-covers/{file}` | 창작물별 표지 정적 서빙 |

`POST /create` 요청 바디는 `{bookId, mode(dialogue|audio), difficulty(children|korean_learner|youth|original),
scope(full|scene), sceneIds[], ideaText}` 형태이며, 진행 상황은 SSE로 `{type: job|progress|result|error}`
이벤트를 스트리밍합니다. 자세한 필드/스키마는 `app/models/request.py` · `app/models/response.py`를
참고하세요.

## 지원 원작 / Available Books

`app/data/`에 현재 6편의 고전소설 데이터(`book.json`)가 등록되어 있습니다. 새 원작 추가는 폴더에
`book.json`을 놓기만 하면 자동으로 목록에 반영됩니다.

| slug | 제목 | Title |
|---|---|---|
| `bakssi_jeon` | 박씨전 🦋 | Story of Lady Bak |
| `heosaeng_jeon` | 허생전 💰 | The Tale of Heosaeng |
| `hong_gildong_jeon` | 홍길동전 ⚔️ | The Tale of Hong Gildong |
| `hongbu_jeon` | 흥부전 🐦 | The Tale of Heungbu |
| `kongjwi_patjwi_jeon` | 콩쥐팥쥐전 🌸 | The Tale of Kongjwi and Patjwi |
| `tokki_jeon` | 토끼전 🐰 | The Tale of the Rabbit |

## 테스트 / Testing

```bash
pytest
```

> 현재 `tests/`에는 표지 생성 서비스에 대한 최소 테스트만 있습니다. 커버리지는 점진적으로 늘려갈
> 예정입니다.

## 프로젝트 상태 / Project Status

버전 `0.1.0`, 활발히 개발 중입니다.

This is an actively evolving `0.1.0` project.

## 라이선스 / License

[MIT License](LICENSE) — 자유롭게 사용·수정·배포할 수 있으며, 원저작권 및 라이선스 고지를 유지해야
합니다. 오디오극 음성(Google Cloud TTS)·표지 이미지(OpenAI) 등 외부 API로 생성되는 산출물은 각 제공자의
약관을 별도로 따릅니다. 원작 고전소설 데이터는 공공누리 제1유형을 따릅니다.

Licensed under the [MIT License](LICENSE). Generated media (TTS audio, AI-generated covers) remain subject to
their respective providers' terms; the public-domain classic-literature source texts follow 공공누리 Type 1.
