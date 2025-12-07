terraform {
  required_providers {
    yandex = {
      source = "yandex-cloud/yandex"
    }
  }
  required_version = ">= 0.13"
}

provider "yandex" {
  zone = "ru-central1-d"
}

// ydb
resource "yandex_ydb_database_serverless" "ydb" {
  name      = "${var.prefix}-ydb"
  folder_id = var.folder_id
}

resource "yandex_ydb_table" "tasks_table" {
  path              = "${var.prefix}_dir/tasks_table"
  connection_string = yandex_ydb_database_serverless.ydb.ydb_full_endpoint

  column {
    name     = "created_at"
    type     = "Timestamp"
    not_null = true
  }
  column {
    name     = "task_id"
    type     = "UUID"
    not_null = true
  }
  column {
    name     = "lecture_title"
    type     = "Utf8"
    not_null = false
  }
  column {
    name     = "video_url"
    type     = "Utf8"
    not_null = false
  }
  column {
    name     = "status"
    type     = "Utf8"
    not_null = true
  }
  column {
    name     = "description"
    type     = "Utf8"
    not_null = false
  }
  primary_key = ["task_id"]
}

// SA
resource "yandex_iam_service_account" "sa" {
  folder_id = var.folder_id
  name      = "${var.prefix}-tf-sa"
}

resource "yandex_resourcemanager_folder_iam_member" "sa_editor" {
  folder_id = var.folder_id
  role      = "editor"
  member    = "serviceAccount:${yandex_iam_service_account.sa.id}"
}

resource "yandex_iam_service_account_api_key" "sa_api_key" {
  service_account_id = yandex_iam_service_account.sa.id
}

// bucket
resource "yandex_resourcemanager_folder_iam_member" "sa_storage_admin" {
  folder_id = var.folder_id
  role      = "storage.admin"
  member    = "serviceAccount:${yandex_iam_service_account.sa.id}"
}

resource "yandex_iam_service_account_static_access_key" "sa_static_key" {
  service_account_id = yandex_iam_service_account.sa.id
  description        = "static access key for object storage"
}

resource "yandex_storage_bucket" "bucket" {
  bucket     = "${var.prefix}-temp"
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
  depends_on = [yandex_resourcemanager_folder_iam_member.sa_storage_admin]

  lifecycle_rule {
    id      = "temp"
    enabled = true

    expiration {
      days = 1
    }
  }
}

// queue
resource "yandex_resourcemanager_folder_iam_member" "sa_ymq_admin" {
  folder_id = var.folder_id
  role      = "ymq.admin"
  member    = "serviceAccount:${yandex_iam_service_account.sa.id}"
}

resource "yandex_message_queue" "deadletter_queue" {
  name       = "${var.prefix}-deadletter-queue"
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
  depends_on = [yandex_resourcemanager_folder_iam_member.sa_ymq_admin]
}

// form-receiver: function
data "archive_file" "form_receiver_zip" {
  type        = "zip"
  output_path = "function-form-receiver.zip"
  source_dir  = "../src/form-receiver"

  excludes = ["__pycache__", "*.pyc", ".DS_Store", ".env", ".python-version", ".venv", "uv.lock"]
}

resource "yandex_function" "form_receiver" {
  name               = "${var.prefix}-form-receiver"
  description        = "Функция получает форму из API gateway, создаёт строку в YDB и отправляет сообщение в очередь download"
  user_hash          = data.archive_file.form_receiver_zip.output_sha256
  runtime            = "python312"
  entrypoint         = "main.handler"
  memory             = "128"
  execution_timeout  = "60"
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id
  content {
    zip_filename = data.archive_file.form_receiver_zip.output_path
  }
  environment = {
    YDB_ENDPOINT          = "grpcs://${yandex_ydb_database_serverless.ydb.ydb_api_endpoint}"
    YDB_DATABASE          = yandex_ydb_database_serverless.ydb.database_path
    YDB_TASKS_TABLE_NAME  = yandex_ydb_table.tasks_table.path
    AWS_ACCESS_KEY_ID     = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    DOWNLOAD_QUEUE_URL    = data.yandex_message_queue.download_queue.url
  }
}

