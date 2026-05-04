# One-shot HTTP trigger for Railway Cron (must exit when done).
# Deploy as a separate Railway service with Root Directory = cron-trigger
# and Cron Schedule = 0 5 * * *  (05:00 UTC daily).
FROM curlimages/curl:8.11.1
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
