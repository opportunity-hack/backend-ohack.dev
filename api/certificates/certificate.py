from typing import *
from PIL import Image, ImageFont, ImageDraw, ImageEnhance
import base64
from io import BytesIO
import os
import openai
import urllib

CERTIFICATE_MASK_PATH: str = "./api/certificates/assets/cert_mask_1024.png"
BACKGROUND_SAVE_LOC: str   = "/tmp/generated_image.png"
BACKUP_BACKGROUND_LOC: str = "./api/certificates/assets/generated_image.png"

FONT_DEFAULT: ImageFont = ImageFont.load_default()
FONT_COLOR_DEFAULT: str = "#000000"

OUT_DIRECTORY: str = "./certificates"

openai.api_key = os.getenv("OPENAI_API_KEY")


class CertificateGenerator:

    def __init__(self):
        self.certificateTemplate: Image = self._get_background_image()
        enhancer = ImageEnhance.Brightness(self.certificateTemplate)
        self.certificateTemplate = enhancer.enhance(0.35)
        self.certificateMask: Image = Image.open(CERTIFICATE_MASK_PATH)
        self.imageDrawer: ImageDraw = ImageDraw.Draw(self.certificateTemplate)

    def _get_background_image(self) -> Image:
        try:
            response = openai.Image.create(
                prompt="without text a mesmerizing background with geometric shapes and fireworks no text high resolution 4k",
                n=1,
                size="1024x1024"
            )
            image_url = response['data'][0]['url']
            urllib.request.urlretrieve(image_url, BACKGROUND_SAVE_LOC)
        except:
            ...
        
        if (os.path.exists(BACKGROUND_SAVE_LOC)):
            return Image.open(BACKGROUND_SAVE_LOC)
    
        return Image.open(BACKUP_BACKGROUND_LOC)


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
        
    def draw_image(self, img: Image, xPosition: int, yPosition: int) -> None:
        imageWidth, imageHeight = img.size
        box = (xPosition, yPosition, xPosition + imageWidth, yPosition + imageHeight)
        self.certificateTemplate.paste(img, box)        


    def toBytes(self) -> bytes:
        self.certificateTemplate.paste(self.certificateMask, (0, 0), mask=self.certificateMask)
        imgBuff: BytesIO = BytesIO()
        self.certificateTemplate.save(imgBuff, format="png")
        imgBytes: bytes = imgBuff.getvalue()
        return imgBytes
        
    def toBase64(self) -> bytes:
        imgBytes: bytes = self.toBytes()
        return base64.b64encode(imgBytes)