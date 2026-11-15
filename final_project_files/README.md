# Final Project: steps to build, test and submit

Every command is run in the terminal inside the container, with the virtual environment active. The folder names and file names are the same as in lab06. The write-up to submit is `Final Project.md`, and each 📸 below says which section of it the screenshot goes into.

## Step 1. Put the folder in place

Unzip `final_project.zip` and put the `final_project` folder in `/ITMO-544`, next to `lab06`. It is the complete project: the lab06 files that did not change, the files that changed, and the new ones. Lab06 itself is never edited.

```
cd /ITMO-544/final_project
ls
```

## Step 2. Copy the settings from lab06

The `.env` file holds the settings of the account, so it is not in the zip. Copy it from lab06:

```
cp ../lab06/.env .
```

## Step 3. Add the new settings

Add the new settings to `.env` and set the cache time to 30 minutes. Paste the whole block at the prompt:

```
cat >> .env << 'EOF'
SNS_TOPIC_NAME=itmo-544-weather-reports
STALE_SECONDS=21600
REPORT_SECONDS=3600
GEMINI_DAILY_CAP=200
GEMINI_PER_MINUTE=10
VISITOR_PER_MINUTE=20
EMAILS_PER_VISITOR_HOUR=5
EOF
sed -i 's/^CACHE_SECONDS=.*/CACHE_SECONDS=1800/' .env
grep -c . .env
```

## Step 4. One new permission for lab-user

The create script makes an SNS topic. In the IAM console, open lab-user, choose Add permissions, Create inline policy, JSON, paste this, and name it `final-sns`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["sns:CreateTopic", "sns:DeleteTopic"],
      "Resource": "arn:aws:sns:us-east-2:*:itmo-544-weather-reports"
    }
  ]
}
```

The inline policies `lab06-rds` and `lab06-ssm` are still needed. If they were deleted after lab06, add them again as in lab06.

## Step 5. Install

```
node --version
pip install -r requirements.txt
(cd weather-app && npm install)
(cd advisor-app && npm install)
```

Node must be version 18 or newer, because the application tests use the test runner that is built into Node.

## Step 6. Tests that need no AWS account and no network

```
python3 -m pytest -v
(cd weather-app && npm test)
(cd advisor-app && npm test)
```

Expected: 31 passed in pytest, 31 passed and 4 skipped in `weather-app`, 39 passed in `advisor-app`.

> 📸 **Screenshot 1** (section 5.1): the last lines of each of the three commands.

## Step 7. Record the real API responses

```
python3 testing/investigate_sources.py
(cd weather-app && npm test)
```

The four `contract:` tests now run. The script also writes `source_map_evidence.md`.

> 📸 **Screenshot 2** (section 2.3): the output of `investigate_sources.py`.
> 📸 **Screenshot 3** (section 5.2): the four `contract:` tests passing.

Then, in `Final Project.md`: paste `source_map_evidence.md` into section 2.3, and complete the three places marked *(...)* in section 2.4 from what the script printed.

## Step 8. Deploy

```
python3 create_database.py
python3 testing/store_gemini_key.py
python3 create_weather_stack.py
```

The database takes 5 to 10 minutes. If the Gemini key was deleted in Google AI Studio after lab07, create a new key first.

> 📸 **Screenshot 4** (section 5.3): the output of `create_weather_stack.py`, sections 1 to 7 and the two addresses.

Wait 3 to 4 minutes, then:

```
python3 testing/app_smoke_test.py
```

> 📸 **Screenshot 5** (section 5.4): the smoke test with all 9 checks passing.

Open the advisor address in a browser, enter a zip code, tomorrow's date, a gender choice and an email address.

> 📸 **Screenshot 6** (section 5.3): the advisor page with the report.

## Step 9. Integration tests

```
python3 -m pytest -m integration -v -s
```

> 📸 **Screenshot 7** (section 5.4): the pytest summary, with the lines `new report took ... s` and `cached report took ... s`. Write the two times into the table of section 5.4.

## Step 10. Email with two inboxes

Follow the five steps of section 5.6 with two email addresses.

> 📸 **Screenshot 8** (section 5.6): the page saying "Waiting for confirmation".
> 📸 **Screenshot 9** (section 5.6): the page saying the report was accepted.
> 📸 **Screenshot 10** (section 5.6): inbox A and inbox B, each with only its own report.

## Step 11. Chaos tests

```
python3 -m pytest -m chaos -v -s
```

This takes about 5 minutes. It terminates the weather instance and waits for the replacement.

> 📸 **Screenshot 11** (section 5.5): the output, with the line about the Gemini cap and the line `weather tier recovered after ... s`. Write both results into the table of section 5.5.

## Step 12. Delete everything and report the cost

Delete the stack before the database, so the usage counts can be read.

```
python3 delete_weather_stack.py
python3 delete_database.py
python3 budget.py
```

> 📸 **Screenshot 12** (section 6): the output of `budget.py`. Write the sentence under it in section 6.

Delete the stack every time a work session ends. `budget.py` adds up all sessions from `usage_log.jsonl`.

## Step 13. After the project

1. Delete the inline policies `final-sns`, `lab06-rds` and `lab06-ssm` from lab-user.
2. Delete the Gemini key in Google AI Studio.
3. Look at the Billing page the next day.

## What changed from lab06

The folder names and file names are the same as in lab06. The lab04 bash scripts in `previous/`, the lab05 uploader and viewer code and tests, the lab06 write-up and the key file are not in this folder.

| File | New or changed | What changed |
|---|---|---|
| `weather-app/app.js`, `package.json` | changed | A date, Morning and Night, seven facts, a second source, the categories |
| `advisor-app/app.js`, `package.json` | changed | Four fields, the report page, three kinds of advice with checking, caps and limits, email |
| `weather-app/app.test.js`, `advisor-app/app.test.js` | new | The application logic tests (Node.js) |
| `stack/wconfig.py`, `wclients.py`, `wpolicies.py`, `wstack.py` | changed | New settings, the SNS client, the advisor policy, the topic, the Python boot script |
| `stack/compute.py`, `stack/policies.py` | changed | The lab05 bash boot script and the lab05 S3 policies were removed |
| `stack/usage.py`, `budget.py` | new | The usage log and the cost report |
| `create_weather_stack.py`, `delete_weather_stack.py`, `create_database.py`, `delete_database.py` | changed | Each writes a line to the usage log |
| `tests/unit/test_weather_stack.py`, `tests/wsupport.py`, `tests/whelpers.py`, `tests/integration/test_weather_*.py` | changed | Tests for the new behavior |
| `tests/unit/test_weather_budget.py` | new | Tests of the cost report |
| `testing/app_smoke_test.py` | changed | Checks for the new report |
| `testing/investigate_sources.py` | new | Records real API responses and writes the source map evidence |
| `Final Project.md`, `images/architecture.png` | new | The write-up |
| `.env.example`, `.gitignore`, `requirements.txt` | changed | New keys, ignored files, moto services |
