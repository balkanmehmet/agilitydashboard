# Streamlit Dashboard for Standup Function App

## Purpose
This dashboard is meant to be shown by the Recall bot as webpage output.

It polls the Azure Function App state endpoint and shows:
- current issue
- current assignee
- bot status
- spoken text
- progress

## Environment variables
- FUNCTION_APP_BASE_URL=https://<your-function-app>.azurewebsites.net
- FUNCTION_APP_CODE=<optional function key>
- STANDUP_INSTANCE_ID=<optional default instance id>
- REFRESH_SECONDS=3

## Install
```bash
pip install -r requirements.txt
```

## Run
```bash
streamlit run streamlit_app.py
```

## Notes
If your Function App is anonymous, you can leave FUNCTION_APP_CODE empty.
If your Function App requires a function key, set FUNCTION_APP_CODE.
