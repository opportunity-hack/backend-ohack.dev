import markdown
import smtplib
from string import Template
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from decouple import config

ADDRESS = config('NEWSLETTER_ADDRESS')
KEY = config('NEWSLETTER_APP_KEY')
NAME = config('NEWSLETTER_NAME')

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
            send_mail(smtp,address,subject,message,html)
        except  Exception as e:
            raise Exception(r'Failed sending email to:{} with address:{}'.format(address.name,address.email)) 
    smtp.quit()
                
def send_mail(s,address,subject, message, html):
    msg = MIMEMultipart()
    msg['From']= NAME
    msg['To']=address.email
    msg['Subject']=subject
    msg.attach(MIMEText(html, 'html'))
    try:
        s.send_message(msg)      
    except  Exception as e:
        raise Exception(r'Failed sending email to:{} with address:{}'.format(address.name,address.email)) 
    del msg
