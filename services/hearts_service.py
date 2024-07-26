from db.db import fetch_users
import logging
from google.cloud import storage
from PIL import ImageFont
from openai import OpenAI


from PIL import ImageDraw
from PIL import Image, ImageEnhance
import urllib.request
from dotenv import load_dotenv
import os
import sys
import uuid

from datetime import datetime
import pytz
from common.utils.cdn import upload_to_cdn

sys.path.append("../")
load_dotenv()
CDN_SERVER = os.getenv("CDN_SERVER")
GCLOUD_CDN_BUCKET = os.getenv("GCLOUD_CDN_BUCKET")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# add logger
logger = logging.getLogger(__name__)
# set logger to standard out
logger.addHandler(logging.StreamHandler())
# set log level
logger.setLevel(logging.DEBUG)
#
from common.utils.firebase import add_hearts_for_user, get_user_by_user_id, add_certificate
from common.utils.slack import send_slack


def get_hearts_for_all_users():    
    users = fetch_users()

    result = []    

    # Result should have slackUsername, totalHearts, heartTypes (how or what) and heartCount
    # This is all within the "history" key for each user
    # User is type model.user.User
    for user in users:                
        total_hearts = 0

        if user.history:
            print(f"User history: {user.history}")
            '''
            Example of user history:
             {'what': {'unit_test_coverage': 0, 'documentation': 0.5, 'productionalized_projects': 0.5, 'unit_test_writing': 0, 'observability': 0, 'code_quality': 0.5, 'requirements_gathering': 0.5, 'design_architecture': 0.5}, 'how': {'iterations_of_code_pushed_to_production': 1.5, 'code_reliability': 2, 'standups_completed': 2.5, 'customer_driven_innovation_and_design_thinking': 1}}
            '''
            # Result should have slackUsername, totalHearts, heartTypes (how or what) and heartCount
            # Count the total hearts
            for key in user.history:
                if "certificates"  in key:
                    continue

                for subkey in user.history[key]:
                    total_hearts += user.history[key][subkey]
            
            result.append({
                "slackUsername": user.name,
                "totalHearts": total_hearts,
                "heartTypes": list(user.history.keys()),
                "history": user.history
            })                        
    return result




def save_hearts(user_id, hearts_json):
    return give_hearts_to_user(hearts_json["slackUsername"], hearts_json["amount"], hearts_json["reasons"], 
                               create_certificate_image=True, 
                               cleanup=True, 
                               generate_backround_image=True)



def give_hearts_to_user(slack_user_id, amount, reasons, create_certificate_image=False, cleanup=True, generate_backround_image=False):
    user = get_user_by_user_id(slack_user_id)
    if user is None:
        error_string = f"User with slack id {slack_user_id} not found"
        logger.error(error_string)
        raise Exception(error_string)
    
    if "id" not in user:
        error_string = f"User with slack id {slack_user_id} not found"
        logger.error(error_string)
        raise Exception(error_string)
    
    id = user["id"]

    certificate_text = ""
    if create_certificate_image:
        certificate_filename = generate_certificate_image(
            userid=id,
            name=user["name"],
            reasons=reasons,
            hearts=amount,
            generate_backround_image=generate_backround_image)
        certificate_text = f"\nCertificate: {CDN_SERVER}/certificates/{certificate_filename}"
        if cleanup:
            os.remove(certificate_filename)
        
    
    if len(reasons) >= 1:
        for reason in reasons:
            add_hearts_for_user(id, amount, reason)

        reasons_string = ""
        for reason in reasons:
            reasons_string += get_reason_pretty(reason) + ", "        

        plural = "s" if amount > 1 else ""

        if amount == 0.5:
            heart_list = ":heart:"
        else:
            heart_list = ":heart: " * amount * len(reasons)

        # Intro Message to Opportunity Hack community to encourage more hearts        
        intro_message = ":heart_eyes: *Heart Announcement*! :heart_eyes:\n"
        outro_message = "\n_Thank you for taking the time out of your day to support a nonprofit with your talents_!\nMore on our heart system at https://ohack.dev/about/hearts and check your profile at https://ohack.dev/profile to see them!"
        # Send a DM
        send_slack(channel=f"{slack_user_id}",
                  message=f"{intro_message}\nHey <@{slack_user_id}> :astronaut-hooray-woohoo-yeahfistpump: You have been given {amount} :heart: heart{plural} each for :point_right: *{reasons_string}* {heart_list}!\n{outro_message} {certificate_text}\nYour profile should now reflect these updates: https://ohack.dev/profile")
        
        # Send to public channel too
        send_slack(channel="general",
                   message=f"{intro_message}\n:astronaut-hooray-woohoo-yeahfistpump: <@{slack_user_id}> has been given {amount} :heart: heart{plural} each for :point_right: *{reasons_string}* {heart_list}!\n{outro_message} {certificate_text}")
    else:
        # Example: ["code_reliability", "iterations_of_code_pushed_to_production
        raise Exception("You must provide at least 1 reasons for giving hearts in a list")
    

