variable "folder_id" {
  description = "ID каталога Yandex Cloud"
  type        = string
}

variable "bucket_name" {
  description = "Имя бакета, совпадает с BUCKET программы cloudphoto"
  type        = string
}

variable "domain" {
  description = "Домен, в который добавляются DNS-записи сайта"
  type        = string
}

variable "cert_id" {
  description = "ID сертификата в Certificate Manager"
  type        = string
}

variable "dns_zone_id" {
  description = "ID DNS-зоны"
  type        = string
}