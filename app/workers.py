from concurrent.futures import ThreadPoolExecutor


ALAS_OVERVIEW_MAX_WORKERS = 6

# A process-wide pool keeps Runtime fan-out bounded even when requests are
# cancelled or served by different event loops. Running work continues to own
# its worker until the blocking HTTP call has actually returned.
alas_overview_executor = ThreadPoolExecutor(
    max_workers=ALAS_OVERVIEW_MAX_WORKERS,
    thread_name_prefix="alas-overview",
)
