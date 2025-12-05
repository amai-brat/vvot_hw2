import json
import logging
import ydb
from dotenv import load_dotenv
from config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_tasks(config: Config) -> list[dict]:
    driver_config = ydb.DriverConfig(
        config.ydb_endpoint, 
        config.ydb_database, 
        credentials=ydb.credentials_from_env_variables(),
        root_certificates=ydb.load_ydb_root_certificate(),
    )

    logger.info(f"Getting lectures from database")
    with ydb.Driver(driver_config) as driver:
        try:
            driver.wait(timeout=5)
            with ydb.QuerySessionPool(driver) as pool:
                result_sets = pool.execute_with_retries(
                    f"""
                    SELECT created_at, task_id, lecture_title, video_url, status, description
                    FROM `{config.ydb_tasks_table_name}`
                    ORDER BY created_at DESC
                    """
                )

                result = []
                for row in result_sets[0].rows:
                    task = {
                        'created_at': str(row.created_at), 
                        'task_id': str(row.task_id), 
                        'lecture_name': row.lecture_title, 
                        'video_url': row.video_url, 
                        'status': row.status, 
                        'description': row.description
                    }
                    result.append(task)
                return result
            
        except TimeoutError:
            logger.warning(f"Connect failed to YDB. Last reported errors by discovery: {driver.discovery_debug_details()}")
            exit(1)


def handler(event, context):
    try:
        logger.info(f"Event: {json.dumps(event, ensure_ascii=False)}")
        load_dotenv(".env")
        config = Config()

        tasks = get_tasks(config)
        body = json.dumps({'tasks': tasks}, ensure_ascii=False)

        return { 
            'statusCode': 200, 
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': body
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