DCP Railway diagnostic

Files:
- dcp_railway_test.py
- requirements.txt

Railway start command:
python dcp_railway_test.py

Optional variables:
DCP_RUN_ONCE=1          Run one cycle and exit.
DCP_TEST_INTERVAL=900   Seconds between cycles (default 15 minutes).
DCP_TIMEOUT=30          HTTP timeout.
DCP_TEST_URLS=URL1,URL2 Replace default public test URLs.

The test is read-only. It does not log in, solve CAPTCHA, bypass Cloudflare,
or change anything on DCP.
