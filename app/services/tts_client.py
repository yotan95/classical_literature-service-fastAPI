"""Google Cloud TTS 합성 + 단일 MP3 병합 모듈.  # (Google TTS synth + merge)

오디오극의 각 line.text를 화자(character)의 voiceProfile에 맞는
Google Cloud TTS(Neural2, ko-KR) 음성으로 합성한 뒤, **하나의 MP3로 병합**하고 라인별
재생 위치(timepoints, ms)를 계산해 result.audio에 채운다.

설계:
- 라인별 합성은 병렬(세마포어) + 해시 캐시(같은 대사·음성은 재합성 안 함).
- 병합은 **재인코딩 없이 ffmpeg concat -c copy**로 붙인다(RPi4에서 1~3초; 재인코딩은 금지).
  → Google이 인코딩을 마친 MP3를 서버는 '붙이기'만 한다. 모든 파트는 동일 sample rate로 합성.
- 라인 길이는 mutagen(순수 파이썬)으로 읽어 누적합 → timepoints(startMs/endMs).
- 병합 MP3는 파트 목록 해시로 캐시(같은 창작 재요청 시 재병합 안 함).

인증은 GOOGLE_APPLICATION_CREDENTIALS(서비스계정 JSON)만 사용한다. 키 없음/합성 실패/ffmpeg
없음은 TtsError로 감싸 호출부(SSE)가 error 이벤트로 surface 한다.
"""

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.config import get_settings
from app.models.request import Mode
from app.models.response import AudioTrack, CreationResult, Timepoint
from app.services import voice_map

logger = logging.getLogger(__name__)

# 라인 병렬 합성 동시 실행 상한(I/O 바운드). 필요시 조정.
_TTS_CONCURRENCY = 8

# 모든 파트를 동일 sample rate로 합성 → ffmpeg -c copy 병합이 안전(틈/클릭 최소화).
_SAMPLE_RATE = 24000

# 지연 임포트한 google 클라이언트 모듈/싱글턴(설치·인증 문제를 합성 시점에만 노출).
_tts = None  # google.cloud.texttospeech 모듈
_client = None  # TextToSpeechAsyncClient 싱글턴
_client_lock = asyncio.Lock()


class TtsError(Exception):
    """TTS 합성/병합 실패 예외.  # (TTS synthesis/merge failure)

    인증 없음/합성 실패/ffmpeg 없음 등을 사람이 읽을 수 있는 메시지로 감싼다.
    """


def _load_tts_module():
    """google TTS 모듈 지연 로드 기능.  # (Lazy-import google TTS module)

    의존성 미설치 시 호출 시점에 명확한 TtsError를 내기 위해 지연 임포트한다.

    Returns:
        google.cloud.texttospeech 모듈.
    Raises:
        TtsError: 패키지가 설치되어 있지 않을 때.
    """
    global _tts
    if _tts is None:
        try:
            from google.cloud import texttospeech as tts  # 무거운 import → 지연
        except ImportError as e:
            raise TtsError(
                "google-cloud-texttospeech가 설치되지 않았습니다(requirements.txt 설치 필요)."
            ) from e
        _tts = tts
    return _tts


async def _get_client():
    """TTS 비동기 클라이언트 싱글턴 반환 기능.  # (Cached async TTS client)

    서비스계정 키 경로(설정)를 표준 env로 노출한 뒤 클라이언트를 한 번만 생성한다(채널 재사용).
    인증 정보가 없으면 명확한 TtsError로 실패한다.

    Returns:
        TextToSpeechAsyncClient 싱글턴.
    Raises:
        TtsError: 인증 정보가 없거나 클라이언트 생성에 실패할 때.
    """
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            tts = _load_tts_module()
            settings = get_settings()
            if settings.google_application_credentials:
                os.environ.setdefault(
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    settings.google_application_credentials,
                )
            try:
                _client = tts.TextToSpeechAsyncClient()
            except Exception as e:  # DefaultCredentialsError 등 — 인증/구성 문제
                raise TtsError(
                    "Google Cloud 인증에 실패했습니다"
                    "(GOOGLE_APPLICATION_CREDENTIALS에 서비스계정 JSON 경로 설정 필요)."
                ) from e
    return _client


