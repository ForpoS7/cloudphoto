import argparse
import configparser
import html
import sys
from pathlib import Path
from urllib.parse import quote

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError

CONFIG_PATH = Path.home() / ".config" / "cloudphoto" / "cloudphotorc"
ENDPOINT = "https://storage.yandexcloud.net"
REGION = "ru-central1"
PHOTO_EXT = (".jpg", ".jpeg")


class CloudPhotoError(Exception):
    """Ошибка, о которой нужно сообщить пользователю."""


# ---------- Настройки и подключение ----------

def load_config():
    if not CONFIG_PATH.exists():
        raise CloudPhotoError("Конфигурация не найдена. Сначала выполните: cloudphoto init")
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")
    cfg = parser["DEFAULT"]
    for key in ("bucket", "aws_access_key_id", "aws_secret_access_key"):
        if not cfg.get(key):
            raise CloudPhotoError(f"В конфигурации нет параметра {key}")
    return cfg


def make_client(cfg):
    return boto3.client(
        "s3",
        endpoint_url=cfg.get("endpoint_url", ENDPOINT),
        region_name=cfg.get("region", REGION),
        aws_access_key_id=cfg["aws_access_key_id"],
        aws_secret_access_key=cfg["aws_secret_access_key"],
    )


# ---------- Вспомогательные функции ----------

def check_album(album):
    if "/" in album:
        raise CloudPhotoError("Название альбома не может содержать символ '/'")


def list_keys(s3, bucket, prefix=""):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def get_albums(s3, bucket):
    albums = set()
    for key in list_keys(s3, bucket):
        if "/" in key:
            albums.add(key.split("/", 1)[0])
    return sorted(albums)


def get_photos(s3, bucket, album):
    prefix = album + "/"
    photos = []
    for key in list_keys(s3, bucket, prefix):
        name = key[len(prefix):]
        if name and "/" not in name and name.lower().endswith(PHOTO_EXT):
            photos.append(name)
    return sorted(photos)


def put_html(s3, bucket, key, body):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
    )


# ---------- Команды ----------

def cmd_init():
    key_id = input("aws_access_key_id: ").strip()
    secret = input("aws_secret_access_key: ").strip()
    bucket = input("bucket: ").strip()
    if not (key_id and secret and bucket):
        raise CloudPhotoError("Все три значения обязательны")

    parser = configparser.ConfigParser()
    parser["DEFAULT"] = {
        "bucket": bucket,
        "aws_access_key_id": key_id,
        "aws_secret_access_key": secret,
        "region": REGION,
        "endpoint_url": ENDPOINT,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        parser.write(f)

    # Проверяем бакет; если его нет — создаём
    s3 = make_client(parser["DEFAULT"])
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            s3.create_bucket(Bucket=bucket)
        else:
            raise CloudPhotoError(f"Нет доступа к бакету {bucket} (код {code})")
    print(f"Настройки сохранены в {CONFIG_PATH}")


def cmd_upload(args, s3, bucket):
    check_album(args.album)
    folder = Path(args.path or ".")
    if not folder.is_dir():
        raise CloudPhotoError(f"Каталог не найден: {folder}")

    # Берём только файлы с расширением фотографии, без вложенных папок
    photos = [
        p for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in PHOTO_EXT
    ]
    if not photos:
        raise CloudPhotoError(f"В каталоге {folder} нет фотографий")

    for p in photos:
        key = f"{args.album}/{p.name}"
        try:
            s3.upload_file(
                str(p), bucket, key,
                ExtraArgs={"ContentType": "image/jpeg"},
            )
            print(f"Загружено: {p.name}")
        except Exception as e:
            print(f"Предупреждение: не удалось загрузить {p.name}: {e}")


def cmd_download(args, s3, bucket):
    check_album(args.album)
    folder = Path(args.path)
    if not folder.is_dir():
        raise CloudPhotoError(f"Каталог {folder} не найден")

    photos = get_photos(s3, bucket, args.album)
    if not photos:
        raise CloudPhotoError(f"Альбом {args.album} не найден")

    for name in photos:
        try:
            s3.download_file(bucket, f"{args.album}/{name}", str(folder / name))
        except OSError as e:
            raise CloudPhotoError(f"Не удалось сохранить {name}: {e}")
        print(f"Скачано: {name}")


def cmd_list(args, s3, bucket):
    if args.album:
        check_album(args.album)
        photos = get_photos(s3, bucket, args.album)
        if not photos:
            raise CloudPhotoError(f"В альбоме {args.album} нет фотографий")
        print("\n".join(photos))
    else:
        albums = get_albums(s3, bucket)
        if not albums:
            raise CloudPhotoError("Фотоальбомы не найдены")
        print("\n".join(albums))


def cmd_delete(args, s3, bucket):
    check_album(args.album)
    keys = list_keys(s3, bucket, args.album + "/")
    if not keys:
        raise CloudPhotoError(f"Альбом {args.album} не найден")

    if args.photo:
        key = f"{args.album}/{args.photo}"
        if key not in keys:
            raise CloudPhotoError(f"Фотография {args.photo} не найдена в альбоме {args.album}")
        s3.delete_object(Bucket=bucket, Key=key)
        print(f"Удалено: {args.photo}")
        return

    # Удаляем все объекты альбома пачками по 1000
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i:i + 1000]]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": batch})
    print(f"Альбом {args.album} удалён")


