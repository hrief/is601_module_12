from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.calculation import Calculation
from app.models.user import User


@pytest.fixture(scope="module")
def client(setup_test_database):
    with TestClient(app) as test_client:
        yield test_client


def registration_payload():
    username = f"user_{uuid4().hex}"
    return {
        "username": username,
        "email": f"{username}@example.com",
        "first_name": "Module",
        "last_name": "Twelve",
        "password": "Module12!Password",
        "confirm_password": "Module12!Password",
    }


def register_and_login(client):
    payload = registration_payload()
    created = client.post("/users/register", json=payload)
    assert created.status_code == 201, created.text
    login = client.post("/users/login", json={
        "username": payload["username"], "password": payload["password"],
    })
    assert login.status_code == 200, login.text
    return created.json()["id"], {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_register_and_login_persist_user(client, db_session):
    payload = registration_payload()
    invalid = client.post("/users/register", json={**payload, "confirm_password": "Different12!"})
    assert invalid.status_code == 422
    assert "Passwords do not match" in invalid.json()["detail"][0]["msg"]
    assert payload["password"] not in invalid.text
    assert "Different12!" not in invalid.text
    assert db_session.query(User).filter_by(username=payload["username"]).one_or_none() is None

    created = client.post("/users/register", json=payload)
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["username"] == payload["username"]
    assert data["email"] == payload["email"]
    for field in ("password", "confirm_password", "hashed_password"):
        assert field not in data

    duplicate = client.post("/users/register", json=payload)
    assert duplicate.status_code == 400
    assert duplicate.json() == {"detail": "Username or email already exists"}

    user = db_session.get(User, UUID(data["id"]))
    assert user.password != payload["password"]
    assert user.verify_password(payload["password"])
    assert user.last_login is None

    denied = client.post("/users/login", json={
        "username": user.username, "password": "WrongPassword12!",
    })
    assert denied.status_code == 401
    assert denied.json() == {"detail": "Invalid username or password"}
    db_session.refresh(user)
    assert user.last_login is None

    login = client.post("/users/login", json={
        "username": user.username, "password": payload["password"],
    })
    assert login.status_code == 200, login.text
    assert login.json()["user_id"] == data["id"]
    assert login.json()["token_type"] == "bearer"
    db_session.refresh(user)
    assert user.last_login is not None


def test_calculation_lifecycle_and_ownership(client, db_session):
    owner_id, headers = register_and_login(client)
    other_id, other_headers = register_and_login(client)
    assert client.get("/calculations").status_code == 401

    created = client.post("/calculations", json={
        "type": "division", "inputs": [24, 2], "user_id": other_id,
    }, headers=headers)
    assert created.status_code == 201, created.text
    data = created.json()
    calculation_id = UUID(data["id"])
    url = f"/calculations/{calculation_id}"
    assert data["user_id"] == owner_id
    assert data["result"] == 12
    assert client.get("/calculations", headers=headers).json() == [data]
    assert client.get("/calculations", headers=other_headers).json() == []

    for response in (
        client.get(url, headers=other_headers),
        client.put(url, json={"inputs": [15, 3]}, headers=other_headers),
        client.delete(url, headers=other_headers),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Calculation not found."}

    read = client.get(url, headers=headers)
    assert read.status_code == 200
    assert read.json() == data
    invalid = client.put(url, json={"inputs": [24, 0]}, headers=headers)
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "Cannot divide by zero."}
    assert client.get(url, headers=headers).json() == data

    updated = client.put(url, json={"inputs": [15, 3]}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"] == 5
    assert client.get(url, headers=headers).json() == updated.json()
    stored = db_session.get(Calculation, calculation_id)
    assert stored.inputs == [15, 3]
    assert stored.result == 5
    db_session.expire_all()

    deleted = client.delete(url, headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(url, headers=headers).status_code == 404
    assert db_session.query(Calculation).filter_by(id=calculation_id).one_or_none() is None


@pytest.mark.parametrize(("payload", "field"), [
    ({"type": "power", "inputs": [2, 3]}, "type"),
    ({"type": "addition", "inputs": [2]}, "inputs"),
    ({"type": "division", "inputs": [2, 0]}, None),
    ({"type": "addition", "inputs": ["Infinity", 2]}, 0),
])
def test_invalid_calculation_creates_no_row(client, payload, field):
    _, headers = register_and_login(client)
    response = client.post("/calculations", json=payload, headers=headers)
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"][-1] == ("body" if field is None else field)
    assert client.get("/calculations", headers=headers).json() == []


def test_openapi_exposes_assignment_endpoints(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/users/register"]
    assert "post" in paths["/users/login"]
    assert {"get", "post"} <= paths["/calculations"].keys()
    assert {"get", "put", "delete"} <= paths["/calculations/{calc_id}"].keys()
