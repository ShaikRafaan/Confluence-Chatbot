import json
import sys
from pydantic import ValidationError
from schema import ConfluenceExport


def validate_json_file(file_path: str) -> ConfluenceExport:
    with open(file_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    validated_data = ConfluenceExport.model_validate(data)
    return validated_data


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_json.py <json_file_path>")
        sys.exit(1)

    file_path = sys.argv[1]

    try:
        validated = validate_json_file(file_path)

        print("JSON validation passed")
        print(f"Total pages: {validated.total_pages}")

    except json.JSONDecodeError as e:
        print("Invalid JSON syntax")
        print(e)
        sys.exit(1)

    except ValidationError as e:
        print("JSON schema validation failed")
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()