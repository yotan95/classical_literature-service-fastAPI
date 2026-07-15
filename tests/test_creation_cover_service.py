"""창작물 고유 표지 서비스 테스트.  # (Per-creation cover service tests)

OpenAI를 실제 호출하지 않고 generate_cover_image를 대체해 성공/폴백 동작만 검증한다.
관련: app/services/creation_cover_service.py.
"""

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.models.request import Difficulty, Mode
from app.models.response import Character, CreationResult, CreationSource, Line, Scene
from app.services import creation_cover_service


def _png_bytes() -> bytes:
    """테스트용 PNG 바이트를 만드는 기능.  # (Build in-memory PNG bytes)

    OpenAI 이미지 응답을 대체하기 위해 800x1120 PNG를 메모리에서 만든다.
    관련: creation_cover_service.attach_cover.

    Returns:
        PNG 바이트.
    """
    buf = io.BytesIO()
    Image.new("RGB", (800, 1120), (80, 120, 90)).save(buf, format="PNG")
    return buf.getvalue()


def _result(creation_id: str = "creation/test") -> CreationResult:
    """테스트용 창작 결과를 만드는 기능.  # (Build a minimal CreationResult)

    CreationResult의 정합성 검증을 통과하는 최소 장면/인물/대사를 구성한다.
    관련: app/models/response.py.

    Args:
        creation_id: 테스트할 creationId.
    Returns:
        CreationResult 인스턴스.
    """
    result = CreationResult(
        creationId=creation_id,
        bookId="heosaeng_jeon",
        title="허생의 새 길",
        mode=Mode.dialogue,
        difficulty=Difficulty.children,
        tags=["모험"],
        intro="허생이 문을 열고 밖으로 나가요.",
        characters=[Character(characterId="char-narrator", name="내레이터")],
        scenes=[
            Scene(
                sceneId="scene-1",
                order=1,
                title="문 앞에서",
                lines=[
                    Line(
                        lineId="line-1",
                        order=1,
                        speaker="char-narrator",
                        speakerName="내레이터",
                        text="허생이 책을 덮고 문밖을 바라보았어요.",
                    )
                ],
            )
        ],
    )
    result.source = CreationSource(
        bookId="heosaeng_jeon",
        title="허생전",
        coverImageUrl="/images/heosaeng_jeon.webp?v=1",
        scope="full",
    )
    return result


class CreationCoverServiceTest(unittest.IsolatedAsyncioTestCase):
    """창작물 표지 생성 서비스 동작 검증.  # (Creation cover service behavior tests)"""

    async def test_attach_cover_writes_webp_and_sets_creation_cover_url(self) -> None:
        """성공 시 webp 파일을 저장하고 최상위 creationCoverImageUrl을 채우는지 검증한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._patch_service(tmpdir, image_bytes=_png_bytes())
            result = await creation_cover_service.attach_cover(_result())

            expected = Path(tmpdir) / "creation_creation-test.webp"
            self.assertTrue(expected.is_file())
            self.assertIn(
                "/creation-covers/creation_creation-test.webp?v=",
                result.creationCoverImageUrl,
            )
            self.assertEqual("💰", result.creationCoverEmoji)

    async def test_attach_cover_falls_back_to_emoji_on_failure(self) -> None:
        """이미지 생성 실패 시 대표 이모티콘으로 폴백하는지 검증한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._patch_service(tmpdir, error=RuntimeError("boom"))
            result = await creation_cover_service.attach_cover(_result())

            self.assertIsNone(result.creationCoverImageUrl)
            self.assertEqual("💰", result.creationCoverEmoji)
            self.assertEqual([], list(Path(tmpdir).iterdir()))

    def _patch_service(
        self, tmpdir: str, image_bytes: bytes | None = None, error: Exception | None = None
    ) -> None:
        """서비스 외부 의존성을 테스트 대역으로 바꾸는 기능.  # (Patch service dependencies)

        설정/원작 로더/OpenAI 이미지 호출을 테스트 안에서만 바꾸고 cleanup으로 원복한다.
        관련: unittest.TestCase.addCleanup.

        Args:
            tmpdir: 창작물 표지 저장 임시 폴더.
            image_bytes: 성공 케이스에서 반환할 PNG 바이트.
            error: 실패 케이스에서 던질 예외.
        """
        original_get_settings = creation_cover_service.get_settings
        original_load_book = creation_cover_service.data_loader.load_book
        original_generate_cover_image = creation_cover_service.generate_cover_image

        class DummySettings:
            """테스트용 설정 객체.  # (Dummy settings for tests)"""

        DummySettings.creation_cover_dir = tmpdir

        async def fake_generate_cover_image(prompt: str) -> bytes:
            """OpenAI 이미지 호출을 대체하는 기능.  # (Fake image generation)"""
            if error is not None:
                raise error
            assert image_bytes is not None
            return image_bytes

        creation_cover_service.get_settings = lambda: DummySettings()
        creation_cover_service.data_loader.load_book = lambda _slug: {
            "title": "허생전",
            "era": "조선",
            "emoji": "💰",
        }
        creation_cover_service.generate_cover_image = fake_generate_cover_image

        self.addCleanup(setattr, creation_cover_service, "get_settings", original_get_settings)
        self.addCleanup(
            setattr,
            creation_cover_service.data_loader,
            "load_book",
            original_load_book,
        )
        self.addCleanup(
            setattr,
            creation_cover_service,
            "generate_cover_image",
            original_generate_cover_image,
        )
