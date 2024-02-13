import uuid

from PIL import ImageFont
from os import getenv, path, remove
from typing import *
from datetime import datetime
import sys
from dotenv import load_dotenv
import textwrap
import pytz
import base64
import hashlib

# Import get_team_by_slack_channel
from common.utils.firebase import get_team_by_slack_channel, save_certificate, get_certficate_by_file_id, get_recent_certs_from_db
from common.utils.cdn import upload_to_cdn

# Import QR code generator
from api.certificates.qr_code import generate_qr_code

from api.certificates.certificate import CertificateGenerator
from api.certificates.scan_repo import GitFameRow, getGitFameData, GitFameTableCombined
from api.certificates.certificate_cryptography import signCertificate, verifyCertificate
load_dotenv()

CDN_SERVER = getenv("CDN_SERVER")

LARGE_FONT: ImageFont   = ImageFont.truetype("./api/certificates/assets/Gidole-Regular.ttf", size=25)
SMALL_FONT: ImageFont   = ImageFont.truetype("./api/certificates/assets/Gidole-Regular.ttf", size=19)
SMALLER_FONT: ImageFont = ImageFont.truetype("./api/certificates/assets/Gidole-Regular.ttf", size=12)
HEADER_FONT: ImageFont  = ImageFont.truetype("./api/certificates/assets/Gidole-Regular.ttf", size=40)

WHITE_COLOR: Tuple[int, int, int] = (255, 255, 255)
GOLD_COLOR:  Tuple[int, int, int] = (255, 215, 0)
BLACK_COLOR: Tuple[int, int, int] = (0, 0, 0)


def get_cert_info(id):
    return get_certficate_by_file_id(id)

def get_recent_certs():
    return get_recent_certs_from_db()

def _get_stat_text_info(certGen: CertificateGenerator, stats: List[Tuple[Union[int, Any]]], font: ImageFont) -> Dict[str, Union[int, List[str]]]:
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


def _write_stat_to_certificate(certGen: CertificateGenerator, statTextInfo: Dict[str, Union[int, List[str]]], startY: int, font: ImageFont, maxY: int = None) -> None:
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

def generate_certificate_from_slack(slack_channel: str) -> List[str]:
    team = get_team_by_slack_channel(slack_channel)
    if (team is None): return []
    
    if "github_links" in team:
        github_links = team["github_links"]
        return [generate_certificate_for_all_authors(link["link"]) for link in github_links]


    return []


def generate_certificate_for_all_authors(repositoryURL: str) -> str:
   """ Generate certificate for each GitFameData.authors """
   gitFameData: GitFameTableCombined = getGitFameData(repositoryURL)
   
   print(f"gitFameData: {gitFameData}")
   print(f"gitFameData.authors: {gitFameData.authors}")
   return [generate_certificate(repositoryURL, row.author) for row in gitFameData.authors]

def generate_hash(data: str) -> str:
    hash_object = hashlib.sha256(data.encode())
    return hash_object.hexdigest()


