import json
import logging
import boto3
import boto3.session
from urllib.parse import parse_qs
import ydb
import uuid
import datetime
from dotenv import load_dotenv
from config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def get_sqs_client(config: Config):
    session = boto3.session.Session()
    sqs = session.client(
        service_name='sqs',
        endpoint_url='https://message-queue.api.cloud.yandex.net',
        region_name='ru-central1',
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
    )
    return sqs

def parse_request_body(event):
    body = event.get('body', '')
    is_base64 = event.get('isBase64Encoded', False)
    
    if is_base64 and body:
        import base64
        body = base64.b64decode(body).decode('utf-8')
    
    try:
        parsed = parse_qs(body)
        result = {}
        for key, value in parsed.items():
            result[key] = value[0] if value else ''
        return result
    except Exception as e:
        logger.error(f"Failed to parse request body: {str(e)}")
    
    return {}

def add_task_to_db(pool: ydb.QuerySessionPool, table, 
                   lecture_title, video_url) -> str:
    current_time = datetime.datetime.now(datetime.timezone.utc)
    id = uuid.uuid4()

    pool.execute_with_retries(
        f"""
        DECLARE $taskId AS Uuid;
        DECLARE $createdAt As Timestamp;
        DECLARE $lectureTitle AS Utf8;
        DECLARE $videoUrl AS Utf8;

        UPSERT INTO `{table}` (
            created_at, task_id, lecture_title, video_url, status, description
        ) VALUES (
            $createdAt,
            $taskId,
            $lectureTitle,
            $videoUrl,
            'В очереди',
            NULL
        );
        """,
        {
            "$taskId": (id, ydb.PrimitiveType.UUID),
            "$createdAt": (current_time, ydb.PrimitiveType.Timestamp),
            "$lectureTitle": (lecture_title, ydb.PrimitiveType.Utf8),
            "$videoUrl": (video_url, ydb.PrimitiveType.Utf8),
        }
    )

    return str(id);

def send_message(config, task_id, video_url):
    logger.info(f"Sending message to queue: {config.download_queue_url}")
        
    message_body = json.dumps({
            'task_id': task_id,
            'video_url': video_url
        }, ensure_ascii=False)
        
    try:
        sqs = get_sqs_client(config)
            
        response = sqs.send_message(
                QueueUrl=config.download_queue_url,
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

def handler(event, context):
    try:
        logger.info(f"Event: {json.dumps(event, ensure_ascii=False)}")
        load_dotenv(".env")
        config = Config()
        
        request_data = parse_request_body(event)
        lecture_title = request_data.get('lecture-title', '')
        video_url = request_data.get('yandex-link', '')
        

        driver_config = ydb.DriverConfig(
            config.ydb_endpoint, 
            config.ydb_database, 
            credentials=ydb.credentials_from_env_variables(),
            root_certificates=ydb.load_ydb_root_certificate(),
        )

        logger.info(f"Received data: lecture_title={lecture_title}, video_url={video_url}")
        logger.info(f"Saving to database")
        task_id = None
        with ydb.Driver(driver_config) as driver:
            try:
                driver.wait(timeout=5)
                with ydb.QuerySessionPool(driver) as pool:
                    task_id = add_task_to_db(pool, config.ydb_tasks_table_name, lecture_title, video_url)
            except TimeoutError:
                logger.warning(f"Connect failed to YDB. Last reported errors by discovery: {driver.discovery_debug_details()}")
                exit(1)
    
        if not task_id:
            logger.warning("Couldn't insert to database")
            exit(1)

        send_message(config, task_id, video_url)

        return {
            'statusCode': 302,
            'headers': {
                'Location': config.redirect_url,
                'Content-Type': 'text/plain'
            },
            'body': f'Redirecting to {config.redirect_url}',
            'isBase64Encoded': False
        }
        
    except Exception as e:
        logger.error(f"Error in handler: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'text/plain'
            },
            'body': f'Error occurred: {str(e)}'
        }
    
handler({"body": "bGVjdHVyZS10aXRsZT1hYm9iYSZ5YW5kZXgtbGluaz1odHRwcyUzQSUyRiUyRmRpc2sueWFuZGV4LnJ1JTJG", "isBase64Encoded": True}, {})