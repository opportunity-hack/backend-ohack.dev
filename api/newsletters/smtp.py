import markdown
import smtplib
from string import Template
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from api.newsletters.template import *
from decouple import config
import logging
from api.newsletters.components import (scan_sentence)
from common.utils import safe_get_env_var

ADDRESS = config('NEWSLETTER_ADDRESS')
KEY = config('NEWSLETTER_APP_KEY')
NAME = config('NEWSLETTER_NAME')
logger = logging.getLogger("myapp")

FRONT_END_URL = safe_get_env_var("CLIENT_ORIGIN_URL")
# TODO: comment this line out during production
FRONT_END_URL = 'http://localhost:3000'
ROLE_EMAIL = {
    "mentor": "mentors@ohack.dev",
    "volunteer": "volunteers@ohack.dev",
    "hacker": "hackers@ohack.dev",
    "no role": "info@ohack.dev"
}



def format_message(message,address):
    html = scan_sentence(message,address)
    
    subscribe_link =  FRONT_END_URL+'/newsletters/subscribe/'+address["id"]
    unsubscribe_link =  FRONT_END_URL+'/newsletters/unsubscribe/'+address["id"]
    content = HEAD+IMAGE+BODY.format(main_body = html)+FOOTER.format(link = unsubscribe_link)
    return content

def send_newsletters( message, subject, addresses, role):
    smtp = smtplib.SMTP(host='smtp.gmail.com', port=587)
    smtp.starttls()
    smtp.login(ADDRESS, KEY)
    for address in addresses:
        try:
            content = format_message(message=message,address=address)
            logger.debug(address)
            send_mail(smtp,address,subject,HTML.format(content=content),role)
        except  Exception as e:
            print(e)
            raise Exception(r'Failed x sending email to:{} with address:{} >>> {}'.format(address["name"],address["email"], str(e))) 
    smtp.quit()
                
def send_mail(smtp,address,subject, html, role):
    msg = MIMEMultipart()
    msg['From']= NAME
    msg['To']=address["email"]
    msg['Subject']=subject
    msg['Reply-To'] = ROLE_EMAIL[role]
    msg.add_header('List-Unsubscribe', FRONT_END_URL+'/newsletters/unsubscribe/'+address["id"])
    msg.attach(MIMEText(html, 'html'))
    try:
        smtp.send_message(msg)      
    except  Exception as e:
        print(e)
        raise Exception(r'Failed sending email to:{} with address:{}'.format(address.name,address.email)) 
    del msg
