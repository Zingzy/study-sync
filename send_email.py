import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from constants import SENDER_EMAIL, SENDER_PASSWORD, SMTP_SERVER, SMTP_PORT


# function to send the verification email
def send_verification_email(recipient_email, verification_code, host):

    subject = "Verify your email address"
    verification_link = f"{host}/verify/{verification_code}"
    body = f"Click the link below to verify your email address:\n\n{verification_link}"
    message = MIMEMultipart()
    message["From"] = SENDER_EMAIL
    message["To"] = recipient_email
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipient_email, message.as_string())
