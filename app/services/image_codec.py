"""이미지 포맷 변환 헬퍼 모듈.  # (Image codec helpers)

OpenAI 이미지 API가 돌려준 PNG 바이트를 앱에서 쓰는 webp 런타임 산출물로 변환한다.
원작 표지와 창작물 표지가 같은 webp 품질 정책을 공유하도록 이 모듈에 둔다.
관련: app/services/cover_service.py, app/services/creation_cover_service.py.
"""

import io

_WEBP_QUALITY = 85


def png_to_webp(png_bytes: bytes) -> bytes:
    """PNG 바이트를 webp 바이트로 변환하는 기능.  # (Convert PNG to webp)

    Pillow로 PNG를 열어 webp로 저장한다. 원작 표지와 창작물 고유 표지가 같은 품질 설정을 쓴다.
    관련: app/services/cover_service.py, app/services/creation_cover_service.py.

    Args:
        png_bytes: OpenAI 이미지 API가 반환한 PNG 바이트.
    Returns:
        webp 바이트.
    """
    from PIL import Image  # 무거운 import 지연

    with Image.open(io.BytesIO(png_bytes)) as img:
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=6)
        return buf.getvalue()
