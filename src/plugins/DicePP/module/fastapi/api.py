from fastapi import FastAPI, Path, Query

dpp_api = FastAPI()


@dpp_api.get("/")
def test_api():
    return {"Test": "This is a test api"}
