"""Configuration for the private, disposable quality backend (real authentication)."""

import secrets


def account_password():
    """96 random bits within AuthLoginReqVO's 16-character maximum."""
    return secrets.token_urlsafe(12)


def verify_empty_database(name, table_count):
    if name != "ruoyi_quality" or table_count != 0:
        raise ValueError("bootstrap requires the empty owned ruoyi_quality database")


def configuration():
    return {
        "server": {"port": 48080},
        "logging": {"file": {"name": "/tmp/aether-quality.log"}},
        "spring": {
            "datasource": {
                "dynamic": {
                    "primary": "master",
                    "datasource": {
                        "master": {
                            "url": "jdbc:mysql://mysql:3306/ruoyi_quality?useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai",
                            "username": "root",
                            "password": "${QUALITY_MYSQL_PASSWORD}",
                            "driver-class-name": "com.mysql.cj.jdbc.Driver",
                        }
                    },
                }
            },
            "data": {
                "redis": {
                    "host": "redis",
                    "port": 6379,
                    "database": 0,
                    "password": "${QUALITY_REDIS_PASSWORD}",
                }
            },
            "quartz": {"auto-startup": False, "job-store-type": "memory"},
            "boot": {"admin": {"client": {"enabled": False}}},
            "main": {"lazy-initialization": False},
        },
        "springdoc": {"api-docs": {"enabled": False}, "swagger-ui": {"enabled": False}},
        "management": {"endpoints": {"web": {"exposure": {"include": "health"}}}},
        "yudao": {
            "web": {"admin-ui": {"url": "http://127.0.0.1:14880"}},
            "security": {"mock-enable": False},
            "demo": False,
            "captcha": {"enable": False},
            "tenant": {"enable": True},
        },
        "aether": {
            "social": {"enabled": False},
            "ops": {"base-url": "http://platform:8090"},
            "identity": {"platform-tenant-id": 1, "service-clients": "aether-quality"},
            "notification-key": "${QUALITY_NOTIFICATION_KEY}",
            "notification-admin-ids": "50001",
        },
        "justauth": {"enabled": False},
    }
