import pytest
import base64
import random
from typing import List
from api.certificates.certificate_service import generate_certificate, validateCertificate

def modify_byte_at_index(obj: bytes, index: int, newVal: int) -> bytes:
    intList: List[int] = list(obj)
    intList[index] = newVal
    return bytes(intList)


def test_valid_certificate():
    certificateBase64: str = generate_certificate(pytest.CERTIFICATE_TEST_REPO_URL, pytest.CERTIFICATE_TEST_USERNAME)
    assert certificateBase64 is not None and len(certificateBase64) > 0
    assert validateCertificate(certificateBase64)


def test_tampered_changes_certifcate():
    origCertificateBase64: str = generate_certificate(pytest.CERTIFICATE_TEST_REPO_URL, pytest.CERTIFICATE_TEST_USERNAME)
    for _ in range(100):
        tamperedCopy: str = str(origCertificateBase64)
        tamperedCopyBytes: bytes = base64.b64decode(tamperedCopy)

        randIndex: int = random.randint(0, len(tamperedCopyBytes) - 1)
        origByte: int = tamperedCopyBytes[randIndex]
        randByte: int = random.randint(0, 255)
        expected: bool = origByte == randByte
        tamperedCopyBytes: bytes = modify_byte_at_index(tamperedCopyBytes, randIndex, randByte)

        tamperedBase64: str = base64.b64encode(tamperedCopyBytes).decode()
        assert validateCertificate(tamperedBase64) == expected


def test_forged_certifcate():
    for _ in range(100):
        forgedCopyBytes: bytes = bytes([random.randint(0, 255) for _ in range(random.randint(0, 1000))])
        forgedCopyBase64: str = base64.b64encode(forgedCopyBytes).decode()
        assert not validateCertificate(forgedCopyBase64)