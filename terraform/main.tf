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
