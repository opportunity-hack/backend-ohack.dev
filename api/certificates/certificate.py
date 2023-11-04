from typing import *
from PIL import Image, ImageFont, ImageDraw, ImageEnhance
from os import path
import base64
from io import BytesIO

# CERTIFICATE_TEMPLATE_PATH: str = "./assets/certificateTemplate.jpg"
CERTIFICATE_MASK_PATH:     str = "./api/certificates/assets/cert_mask_1024.png"

FONT_DEFAULT: ImageFont = ImageFont.load_default()
FONT_COLOR_DEFAULT: str = "#000000"

OUT_DIRECTORY: str = "./certificates"


# Sample Certificate: https://media.licdn.com/dms/image/C5622AQGSGOf0g8fUTw/feedshare-shrink_2048_1536/0/1597446630591?e=1683158400&v=beta&t=ICzKDT_6y-BS24FldIpAL7UzHnYUCtB_I-pjGHT49Qc
# Greg's Example: https://github.com/opportunity-hack/backend-ohack.dev/pull/23/files#diff-9290f3f2480035f4c8cce6994884d997aa22397fbd26925bfd28d3fc9f53a8e1R166
class CertificateGenerator:

    def __init__(self):
        # self.certificateTemplate: Image = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
        self.certificateTemplate: Image = Image.open("./api/certificates/assets/generated_image.png")
        enhancer = ImageEnhance.Brightness(self.certificateTemplate)
        self.certificateTemplate = enhancer.enhance(0.35)
        self.certificateMask: Image = Image.open(CERTIFICATE_MASK_PATH)
        self.imageDrawer: ImageDraw = ImageDraw.Draw(self.certificateTemplate)

    def draw_multiline_text_relative(self, text: str, xPosPercentage: float, yPosPercentage: float, fontColor: str = FONT_COLOR_DEFAULT, font: ImageFont = FONT_DEFAULT, align: str = "center") -> None:
        xPosition, yPosition = self.percentageToPixelCoords(xPosPercentage, yPosPercentage)
        self.draw_multiline_text_absolute(text, xPosition, yPosition, fontColor=fontColor, font=font, align=align)

    def draw_multiline_text_absolute(self, text: str, xPosition: int, yPosition: int, fontColor: str = FONT_COLOR_DEFAULT, font: ImageFont = FONT_DEFAULT, align: str = "center") -> None:
        adjustedXPosition, adjustedYPosition = self._get_adjusted_pixel_coords(text, xPosition, yPosition, font=font, align=align)
        self.imageDrawer.multiline_text(
            (adjustedXPosition, adjustedYPosition), text, fill=fontColor, font=font, align=align)

    def draw_text_relative(self, text: str, xPosPercentage: float, yPosPercentage: float, fontColor: str = FONT_COLOR_DEFAULT, font: ImageFont = FONT_DEFAULT, align: str = "center") -> None:
        xPosition, yPosition = self.percentageToPixelCoords(xPosPercentage, yPosPercentage)
        self.draw_text_absolute(text, xPosition, yPosition, fontColor=fontColor, font=font, align=align)

    def draw_text_absolute(self, text: str, xPosition: int, yPosition: int, fontColor: str = FONT_COLOR_DEFAULT, font: ImageFont = FONT_DEFAULT, align: str = "center") -> None:
        adjustedXPosition, adjustedYPosition = self._get_adjusted_pixel_coords(text, xPosition, yPosition, font=font, align=align)
        self.imageDrawer.text(
            (adjustedXPosition, adjustedYPosition), text, fill=fontColor, font=font, align=align)
    
    def pixelCoordsToPercentage(self, xPos: int, yPos: int) -> Tuple[float, float]:
        imageWidth, imageHeight = self.certificateTemplate._size
        return (xPos / imageWidth, yPos / imageHeight)
    
    def percentageToPixelCoords(self, xPosPercentage: float, yPosPercentage: float) -> Tuple[int, int]:
        imageWidth, imageHeight = self.certificateTemplate._size
        return (round(imageWidth * xPosPercentage), round(imageHeight * yPosPercentage))

    def get_text_size(self, text: str, font: ImageFont = FONT_DEFAULT) -> Tuple[int, int]:
        _left, _top, right, bottom = self.imageDrawer.textbbox((0, 0), text, font=font, align="left")
        return right, bottom

    def _get_adjusted_pixel_coords(self, text: str, xPosition: int, yPosition: int, font: ImageFont = FONT_DEFAULT, align: str = "center") -> Tuple[int, int]:
        textWidth, textHeight = self.get_text_size(text, font=font)
        if (align == "center"):
            return (xPosition - (textWidth // 2), yPosition - (textHeight // 2))
        if (align == "left"):
            return (xPosition, yPosition - (textHeight // 2))

    def toBytes(self) -> bytes:
        self.certificateTemplate.paste(self.certificateMask, (0, 0), mask=self.certificateMask)
        imgBuff: BytesIO = BytesIO()
        self.certificateTemplate.save(imgBuff, format="png")
        imgBytes: bytes = imgBuff.getvalue()
        return imgBytes
        
    def toBase64(self) -> bytes:
        imgBytes: bytes = self.toBytes()
        return base64.b64encode(imgBytes)