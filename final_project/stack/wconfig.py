"""Settings for the weather advisor stack, read from the same .env file."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, Tier, load_dotenv

REQUIRED_KEYS = [
    "AWS_REGION", "AZ_A", "AZ_B", "AMI_ID", "INSTANCE_TYPE", "KEY_NAME", "APP_SECURITY_GROUP_ID",
    "WEATHER_ROLE_NAME", "WEATHER_PROFILE_NAME", "WEATHER_LT_NAME", "WEATHER_TG_NAME",
    "WEATHER_ASG_NAME", "WEATHER_LISTENER_PORT",
    "ADVISOR_ROLE_NAME", "ADVISOR_PROFILE_NAME", "ADVISOR_LT_NAME", "ADVISOR_TG_NAME",
    "ADVISOR_ASG_NAME", "ADVISOR_LISTENER_PORT",
    "ALB_NAME", "ALB_SG_NAME",
    "DB_INSTANCE_ID", "DB_NAME", "DB_USER", "DB_SG_NAME", "DB_SUBNET_GROUP_NAME",
    "DB_PASSWORD_PARAM", "CACHE_SECONDS", "GEMINI_PARAM_NAME", "GEMINI_MODEL",
    "KEY_CACHE_SECONDS", "CONTACT_EMAIL",
    "SNS_TOPIC_NAME", "STALE_SECONDS", "REPORT_SECONDS", "GEMINI_DAILY_CAP", "GEMINI_PER_MINUTE",
    "VISITOR_PER_MINUTE", "EMAILS_PER_VISITOR_HOUR",
]


def positive(v: dict[str, str], key: str) -> int:
    """Read one setting as a whole number above zero, naming the key if it is not."""
    try:
        number = int(v[key])
    except ValueError:
        number = 0
    if number < 1:
        raise ConfigError(f"{key} in .env must be a whole number above zero.")
    return number


@dataclass(frozen=True)
class WeatherSettings:
    """Everything the database and weather stack scripts need, read from .env."""
    region: str
    az_a: str
    az_b: str
    ami_id: str
    instance_type: str
    key_name: str
    app_sg_id: str
    alb_name: str
    alb_sg_name: str
    db_instance_id: str
    db_name: str
    db_user: str
    db_sg_name: str
    db_subnet_group_name: str
    db_password_param: str
    gemini_param_name: str
    gemini_model: str
    key_cache_seconds: int
    cache_seconds: int
    contact_email: str
    sns_topic_name: str
    stale_seconds: int
    report_seconds: int
    gemini_daily_cap: int
    gemini_per_minute: int
    visitor_per_minute: int
    emails_per_visitor_hour: int
    weather: Tier
    advisor: Tier
    app_port: int = 3000
    db_port: int = 5432

    @property
    def tiers(self) -> tuple[Tier, Tier]:
        """Both application tiers, weather first."""
        return (self.weather, self.advisor)

    def tier(self, name: str) -> Tier:
        """Look a tier up by name."""
        return {"weather": self.weather, "advisor": self.advisor}[name]

    @classmethod
    def from_values(cls, v: dict[str, str], base_dir: str | Path = ".") -> "WeatherSettings":
        """Build settings from a dict, naming every missing key in the error."""
        missing = [k for k in REQUIRED_KEYS if not v.get(k)]
        if missing:
            raise ConfigError("Missing keys in .env: " + ", ".join(missing))
        base = Path(base_dir)

        def tier(prefix: str) -> Tier:
            return Tier(
                name=prefix.lower(),
                role_name=v[f"{prefix}_ROLE_NAME"],
                profile_name=v[f"{prefix}_PROFILE_NAME"],
                lt_name=v[f"{prefix}_LT_NAME"],
                tg_name=v[f"{prefix}_TG_NAME"],
                asg_name=v[f"{prefix}_ASG_NAME"],
                listener_port=int(v[f"{prefix}_LISTENER_PORT"]),
                app_dir=base / f"{prefix.lower()}-app",
            )

        return cls(
            region=v["AWS_REGION"], az_a=v["AZ_A"], az_b=v["AZ_B"], ami_id=v["AMI_ID"],
            instance_type=v["INSTANCE_TYPE"], key_name=v["KEY_NAME"],
            app_sg_id=v["APP_SECURITY_GROUP_ID"], alb_name=v["ALB_NAME"],
            alb_sg_name=v["ALB_SG_NAME"], db_instance_id=v["DB_INSTANCE_ID"],
            db_name=v["DB_NAME"], db_user=v["DB_USER"], db_sg_name=v["DB_SG_NAME"],
            db_subnet_group_name=v["DB_SUBNET_GROUP_NAME"],
            db_password_param=v["DB_PASSWORD_PARAM"],
            gemini_param_name=v["GEMINI_PARAM_NAME"], gemini_model=v["GEMINI_MODEL"],
            key_cache_seconds=positive(v, "KEY_CACHE_SECONDS"),
            cache_seconds=positive(v, "CACHE_SECONDS"), contact_email=v["CONTACT_EMAIL"],
            sns_topic_name=v["SNS_TOPIC_NAME"], stale_seconds=positive(v, "STALE_SECONDS"),
            report_seconds=positive(v, "REPORT_SECONDS"),
            gemini_daily_cap=positive(v, "GEMINI_DAILY_CAP"),
            gemini_per_minute=positive(v, "GEMINI_PER_MINUTE"),
            visitor_per_minute=positive(v, "VISITOR_PER_MINUTE"),
            emails_per_visitor_hour=positive(v, "EMAILS_PER_VISITOR_HOUR"),
            weather=tier("WEATHER"), advisor=tier("ADVISOR"),
        )

    @classmethod
    def from_file(cls, path: str | Path = ".env") -> "WeatherSettings":
        """Read the .env file at the given path."""
        path = Path(path)
        return cls.from_values(load_dotenv(path), base_dir=path.resolve().parent)