def _cache_name(text: str, voice: str, rate: float, pitch: float) -> str:
    """라인 캐시 파일명(해시) 생성 기능.  # (Per-line cache filename)

    text+음성설정이 같으면 같은 파일명 → 재합성 없이 재사용.

    Args:
        text: 대사 본문.
        voice: Google 음성명.
        rate: 말속도.
        pitch: 반음.
    Returns:
        "line_<sha256[:32]>.mp3" 형태의 파일명.
    """
    key = f"{voice_map.LANGUAGE_CODE}|{_SAMPLE_RATE}|{voice}|{rate}|{pitch}|{text}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return f"line_{digest}.mp3"


def _mp3_duration_ms(path: Path) -> int:
    """MP3 재생 길이(ms) 반환 기능.  # (MP3 duration via mutagen)

    mutagen(순수 파이썬)으로 길이를 읽어 timepoints 계산에 쓴다(ffmpeg/ffprobe 불필요).

    Args:
        path: MP3 경로.
    Returns:
        길이(ms, 반올림).
    Raises:
        TtsError: mutagen 미설치 또는 읽기 실패 시.
    """
    try:
        from mutagen.mp3 import MP3  # 가벼우나 명확한 에러 위해 지연 임포트
    except ImportError as e:
        raise TtsError("mutagen이 설치되지 않았습니다(requirements.txt 설치 필요).") from e
    try:
        return round(MP3(str(path)).info.length * 1000)
    except Exception as e:
        raise TtsError(f"MP3 길이 계산 실패: {path} ({e})") from e


async def _synthesize_one(
    client, audio_dir: Path, sem: asyncio.Semaphore, text: str, cfg: dict
) -> str:
    """단일 라인 합성/캐시 기능.  # (Synthesize or reuse one line MP3)

    캐시에 있으면 합성을 건너뛰고, 없으면 Google TTS로 MP3를 만들어 저장한다.

    Args:
        client: TextToSpeechAsyncClient.
        audio_dir: MP3 저장 루트.
        sem: 동시 합성 제한 세마포어.
        text: 대사 본문.
        cfg: voice_map의 {voice, rate, pitch}.
    Returns:
        저장된 MP3 파일명.
    Raises:
        TtsError: 합성 호출 실패 시.
    """
    name = _cache_name(text, cfg["voice"], cfg["rate"], cfg["pitch"])
    out_path = audio_dir / name
    if out_path.exists():  # 캐시 적중 → 재합성 불필요
        return name

    tts = _load_tts_module()
    request = {
        "input": tts.SynthesisInput(text=text),
        "voice": tts.VoiceSelectionParams(
            language_code=voice_map.LANGUAGE_CODE, name=cfg["voice"]
        ),
        "audio_config": tts.AudioConfig(
            audio_encoding=tts.AudioEncoding.MP3,
            speaking_rate=cfg["rate"],
            pitch=cfg["pitch"],
            sample_rate_hertz=_SAMPLE_RATE,  # 파트 동일 규격 → -c copy 병합 안전
        ),
    }
    async with sem:
        try:
            response = await client.synthesize_speech(request=request)
        except Exception as e:  # GoogleAPICallError 등
            detail = (getattr(e, "message", None) or str(e)).splitlines()[0].strip()
            raise TtsError(f"Google TTS 합성 실패: {detail}") from e
    tmp_path = out_path.with_suffix(".mp3.part")
    tmp_path.write_bytes(response.audio_content)
    tmp_path.replace(out_path)  # 원자적 교체(부분 파일이 캐시로 남지 않게)
    return name


