import markdown
import smtplib
from string import Template
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from api.newsletters.template import *
from decouple import config
import logging

ADDRESS = config('NEWSLETTER_ADDRESS')
KEY = config('NEWSLETTER_APP_KEY')
NAME = config('NEWSLETTER_NAME')
logger = logging.getLogger("myapp")

def send_newsletters( message, subject, addresses, is_html):
    smtp = smtplib.SMTP(host='smtp.gmail.com', port=587)
    smtp.starttls()
    smtp.login(ADDRESS, KEY)
    # TODO create header based on the header of the api and footer based on the styling of the front end
    # TODO add selector to front end for either md or html
    # TODO enable both html and markdown just in case
    html = message if is_html else markdown.markdown(message)
    # TODO explore multithreading here to make sending emails one instant
    for address in addresses:
        try:
            # NOTE: change local host to actual server
            subscribe_link =  '"http://localhost:3000/newsletters/subscribe/"'+address["id"]
            unsubscribe_link =  '"http://localhost:3000/newsletters/unsubscribe/"'+address["id"]
            content = HEAD+IMAGE+BODY.format(main_body = html+BUTTON.format(link =unsubscribe_link, text="Call to action test"))+FOOTER.format(link = subscribe_link)
            send_mail(smtp,address,subject,HTML.format(content=content))
        except  Exception as e:
            raise Exception(r'Failed sending email to:{} with address:{}'.format(address["name"],address["email"])) 
    smtp.quit()
                
def send_mail(smtp,address,subject, html):
    msg = MIMEMultipart()
    msg['From']= NAME
    msg['To']=address["email"]
    msg['Subject']=subject
    msg.attach(MIMEText(html, 'html'))
    try:
        smtp.send_message(msg)      
    except  Exception as e:
        raise Exception(r'Failed sending email to:{} with address:{}'.format(address.name,address.email)) 
    del msg
