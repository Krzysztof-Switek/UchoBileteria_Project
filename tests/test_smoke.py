import pytest
from django.conf import settings


def test_home_page_returns_200(client, db):
    response = client.get("/")
    assert response.status_code == 200


def test_timezone_is_warsaw():
    assert settings.TIME_ZONE == "Europe/Warsaw"


@pytest.mark.django_db
def test_admin_login_page_available(client):
    response = client.get("/admin/login/")
    assert response.status_code == 200