async def _merge_mp3(part_paths: list[Path], out_path: Path) -> None:
    """라인 MP3들을 단일 MP3로 병합 기능(ffmpeg -c copy).  # (Concat parts into one MP3)

    재인코딩 없이 concat demuxer로 붙인다(RPi4에서도 빠름). ffmpeg가 없으면 명확히 실패한다.

    Args:
        part_paths: 재생 순서대로의 라인 MP3 경로 목록.
        out_path: 병합 결과 MP3 경로.
    Raises:
        TtsError: ffmpeg 미설치 또는 병합 실패 시.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TtsError("ffmpeg가 설치되어 있지 않습니다(오디오 병합 필요; `apt install ffmpeg`).")

    # concat demuxer 입력 목록 파일 작성.
    # ffmpeg concat은 'file' 경로를 '목록 파일이 있는 디렉터리' 기준 상대경로로 해석한다.
    # 목록 파일과 파트가 같은 audio_dir에 있어 상대경로(app/audio/...)를 쓰면 app/audio/app/audio/...
    # 를 찾아 'No such file'로 실패하므로, 반드시 절대경로로 적는다(-safe 0로 절대경로 허용됨).
    list_path = out_path.with_suffix(".txt")
    lines = "\n".join(f"file '{p.resolve().as_posix()}'" for p in part_paths)
    list_path.write_text(lines + "\n", encoding="utf-8")

    # tmp 확장자가 .part라 muxer 추론이 안 되므로 -f mp3로 출력 포맷을 명시한다.
    tmp_out = out_path.with_suffix(".mp3.part")
    # 블로킹 subprocess를 스레드로 돌린다(asyncio.to_thread). asyncio.create_subprocess_exec는
    # Windows의 SelectorEventLoop(uvicorn 기본)에서 NotImplementedError로 실패하므로, 이벤트 루프
    # 종류와 무관하게 동작하도록 표준 subprocess.run을 워커 스레드에서 실행한다(Mac/RPi/Windows 공통).
    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy", "-f", "mp3", str(tmp_out),
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    list_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        tmp_out.unlink(missing_ok=True)
        detail = (proc.stderr.decode("utf-8", "ignore").strip().splitlines() or ["unknown"])[-1]
        raise TtsError(f"오디오 병합(ffmpeg) 실패: {detail}")
    tmp_out.replace(out_path)


async def synthesize_creation(result: CreationResult) -> CreationResult:
    """창작 결과에 단일 MP3 + timepoints 채우기 기능.  # (Fill audio track)

    오디오극(mode=audio)일 때만 동작한다. 각 line을 화자 voiceProfile 음성으로 병렬 합성하고,
    재생 순서대로 하나의 MP3로 병합한 뒤 라인별 timepoints(누적 ms)를 계산해 result.audio에
    채워 돌려준다. 대화극은 그대로 반환한다(audio=None).
    audioUrl은 `/audio/...` 상대경로로 만들어 호스트/포트를 응답에 박지 않는다.

    Args:
        result: formatter가 검증한 CreationResult.
    Returns:
        (audio면) result.audio가 채워진 CreationResult, (dialogue면) 입력 그대로.
    Raises:
        TtsError: 인증 없음/합성 실패/ffmpeg 없음 시.
    """
    if result.mode is not Mode.audio:
        return result  # 대화극은 음성 합성 없음

    settings = get_settings()
    audio_dir = Path(settings.audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # 화자(characterId) → 정규화 voiceProfile (formatter가 이미 닫힌 어휘로 맞춤).
    profile_by_char = {
        c.characterId: voice_map.resolve_profile(c.voiceProfile) for c in result.characters
    }

    # 재생 순서대로 (line, cfg) 수집(빈 텍스트는 건너뜀).
    ordered_lines = []  # [(line, cfg)]
    for scene in result.scenes:
        for line in scene.lines:
            if not line.text.strip():
                continue
            profile = profile_by_char.get(line.speaker) or voice_map.resolve_profile(None)
            ordered_lines.append((line, voice_map.VOICE_MAP[profile]))

    if not ordered_lines:
        return result  # 합성할 대사가 없으면 audio 없음

    client = await _get_client()
    sem = asyncio.Semaphore(_TTS_CONCURRENCY)

    # 라인별 병렬 합성(첫 TtsError 발생 시 전체 실패).
    names = await asyncio.gather(
        *(_synthesize_one(client, audio_dir, sem, line.text, cfg) for line, cfg in ordered_lines)
    )
    part_paths = [audio_dir / n for n in names]

    # 병합 MP3는 파트 목록 해시로 캐시(같은 내용 재요청 시 재병합 안 함).
    merge_key = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()[:32]
    merged_name = f"creation_{merge_key}.mp3"
    merged_path = audio_dir / merged_name
    if not merged_path.exists():
        await _merge_mp3(part_paths, merged_path)

    # 라인별 길이 → 누적 timepoints.
    timepoints: list[Timepoint] = []
    cursor = 0
    for (line, _cfg), part in zip(ordered_lines, part_paths):
        dur = _mp3_duration_ms(part)
        timepoints.append(Timepoint(lineId=line.lineId, startMs=cursor, endMs=cursor + dur))
        cursor += dur

    result.audio = AudioTrack(
        audioUrl=f"/audio/{merged_name}",
        totalDurationMs=cursor,
        timepoints=timepoints,
    )
    return result
