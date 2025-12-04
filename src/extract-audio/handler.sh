#!/usr/bin/env bash

set -euo pipefail

missing_vars=()
[ -z "$S3_BUCKET_NAME" ] && missing_vars+=("S3_BUCKET_NAME")
[ -z "$RECOGNIZE_SPEECH_QUEUE_URL" ] && missing_vars+=("RECOGNIZE_SPEECH_QUEUE_URL")
[ -z "$AWS_ACCESS_KEY_ID" ] && missing_vars+=("AWS_ACCESS_KEY_ID")
[ -z "$AWS_SECRET_ACCESS_KEY" ] && missing_vars+=("AWS_SECRET_ACCESS_KEY")

if [ ${#missing_vars[@]} -gt 0 ]; then
  echo "Error: Missing required environment variables: ${missing_vars[*]}" >&2
  exit 1
fi

input_json=$(cat)

echo "$input_json" | jq -c '.messages[]' | while IFS= read -r message; do
 
  body=$(echo "$message" | jq -r '.details.message.body')
  task_id=$(echo "$body" | jq -r '.task_id')
  video_path=$(echo "$body" | jq -r '.object_name')
  
  video_file="/tmp/${task_id}.video"
  audio_file="/tmp/${task_id}.mp3"
  audio_path="audio/${task_id}"
  notification_body=$(jq -nc --arg tid "$task_id" --arg obj "audio/$task_id" \
    '{task_id: $tid, object_name: $obj}')

  echo "Processing task: $task_id" >&2
  echo "Downloading video: $video_path" >&2

  yc storage s3api get-object \
    --bucket "$S3_BUCKET_NAME" \
    --key "$video_path" \
    "$video_file" >/dev/null

  echo "Extracting audio to MP3 format" >&2
  
  ffmpeg -loglevel error -i "$video_file" -vn -acodec libmp3lame "$audio_file" >&2

  echo "Uploading audio to: $audio_path" >&2

  yc storage s3api put-object \
    --bucket "$S3_BUCKET_NAME" \
    --key "$audio_path" \
    --body "$audio_file" \
    --content-type "audio/mpeg" >/dev/null

  rm -f "$video_file" "$audio_file"

  curl \
    --request POST \
    --header 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'Action=SendMessage' \
    --data-urlencode "MessageBody=$notification_body" \
    --data-urlencode "QueueUrl=$RECOGNIZE_SPEECH_QUEUE_URL" \
    --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
    --aws-sigv4 'aws:amz:ru-central1:sqs' \
    https://message-queue.api.cloud.yandex.net/ >&2

  echo "Completed processing task: $task_id" >&2
done

echo '{"statusCode": 200}'