// download: queue -> trigger -> function
resource "yandex_message_queue" "download_queue" {
  name                       = "${var.prefix}-download-queue"
  visibility_timeout_seconds = 600
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.deadletter_queue.arn
    maxReceiveCount     = 3
  })
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}
data "yandex_message_queue" "download_queue" {
  name       = yandex_message_queue.download_queue.name
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}

resource "yandex_function_trigger" "download_trigger" {
  name      = "${var.prefix}-download-trigger"
  folder_id = var.folder_id
  message_queue {
    queue_id           = yandex_message_queue.download_queue.arn
    batch_cutoff       = "2"
    batch_size         = 1
    service_account_id = yandex_iam_service_account.sa.id
  }
  function {
    id                 = yandex_function.download.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

data "archive_file" "download_zip" {
  type        = "zip"
  output_path = "function-download.zip"
  source_dir  = "../src/download"

  excludes = ["__pycache__", "*.pyc", ".DS_Store", ".env", ".python-version", ".venv", "uv.lock"]
}

resource "yandex_function" "download" {
  name               = "${var.prefix}-download"
  description        = "Функция получает сообщение с очереди, скачивает в s3 video/* и отправляет сообщение c названием объекта в очередь extract-audio"
  user_hash          = data.archive_file.download_zip.output_sha256
  runtime            = "python312"
  entrypoint         = "main.handler"
  memory             = "1024"
  execution_timeout  = "60"
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id
  content {
    zip_filename = data.archive_file.download_zip.output_path
  }
  environment = {
    YDB_ENDPOINT            = "grpcs://${yandex_ydb_database_serverless.ydb.ydb_api_endpoint}"
    YDB_DATABASE            = yandex_ydb_database_serverless.ydb.database_path
    YDB_TASKS_TABLE_NAME    = yandex_ydb_table.tasks_table.path
    AWS_ACCESS_KEY_ID       = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY   = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    S3_BUCKET_NAME          = yandex_storage_bucket.bucket.bucket
    EXTRACT_AUDIO_QUEUE_URL = data.yandex_message_queue.extract_audio_queue.url
  }
}

// extract-audio: queue -> trigger -> function
resource "yandex_message_queue" "extract_audio_queue" {
  name                       = "${var.prefix}-extract-audio"
  visibility_timeout_seconds = 600
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.deadletter_queue.arn
    maxReceiveCount     = 3
  })
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}
data "yandex_message_queue" "extract_audio_queue" {
  name       = yandex_message_queue.extract_audio_queue.name
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}

resource "yandex_function_trigger" "extract_audio_trigger" {
  name      = "${var.prefix}-extract-audio-trigger"
  folder_id = var.folder_id
  message_queue {
    // https://yandex.cloud/ru/docs/functions/operations/trigger/ymq-trigger-create#tf_1
    queue_id           = yandex_message_queue.extract_audio_queue.arn
    batch_cutoff       = "2"
    batch_size         = 1
    service_account_id = yandex_iam_service_account.sa.id
  }
  function {
    id                 = yandex_function.extract_audio.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

data "archive_file" "extract_audio_zip" {
  type        = "zip"
  output_path = "function-extract-audio.zip"
  source_dir  = "../src/extract-audio"

  excludes = [".env"]
}

// большой размер zip из-за ffmpeg -> надо сначала загрузить в s3
resource "yandex_storage_object" "extract_audio_zip" {
  bucket = yandex_storage_bucket.bucket.bucket
  key    = "extract_audio_function.zip"
  source = data.archive_file.extract_audio_zip.output_path
}

resource "yandex_function" "extract_audio" {
  name               = "${var.prefix}-extract-audio"
  description        = "Функция получает сообщение с очереди, выделяет аудио, сохраняет в s3 audio/* и отправляет сообщение c названием объекта в очередь recognize-speech"
  user_hash          = data.archive_file.extract_audio_zip.output_sha256
  runtime            = "bash-2204"
  entrypoint         = "handler.sh"
  memory             = "128"
  execution_timeout  = "60"
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id
  package {
    bucket_name = yandex_storage_bucket.bucket.bucket
    object_name = yandex_storage_object.extract_audio_zip.key
  }
  environment = {
    AWS_ACCESS_KEY_ID          = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY      = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    S3_BUCKET_NAME             = yandex_storage_bucket.bucket.bucket
    RECOGNIZE_SPEECH_QUEUE_URL = data.yandex_message_queue.recognize_speech_queue.url
  }
}

// recognize-speech: queue -> trigger -> function -> s3 (speech-tasks/*) <-- recognize-speech-cron
resource "yandex_message_queue" "recognize_speech_queue" {
  name                       = "${var.prefix}-recognize-speech-queue"
  visibility_timeout_seconds = 600
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.deadletter_queue.arn
    maxReceiveCount     = 3
  })
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}
data "yandex_message_queue" "recognize_speech_queue" {
  name       = yandex_message_queue.recognize_speech_queue.name
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}