def generate_certificate_image(userid, name, reasons, hearts, generate_backround_image=False):
    total_hearts = hearts * len(reasons)

    # generate image for certificate/announcement
    font = "common/fonts/Gidole-Regular.ttf"

    header_font = ImageFont.truetype(font, size=40)
    large_font = ImageFont.truetype(font, size=25)
    small_font = ImageFont.truetype(font, size=19)
    smaller_font = ImageFont.truetype(font, size=12)
    
    # Used as bullet points for each reason in the image
    ohack_heart = Image.open('common/images/ohack_logo_april_25x25_2023.png')

    # Main certificate image that has transparent background
    foreground_image = Image.open('common/images/cert_mask_1024.png')
    
    # Draw allows us to draw text on the image
    draw = ImageDraw.Draw(foreground_image)
    
    Y_OFFSET = 150
    white_color = (255, 255, 255)  # White color
    gold_color = (255, 215, 0)  # Gold color

    # Header
    header_text = f"Congratulations {name}"
    width = draw.textlength(header_text, font=header_font)
    draw.text(((1024/2)-width/2, Y_OFFSET+190), header_text,
            font=header_font, fill=gold_color)

    # Awarded Text
    add_s = "s" if total_hearts > 1 else ""
    awarded_text = f"You have been awarded {total_hearts} heart{add_s}"
    width = draw.textlength(awarded_text, font=header_font)
    draw.text((1024/2-width/2, Y_OFFSET+240), awarded_text,
            font=header_font, fill=white_color)

    draw.text((200, Y_OFFSET+320), "For your contributions in the following areas:",
            font=large_font, fill=white_color)
    for reason in reasons:
        reason_text = get_reason_pretty(reason)
        draw.text((200, Y_OFFSET+360), reason_text,
                font=large_font, fill=white_color)
        foreground_image.paste(
            ohack_heart, (200-30, Y_OFFSET+360+1), mask=ohack_heart)
        Y_OFFSET += 25

    file_id = uuid.uuid1()
    filename = f"{file_id.hex}.png"

    footer_text = "Write code for social good @ ohack.dev"
    width = draw.textlength(footer_text, font=small_font)
    draw.text((1024/2-width/2, 1024-150), footer_text,
            font=small_font, fill=white_color)
    
    socials_text = "Follow us on Facebook, Instagram, and LinkedIn @opportunityhack"
    width = draw.textlength(socials_text, font=small_font)
    draw.text((1024/2-width/2, 1024-130), socials_text,
              font=small_font, fill=white_color)

    # Our nonprofit is based in Arizona
    az_time = datetime.now(pytz.timezone('US/Arizona'))
    iso_date = az_time.isoformat()  # Using ISO 8601 format
    bottom_text = iso_date + " " + file_id.hex
    width = draw.textlength(bottom_text, font=smaller_font)
    draw.text((1024/2-width/2, 1024-25), bottom_text,
            font=smaller_font, fill=white_color)

    logger.info(f"Generated certificate for {name} with {total_hearts} hearts with filename {filename}")

    # Generate a unique background image
    if generate_backround_image:
        response = client.images.generate(prompt="without text a mesmerizing background with geometric shapes and fireworks no text high resolution 4k",
        n=1,
        size="1024x1024")
        # Example response object: ImagesResponse(created=1721966968, data=[Image(b64_json=None, revised_prompt=None, url='https://oaidalleapiprodscus.blob.core.windows.net/private/org-EzLrpl9lBdn7NnQA25JOTnpt/user-AqP0NOd4VCetE82ZaR72KYLD/img-ahgzSnHmp5CT7NRxK2Ceczd3.png?st=2024-07-26T03%3A09%3A28Z&se=2024-07-26T05%3A09%3A28Z&sp=r&sv=2023-11-03&sr=b&rscd=inline&rsct=image/png&skoid=6aaadede-4fb3-4698-a8f6-684d7786b067&sktid=a48cca56-e6da-484e-a814-9c849652bcb3&skt=2024-07-25T22%3A07%3A56Z&ske=2024-07-26T22%3A07%3A56Z&sks=b&skv=2023-11-03&sig=GyYscH4FV8vExnSHl8vsNL6usvEjqeRITl8CStdVfRQ%3D')])
    
        # Check if response is valid
        if not response.data:
            # Print error from response
            logger.error(response)    
            raise Exception("OpenAI response is not valid")
        
        image_url = response.data[0].url
        logger.info(f"Generated image from OpenAI: {image_url}")

        urllib.request.urlretrieve(image_url, "./generated_image.png")

    background_image = Image.open('generated_image.png')
    enhancer = ImageEnhance.Brightness(background_image)
    background_image_darker = enhancer.enhance(0.35)

    # Randonly pick a Color that is always dark enough to see the text
    import random
    color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    text_img = Image.new('RGBA', (1024, 1024), color)
    text_img = ImageEnhance.Brightness(text_img).enhance(0.35)
    
    text_img.paste(background_image_darker, (0, 0))
    text_img.paste(foreground_image, (0, 0), mask=foreground_image)
    text_img.save(filename, format="png")
    upload_to_cdn("certificates", filename)
    add_certificate(user_id=userid, certificate=filename)
    logger.info(f"Generated certificate for {name} with {total_hearts} hearts with filename {filename}")
    return filename



