"""Fixtures for tests that talk to your deployed weather stack."""
import pytest

from stack.database import DbOutputs
from stack.wclients import WeatherClients
from stack.wconfig import WeatherSettings
from stack.wstack import WeatherOutputs
from tests.whelpers import ROOT, wait_healthy


@pytest.fixture(scope="session")
def wsettings():
    return WeatherSettings.from_file(ROOT / ".env")


@pytest.fixture(scope="session")
def waws(wsettings):
    return WeatherClients.create(wsettings.region)


@pytest.fixture(scope="session")
def wouts():
    try:
        return WeatherOutputs.load(ROOT)
    except FileNotFoundError:
        pytest.skip("stack_outputs.json not found. Run python3 create_weather_stack.py first.")


@pytest.fixture(scope="session")
def wdb():
    try:
        return DbOutputs.load(ROOT)
    except FileNotFoundError:
        pytest.skip("db_outputs.env not found. Run python3 create_database.py first.")


@pytest.fixture(scope="session")
def healthy(waws, wouts):
    assert wait_healthy(waws, wouts), "targets never became healthy"
    return True


@pytest.fixture(scope="session")
def weather_url(wsettings, wouts):
    return f"http://{wouts.alb_dns_name}:{wsettings.weather.listener_port}"


@pytest.fixture(scope="session")
def advisor_url(wsettings, wouts):
    return f"http://{wouts.alb_dns_name}:{wsettings.advisor.listener_port}"
