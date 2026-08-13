import io

from PIL import Image


def image_bytes(image_format: str = "PNG", *, color: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, format=image_format)
    return buffer.getvalue()


PNG_BYTES = image_bytes()
JPEG_BYTES = image_bytes("JPEG")