def get_reason_pretty(reason):
    reasons_string = None
    
    # 4 things in "how"
    if reason == "code_reliability":
        reasons_string = "Code Reliability"
    if reason == "customer_driven_innovation_and_design_thinking":
        reasons_string = "Customer Driven Innovation and Design Thinking"    
    if reason == "iterations_of_code_pushed_to_production":
        reasons_string = "Iterations of Code Pushed to Production"
    if reason == "standups_completed":
        reasons_string = "Standups Completed"

    # 8 things in "what"
    if reason == "code_quality":
        reasons_string = "Code Quality"       
    if reason == "design_architecture":
        reasons_string = "Design Architecture"
    if reason == "documentation":
        reasons_string = "Documentation"
    if reason == "observability":
        reasons_string = "Observability"            
    if reason == "productionalized_projects":
        reasons_string = "Productionalized Projects"
    if reason == "requirements_gathering":
        reasons_string = "Requirements Gathering"            
    if reason == "unit_test_coverage":
        reasons_string = "Unit Test Coverage"
    if reason == "unit_test_writing":
        reasons_string = "Unit Test Writing"
    if reason == "judge":
        reasons_string = "Judging Projects"
    if reason == "mentor":
        reasons_string = "Mentoring Teams"

    if reasons_string is None:
        raise Exception(f"Reason {reason} is not valid")    
    
    return reasons_string

