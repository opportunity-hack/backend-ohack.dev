import uuid
import pandas as pd

from PIL import Image, ImageFont, ImageDraw
from os import path
from enum import Enum
from typing import *



CERTIFICATE_TEMPLATE_PATH: str = None

FONT_FILE_DEFAULT_PATH: str = None
FONT_COLOR_DEFAULT: str = "#FFFFFF"

OUT_DIRECTORY: str = None


class DrawPosition(Enum):
    TOPLEFT: int     = 1
    LEFT: int        = 2
    BOTTOMLEFT: int  = 3
    TOP: int         = 4
    BOTTOM: int      = 5
    TOPRIGHT: int    = 6
    RIGHT: int       = 7
    BOTTOMRIGHT: int = 8
    CENTER: int      = 9


# Following along with https://github.com/tusharnankani/CertificateGenerator
# Sample Certificate: https://media.licdn.com/dms/image/C5622AQGSGOf0g8fUTw/feedshare-shrink_2048_1536/0/1597446630591?e=1683158400&v=beta&t=ICzKDT_6y-BS24FldIpAL7UzHnYUCtB_I-pjGHT49Qc
class CertificateGenerator:

    def __init__(self):
        self.certificateTemplate: Image = Image.open(CERTIFICATE_TEMPLATE_PATH)
        self.imageDrawer: ImageDraw = ImageDraw.Draw(self.certificateTemplate)

    def _commit_to_certificate(self) -> None:
        pass

    def save_certificate(self, writeDirectory: str = OUT_DIRECTORY, fileName: str = None) -> str:
        self._commit_to_certificate()

        if (fileName is None):
            fileName = f"{str(uuid.uuid4())}.png"
        elif (".png" not in fileName):
            fileName += ".png"

        filePath: str = path.join(writeDirectory, fileName)
        self.certificateTemplate.save(filePath)
        return filePath

    def _get_text_dimensions(self, text: str, fontFile: str = FONT_FILE_DEFAULT_PATH) -> Tuple[int, int]:
        return self.imageDrawer.textsize(text, font=fontFile)

    def _draw_text(self, text: str, xPosition: int, yPosition: int, drawFrom: DrawPosition = DrawPosition.TOPLEFT, fontColor: str = FONT_COLOR_DEFAULT, fontFile: str = FONT_FILE_DEFAULT_PATH) -> None:
        # draw.text(((WIDTH - name_width) / 2, (HEIGHT - name_height) / 2 - 30), name, fill=FONT_COLOR, font=FONT_FILE)
        textWidth, textHeight = self._get_text_dimensions(
            text, fontFile=fontFile)

        adjustedXPosition: int = xPosition
        adjustedYPosition: int = yPosition
        self.imageDrawer.text(
            adjustedXPosition, adjustedYPosition, text, fill=fontColor, font=fontFile)


def generate_certificate(userInfo: pd.DataFrame) -> str:
    """ Automatically generate a certificate and returns the path to it """
    certGen: CertificateGenerator = CertificateGenerator()

    return certGen.save_certificate()