def generate_certificate(repositoryURL: str, username: str) -> str:
    """ Automatically generate a certificate and returns the base64 representation of it"""
    certGen: CertificateGenerator = CertificateGenerator()

    gitFameData: GitFameTableCombined = getGitFameData(repositoryURL)
    print(f"gitFameData: {gitFameData}")
    print(f"gitFameData.authors: {gitFameData.authors}")

    authorWithEmailData: GitFameRow = None
    authorData: GitFameRow = None

    index = 0   
    for row in gitFameData.authors:

        if (row.author == username):
            authorData = row                 
            # Get the same index from authorsEmails
            authorWithEmailData = gitFameData.authorsEmails[index]
            break

        index += 1

    if (not authorData): return ""

    certGen.draw_multiline_text_absolute("Certificate of Achievement", 1024 // 2, 360, GOLD_COLOR, HEADER_FONT, "center")
    certGen.draw_multiline_text_absolute("Congratulations", 1024 // 2, 405, GOLD_COLOR, LARGE_FONT, "center")
    certGen.draw_multiline_text_absolute(username, 1024 // 2, 450, WHITE_COLOR, HEADER_FONT, "center")

    exText = "This certifies that hard work, determination, and extreme learning are innate in this person as they volunteered their summer to help non-profits. They could have been doing anything else, but they chose to do something for their community!"
    wrappedText: str = "\n".join(textwrap.wrap(exText, width=85))
    certGen.draw_multiline_text_absolute(wrappedText, 1024 // 2, 540, WHITE_COLOR, SMALL_FONT, "center")

    certGen.draw_text_absolute("Stats", 1024 // 2, 620, WHITE_COLOR, HEADER_FONT, "center")

    stats = [
        ["Hours", f"{authorData.hours}"],
        ["Commits", f"{authorData.commits}"],
        ["Lines of Code", f"{authorData.linesOfCode }"],
        ["Files", f"{authorData.files }"],
    ]

    statsInfo: Dict[str, int | List[str]] = _get_stat_text_info(certGen, stats, LARGE_FONT)
    _write_stat_to_certificate(certGen, statsInfo, 660, LARGE_FONT, None)

    certGen.draw_text_absolute(f"∑ Team Totals | Hours: { gitFameData.totalHours } Commits: { gitFameData.totalCommits } LOC: {gitFameData.totalLinesOfCode} Files: {gitFameData.totalFiles}", 1024 // 2, 820, WHITE_COLOR, SMALL_FONT, "center")
    


    certGen.draw_multiline_text_absolute("Write code for social good @ ohack.dev\nFollow us on Facebook, Instagram, and Linkedin @opportunityhack", 1024 // 2, 890, WHITE_COLOR, SMALL_FONT, "center")
    # Make a short SHA file_id a hash of authorData.author, repositoryURL, authorData.commits, authorData.linesOfCode, authorData.files

    file_id_hash = f"{authorData.author}{repositoryURL}{authorData.commits}{authorData.linesOfCode}{authorData.files}"
    file_id = generate_hash(file_id_hash)
    az_time = datetime.now(pytz.timezone('US/Arizona'))
    iso_date = az_time.isoformat()  # Using ISO 8601 format
    

    # Generate QR code
    qr_code_text = f"https://ohack.dev/cert/{file_id}"
    qr_code = generate_qr_code(qr_code_text)
    # Draw image on certificate
    certGen.draw_image(qr_code, 1024 - 125, 1024 - 125)

    bottom_text = iso_date + " | " + qr_code_text
    certGen.draw_text_absolute(bottom_text, 1024 // 2, 1024 - 25, WHITE_COLOR, SMALLER_FONT, "center")
    

    certificateBytes: bytes = certGen.toBytes()
    signedCertificate: bytes = signCertificate(certificateBytes)
    certificateBase64Bytes: bytes = base64.b64encode(signedCertificate)
    
    # Save bytes to file
    with open(f"certificate_{file_id}.png", "wb") as f:
        f.write(certificateBytes)    

    # Save certificate to CDN
    file_url = upload_to_cdn("certificates", f"certificate_{file_id}.png")

    # Delete file
    remove(f"certificate_{file_id}.png")

    stats_json = {
        "hours": authorData.hours,
        "commits": authorData.commits,
        "lines_of_code": authorData.linesOfCode,
        "files": authorData.files
    }
    totals_json = {
        "hours": gitFameData.totalHours,
        "commits": gitFameData.totalCommits,
        "lines_of_code": gitFameData.totalLinesOfCode,
        "files": gitFameData.totalFiles
    }

    result = {
        "certificate_url" : file_url,
        "author_name": username,
        "author_email" : authorWithEmailData.author,
        "stats": stats_json,
        "totals": totals_json,
        "file_id": file_id,
        "file_id_hash": file_id_hash,
        "date": iso_date,
        "repository_url": repositoryURL        
    }

    save_certificate(result)
    return result

def validateCertificate(certificateBase64Str: str) -> bool:
    certificateBytes = base64.b64decode(certificateBase64Str)
    return verifyCertificate(certificateBytes)