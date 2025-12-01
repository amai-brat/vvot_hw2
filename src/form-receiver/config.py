import os

class Config:
  def __init__(self):
    self.download_queue_url = os.environ["DOWNLOAD_QUEUE_URL"]
    self.aws_access_key_id = os.environ["AWS_ACCESS_KEY_ID"]
    self.aws_secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"]
    self.redirect_url = os.environ['REDIRECT_URL']