ERROR_PAGE = """<!doctype html>
<html>
    <head>
        <title>Фотоархив</title>
    </head>
<body>
    <h1>Ошибка</h1>
    <p>Ошибка при доступе к фотоархиву. Вернитесь на <a href="index.html">главную страницу</a> фотоархива.</p>
</body>
</html>
"""


def render_index(albums):
    if not albums:
        return """<!doctype html>
<html>
    <head>
        <title>Фотоархив</title>
    </head>
<body>
    <h1>Фотоархив</h1>
    <p>Фотоархив пустой.</p>
</body>
"""
    items = "\n".join(
        f'        <li><a href="album{i}.html">{html.escape(album)}</a></li>'
        for i, album in enumerate(albums, start=1)
    )
    return f"""<!doctype html>
<html>
    <head>
        <title>Фотоархив</title>
    </head>
<body>
    <h1>Фотоархив</h1>
    <ul>
{items}
    </ul>
</body>
"""


def render_album(album, photos):
    name = html.escape(album)
    if not photos:
        return f"""<!doctype html>
<html>
    <head>
        <title>Фотоальбом "{name}"</title>
    </head>
    <body>
        <h1>Фотоальбом "{name}"</h1>
        <p>Фотоальбом пустой.</p>
        <p>Вернуться на <a href="index.html">главную страницу</a> фотоархива</p>
    </body>
</html>
"""
    images = "\n".join(
        f'            <img src="{quote(album + "/" + photo)}" data-title="{html.escape(photo)}">'
        for photo in photos
    )
    # Двойные фигурные скобки в f-строке дают одинарные в итоговом HTML
    return f"""<!doctype html>
<html>
    <head>
        <title>Фотоальбом "{name}"</title>
        <link rel="stylesheet" type="text/css" href="https://cdnjs.cloudflare.com/ajax/libs/galleria/1.6.1/themes/classic/galleria.classic.min.css" />
        <style>
            .galleria{{ width: 960px; height: 540px; background: #000 }}
        </style>
        <script src="https://ajax.googleapis.com/ajax/libs/jquery/3.6.0/jquery.min.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/galleria/1.6.1/galleria.min.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/galleria/1.6.1/themes/classic/galleria.classic.min.js"></script>
    </head>
    <body>
        <h1>Фотоальбом "{name}"</h1>
        <div class="galleria">
{images}
        </div>
        <p>Вернуться на <a href="index.html">главную страницу</a> фотоархива</p>
        <script>
            (function() {{
                Galleria.run('.galleria');
            }}());
        </script>
    </body>
</html>
"""


def cmd_mksite(args, s3, bucket):
    # Открываем бакет на чтение и включаем хостинг сайта
    s3.put_bucket_acl(Bucket=bucket, ACL="public-read")
    s3.put_bucket_website(
        Bucket=bucket,
        WebsiteConfiguration={
            "IndexDocument": {"Suffix": "index.html"},
            "ErrorDocument": {"Key": "error.html"},
        },
    )

    albums = get_albums(s3, bucket)
    for i, album in enumerate(albums, start=1):
        photos = get_photos(s3, bucket, album)
        put_html(s3, bucket, f"album{i}.html", render_album(album, photos))

    put_html(s3, bucket, "index.html", render_index(albums))
    put_html(s3, bucket, "error.html", ERROR_PAGE)

    print(f"http://{bucket}.website.yandexcloud.net/")

def main():
    parser = argparse.ArgumentParser(prog="cloudphoto")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    p = sub.add_parser("upload")
    p.add_argument("--album", required=True)
    p.add_argument("--path", default=".")

    p = sub.add_parser("download")
    p.add_argument("--album", required=True)
    p.add_argument("--path", default=".")

    p = sub.add_parser("list")
    p.add_argument("--album")

    p = sub.add_parser("delete")
    p.add_argument("--album", required=True)
    p.add_argument("--photo")

    sub.add_parser("mksite")

    args = parser.parse_args()
    handlers = {
        "upload": cmd_upload,
        "download": cmd_download,
        "list": cmd_list,
        "delete": cmd_delete,
        "mksite": cmd_mksite,
    }

    try:
        if args.command == "init":
            cmd_init()
        else:
            cfg = load_config()
            handlers[args.command](args, make_client(cfg), cfg["bucket"])
    except CloudPhotoError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except ClientError as e:
        print(f"Ошибка облака: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
