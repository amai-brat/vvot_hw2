import os

class Config:
  def __init__(self):
    self.ydb_endpoint = os.environ["YDB_ENDPOINT"]
    self.ydb_database = os.environ["YDB_DATABASE"]
    self.ydb_tasks_table_name = os.environ["YDB_TASKS_TABLE_NAME"]
    self.download_queue_url = os.environ["DOWNLOAD_QUEUE_URL"]
    self.aws_access_key_id = os.environ["AWS_ACCESS_KEY_ID"]
    self.aws_secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"]