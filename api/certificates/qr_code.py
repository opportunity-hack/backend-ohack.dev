
import qrcode
import io
from PIL import Image


def generate_qr_code(text:str) -> Image:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#003087", back_color="white")

    # Make the image 100x100
    img = img.resize((100, 100))    

    return img