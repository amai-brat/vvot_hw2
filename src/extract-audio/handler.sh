#!/usr/bin/env bash

set -euo pipefail

if [ -z "$S3_BUCKET_NAME" ]; then
  echo "Error: S3_BUCKET_NAME environment variable is not set" >&2
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
  echo "Completed processing task: $task_id" >&2
done

echo '{"statusCode": 200}'