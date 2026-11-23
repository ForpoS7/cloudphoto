terraform {
  required_providers {
    yandex = {
      source = "yandex-cloud/yandex"
    }
  }
}

# Токен берётся из переменной окружения YC_TOKEN
provider "yandex" {
  folder_id = var.folder_id
}

# Сервисный аккаунт для программы cloudphoto
resource "yandex_iam_service_account" "sa" {
  name      = "vvot20-cloudphoto-sa"
  folder_id = var.folder_id
}

resource "yandex_resourcemanager_folder_iam_member" "sa_storage_admin" {
  folder_id = var.folder_id
  role      = "storage.admin"
  member    = "serviceAccount:${yandex_iam_service_account.sa.id}"
}

# Статический ключ: AWS_ACCESS_KEY_ID и AWS_SECRET_ACCESS_KEY
resource "yandex_iam_service_account_static_access_key" "sa_key" {
  service_account_id = yandex_iam_service_account.sa.id
  description        = "Ключ для cloudphoto"
}

# Бакет фотоархива, имя совпадает с доменом сайта
resource "yandex_storage_bucket" "photos" {
  bucket        = var.bucket_name
  access_key    = yandex_iam_service_account_static_access_key.sa_key.access_key
  secret_key    = yandex_iam_service_account_static_access_key.sa_key.secret_key
  force_destroy = true

  https {
    certificate_id = var.cert_id
  }

  # Публичный доступ и хостинг настраивает команда mksite
  lifecycle {
    ignore_changes = [acl, grant, website, anonymous_access_flags]
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.sa_storage_admin]
}

# CNAME для сайта photos.<domain>
resource "yandex_dns_recordset" "photos" {
  zone_id = var.dns_zone_id
  name    = "photos.${var.domain}."
  type    = "CNAME"
  ttl     = 600
  data    = ["${var.bucket_name}.website.yandexcloud.net."]
}
