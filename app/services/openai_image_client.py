import base64
import io

from openai import AsyncOpenAI
from PIL import Image

from app.config import get_settings


class OpenAIImageError(Exception):
    pass


async def generate_cover_image(prompt: str) -> bytes:
    settings = get_settings()

    if not settings.openai_api_key:
        raise OpenAIImageError("OPENAI_API_KEY가 설정되지 않았습니다.")

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        result = await client.images.generate(
            model=settings.openai_image_model,
            prompt=prompt,
            size=settings.openai_image_size,
            quality=settings.openai_image_quality,
            output_format="png",
        )
    except Exception as e:
        raise OpenAIImageError("OpenAI 이미지 생성에 실패했습니다.") from e

    image_base64 = result.data[0].b64_json
    if not image_base64:
        raise OpenAIImageError("OpenAI 이미지 응답에 b64_json이 없습니다.")

    image_bytes = base64.b64decode(image_base64)

    with Image.open(io.BytesIO(image_bytes)) as img:
        if img.size != (800, 1120):
            raise OpenAIImageError(f"이미지 크기가 800x1120이 아닙니다: {img.size}")

    return image_bytes