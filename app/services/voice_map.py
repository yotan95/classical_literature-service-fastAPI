"""음성 프로필 ↔ Google TTS 음성 매핑 모듈.  # (voiceProfile → Google voice map)

오디오극에서 사용하는 '닫힌 어휘(closed vocabulary)'의 voiceProfile 슬러그를
Google Cloud Neural2(ko-KR) 음성 설정으로 매핑한다. 한국어 Neural2 기본 보이스는
A·B(여성)·C(남성) 3종뿐이라, 적은 보이스로 노인/청년/아이 등을 구분하기 위해
speakingRate(말속도)·pitch(반음, -20~20)를 함께 둔다.

이 모듈은 **google 라이브러리를 import 하지 않는다**(가벼움). formatter/prompt_builder가
voiceProfile 정규화·프롬프트 어휘에 쓰고, 실제 합성은 tts_client가 이 맵을 읽어 수행한다.
"""

# 합성 언어 코드(현재 한국어 고정). 등급은 Neural2(결정 2026-06-23).
LANGUAGE_CODE = "ko-KR"

# 매핑에 없거나 비어 있는 voiceProfile의 폴백(특정 못 하는 개체 포함). 항상 VOICE_MAP에 존재해야 함.
DEFAULT_PROFILE = "narrator_calm"

# voiceProfile 슬러그 → {voice: Neural2 음성명, rate: 말속도(0.25~4.0), pitch: 반음(-20~20)}.
# 키 집합 = Claude에게 제시하는 '허용 어휘'. 새 프로필이 필요하면 여기 한 곳만 추가하면 된다.
# (참고: 남성 Neural2는 C 한 종뿐이라 노인/청년/코믹은 pitch·rate로 파생한다.
#  더 다양한 남성 음색이 필요하면 ko-KR-Wavenet-D 등을 값에 섞어도 동작한다.)
VOICE_MAP: dict[str, dict] = {
    "narrator_calm":   {"voice": "ko-KR-Neural2-A", "rate": 0.98, "pitch": -1.0},
    "young_hero_male": {"voice": "ko-KR-Neural2-C", "rate": 1.05, "pitch": 1.0},
    "adult_male":      {"voice": "ko-KR-Neural2-C", "rate": 1.0,  "pitch": -1.0},
    "old_male":        {"voice": "ko-KR-Neural2-C", "rate": 0.90, "pitch": -3.0},
    "female_gentle":   {"voice": "ko-KR-Neural2-B", "rate": 1.0,  "pitch": 1.0},
    "female_middle":   {"voice": "ko-KR-Neural2-A", "rate": 0.97, "pitch": -1.5},
    "young_female":    {"voice": "ko-KR-Neural2-B", "rate": 1.05, "pitch": 2.0},
    "child":           {"voice": "ko-KR-Neural2-B", "rate": 1.08, "pitch": 3.0},
    "comic_male":      {"voice": "ko-KR-Neural2-C", "rate": 1.10, "pitch": 2.0},
    "creature":        {"voice": "ko-KR-Neural2-A", "rate": 1.06, "pitch": 4.0},
}

# 프롬프트에 넣을 사람이 읽는 설명(Claude가 인물마다 가장 가까운 값을 고르게 함).
PROFILE_DESCRIPTIONS: dict[str, str] = {
    "narrator_calm":   "내레이터 — 차분하고 중립적인 해설 목소리",
    "young_hero_male": "젊은 남성 주인공 — 밝고 또렷",
    "adult_male":      "일반 성인 남성",
    "old_male":        "노인/연장자 남성 — 느리고 낮은 톤",
    "female_gentle":   "여성 주인공 — 부드럽고 다정",
    "female_middle":   "중년 여성",
    "young_female":    "젊은 여성/소녀",
    "child":           "아이 — 빠르고 높은 톤",
    "comic_male":      "익살스러운 남성(놀부·악역 등) — 과장된 톤",
    "creature":        "동물/사물/특정하기 어려운 개체(예: 흥부전 제비)",
}


def voice_profiles() -> list[str]:
    """허용 voiceProfile 슬러그 목록 반환 기능.  # (Allowed voiceProfile vocabulary)

    Claude 프롬프트의 허용 어휘 제시·검증에 쓴다.

    Returns:
        VOICE_MAP의 키 리스트(닫힌 어휘).
    """
    return list(VOICE_MAP.keys())


def resolve_profile(profile: str | None) -> str:
    """voiceProfile을 닫힌 어휘로 정규화하는 기능.  # (Normalize to closed vocabulary)

    맵에 있으면 그대로, 없거나 비어 있으면 DEFAULT_PROFILE로 폴백한다(합성이 절대 깨지지 않게).

    Args:
        profile: Claude가 준 voiceProfile(또는 None).
    Returns:
        VOICE_MAP에 존재함이 보장되는 유효한 voiceProfile 슬러그.
    """
    if profile and profile in VOICE_MAP:
        return profile
    return DEFAULT_PROFILE


def voice_config(profile: str | None) -> dict:
    """voiceProfile의 Google 음성 설정 반환 기능.  # (Resolve voice config)

    정규화 후 {voice, rate, pitch}를 돌려준다.

    Args:
        profile: voiceProfile 슬러그(또는 None).
    Returns:
        {"voice": str, "rate": float, "pitch": float}.
    """
    return VOICE_MAP[resolve_profile(profile)]
