import json
import logging
import boto3
import boto3.session
import requests
from dotenv import load_dotenv
from config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_s3_client = None

def get_s3_client(config: Config):
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            's3',
            endpoint_url='https://storage.yandexcloud.net',
            region_name='ru-central1',
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
    return _s3_client


def check_recognition_status(config: Config, operation_id: str) -> tuple[bool, dict]:
    logger.info(f"Checking status for operation ID: {operation_id}")
    
    headers = {
        "Authorization": f"Api-Key {config.ya_api_key}"
    }
    
    params = {
        "operationId": operation_id
    }

    url = f"https://stt.api.cloud.yandex.net/stt/v3/getRecognition"
    
    try:
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 404:
            return (False, response.json())
        
        # Почему-то ответ с несколькими JSON-объектами приходит, только в последней строке есть резюме распознавания
        return (True, json.loads(response.text.splitlines()[-1]))
    
    except Exception as e:
        logger.error(f"Failed to check recognition status: {str(e)}")
        raise


def save_recognition_result(config: Config, task_id: str, result_data: dict) -> str:
    s3_client = get_s3_client(config)
    object_key = f"speech/{task_id}"
    
    try:
        s3_client.put_object(
            Bucket=config.s3_bucket_name,
            Key=object_key,
            Body=json.dumps(result_data, ensure_ascii=False),
            ContentType='application/json'
        )
        logger.info(f"Recognition result saved to {object_key}")
        return object_key
    except Exception as e:
        logger.error(f"Failed to save recognition result: {str(e)}")
        raise


def send_message_to_queue(config: Config, queue_url: str, message_body: str):
    logger.info(f"Sending message to queue: {queue_url}")

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
                QueueUrl=queue_url,
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


def check_completed_tasks(config: Config):
    s3_client = get_s3_client(config)
    
    try:
        response = s3_client.list_objects_v2(
            Bucket=config.s3_bucket_name,
            Prefix='speech-tasks/'
        )
        
        if 'Contents' not in response:
            logger.info("No active tasks found")
            return
            
        for obj in response['Contents']:
            task_key = obj['Key']
            task_id = task_key.split('/')[-1]
            
            try:
                # Читаем информацию о задаче
                task_obj = s3_client.get_object(Bucket=config.s3_bucket_name, Key=task_key)
                task_info = json.loads(task_obj['Body'].read().decode('utf-8'))
                
                # Проверяем статус операции
                ok, resp = check_recognition_status(config, task_info['operation_id'])
                
                if ok:
                    logger.info(f"Task {task_id} completed")

                    object_name = save_recognition_result(
                        config, 
                        task_id, 
                        json.loads(resp['result']['summarization']['results'][0]['response']))
                    message = json.dumps({
                        "task_id": task_id,
                        "object_name": object_name
                    })
                    
                    send_message_to_queue(config, config.summary_queue_url, message)
                    
                    s3_client.delete_object(
                        Bucket=config.s3_bucket_name,
                        Key=task_key
                    )
                    logger.info(f"Task {task_id} processed and removed from active tasks")
                else:
                    logger.info(f"Text is not ready yet: {resp['error']['message']}")        
                    
            except Exception as e:
                logger.error(f"Error processing task {task_id}: {str(e)}")
                continue
                
    except Exception as e:
        logger.error(f"Error checking completed tasks: {str(e)}")
        raise


def handler(event, context):
    try:
        logger.info(f"Event: {json.dumps(event, ensure_ascii=False)}")
        load_dotenv(".env")
        config = Config()

        logger.info(f"Checking completed tasks")
        
        check_completed_tasks(config)
        
        return {'statusCode': 200}
        
    except Exception as e:
        logger.error(f"Error in handler: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'text/plain'
            },
            'body': f'Error occurred: {str(e)}'
        }
    
handler({'messages':[
    {'details': {'message': {'body': '{\"object_name\": \"audio/829f2916-a98c-4003-9487-83aed1a2a72e\", \"task_id\": \"829f2916-a98c-4003-9487-83aed1a2a72e\"}'}}}]}, {})