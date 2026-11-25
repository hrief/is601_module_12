# IS601 Module 12 Calculations API

FastAPI and PostgreSQL backend with user registration, JWT login, and calculation history scoped to each user.

- [GitHub repository](https://github.com/hrief/is601_module_12)
- [Docker Hub repository](https://hub.docker.com/r/hrief/is601_module_12), assuming `DOCKERHUB_USERNAME` is `hrief`

## Run locally

Start Docker and run:

```sh
docker compose up --build -d db web
```

Open [Swagger UI](http://localhost:8000/docs) or [ReDoc](http://localhost:8000/redoc). The Compose database initializes both `fastapi_db` and `fastapi_test_db` on its first startup.

## Check the API in Swagger UI

1. Try `POST /users/register` with the example below. Expect `201 Created` with the new user ID and profile. Repeating the registration returns `400` with an error in `detail`.

   ```json
   {
     "username": "module12_demo",
     "email": "module12_demo@example.com",
     "first_name": "Module",
     "last_name": "Twelve",
     "password": "Module12!Password",
     "confirm_password": "Module12!Password"
   }
   ```

2. Try `POST /users/login` with `{"username":"module12_demo","password":"Module12!Password"}`. Expect `200 OK` with an access token, refresh token, and user ID. A wrong password of valid length returns `401`.
3. Click **Authorize** and enter the same username and password. Swagger UI uses the form endpoint `POST /auth/token` and adds the bearer token to requests.
4. Try `POST /calculations` with `{"type":"division","inputs":[24,2]}`. Expect `201`, a calculation ID, your user ID, and `result: 12`. Try `GET /calculations` and confirm it contains the new calculation. Use its ID in `GET /calculations/{calc_id}` to read it directly.
5. Try `PUT /calculations/{calc_id}` with `{"inputs":[15,3]}`. Expect `200` and `result: 5`. Submitting `{"inputs":[15,0]}` returns `400` and leaves the stored result at `5`. Creating a division with a zero divisor returns `422`.
6. Try `DELETE /calculations/{calc_id}`. Expect `204` with an empty response. Reading the same ID returns `404`.

The calculation type is set at creation. Edits replace the operands and recalculate the result.

## Run integration tests

Start PostgreSQL with `docker compose up -d db`. Use Python 3.13 and point `DATABASE_URL` to the dedicated test database. The test fixtures drop and recreate application tables in that database.

macOS or Linux:

```sh
python3.13 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/fastapi_test_db
python -m pytest tests/integration/
```

Windows PowerShell:

```powershell
py -3.13 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/fastapi_test_db"
.\venv\Scripts\python.exe -m pytest tests/integration/
```

With the same test database configured, run `python -m pytest --run-slow` using the virtual environment for the full suite.

## CI/CD and Docker Hub

GitHub Actions runs pytest with a PostgreSQL 17 service on branch pushes and on pull requests targeting `main`. A push to `main` that passes tests and the image security scan publishes Linux AMD64 and ARM64 images tagged `latest` and with the commit SHA.

Create the `is601_module_12` repository in the Docker Hub account you use for this project. Set `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` in GitHub Actions secrets for the repository or its `production` environment. The token needs write access to the Docker Hub repository.

The published image requires a reachable PostgreSQL database and a `DATABASE_URL`, `JWT_SECRET_KEY`, and `JWT_REFRESH_SECRET_KEY` for the deployment environment.
