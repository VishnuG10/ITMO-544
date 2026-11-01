#!/usr/bin/env python3
"""Store the Gemini API key in SSM Parameter Store as a SecureString."""
import getpass
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stack.config import load_dotenv


def main() -> None:
    """Ask for the key at a hidden prompt and store it encrypted."""
    env = load_dotenv(ROOT / ".env")
    ssm = boto3.client("ssm", region_name=env["AWS_REGION"])
    key = getpass.getpass("Gemini API key: ")
    ssm.put_parameter(Name=env["GEMINI_PARAM_NAME"], Type="SecureString", Value=key, Overwrite=True)
    print(f"Stored {env['GEMINI_PARAM_NAME']} as a SecureString.")


if __name__ == "__main__":
    main()
