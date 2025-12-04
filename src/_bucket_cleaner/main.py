import os
import boto3
import boto3.session
from botocore.exceptions import ClientError
from dotenv import load_dotenv

class Config:
    def __init__(self):
        self.s3_bucket_name = os.environ["S3_BUCKET_NAME"]
        self.aws_access_key_id = os.environ["AWS_ACCESS_KEY_ID"]
        self.aws_secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"]

def delete_all_objects_in_bucket():
    config = Config()
    
    session = boto3.session.Session()
    s3 = session.client(
        service_name='s3',
        endpoint_url="https://storage.yandexcloud.net",
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
    )
    
    bucket_name = config.s3_bucket_name
    print(f"Starting deletion of all objects in bucket: {bucket_name}")
    
    try:
        continuation_token = None
        total_deleted = 0
        
        while True:
            list_kwargs = {
                'Bucket': bucket_name,
                'MaxKeys': 1000
            }
            
            if continuation_token:
                list_kwargs['ContinuationToken'] = continuation_token
            
            response = s3.list_objects_v2(**list_kwargs)
            
            if 'Contents' not in response:
                print("No objects found in the bucket.")
                break
            
            objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]
            
            delete_response = s3.delete_objects(
                Bucket=bucket_name,
                Delete={'Objects': objects_to_delete}
            )
            
            deleted_count = len(delete_response.get('Deleted', []))
            total_deleted += deleted_count
            print(f"Deleted {deleted_count} objects in this batch. Total deleted so far: {total_deleted}")
            
            if response.get('IsTruncated', False):
                continuation_token = response.get('NextContinuationToken')
            else:
                break
        
        print(f"Successfully deleted all {total_deleted} objects from bucket {bucket_name}")
        return total_deleted
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'NoSuchBucket':
            print(f"Error: Bucket {bucket_name} does not exist")
        else:
            print(f"An error occurred: {e}")
        return 0

if __name__ == "__main__":
    load_dotenv('.env')
    required_env_vars = ["S3_BUCKET_NAME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    for var in required_env_vars:
        if var not in os.environ:
            print(f"Error: Environment variable {var} is not set")
            exit(1)
    
    delete_all_objects_in_bucket()