resource "yandex_function_trigger" "recognize_speech_trigger" {
  name      = "${var.prefix}-recognize-speech-trigger"
  folder_id = var.folder_id
  message_queue {
    queue_id           = yandex_message_queue.recognize_speech_queue.arn
    batch_cutoff       = "2"
    batch_size         = 1
    service_account_id = yandex_iam_service_account.sa.id
  }
  function {
    id                 = yandex_function.recognize_speech.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

data "archive_file" "recognize_speech_zip" {
  type        = "zip"
  output_path = "function-recognize-speech.zip"
  source_dir  = "../src/recognize-speech"

  excludes = ["__pycache__", "*.pyc", ".DS_Store", ".env", ".python-version", ".venv", "uv.lock"]
}

resource "yandex_function" "recognize_speech" {
  name               = "${var.prefix}-recognize-speech"
  description        = "Функция получает сообщение с очереди, отправляет задачу на распознавание текста и сохраняет об этом информацию в s3 speech-tasks/*"
  user_hash          = data.archive_file.recognize_speech_zip.output_sha256
  runtime            = "python312"
  entrypoint         = "main.handler"
  memory             = "1024"
  execution_timeout  = "60"
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id
  content {
    zip_filename = data.archive_file.recognize_speech_zip.output_path
  }
  environment = {
    AWS_ACCESS_KEY_ID     = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    S3_BUCKET_NAME        = yandex_storage_bucket.bucket.bucket
    FOLDER_ID             = var.folder_id
    YA_API_KEY            = yandex_iam_service_account_api_key.sa_api_key.secret_key
  }
}

resource "yandex_function_trigger" "recognize_speech_cron_trigger" {
  name      = "${var.prefix}-recognize-speech-cron-trigger"
  folder_id = var.folder_id
  timer {
    cron_expression = "* * ? * * *"
  }
  function {
    id                 = yandex_function.recognize_speech_cron.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

data "archive_file" "recognize_speech_cron_zip" {
  type        = "zip"
  output_path = "function-recognize-speech-cron.zip"
  source_dir  = "../src/recognize-speech-cron"

  excludes = ["__pycache__", "*.pyc", ".DS_Store", ".env", ".python-version", ".venv", "uv.lock"]
}

resource "yandex_function" "recognize_speech_cron" {
  name               = "${var.prefix}-recognize-speech-cron"
  description        = "Функция запускается по cron, смотрит по всем задачам на распознавание в s3 speech-tasks/*. Если задача закончилась, сохраняет транскрипцию в s3 speech/* и отправляет сообщение в очередь summary"
  user_hash          = data.archive_file.recognize_speech_cron_zip.output_sha256
  runtime            = "python312"
  entrypoint         = "main.handler"
  memory             = "256"
  execution_timeout  = "60"
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id
  content {
    zip_filename = data.archive_file.recognize_speech_cron_zip.output_path
  }
  environment = {
    AWS_ACCESS_KEY_ID     = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    S3_BUCKET_NAME        = yandex_storage_bucket.bucket.bucket
    YA_API_KEY            = yandex_iam_service_account_api_key.sa_api_key.secret_key
    SUMMARY_QUEUE_URL     = data.yandex_message_queue.summary_queue.url
  }
}

// summary: queue -> trigger -> function -> ydb
resource "yandex_message_queue" "summary_queue" {
  name                       = "${var.prefix}-summary-queue"
  visibility_timeout_seconds = 600
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.deadletter_queue.arn
    maxReceiveCount     = 3
  })
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}
data "yandex_message_queue" "summary_queue" {
  name       = yandex_message_queue.summary_queue.name
  access_key = yandex_iam_service_account_static_access_key.sa_static_key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
}

resource "yandex_function_trigger" "summary_trigger" {
  name      = "${var.prefix}-summary-trigger"
  folder_id = var.folder_id
  message_queue {
    queue_id           = yandex_message_queue.summary_queue.arn
    batch_cutoff       = "2"
    batch_size         = 1
    service_account_id = yandex_iam_service_account.sa.id
  }
  function {
    id                 = yandex_function.summary.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

data "archive_file" "summary_zip" {
  type        = "zip"
  output_path = "function-summary.zip"
  source_dir  = "../src/summary"

  excludes = ["__pycache__", "*.pyc", ".DS_Store", ".env", ".python-version", ".venv", "uv.lock"]
}

resource "yandex_function" "summary" {
  name               = "${var.prefix}-summary"
  description        = "Функция получает сообщение с очереди, отправляет запрос в LLM для генерации HTML, из HTML генерируется PDF и сохраняется в s3 pdf/{task_id}/{lecture_name}.pdf"
  user_hash          = data.archive_file.summary_zip.output_sha256
  runtime            = "python312"
  entrypoint         = "main.handler"
  memory             = "1024"
  execution_timeout  = "60"
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id
  content {
    zip_filename = data.archive_file.summary_zip.output_path
  }
  environment = {
    YDB_ENDPOINT          = "grpcs://${yandex_ydb_database_serverless.ydb.ydb_api_endpoint}"
    YDB_DATABASE          = yandex_ydb_database_serverless.ydb.database_path
    YDB_TASKS_TABLE_NAME  = yandex_ydb_table.tasks_table.path
    AWS_ACCESS_KEY_ID     = yandex_iam_service_account_static_access_key.sa_static_key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa_static_key.secret_key
    S3_BUCKET_NAME        = yandex_storage_bucket.bucket.bucket
    YA_API_KEY            = yandex_iam_service_account_api_key.sa_api_key.secret_key
    FOLDER_ID             = var.folder_id
  }
}

// fetch-ydb: function
data "archive_file" "fetch_ydb_zip" {
  type        = "zip"
  output_path = "function-fetch-ydb.zip"
  source_dir  = "../src/fetch-ydb"

  excludes = ["__pycache__", "*.pyc", ".DS_Store", ".env", ".python-version", ".venv", "uv.lock"]
}

resource "yandex_function" "fetch_ydb" {
  name               = "${var.prefix}-fetch-ydb"
  description        = "Функция возвращает все задачи с YDB"
  user_hash          = data.archive_file.fetch_ydb_zip.output_sha256
  runtime            = "python312"
  entrypoint         = "main.handler"
  memory             = "256"
  execution_timeout  = "60"
  folder_id          = var.folder_id
  service_account_id = yandex_iam_service_account.sa.id
  content {
    zip_filename = data.archive_file.fetch_ydb_zip.output_path
  }
  environment = {
    YDB_ENDPOINT         = "grpcs://${yandex_ydb_database_serverless.ydb.ydb_api_endpoint}"
    YDB_DATABASE         = yandex_ydb_database_serverless.ydb.database_path
    YDB_TASKS_TABLE_NAME = yandex_ydb_table.tasks_table.path
  }
}

// api gateway
resource "yandex_storage_object" "form_html" {
  bucket       = yandex_storage_bucket.bucket.bucket
  key          = "form.html"
  source       = "../src/html/form.html"
  content_type = "text/html"
}

resource "yandex_storage_object" "tasks_html" {
  bucket       = yandex_storage_bucket.bucket.bucket
  key          = "tasks.html"
  source       = "../src/html/tasks.html"
  content_type = "text/html"
}

resource "yandex_api_gateway" "tasks_gateway" {
  name      = "${var.prefix}-gateway"
  folder_id = var.folder_id

  spec = templatefile("./gateway_spec.yaml.tpl", {
    api_name = "${var.prefix}-api"

    bucket_name      = yandex_storage_bucket.bucket.bucket
    index_object_key = yandex_storage_object.form_html.key
    tasks_object_key = yandex_storage_object.tasks_html.key

    service_account_id = yandex_iam_service_account.sa.id

    fetch_ydb_function_id     = yandex_function.fetch_ydb.id
    form_receiver_function_id = yandex_function.form_receiver.id
  })
}

output "api_gateway_url" {
  value       = yandex_api_gateway.tasks_gateway.domain
  description = "API Gateway URL"
}