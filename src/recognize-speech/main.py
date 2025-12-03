import json
import logging
import boto3
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from config import Config
from urllib.parse import quote

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


def get_public_object_url(config: Config, object_name: str) -> str:
    encoded_object_name = quote(object_name)
    return f"https://storage.yandexcloud.net/{config.s3_bucket_name}/{encoded_object_name}"

def start_speech_recognition(config: Config, object_url: str) -> str:
    logger.info(f"Starting speech recognition for URL: {object_url}")
    
    headers = {
        "Authorization": f"Api-Key {config.ya_api_key}"
    }
    
    data = {
        "uri": object_url,
        "recognitionModel": {
            "model": "general",
            "audioFormat": {
            "containerAudio": {
                "containerAudioType": "MP3"
            }
            },
            "languageRestriction": {
            "restrictionType": "WHITELIST",
            "languageCode": [
                "ru-RU",
                "en-US"
            ]
            }
        },
        "summarization": {
            "modelUri": f"gpt://{config.folder_id}/qwen3-235b-a22b-fp8/latest",
            "properties": [
            {
                "instruction": "Напиши конспект по лекции. Хорошо структурируй информацию, запоминай примеры. Названия полей в JSON пиши на английском языке.",
                "jsonObject": True 
            }]
        }
    }
    
    url = "https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync"
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        operation_id = result.get('id')
        
        logger.info(f"Speech recognition started successfully. Operation ID: {operation_id}")
        return operation_id
    
    except Exception as e:
        logger.error(f"Failed to start speech recognition: {str(e)}")
        raise


def process_recognition_task(config: Config, task_id: str, object_name: str):
    try:
        object_url = get_public_object_url(config, object_name)
        logger.info(f"Object URL: {object_url}")
        
        operation_id = start_speech_recognition(config, object_url)
        
        task_info = {
            "task_id": task_id,
            "object_name": object_name,
            "operation_id": operation_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        s3_client = get_s3_client(config)
        task_key = f"speech-tasks/{task_id}"
        
        s3_client.put_object(
            Bucket=config.s3_bucket_name,
            Key=task_key,
            Body=json.dumps(task_info, ensure_ascii=False),
            ContentType='application/json'
        )
        
        logger.info(f"Task info saved to {task_key}. Operation ID: {operation_id}")
        
        return task_info
        
    except Exception as e:
        logger.error(f"Error processing recognition task: {str(e)}")
        raise


def handler(event, context):
    try:
        logger.info(f"Event: {json.dumps(event, ensure_ascii=False)}")
        load_dotenv(".env")
        config = Config()
        
        for message in event["messages"]:
            body = json.loads(message['details']['message']['body'])
            task_id = body['task_id']
            object_name = body['object_name']
            
            logger.info(f"Received data: task_id={task_id}, object_name={object_name}")
            
            process_recognition_task(config, task_id, object_name)
        
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