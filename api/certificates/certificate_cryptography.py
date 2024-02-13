from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.exceptions import InvalidSignature
import os
import sys
from dotenv import load_dotenv
load_dotenv()

KEY_STR: str        = "CERTIFICATE_KEY"
KEY_PASSWORD: str   = os.getenv("PRIVATE_KEY_PASSWORD") or "DEBUG"
KEY_PASSWORD: bytes = KEY_PASSWORD.encode()

SIGNATURE_LEN: int = 256
SIGNATURE_LEN_INT_SIZE: int = 4

def _getPrivateKeyBytes(privateKey: rsa.RSAPrivateKey) -> bytes:
    return privateKey.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(KEY_PASSWORD)
    )

def _getPublicKey(privateKey: rsa.RSAPrivateKey) -> rsa.RSAPublicKey:
    return privateKey.public_key()

def _getPrivateKey() -> rsa.RSAPrivateKey:
    # Load Key if exists
    if (KEY_STR in os.environ):
        return serialization.load_pem_private_key(
            os.environ[KEY_STR].encode(),
            password=KEY_PASSWORD
        )

    # Generate Key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_key_bytes = _getPrivateKeyBytes(private_key)
    os.environ[KEY_STR] = private_key_bytes.decode()

    return private_key

from cryptography.exceptions import InvalidSignature

def signCertificate(certificateBytes: bytes) -> bytes:
    privateKey: rsa.RSAPrivateKey = _getPrivateKey()
    signature: bytes = privateKey.sign(
        certificateBytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    return certificateBytes + signature + (len(signature)).to_bytes(SIGNATURE_LEN_INT_SIZE, "little", signed=False)

def verifyCertificate(certificateBytes: bytes) -> bool:
    print("Verifying certificate...")
    privateKey: rsa.RSAPrivateKey = _getPrivateKey()
    publicKey:  rsa.RSAPublicKey = _getPublicKey(privateKey)
    certificateLen: int = len(certificateBytes)
    print(f"Certificate Length: {certificateLen}")
    if (certificateLen < 4): 
        print("Certificate Length < 4")
        return False
    
    signatureLen: int = int.from_bytes(certificateBytes[-SIGNATURE_LEN_INT_SIZE:], "little", signed=False)
    
    if (certificateLen <= signatureLen + SIGNATURE_LEN_INT_SIZE): 
        print("Certificate Length <= Signature Length + Signature Length Integer Size")
        return False
    
    data: bytes = certificateBytes[:-signatureLen - SIGNATURE_LEN_INT_SIZE]
    signature: bytes = certificateBytes[-signatureLen - SIGNATURE_LEN_INT_SIZE:-SIGNATURE_LEN_INT_SIZE]
    try:
        publicKey.verify(
            signature,
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return True
    except InvalidSignature as e:
        print(f"Invalid Signature, error: {e}")
        # Print error message
        
        # Print stacktrace and error 
        import traceback
        traceback.print_exc()
        

        return False