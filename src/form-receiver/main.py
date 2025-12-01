import json
import logging
import boto3
import boto3.session
from urllib.parse import parse_qs
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

def handler(event, context):
    try:
        logger.info(f"Event: {json.dumps(event, ensure_ascii=False)}")
        
        config = Config()
        
        request_data = parse_request_body(event)
        lecture_title = request_data.get('lecture-title', '')
        yandex_link = request_data.get('yandex-link', '')
        
        logger.info(f"Received data: lecture_title={lecture_title}, yandex_link={yandex_link}")
        logger.info(f"Sending message to queue: {config.download_queue_url}")
        
        message_body = json.dumps({
            'lecture_title': lecture_title,
            'yandex_link': yandex_link
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
    
handler({}, {})