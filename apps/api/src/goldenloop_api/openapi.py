"""Print the API contract without starting the server or opening the database."""

import json

from .main import app


def main():
    print(json.dumps(app.openapi(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
