import uuid
import pandas as pd

from PIL import ImageFont
from os import path, getenv
from typing import *
from datetime import datetime
import sys
from dotenv import load_dotenv
import textwrap
import pytz
from typing import *

from api.certificates.certificate import CertificateGenerator
from api.certificates.scan_repo import GitFameTable, GitFameRow, getGitFameData

# import openai

sys.path.append("../")
load_dotenv()

# openai.api_key = getenv("OPENAI_API_KEY")
CDN_SERVER = getenv("CDN_SERVER")
GCLOUD_CDN_BUCKET = getenv("GCLOUD_CDN_BUCKET")

LARGE_FONT: ImageFont   = ImageFont.truetype("./api/certificates/assets/Gidole-Regular.ttf", size=25)
SMALL_FONT: ImageFont   = ImageFont.truetype("./api/certificates/assets/Gidole-Regular.ttf", size=19)
SMALLER_FONT: ImageFont = ImageFont.truetype("./api/certificates/assets/Gidole-Regular.ttf", size=12)
HEADER_FONT: ImageFont  = ImageFont.truetype("./api/certificates/assets/Gidole-Regular.ttf", size=40)

WHITE_COLOR: Tuple[int, int, int] = (255, 255, 255)
GOLD_COLOR:  Tuple[int, int, int] = (255, 215, 0)
BLACK_COLOR: Tuple[int, int, int] = (0, 0, 0)


def _get_stat_text_info(certGen: CertificateGenerator, stats: List[Tuple[int, Any]], font: ImageFont) -> Dict[str, int | List[str]]:
    maxStatWidth: int = 0
    maxStatHeight: int = 0
    maxValWidth: int = 0
    maxValHeight: int = 0
    statStrs: List[str] = []
    valStrs: List[str] = []

    for statName, statVal in stats:
        statStrs.append(statName)
        statWidth, statHeight = certGen.get_text_size(statName, font=font)
        maxStatWidth = max(maxStatWidth, statWidth)
        maxStatHeight = max(maxStatHeight, statHeight)

        valStr: str = f": {statVal}"
        valStrs.append(valStr)
        valWidth, valHeight = certGen.get_text_size(valStr, font=font)
        maxValWidth = max(maxValWidth, valWidth)
        maxValHeight = max(maxValHeight, valHeight)

    return {
        "maxStatWidth": maxStatWidth,
        "maxStatHeight": maxStatHeight,
        "statStrs": statStrs,
        "maxValWidth": maxValWidth,
        "maxValHeight": maxValHeight,
        "valStrs": valStrs
    }


def _write_stat_to_certificate(certGen: CertificateGenerator, statTextInfo: Dict[str, int | List[str]], startY: int, font: ImageFont, maxY: int = None) -> None:
    if (maxY is None):
        maxY = 999999
    
    textBoxWidth: int = statTextInfo["maxStatWidth"] + statTextInfo["maxValWidth"]
    leftEdge: int = (1024 - textBoxWidth) // 2

    dy: int = max(statTextInfo["maxStatHeight"], statTextInfo["maxValHeight"])

    for statName, valStr in zip(statTextInfo["statStrs"], statTextInfo["valStrs"]):
        certGen.draw_text_absolute(statName, leftEdge, startY, WHITE_COLOR, font=font, align="left")
        certGen.draw_text_absolute(valStr, leftEdge + statTextInfo["maxStatWidth"], startY, WHITE_COLOR, font=font, align="left")
        startY += dy
        if (startY >= maxY):
            break

def generate_certificate(repositoryURL: str, username: str) -> bytes:
    """ Automatically generate a certificate and returns the path to it """
    certGen: CertificateGenerator = CertificateGenerator()

    gitFameData: GitFameTable = getGitFameData(repositoryURL)
    print(gitFameData)
    authorData: GitFameRow = None
    for row in gitFameData.authors:
        if (row.author == username):
            authorData = row
            break

    if (not authorData): return b""

    certGen.draw_multiline_text_absolute("Certificate of Achievment\nCongratulations", 1024 // 2, 360, GOLD_COLOR, HEADER_FONT, "center")
    certGen.draw_multiline_text_absolute(username, 1024 // 2, 450, WHITE_COLOR, HEADER_FONT, "center")

    exText = "This certifies that hard work, determination, and extreme learning are innate in this person as they volunteered their summer to help non-profits. They could have been doing anything else, but they chose to do something for their community!"
    wrappedText: str = "\n".join(textwrap.wrap(exText, width=85))
    certGen.draw_multiline_text_absolute(wrappedText, 1024 // 2, 540, WHITE_COLOR, SMALL_FONT, "center")

    certGen.draw_text_absolute("Stats", 1024 // 2, 620, WHITE_COLOR, HEADER_FONT, "center")

    stats = [
        ["Commits", authorData.commits],
        ["Lines of Code", authorData.linesOfCode],
        ["Files", authorData.files],
    ]

    statsInfo: Dict[str, int | List[str]] = _get_stat_text_info(certGen, stats, LARGE_FONT)
    _write_stat_to_certificate(certGen, statsInfo, 660, LARGE_FONT, None)

    certGen.draw_multiline_text_absolute("Write code for social good @ ohack.dev\nFollow us on Facebook, Instagram, and Linkedin @opportunityhack", 1024 // 2, 890, WHITE_COLOR, SMALL_FONT, "center")

    file_id = uuid.uuid1()
    az_time = datetime.now(pytz.timezone('US/Arizona'))
    iso_date = az_time.isoformat()  # Using ISO 8601 format
    bottom_text = iso_date + " " + file_id.hex
    certGen.draw_text_absolute(bottom_text, 1024 // 2, 1024 - 25, WHITE_COLOR, SMALLER_FONT, "center")

    return certGen.toBase64()