import json
import logging
import boto3
import boto3.exceptions
import boto3.session
import ydb
import uuid
from dotenv import load_dotenv
from config import Config
import requests
from io import BytesIO
from urllib.parse import urlparse, quote

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def is_yandex_disk_public_video(link):
    parsed_url = urlparse(link)
    if parsed_url.scheme != 'https':
        return False
    
    allowed_domains = [
        'yadi.sk', 
        'disk.yandex.ru', 'disk.360.yandex.ru', 
        'disk.yandex.com', 'disk.360.yandex.com', 
        'disk.yandex.by',  'disk.360.yandex.by', 
        'disk.yandex.kz', 'disk.360.yandex.kz'
    ]
    
    if not any(parsed_url.netloc.endswith(domain) for domain in allowed_domains):
        return False

    api_url = "https://cloud-api.yandex.net/v1/disk/public/resources"
    encoded_link = quote(link, safe='')
    params = {'public_key': encoded_link}
    headers = {'Accept': 'application/json'}

    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=10)
    except requests.exceptions.RequestException:
        return False

    if response.status_code == 200:
        try:
            data = response.json()
        except ValueError:
            return False
        
        return data.get('type') == 'file' and data.get('mime_type', '').startswith('video/')
    
    return False


def change_status_in_db(config: Config, task_id: str, status: str, description: str | None):
    driver_config = ydb.DriverConfig(
        config.ydb_endpoint, 
        config.ydb_database, 
        credentials=ydb.credentials_from_env_variables(),
        root_certificates=ydb.load_ydb_root_certificate(),
    )

    logger.info(f"Saving status {status} for task_id {task_id} to database")
    with ydb.Driver(driver_config) as driver:
        try:
            driver.wait(timeout=5)
            with ydb.QuerySessionPool(driver) as pool:
                id = uuid.UUID(task_id)

                pool.execute_with_retries(
                    f"""
                    UPDATE `{config.ydb_tasks_table_name}`
                    SET status = $status, description = $description
                    WHERE task_id = $taskId
                    """,
                    {
                        "$taskId": (id, ydb.PrimitiveType.UUID),
                        "$status": (status, ydb.PrimitiveType.Utf8),
                        "$description": (description, ydb.OptionalType(ydb.PrimitiveType.Utf8)),
                    }
                )
        except TimeoutError:
            logger.warning(f"Connect failed to YDB. Last reported errors by discovery: {driver.discovery_debug_details()}")
            exit(1)


def download_video_to_s3(config: Config, task_id: str, video_url: str) -> str:
    object_name = f"video/{task_id}"
    logger.info(f"Downloading video {object_name} to bucket {config.s3_bucket_name}")
        
    try:
        api_url = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
        encoded_link = quote(video_url, safe='')
        params = {'public_key': encoded_link}
        headers = {'Accept': 'application/json'}
        response = requests.get(api_url, params=params, headers=headers, timeout=10)
        real_video_url = response.json()['href']

        logger.info(f"Fetching video from URL: {real_video_url}")

        response = requests.get(real_video_url, stream=True, timeout=30)
        response.raise_for_status()
        
        session = boto3.session.Session()
        s3 = session.client(
            service_name='s3',
            endpoint_url="https://storage.yandexcloud.net",
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
        
        file_buffer = BytesIO(response.content)
        s3.upload_fileobj(
            file_buffer,
            config.s3_bucket_name,
            object_name,
            ExtraArgs={'ContentType': response.headers.get('content-type', 'video/mp4')}
        )

        return object_name
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download video from URL: {str(e)}")
        raise Exception(f"Video download failed: {str(e)}")
    except boto3.exceptions.S3UploadFailedError as e:
        logger.error(f"S3 upload failed: {str(e)}")
        raise Exception(f"S3 upload failed: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to process video upload: {str(e)}")
        raise


def send_message_to_queue(config: Config, task_id: str, object_name: str):
    logger.info(f"Sending message to queue: {config.extract_audio_queue_url}")
        
    message_body = json.dumps({
            'task_id': task_id,
            'object_name': object_name
        }, ensure_ascii=False)
        
    try:
        session = boto3.session.Session()
        sqs = session.client(
            service_name='sqs',
            endpoint_url='https://message-queue.api.cloud.yandex.net',
            region_name='ru-central1',
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
            
        response = sqs.send_message(
                QueueUrl=config.extract_audio_queue_url,
                MessageBody=message_body,
                MessageAttributes={
                    'Source': {
                        'StringValue': 'cloud-function',
                        'DataType': 'String'
                    }
                }
            )
            
        logger.info(f"Message sent successfully. MessageId: {response.get('MessageId', 'Unknown')}")
            
    except Exception as e:
        logger.error(f"Failed to send message to queue: {str(e)}")
        raise


def handler(event, context):
    try:
        logger.info(f"Event: {json.dumps(event, ensure_ascii=False)}")
        load_dotenv(".env")
        config = Config()
        
        body = json.loads(event['body'])
        task_id = body['task_id']
        video_url = body['video_url']
        
        logger.info(f"Received data: task_id={task_id}, video_url={video_url}")
        
        if not is_yandex_disk_public_video(video_url):
            change_status_in_db(config, task_id, "Ошибка", "Ссылка не ведет к публичному видео в Яндекс.Диск")
            return { 'statusCode': 200 }
        else:
            change_status_in_db(config, task_id, "В обработке", None)
        
        object_name = download_video_to_s3(config, task_id, video_url)

        send_message_to_queue(config, task_id, object_name)

        return { 'statusCode': 200 }
        
    except Exception as e:
        logger.error(f"Error in handler: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'text/plain'
            },
            'body': f'Error occurred: {str(e)}'
        }