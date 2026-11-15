"""Fake settings and fake app folders shared by the weather unit tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WFAKE_VALUES = {
    "AWS_REGION": "us-east-2", "AZ_A": "us-east-2a", "AZ_B": "us-east-2b",
    "AMI_ID": "ami-12345678", "INSTANCE_TYPE": "t3.micro", "KEY_NAME": "test-key",
    "APP_SECURITY_GROUP_ID": "sg-placeholder",
    "WEATHER_ROLE_NAME": "t-weather-role", "WEATHER_PROFILE_NAME": "t-weather-profile",
    "WEATHER_LT_NAME": "t-weather-lt", "WEATHER_TG_NAME": "t-weather-tg",
    "WEATHER_ASG_NAME": "t-weather-asg", "WEATHER_LISTENER_PORT": "8080",
    "ADVISOR_ROLE_NAME": "t-advisor-role", "ADVISOR_PROFILE_NAME": "t-advisor-profile",
    "ADVISOR_LT_NAME": "t-advisor-lt", "ADVISOR_TG_NAME": "t-advisor-tg",
    "ADVISOR_ASG_NAME": "t-advisor-asg", "ADVISOR_LISTENER_PORT": "8081",
    "ALB_NAME": "t-weather-alb", "ALB_SG_NAME": "t-weather-alb-sg",
    "DB_INSTANCE_ID": "t-weather-db", "DB_NAME": "weather", "DB_USER": "weatherapp",
    "DB_SG_NAME": "t-weather-db-sg", "DB_SUBNET_GROUP_NAME": "t-weather-db-subnets",
    "DB_PASSWORD_PARAM": "/t444/db-password", "CACHE_SECONDS": "600",
    "GEMINI_PARAM_NAME": "/t444/gemini-api-key", "GEMINI_MODEL": "gemini-test-model",
    "KEY_CACHE_SECONDS": "60", "CONTACT_EMAIL": "student@example.com",
}


def make_app_dirs(base) -> None:
    """Create tiny fake weather-app and advisor-app folders."""
    for name in ("weather-app", "advisor-app"):
        folder = Path(base) / name
        folder.mkdir(exist_ok=True)
        (folder / "app.js").write_text(f"console.log('{name}');\n")
        (folder / "package.json").write_text('{"name": "%s"}\n' % name)
