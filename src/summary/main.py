import json
import logging
import boto3
import boto3.session
from yandex_cloud_ml_sdk import YCloudML
import ydb
import uuid
from weasyprint import HTML
import io
from dotenv import load_dotenv
from config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_lecture_name(config: Config, task_id: str) -> str:
    driver_config = ydb.DriverConfig(
        config.ydb_endpoint, 
        config.ydb_database, 
        credentials=ydb.credentials_from_env_variables(),
        root_certificates=ydb.load_ydb_root_certificate(),
    )

    logger.info(f"Getting lecture name of task_id {task_id} from database")
    with ydb.Driver(driver_config) as driver:
        try:
            driver.wait(timeout=5)
            with ydb.QuerySessionPool(driver) as pool:
                id = uuid.UUID(task_id)

                result_sets = pool.execute_with_retries(
                    f"""
                    SELECT lecture_title 
                    FROM `{config.ydb_tasks_table_name}`
                    WHERE task_id = $taskId
                    """,
                    {
                        "$taskId": (id, ydb.PrimitiveType.UUID),
                    }
                )
                row = result_sets[0].rows[0]
                return row.lecture_title
        except TimeoutError:
            logger.warning(f"Connect failed to YDB. Last reported errors by discovery: {driver.discovery_debug_details()}")
            exit(1)
            

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


def get_speech_summary_from_s3(config: Config, object_name: str) -> str:
    try:
      s3 = boto3.client(
        's3',
        endpoint_url='https://storage.yandexcloud.net',
        region_name='ru-central1',
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
      )
      
      resp = s3.get_object(Bucket=config.s3_bucket_name, Key=object_name)
      return resp["Body"].read().decode("utf-8")
    except Exception as e:
      logger.error(f"Error when getting object from s3: {e}")
      raise


def get_ai_html_summary(config: Config, lecture_name: str, speech_summary: str) -> str:
    instruction = f"Тебе даётся ТЕКСТ конспекта лекции, структурированный в виде JSON. Сделай из него HTML страницу, вставляя значения из JSON. В HTML в начале тега body должен быть заголовок <h1>{lecture_name}</h1>. Ответ должен начинаться с <!DOCTYPE html><html>. Пиши только в одной строке, т.е. новых строк, табов не должно быть между элементами. НЕ обрамляй ответ символами markdown code типа ```html. ТЕКСТ:"
    sdk = YCloudML(
        folder_id=config.folder_id,
        auth=config.ya_api_key,
    )

    model = sdk.models.completions("yandexgpt-lite", model_version="rc").configure(temperature=0.2)
    messages = [{"role": "system", "text": instruction}, {"role": "user", "text": speech_summary}]

    result = model.run(messages)
    return result.alternatives[0].text


def generate_s3_pdf_from_html(config: Config, html_str: str, 
                              task_id: str, lecture_name: str) -> str:
    try:
        pdf_buffer = io.BytesIO()
        HTML(string=html_str).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        
        object_name = f"pdf/{task_id}/{lecture_name}.pdf"

        s3_client = boto3.client(
            's3',
            endpoint_url='https://storage.yandexcloud.net',
            region_name='ru-central1',
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
        
        s3_client.upload_fileobj(
            pdf_buffer,
            config.s3_bucket_name,
            object_name,
            ExtraArgs={'ContentType': 'application/pdf'}
        )
        
        logger.info(f"PDF successfully uploaded as {object_name}")
        return object_name
        
    except Exception as e:
        print(f"Error generating or uploading PDF: {str(e)}")
        raise
    finally:
        if 'pdf_buffer' in locals():
            pdf_buffer.close() # type: ignore


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
        
            speech_summary = get_speech_summary_from_s3(config, object_name)
            lecture_name = get_lecture_name(config, task_id)

            html_summary = get_ai_html_summary(config, lecture_name, speech_summary)
            if html_summary.startswith("```") and html_summary.endswith("```"):
                html_summary = html_summary[3:-3]

            pdf_object_name = generate_s3_pdf_from_html(config, html_summary, task_id, lecture_name)

            change_status_in_db(config, task_id, "Успешно завершено", pdf_object_name)

            # TODO: update tasks html?
            # send_message_to_queue(config, "queue", "{}")
            
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
handler({"messages": [{'details': {'message': {"body": "{\"task_id\": \"42e9475a-8d1e-4603-a911-a0262af60b22\", \"object_name\": \"speech/42e9475a-8d1e-4603-a911-a0262af60b22\"}"}}}]}, {})